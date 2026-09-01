import hashlib
import io
from datetime import datetime, timedelta

import peewee as pw
import pytest
from flask import Flask

from app.core.database_models import (
    ReIdModelArtifact,
    ReIdModelBundle,
    ReIdModelImportJob,
    User,
)
from app.web.api import reid
from app.web.api.auth import generate_token


@pytest.fixture()
def reid_api(monkeypatch, tmp_path):
    database = pw.SqliteDatabase(tmp_path / 'reid-api.db', pragmas={'foreign_keys': 1})
    models = [User, ReIdModelBundle, ReIdModelArtifact, ReIdModelImportJob]
    originals = {model: model._meta.database for model in models}
    database.bind(models, bind_refs=False, bind_backrefs=False)
    database.create_tables(models)
    monkeypatch.setattr(reid, 'db', database)
    monkeypatch.setattr(reid, 'REID_MODEL_PATH', str(tmp_path / 'reid-models'))
    user = User.create(
        username='reid-admin', password_hash='unused', role='admin', enabled=True,
        created_at=datetime.now(),
    )
    app = Flask(__name__)
    app.register_blueprint(reid.reid_bp)
    client = app.test_client()
    headers = {'Authorization': f'Bearer {generate_token(user.id, user.username, user.role)}'}
    try:
        yield client, headers
    finally:
        database.drop_tables(models)
        database.close()
        for model, original in originals.items():
            model._meta.set_database(original)


def _create_bundle(client, headers):
    response = client.post('/api/reid/model-bundles', headers=headers, json={
        'name': 'portable-osnet', 'version': 'v1',
        'contract_id': 'osnet-x0.25-512-v1', 'embedding_dimension': 512,
        'input_size': '256x128', 'default_similarity_threshold': 0.76,
        'commercial_use_allowed': True,
    })
    assert response.status_code == 201
    return response.get_json()['bundle']


def test_bundle_api_requires_auth_and_creates_contract(reid_api):
    client, headers = reid_api
    assert client.get('/api/reid/model-bundles').status_code == 401
    bundle = _create_bundle(client, headers)
    assert bundle['target_type'] == 'person'
    assert bundle['distance_metric'] == 'cosine'
    assert bundle['default_similarity_threshold'] == 0.76


def test_upload_artifact_hashes_and_publishes_atomically(reid_api):
    client, headers = reid_api
    bundle = _create_bundle(client, headers)
    payload = b'fake-onnx-model'
    digest = hashlib.sha256(payload).hexdigest()
    response = client.post(
        f"/api/reid/model-bundles/{bundle['id']}/artifacts",
        headers=headers,
        data={
            'runtime': 'onnxruntime-cuda', 'architecture': 'amd64',
            'device': 'cuda', 'sha256': digest,
            'file': (io.BytesIO(payload), 'osnet.onnx'),
        },
        content_type='multipart/form-data',
    )
    assert response.status_code == 201
    artifact = response.get_json()['bundle']['artifacts'][0]
    assert artifact['runtime'] == 'onnxruntime'
    assert artifact['device'] == 'cuda'
    assert artifact['artifact_sha256'] == digest


def test_upload_rejects_wrong_sha(reid_api):
    client, headers = reid_api
    bundle = _create_bundle(client, headers)
    response = client.post(
        f"/api/reid/model-bundles/{bundle['id']}/artifacts",
        headers=headers,
        data={
            'runtime': 'onnxruntime', 'sha256': '0' * 64,
            'file': (io.BytesIO(b'wrong'), 'osnet.onnx'),
        },
        content_type='multipart/form-data',
    )
    assert response.status_code == 400
    assert ReIdModelArtifact.select().count() == 0


def test_huggingface_import_requires_pinned_revision_and_sha(reid_api):
    client, headers = reid_api
    bundle = _create_bundle(client, headers)
    response = client.post(
        f"/api/reid/model-bundles/{bundle['id']}/imports",
        headers=headers,
        json={
            'type': 'huggingface', 'repo_id': 'org/repo',
            'filename': 'model.onnx', 'revision': 'main',
            'sha256': 'a' * 64, 'runtime': 'onnxruntime',
        },
    )
    assert response.status_code == 400
    assert '固定 revision' in response.get_json()['error']


def test_catalog_import_persists_expanded_platform_contract(
    reid_api, monkeypatch,
):
    client, headers = reid_api
    bundle = _create_bundle(client, headers)
    monkeypatch.setattr(reid, '_ensure_import_worker', lambda _job_id: None)
    monkeypatch.setattr(reid, '_load_catalog', lambda: [{
        'id': 'osnet-cpu', 'repo_id': 'org/repo', 'filename': 'osnet.pt',
        'revision': 'deadbeef', 'sha256': 'a' * 64,
        'runtime': 'torchscript', 'architecture': 'amd64', 'device': 'cpu',
        'metadata': {'fixed_batch': True, 'batch_size': 1},
    }])

    response = client.post(
        f"/api/reid/model-bundles/{bundle['id']}/imports",
        headers=headers,
        json={'type': 'catalog', 'catalog_id': 'osnet-cpu'},
    )

    assert response.status_code == 202
    source = ReIdModelImportJob.get().source
    assert source['type'] == 'huggingface'
    assert source['runtime'] == 'torchscript'
    assert source['architecture'] == 'amd64'
    assert source['device'] == 'cpu'


def test_polling_recovers_stale_running_import(reid_api, monkeypatch):
    client, headers = reid_api
    bundle = _create_bundle(client, headers)
    model_bundle = ReIdModelBundle.get_by_id(bundle['id'])
    job = ReIdModelImportJob.create(
        bundle=model_bundle, status='running', source_json='{}', progress=41,
        created_at=datetime.now() - timedelta(minutes=5),
        updated_at=datetime.now() - timedelta(minutes=5), created_by='admin',
    )
    started = []
    monkeypatch.setattr(reid, '_IMPORT_LEASE_SECONDS', 60)
    monkeypatch.setattr(reid, '_ensure_import_worker', started.append)

    response = client.get(f'/api/reid/imports/{job.id}', headers=headers)

    assert response.status_code == 200
    assert response.get_json()['job']['status'] == 'pending'
    assert started == [job.id]


def test_deleting_bundle_removes_artifacts(reid_api):
    client, headers = reid_api
    bundle = _create_bundle(client, headers)
    response = client.delete(f"/api/reid/model-bundles/{bundle['id']}", headers=headers)
    assert response.status_code == 200
    assert ReIdModelBundle.select().count() == 0
