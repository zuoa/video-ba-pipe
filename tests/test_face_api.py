import base64
import io
import json
import zipfile
from datetime import datetime, timedelta

import peewee as pw
import numpy as np
import pytest
from flask import Flask
from PIL import Image

from app.core import face_crypto
from app.core.database_models import (
    FaceGallery,
    FaceGalleryMembership,
    FaceImportJob,
    FaceModelArtifact,
    FaceModelBundle,
    FacePerson,
    FaceTemplate,
    User,
)
from app.core.face_gallery import serialize_embedding
from app.web.api import faces
from app.web.api.auth import generate_token


@pytest.fixture()
def face_api(monkeypatch, tmp_path):
    database = pw.SqliteDatabase(tmp_path / 'face-api.db', pragmas={'foreign_keys': 1})
    models = [
        User,
        FaceModelBundle,
        FaceModelArtifact,
        FaceGallery,
        FacePerson,
        FaceGalleryMembership,
        FaceTemplate,
        FaceImportJob,
    ]
    originals = {model: model._meta.database for model in models}
    database.bind(models, bind_refs=False, bind_backrefs=False)
    database.create_tables(models)
    monkeypatch.setattr(faces, 'db', database)
    monkeypatch.setattr(faces, 'FACE_MODEL_PATH', str(tmp_path / 'face-models'))
    monkeypatch.setattr(faces, 'FACE_DATA_PATH', str(tmp_path / 'face-data'))
    monkeypatch.setattr(faces, 'FACE_EVENT_PATH', str(tmp_path / 'face-events'))

    key = base64.urlsafe_b64encode(b'a' * 32).decode('ascii').rstrip('=')
    monkeypatch.setenv('FACE_DATA_ENCRYPTION_KEY', key)
    monkeypatch.delenv('FACE_DATA_ENCRYPTION_KEY_FILE', raising=False)
    face_crypto.face_encryption_key.cache_clear()

    user = User.create(
        username='face-admin',
        password_hash='unused',
        role='admin',
        enabled=True,
        created_at=datetime.now(),
    )
    app = Flask(__name__)
    app.register_blueprint(faces.faces_bp)
    client = app.test_client()
    token = generate_token(user.id, user.username, user.role)
    headers = {'Authorization': f'Bearer {token}'}
    try:
        yield client, headers
    finally:
        database.drop_tables(models)
        database.close()
        for model, original in originals.items():
            model._meta.set_database(original)
        face_crypto.face_encryption_key.cache_clear()


def test_admin_can_generate_face_encryption_key(face_api, monkeypatch, tmp_path):
    client, headers = face_api
    key_path = tmp_path / 'secrets' / 'face-data.key'
    monkeypatch.delenv('FACE_DATA_ENCRYPTION_KEY', raising=False)
    monkeypatch.setenv('FACE_DATA_ENCRYPTION_KEY_FILE', str(key_path))
    face_crypto.face_encryption_key.cache_clear()

    response = client.post('/api/face/encryption-key/generate', headers=headers)

    assert response.status_code == 201
    assert response.get_json() == {
        'success': True,
        'encryption_ready': True,
        'created': True,
        'source': 'configured_file',
    }
    assert key_path.stat().st_mode & 0o777 == 0o400
    assert client.get('/api/face/runtime', headers=headers).get_json()[
        'encryption_ready'
    ] is True

    second = client.post('/api/face/encryption-key/generate', headers=headers)
    assert second.status_code == 200
    assert second.get_json()['created'] is False


def test_face_encryption_key_generation_requires_admin(
    face_api, monkeypatch, tmp_path,
):
    client, _headers = face_api
    user = User.create(
        username='face-viewer', password_hash='unused', role='user', enabled=True,
        created_at=datetime.now(),
    )
    viewer_headers = {
        'Authorization': f'Bearer {generate_token(user.id, user.username, user.role)}'
    }
    monkeypatch.delenv('FACE_DATA_ENCRYPTION_KEY', raising=False)
    monkeypatch.setenv(
        'FACE_DATA_ENCRYPTION_KEY_FILE', str(tmp_path / 'viewer-face-data.key')
    )
    face_crypto.face_encryption_key.cache_clear()

    response = client.post(
        '/api/face/encryption-key/generate', headers=viewer_headers
    )

    assert response.status_code == 403


def test_face_encryption_key_generation_refuses_existing_protected_data(
    face_api, monkeypatch, tmp_path,
):
    client, headers = face_api
    key_path = tmp_path / 'replacement-face-data.key'
    monkeypatch.delenv('FACE_DATA_ENCRYPTION_KEY', raising=False)
    monkeypatch.setenv('FACE_DATA_ENCRYPTION_KEY_FILE', str(key_path))
    monkeypatch.setattr(faces, '_protected_face_data_exists', lambda: True)
    face_crypto.face_encryption_key.cache_clear()

    response = client.post('/api/face/encryption-key/generate', headers=headers)

    assert response.status_code == 409
    assert '恢复原密钥' in response.get_json()['error']
    assert not key_path.exists()


def test_face_admin_enrollment_flow_encrypts_biometrics(face_api, monkeypatch):
    client, headers = face_api
    assert client.get('/api/face/galleries').status_code == 401

    response = client.post('/api/face/model-bundles', headers=headers, json={
        'name': 'portable-face',
        'version': 'v1',
        'contract_id': 'arcface-512-v1',
        'embedding_dimension': 32,
        'commercial_use_allowed': False,
    })
    assert response.status_code == 201
    bundle_id = response.get_json()['bundle']['id']

    response = client.post('/api/face/galleries', headers=headers, json={
        'name': 'employees',
        'model_bundle_id': bundle_id,
        'low_threshold': 0.5,
        'high_threshold': 0.6,
    })
    assert response.status_code == 201
    gallery_id = response.get_json()['gallery']['id']

    response = client.post('/api/face/persons', headers=headers, json={
        'person_code': 'E-1001',
        'name': '测试人员',
        'gallery_ids': [gallery_id],
    })
    assert response.status_code == 201
    person_id = response.get_json()['person']['id']

    embedding = serialize_embedding([1.0] + [0.0] * 31)
    monkeypatch.setattr(faces, '_extract_enrollment', lambda *_args, **_kwargs: {
        'embedding_base64': base64.b64encode(embedding).decode('ascii'),
        'quality': {'accepted': True, 'score': 0.91},
        'model_contract': 'arcface-512-v1',
        'backend': 'onnxruntime',
    })
    image_buffer = io.BytesIO()
    Image.new('RGB', (16, 16), color=(120, 90, 70)).save(
        image_buffer, format='JPEG'
    )
    image = image_buffer.getvalue()
    response = client.post(
        f'/api/face/persons/{person_id}/templates',
        headers=headers,
        data={'file': (io.BytesIO(image), 'face.jpg')},
        content_type='multipart/form-data',
    )
    assert response.status_code == 201
    template = FaceTemplate.get_by_id(response.get_json()['template_id'])
    assert image not in bytes(template.encrypted_image)
    assert embedding not in bytes(template.encrypted_embedding)
    assert template.model_contract == 'arcface-512-v1'
    assert FaceGallery.get_by_id(gallery_id).gallery_version == 3
    image_response = client.get(
        f'/api/face/templates/{template.id}', headers=headers
    )
    assert image_response.data == image
    assert image_response.headers['Cache-Control'] == 'no-store, private'


def test_threshold_update_bumps_version_and_negative_calibration_is_applicable(
    face_api, monkeypatch,
):
    client, headers = face_api
    now = datetime.now()
    bundle = FaceModelBundle.create(
        name='calibration-bundle', version='v1', contract_id='calibration-v1',
        embedding_dimension=2, created_at=now, updated_at=now,
    )
    gallery = FaceGallery.create(
        name='negative-calibration', model_bundle=bundle,
        created_at=now, updated_at=now, created_by='face-admin',
    )
    monkeypatch.setattr(
        faces.gallery_index_cache,
        'get',
        lambda _gallery_id: type('Index', (), {
            'template_count': 2,
            'matrix': np.asarray([[1.0, 0.0], [-1.0, 0.0]], dtype=np.float32),
            'people': [(1, 'P-1', '甲'), (2, 'P-2', '乙')],
        })(),
    )
    invalidated = []
    monkeypatch.setattr(
        faces.gallery_index_cache, 'invalidate', invalidated.append
    )

    response = client.post(
        '/api/face/calibrations',
        headers=headers,
        json={'gallery_id': gallery.id},
    )

    assert response.status_code == 200
    recommendation = response.get_json()
    low = recommendation['suggested_low_threshold']
    high = recommendation['suggested_high_threshold']
    assert 0 <= low < high <= 1

    response = client.patch(
        f'/api/face/galleries/{gallery.id}',
        headers=headers,
        json={'low_threshold': low, 'high_threshold': high},
    )

    assert response.status_code == 200
    assert FaceGallery.get_by_id(gallery.id).gallery_version == 2
    assert invalidated == [gallery.id]


def test_multicontract_enrollment_selects_gallery_contract(face_api, monkeypatch):
    client, headers = face_api
    now = datetime.now()
    bundles = [
        FaceModelBundle.create(
            name=f'enrollment-bundle-{index}', version='v1',
            contract_id=f'enrollment-v{index}', embedding_dimension=3,
            created_at=now, updated_at=now,
        )
        for index in (1, 2)
    ]
    galleries = [
        FaceGallery.create(
            name=f'enrollment-gallery-{index}', model_bundle=bundle,
            created_at=now, updated_at=now, created_by='face-admin',
        )
        for index, bundle in enumerate(bundles, start=1)
    ]
    person = FacePerson.create(
        person_code='MULTI-1', name='多契约人员', created_by='face-admin',
        created_at=now, updated_at=now,
    )
    for gallery in galleries:
        FaceGalleryMembership.create(gallery=gallery, person=person, created_at=now)
    selected_bundle_ids = []

    def extract(bundle_id, _image_bytes):
        selected_bundle_ids.append(bundle_id)
        bundle = FaceModelBundle.get_by_id(bundle_id)
        return {
            'embedding_base64': base64.b64encode(
                serialize_embedding([1.0, 0.0, 0.0])
            ).decode('ascii'),
            'quality': {'accepted': True, 'score': 0.9},
            'model_contract': bundle.contract_id,
            'backend': 'onnxruntime',
        }

    monkeypatch.setattr(faces, '_extract_enrollment', extract)
    image_buffer = io.BytesIO()
    Image.new('RGB', (16, 16), color=(50, 100, 150)).save(
        image_buffer, format='JPEG'
    )

    response = client.post(
        f'/api/face/persons/{person.id}/templates',
        headers=headers,
        data={
            'gallery_id': str(galleries[1].id),
            'file': (io.BytesIO(image_buffer.getvalue()), 'face.jpg'),
        },
        content_type='multipart/form-data',
    )

    assert response.status_code == 201
    assert selected_bundle_ids == [bundles[1].id]
    template = FaceTemplate.get_by_id(response.get_json()['template_id'])
    assert template.model_contract == bundles[1].contract_id
    assert FaceGallery.get_by_id(galleries[0].id).gallery_version == 1
    assert FaceGallery.get_by_id(galleries[1].id).gallery_version == 2


def test_face_import_preflight_rejects_path_traversal(face_api):
    client, headers = face_api
    archive = io.BytesIO()
    import zipfile
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('manifest.csv', 'person_code,name\nE-1,测试\n')
        output.writestr('../outside.jpg', b'bad')
    archive.seek(0)
    response = client.post(
        '/api/face/imports/preflight',
        headers=headers,
        data={'file': (archive, 'people.zip')},
        content_type='multipart/form-data',
    )
    assert response.status_code == 400
    assert '非法路径' in response.get_json()['error']


def test_face_artifact_runtime_requires_compatible_file_type(face_api):
    client, headers = face_api
    bundle = FaceModelBundle.create(
        name='artifacts', version='v1', contract_id='artifact-v1',
        created_at=datetime.now(), updated_at=datetime.now(),
    )
    response = client.post(
        f'/api/face/model-bundles/{bundle.id}/artifacts',
        headers=headers,
        data={
            'role': 'detection',
            'runtime': 'rknn',
            'file': (io.BytesIO(b'onnx'), 'detector.onnx'),
        },
        content_type='multipart/form-data',
    )
    assert response.status_code == 400
    assert '.rknn' in response.get_json()['error']


def test_insightface_model_package_imports_detection_and_embedding(face_api):
    client, headers = face_api
    bundle = FaceModelBundle.create(
        name='buffalo-package', version='v1', contract_id='buffalo-v1',
        created_at=datetime.now(), updated_at=datetime.now(),
    )
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('buffalo_l/det_10g.onnx', b'scrfd-model')
        archive.writestr('buffalo_l/w600k_r50.onnx', b'arcface-model')
        archive.writestr('buffalo_l/genderage.onnx', b'ignored-model')
    payload.seek(0)

    response = client.post(
        f'/api/face/model-bundles/{bundle.id}/packages',
        headers=headers,
        data={
            'runtime': 'onnxruntime',
            'architecture': 'any',
            'device': 'any',
            'file': (payload, 'buffalo_l.zip'),
        },
        content_type='multipart/form-data',
    )

    assert response.status_code == 201
    assert response.get_json()['imported'] == {
        'detection': 'buffalo_l/det_10g.onnx',
        'embedding': 'buffalo_l/w600k_r50.onnx',
    }
    artifacts = {
        artifact.role: artifact
        for artifact in FaceModelArtifact.select().where(
            FaceModelArtifact.bundle == bundle.id
        )
    }
    assert set(artifacts) == {'detection', 'embedding'}
    assert artifacts['detection'].metadata['output_format'] == 'scrfd'
    assert artifacts['detection'].metadata['input_shape'] == '640x640'
    assert artifacts['embedding'].metadata['batch_size'] == 1
    assert open(artifacts['detection'].file_path, 'rb').read() == b'scrfd-model'
    assert open(artifacts['embedding'].file_path, 'rb').read() == b'arcface-model'


def test_insightface_model_package_rejects_unsafe_paths(face_api):
    client, headers = face_api
    bundle = FaceModelBundle.create(
        name='unsafe-package', version='v1', contract_id='unsafe-v1',
        created_at=datetime.now(), updated_at=datetime.now(),
    )
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w') as archive:
        archive.writestr('../det_10g.onnx', b'detector')
        archive.writestr('w600k_r50.onnx', b'embedding')
    payload.seek(0)

    response = client.post(
        f'/api/face/model-bundles/{bundle.id}/packages',
        headers=headers,
        data={'file': (payload, 'unsafe.zip')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 400
    assert '非法路径' in response.get_json()['error']
    assert FaceModelArtifact.select().where(
        FaceModelArtifact.bundle == bundle.id
    ).count() == 0


def test_insightface_model_package_reports_missing_pair(face_api):
    client, headers = face_api
    bundle = FaceModelBundle.create(
        name='incomplete-package', version='v1', contract_id='incomplete-v1',
        created_at=datetime.now(), updated_at=datetime.now(),
    )
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w') as archive:
        archive.writestr('det_10g.onnx', b'detector')
    payload.seek(0)

    response = client.post(
        f'/api/face/model-bundles/{bundle.id}/packages',
        headers=headers,
        data={'file': (payload, 'incomplete.zip')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 400
    assert 'w600k_r50.onnx' in response.get_json()['error']


def test_runtime_reports_bundle_not_ready_when_verification_fails(
    face_api, monkeypatch,
):
    client, headers = face_api
    now = datetime.now()
    bundle = FaceModelBundle.create(
        name='missing-runtime-artifact', version='v1', contract_id='missing-v1',
        created_at=now, updated_at=now,
    )
    monkeypatch.setattr(faces, 'runtime_capabilities', lambda: {})
    monkeypatch.setattr(
        faces,
        'verify_bundle_artifacts',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError('模型制品文件不存在')
        ),
    )

    response = client.get('/api/face/runtime', headers=headers)

    assert response.status_code == 200
    runtime_bundle = next(
        item for item in response.get_json()['bundles']
        if item['bundle_id'] == bundle.id
    )
    assert runtime_bundle['ready'] is False
    assert '文件不存在' in runtime_bundle['error']


@pytest.mark.parametrize('field', ['name', 'person_code'])
def test_person_update_rejects_empty_identity_fields(face_api, field):
    client, headers = face_api
    now = datetime.now()
    person = FacePerson.create(
        person_code='VALID-1', name='有效姓名', created_by='face-admin',
        created_at=now, updated_at=now,
    )

    response = client.patch(
        f'/api/face/persons/{person.id}',
        headers=headers,
        json={field: '   '},
    )

    assert response.status_code == 400
    unchanged = FacePerson.get_by_id(person.id)
    assert unchanged.person_code == 'VALID-1'
    assert unchanged.name == '有效姓名'


def test_face_import_job_is_claimed_durably(face_api):
    bundle = FaceModelBundle.create(
        name='import', version='v1', contract_id='import-v1',
        created_at=datetime.now(), updated_at=datetime.now(),
    )
    gallery = FaceGallery.create(
        name='import-gallery', model_bundle=bundle,
        created_at=datetime.now(), updated_at=datetime.now(),
    )
    job = FaceImportJob.create(
        gallery=gallery,
        status='pending',
        encrypted_archive_path='/tmp/encrypted.face',
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    assert faces._claim_next_face_import() == job.id
    job = FaceImportJob.get_by_id(job.id)
    assert job.status == 'processing'
    assert job.locked_at is not None
    assert faces._claim_next_face_import() is None


def test_person_identity_change_bumps_all_member_galleries(face_api, monkeypatch):
    client, headers = face_api
    now = datetime.now()
    bundle = FaceModelBundle.create(
        name='identity-bundle', version='v1', contract_id='identity-v1',
        created_at=now, updated_at=now,
    )
    galleries = [
        FaceGallery.create(
            name=f'identity-gallery-{index}', model_bundle=bundle,
            created_at=now, updated_at=now,
        )
        for index in range(2)
    ]
    person = FacePerson.create(
        person_code='IDENTITY-1', name='旧姓名', created_by='face-admin',
        created_at=now, updated_at=now,
    )
    for gallery in galleries:
        FaceGalleryMembership.create(gallery=gallery, person=person, created_at=now)
    invalidated = []
    monkeypatch.setattr(
        faces.gallery_index_cache, 'invalidate', invalidated.append
    )

    response = client.patch(
        f'/api/face/persons/{person.id}', headers=headers,
        json={'name': '新姓名', 'enabled': False},
    )

    assert response.status_code == 200
    assert FacePerson.get_by_id(person.id).enabled is False
    assert {
        FaceGallery.get_by_id(gallery.id).gallery_version for gallery in galleries
    } == {2}
    assert set(invalidated) == {gallery.id for gallery in galleries}


def test_stale_import_recovery_resets_progress_before_replay(face_api):
    now = datetime.now()
    bundle = FaceModelBundle.create(
        name='recover-bundle', version='v1', contract_id='recover-v1',
        created_at=now, updated_at=now,
    )
    gallery = FaceGallery.create(
        name='recover-gallery', model_bundle=bundle,
        created_at=now, updated_at=now,
    )
    job = FaceImportJob.create(
        gallery=gallery,
        status='processing',
        encrypted_archive_path='/tmp/recover.face',
        total_people=3,
        processed_people=2,
        succeeded_people=1,
        failed_people=1,
        errors_json=json.dumps([{'error': 'obsolete'}]),
        locked_at=now - timedelta(seconds=faces._FACE_IMPORT_LEASE_SECONDS + 5),
        created_at=now,
        updated_at=now,
    )

    assert faces._recover_stale_face_imports() == 1

    job = FaceImportJob.get_by_id(job.id)
    assert job.status == 'pending'
    assert job.processed_people == 0
    assert job.succeeded_people == 0
    assert job.failed_people == 0
    assert job.errors == []
    assert job.locked_at is None


def test_stale_retry_recovery_preserves_attempt_count(face_api):
    now = datetime.now()
    bundle = FaceModelBundle.create(
        name='retry-recovery-bundle', version='v1',
        contract_id='retry-recovery-v1', created_at=now, updated_at=now,
    )
    gallery = FaceGallery.create(
        name='retry-recovery-gallery', model_bundle=bundle,
        created_at=now, updated_at=now,
    )
    job = FaceImportJob.create(
        gallery=gallery,
        status='processing',
        encrypted_archive_path='/tmp/retry-recovery.face',
        errors_json=json.dumps([{
            'error': 'worker unavailable',
            'status_code': 503,
            'retrying': True,
            'retry_attempt': 1,
            'max_attempts': faces._FACE_IMPORT_MAX_ATTEMPTS,
        }]),
        locked_at=now - timedelta(seconds=faces._FACE_IMPORT_LEASE_SECONDS + 5),
        created_at=now,
        updated_at=now,
    )

    assert faces._recover_stale_face_imports() == 1

    job = FaceImportJob.get_by_id(job.id)
    assert job.status == 'pending'
    assert job.errors[0]['retry_attempt'] == 1
    assert job.errors[0]['retrying'] is True


def test_import_invalidates_every_same_contract_gallery(
    face_api, monkeypatch, tmp_path,
):
    _client, _headers = face_api
    now = datetime.now()
    bundle = FaceModelBundle.create(
        name='shared-import-bundle', version='v1', contract_id='shared-import-v1',
        embedding_dimension=3, created_at=now, updated_at=now,
    )
    target = FaceGallery.create(
        name='import-target', model_bundle=bundle, created_at=now, updated_at=now,
    )
    sibling = FaceGallery.create(
        name='import-sibling', model_bundle=bundle, created_at=now, updated_at=now,
    )
    person = FacePerson.create(
        person_code='SHARED-1', name='共享人员', created_by='face-admin',
        created_at=now, updated_at=now,
    )
    for gallery in (target, sibling):
        FaceGalleryMembership.create(gallery=gallery, person=person, created_at=now)

    image_buffer = io.BytesIO()
    Image.new('RGB', (16, 16), color=(90, 120, 150)).save(
        image_buffer, format='JPEG'
    )
    archive_path = tmp_path / 'shared.zip.face'
    with zipfile.ZipFile(archive_path, 'w') as archive:
        archive.writestr('manifest.csv', 'person_code,name\nSHARED-1,共享人员\n')
        archive.writestr('photos/SHARED-1/front.jpg', image_buffer.getvalue())
    job = FaceImportJob.create(
        gallery=target,
        status='processing',
        encrypted_archive_path=str(archive_path),
        total_people=1,
        total_images=1,
        created_at=now,
        updated_at=now,
        created_by='face-admin',
    )
    monkeypatch.setattr(faces, 'FACE_DATA_PATH', str(tmp_path))
    monkeypatch.setattr(
        faces, 'decrypt_biometric_stream',
        lambda source, destination, **_kwargs: destination.write(source.read()),
    )
    batch_calls = []

    def extract_batch(_bundle_id, images, **_kwargs):
        batch_calls.append(len(images))
        return [{
            'success': True,
            'embedding_base64': base64.b64encode(
                serialize_embedding([1.0, 0.0, 0.0])
            ).decode('ascii'),
            'quality': {'score': 0.9},
            'model_contract': bundle.contract_id,
            'backend': 'onnxruntime',
        } for _image in images]

    monkeypatch.setattr(faces, '_extract_enrollment_batch', extract_batch)
    invalidated = []
    monkeypatch.setattr(
        faces.gallery_index_cache, 'invalidate', invalidated.append
    )

    faces._run_face_import(job.id)

    job = FaceImportJob.get_by_id(job.id)
    assert job.status == 'completed'
    assert FaceGallery.get_by_id(target.id).gallery_version == 2
    assert FaceGallery.get_by_id(sibling.id).gallery_version == 2
    assert set(invalidated) == {target.id, sibling.id}
    assert batch_calls == [1]


def test_face_person_list_uses_server_pagination_without_n_plus_one(
    face_api, monkeypatch,
):
    client, headers = face_api
    now = datetime.now()
    bundle = FaceModelBundle.create(
        name='pagination-bundle', version='v1', contract_id='pagination-v1',
        created_at=now, updated_at=now,
    )
    gallery = FaceGallery.create(
        name='pagination-gallery', model_bundle=bundle,
        created_at=now, updated_at=now,
    )
    for index in range(25):
        person = FacePerson.create(
            person_code=f'PAGE-{index:03d}', name=f'人员{index}',
            created_by='face-admin', created_at=now, updated_at=now,
        )
        FaceGalleryMembership.create(gallery=gallery, person=person, created_at=now)

    database = FacePerson._meta.database
    original_execute = database.execute_sql
    statements = []

    def counted_execute(sql, *args, **kwargs):
        statements.append(sql)
        return original_execute(sql, *args, **kwargs)

    monkeypatch.setattr(database, 'execute_sql', counted_execute)
    response = client.get(
        f'/api/face/persons?gallery_id={gallery.id}&page=2&page_size=10',
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload['persons']) == 10
    assert payload['pagination'] == {
        'page': 2, 'page_size': 10, 'total': 25, 'total_pages': 3,
    }
    assert len(statements) <= 8


def test_enrollment_preserves_worker_status_code(face_api, monkeypatch):
    client, headers = face_api
    now = datetime.now()
    bundle = FaceModelBundle.create(
        name='status-bundle', version='v1', contract_id='status-v1',
        created_at=now, updated_at=now,
    )
    gallery = FaceGallery.create(
        name='status-gallery', model_bundle=bundle,
        created_at=now, updated_at=now,
    )
    person = FacePerson.create(
        person_code='STATUS-1', name='状态测试', created_by='face-admin',
        created_at=now, updated_at=now,
    )
    FaceGalleryMembership.create(gallery=gallery, person=person, created_at=now)
    monkeypatch.setattr(
        faces,
        'submit_algorithm_test',
        lambda _job: ({'success': False, 'error': 'worker overloaded'}, 503),
    )
    image_buffer = io.BytesIO()
    Image.new('RGB', (16, 16), color=(10, 20, 30)).save(
        image_buffer, format='JPEG'
    )

    response = client.post(
        f'/api/face/persons/{person.id}/templates',
        headers=headers,
        data={'file': (io.BytesIO(image_buffer.getvalue()), 'face.jpg')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 503
    assert response.get_json()['error'] == 'worker overloaded'


def test_failed_import_row_rolls_back_identity_and_membership(
    face_api, monkeypatch, tmp_path,
):
    _client, _headers = face_api
    now = datetime.now()
    bundle = FaceModelBundle.create(
        name='rollback-bundle', version='v1', contract_id='rollback-v1',
        created_at=now, updated_at=now,
    )
    gallery = FaceGallery.create(
        name='rollback-gallery', model_bundle=bundle,
        created_at=now, updated_at=now,
    )
    person = FacePerson.create(
        person_code='ROLLBACK-1', name='原姓名', created_by='face-admin',
        created_at=now, updated_at=now,
    )
    image_buffer = io.BytesIO()
    Image.new('RGB', (16, 16), color=(50, 60, 70)).save(
        image_buffer, format='JPEG'
    )
    archive_path = tmp_path / 'rollback.zip.face'
    with zipfile.ZipFile(archive_path, 'w') as archive:
        archive.writestr('manifest.csv', 'person_code,name\nROLLBACK-1,新姓名\n')
        archive.writestr('photos/ROLLBACK-1/front.jpg', image_buffer.getvalue())
    job = FaceImportJob.create(
        gallery=gallery, status='processing',
        encrypted_archive_path=str(archive_path), total_people=1, total_images=1,
        created_at=now, updated_at=now, created_by='face-admin',
    )
    monkeypatch.setattr(
        faces, 'decrypt_biometric_stream',
        lambda source, destination, **_kwargs: destination.write(source.read()),
    )
    monkeypatch.setattr(
        faces, '_extract_enrollment_batch',
        lambda _bundle_id, images, **_kwargs: [
            {'success': False, 'error': '质量不合格', 'status_code': 422}
            for _image in images
        ],
    )

    faces._run_face_import(job.id)

    assert FaceImportJob.get_by_id(job.id).status == 'completed_with_errors'
    assert FacePerson.get_by_id(person.id).name == '原姓名'
    assert not FaceGalleryMembership.select().where(
        (FaceGalleryMembership.gallery == gallery.id)
        & (FaceGalleryMembership.person == person.id)
    ).exists()
    assert not FaceTemplate.select().where(FaceTemplate.person == person.id).exists()


def test_transient_import_failure_keeps_encrypted_archive_for_retry(
    face_api, monkeypatch, tmp_path,
):
    _client, _headers = face_api
    now = datetime.now()
    bundle = FaceModelBundle.create(
        name='retry-bundle', version='v1', contract_id='retry-v1',
        created_at=now, updated_at=now,
    )
    gallery = FaceGallery.create(
        name='retry-gallery', model_bundle=bundle,
        created_at=now, updated_at=now,
    )
    image_buffer = io.BytesIO()
    Image.new('RGB', (16, 16), color=(80, 90, 100)).save(
        image_buffer, format='JPEG'
    )
    archive_path = tmp_path / 'retry.zip.face'
    with zipfile.ZipFile(archive_path, 'w') as archive:
        archive.writestr('manifest.csv', 'person_code,name\nRETRY-1,重试人员\n')
        archive.writestr('photos/RETRY-1/front.jpg', image_buffer.getvalue())
    job = FaceImportJob.create(
        gallery=gallery, status='processing',
        encrypted_archive_path=str(archive_path), total_people=1, total_images=1,
        created_at=now, updated_at=now, created_by='face-admin',
    )
    monkeypatch.setattr(
        faces, 'decrypt_biometric_stream',
        lambda source, destination, **_kwargs: destination.write(source.read()),
    )

    def unavailable(*_args, **_kwargs):
        raise faces.FaceEnrollmentServiceError('worker unavailable', 503)

    monkeypatch.setattr(faces, '_extract_enrollment_batch', unavailable)

    faces._run_face_import(job.id)

    job = FaceImportJob.get_by_id(job.id)
    assert job.status == 'processing'
    assert job.processed_people == 0
    assert job.failed_people == 0
    assert job.errors[0]['retrying'] is True
    assert archive_path.exists()
    assert not FacePerson.select().where(FacePerson.person_code == 'RETRY-1').exists()


@pytest.mark.parametrize(
    ('status_code', 'previous_attempt'),
    [(400, 0), (500, 0), (503, faces._FACE_IMPORT_MAX_ATTEMPTS - 1)],
)
def test_permanent_or_exhausted_import_failure_is_terminal(
    face_api, monkeypatch, tmp_path, status_code, previous_attempt,
):
    _client, _headers = face_api
    now = datetime.now()
    bundle = FaceModelBundle.create(
        name=f'terminal-bundle-{status_code}-{previous_attempt}',
        version='v1', contract_id=f'terminal-{status_code}-{previous_attempt}',
        created_at=now, updated_at=now,
    )
    gallery = FaceGallery.create(
        name=f'terminal-gallery-{status_code}-{previous_attempt}',
        model_bundle=bundle, created_at=now, updated_at=now,
    )
    image_buffer = io.BytesIO()
    Image.new('RGB', (16, 16), color=(25, 50, 75)).save(
        image_buffer, format='JPEG'
    )
    archive_path = tmp_path / f'terminal-{status_code}-{previous_attempt}.zip.face'
    with zipfile.ZipFile(archive_path, 'w') as archive:
        archive.writestr('manifest.csv', 'person_code,name\nTERM-1,终止人员\n')
        archive.writestr('photos/TERM-1/front.jpg', image_buffer.getvalue())
    prior_errors = []
    if previous_attempt:
        prior_errors = [{
            'error': 'previous transient failure',
            'status_code': status_code,
            'retrying': True,
            'retry_attempt': previous_attempt,
        }]
    job = FaceImportJob.create(
        gallery=gallery,
        status='processing',
        encrypted_archive_path=str(archive_path),
        total_people=1,
        total_images=1,
        errors_json=json.dumps(prior_errors),
        created_at=now,
        updated_at=now,
        created_by='face-admin',
    )
    monkeypatch.setattr(
        faces, 'decrypt_biometric_stream',
        lambda source, destination, **_kwargs: destination.write(source.read()),
    )
    monkeypatch.setattr(
        faces,
        '_extract_enrollment_batch',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            faces.FaceEnrollmentServiceError('inference failed', status_code)
        ),
    )

    faces._run_face_import(job.id)

    job = FaceImportJob.get_by_id(job.id)
    assert job.status == 'failed'
    assert job.completed_at is not None
    assert job.locked_at is None
    assert job.errors[0]['retrying'] is False
    assert job.errors[0]['retry_attempt'] == previous_attempt + 1
    assert not archive_path.exists()
    assert faces._gallery_has_active_import(gallery.id) is False


def test_stale_plaintext_import_archives_are_reclaimed(
    face_api, monkeypatch, tmp_path,
):
    _client, _headers = face_api
    old_archive = tmp_path / '.face-import-old.zip'
    fresh_archive = tmp_path / '.face-import-fresh.zip'
    old_archive.write_bytes(b'plaintext')
    fresh_archive.write_bytes(b'plaintext')
    now = datetime.now().timestamp()
    old_time = now - faces._FACE_IMPORT_LEASE_SECONDS - 1
    import os
    os.utime(old_archive, (old_time, old_time))
    monkeypatch.setattr(
        faces, '_face_import_plaintext_directories', lambda create=False: [str(tmp_path)]
    )

    assert faces._cleanup_stale_plaintext_face_imports(now) == 1
    assert not old_archive.exists()
    assert fresh_archive.exists()


def test_artifact_old_file_cleanup_is_best_effort(face_api, monkeypatch, tmp_path):
    client, headers = face_api
    now = datetime.now()
    bundle = FaceModelBundle.create(
        name='artifact-cleanup', version='v1', contract_id='artifact-cleanup-v1',
        created_at=now, updated_at=now,
    )
    old_path = tmp_path / 'old-detector.onnx'
    old_path.write_bytes(b'old')
    artifact = FaceModelArtifact.create(
        bundle=bundle, role='detection', runtime='onnxruntime',
        architecture='any', device='any', filename=old_path.name,
        file_path=str(old_path), file_size=3, artifact_sha256='0' * 64,
        created_at=now,
    )
    original_remove = faces.os.remove

    def fail_old_only(path):
        if str(path) == str(old_path):
            raise PermissionError('read only')
        return original_remove(path)

    monkeypatch.setattr(faces.os, 'remove', fail_old_only)
    response = client.post(
        f'/api/face/model-bundles/{bundle.id}/artifacts', headers=headers,
        data={
            'role': 'detection', 'runtime': 'onnxruntime',
            'architecture': 'any', 'device': 'any',
            'file': (io.BytesIO(b'new-model'), 'detector.onnx'),
        },
        content_type='multipart/form-data',
    )

    assert response.status_code == 201
    artifact = FaceModelArtifact.get_by_id(artifact.id)
    assert artifact.file_path != str(old_path)
    assert faces.os.path.isfile(artifact.file_path)
    assert old_path.exists()


def test_artifact_database_failure_keeps_old_record_and_file(
    face_api, monkeypatch, tmp_path,
):
    client, headers = face_api
    now = datetime.now()
    bundle = FaceModelBundle.create(
        name='artifact-rollback', version='v1', contract_id='artifact-rollback-v1',
        created_at=now, updated_at=now,
    )
    old_path = tmp_path / 'rollback-detector.onnx'
    old_path.write_bytes(b'old')
    artifact = FaceModelArtifact.create(
        bundle=bundle, role='detection', runtime='onnxruntime',
        architecture='any', device='any', filename=old_path.name,
        file_path=str(old_path), file_size=3, artifact_sha256='1' * 64,
        created_at=now,
    )
    original_save = FaceModelBundle.save

    def fail_bundle_save(self, *args, **kwargs):
        if self.id == bundle.id:
            raise RuntimeError('database write failed')
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(FaceModelBundle, 'save', fail_bundle_save)
    response = client.post(
        f'/api/face/model-bundles/{bundle.id}/artifacts', headers=headers,
        data={
            'role': 'detection', 'runtime': 'onnxruntime',
            'architecture': 'any', 'device': 'any',
            'file': (io.BytesIO(b'new-model'), 'detector.onnx'),
        },
        content_type='multipart/form-data',
    )

    assert response.status_code == 500
    artifact = FaceModelArtifact.get_by_id(artifact.id)
    assert artifact.file_path == str(old_path)
    assert old_path.exists()
    target_dir = tmp_path / 'face-models' / str(bundle.id) / 'onnxruntime' / 'detection'
    assert list(target_dir.glob('*.onnx')) == []
