import base64
import io
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import peewee as pw
import pytest

from app.core import face_crypto
from app.core import face_inference
from app.core import face_settings
from app.core import face_event_storage
from app.core.database_models import (
    FaceGallery,
    FaceGalleryMembership,
    FaceModelBundle,
    FacePerson,
    FaceTemplate,
)
from app.core.face_gallery import (
    GalleryIndex,
    GalleryIndexCache,
    serialize_embedding,
)
from app.core.face_inference import (
    FaceCandidate,
    FaceDetector,
    FaceEmbedder,
    available_face_runtimes,
    assess_face_quality,
    select_bundle_artifacts,
    supported_face_runtimes,
    verify_bundle_artifacts,
)
from app.user_scripts.templates import face_recognizer
from app.user_scripts.templates.face_recognizer import (
    _decision_for_track,
    _prune_track_state,
    _record_event,
    _refresh_backend_for_gallery,
    _refresh_gallery_version,
)


@pytest.fixture(autouse=True)
def _face_system_defaults(monkeypatch):
    monkeypatch.setattr(
        face_settings,
        'get_face_recognition_config',
        lambda: face_settings.FaceRecognitionConfig(),
    )


def _set_face_key(monkeypatch):
    key = base64.urlsafe_b64encode(b'k' * 32).decode('ascii').rstrip('=')
    monkeypatch.setenv('FACE_DATA_ENCRYPTION_KEY', key)
    monkeypatch.delenv('FACE_DATA_ENCRYPTION_KEY_FILE', raising=False)
    face_crypto.face_encryption_key.cache_clear()


def test_face_crypto_round_trip_and_purpose_binding(monkeypatch):
    _set_face_key(monkeypatch)
    encrypted = face_crypto.encrypt_biometric(b'private-face-data', purpose='image:1')
    assert b'private-face-data' not in encrypted
    assert face_crypto.decrypt_biometric(encrypted, purpose='image:1') == b'private-face-data'
    with pytest.raises(Exception):
        face_crypto.decrypt_biometric(encrypted, purpose='embedding:1')


def test_face_crypto_stream_round_trip(monkeypatch):
    _set_face_key(monkeypatch)
    cleartext = (b'face-import-archive' * 4096) + b'end'
    encrypted = io.BytesIO()
    face_crypto.encrypt_biometric_stream(
        io.BytesIO(cleartext), encrypted, purpose='face-import:9', chunk_size=997
    )
    assert cleartext[:64] not in encrypted.getvalue()
    decrypted = io.BytesIO()
    encrypted.seek(0)
    face_crypto.decrypt_biometric_stream(
        encrypted, decrypted, purpose='face-import:9', chunk_size=1013
    )
    assert decrypted.getvalue() == cleartext


def test_face_event_storage_writes_encrypted_snapshot_atomically(monkeypatch, tmp_path):
    _set_face_key(monkeypatch)
    monkeypatch.setattr(face_event_storage, 'FACE_EVENT_PATH', str(tmp_path))

    relative = face_event_storage.write_encrypted_face_event_snapshot(
        b'jpeg-payload', 'track/7'
    )
    target = tmp_path / relative

    assert target.is_file()
    assert b'jpeg-payload' not in target.read_bytes()
    assert face_crypto.decrypt_biometric(
        target.read_bytes(), purpose='face-event-snapshot'
    ) == b'jpeg-payload'
    assert not list(target.parent.glob('*.tmp'))


def test_platform_selector_falls_back_only_to_complete_artifact_pair(monkeypatch):
    monkeypatch.setattr('app.core.face_inference.runtime_capabilities', lambda: {
        'machine': 'aarch64',
        'is_rockchip': True,
        'is_jetson': False,
        'onnx_providers': ['CPUExecutionProvider'],
        'rknn_available': True,
        'torch_cuda_available': False,
        'preferred_backend': 'rknn',
    })
    artifacts = [
        SimpleNamespace(role='detection', runtime='rknn', architecture='arm64', device='rk3588', enabled=True),
        SimpleNamespace(role='embedding', runtime='onnxruntime', architecture='arm64', device='cpu', enabled=True),
        SimpleNamespace(role='detection', runtime='onnxruntime', architecture='arm64', device='cpu', enabled=True),
    ]
    runtime, selected, _ = select_bundle_artifacts(SimpleNamespace(artifacts=artifacts))
    assert runtime == 'onnxruntime'
    assert {item.runtime for item in selected.values()} == {'onnxruntime'}


def test_host_available_backends_exclude_upload_only_runtimes(monkeypatch):
    monkeypatch.setattr(face_inference, '_RUNNER_PLUGINS', {})
    monkeypatch.setattr(face_inference, '_RUNNER_PLUGINS_LOADED', True)
    monkeypatch.setattr('app.core.face_inference.runtime_capabilities', lambda: {
        'is_rockchip': False,
        'onnx_providers': ['CPUExecutionProvider'],
        'rknn_available': False,
        'torch_available': False,
        'torch_cuda_available': False,
        'plugin_errors': [],
    })

    available, _errors = available_face_runtimes()
    uploadable, _errors = supported_face_runtimes()

    assert available == ['onnxruntime']
    assert {'rknn', 'tensorrt'}.issubset(uploadable)
    assert 'rknn' not in available
    assert 'tensorrt' not in available


def test_platform_selector_rejects_explicit_unavailable_backend(monkeypatch):
    monkeypatch.setattr('app.core.face_inference.runtime_capabilities', lambda: {
        'machine': 'aarch64',
        'is_rockchip': False,
        'is_jetson': False,
        'onnx_providers': ['CPUExecutionProvider'],
        'rknn_available': False,
        'torch_available': False,
        'torch_cuda_available': False,
        'preferred_backend': 'onnxruntime',
    })
    artifacts = [
        SimpleNamespace(
            role=role, runtime='rknn', architecture='arm64', device='rk3588',
            enabled=True,
        )
        for role in ('detection', 'embedding')
    ]

    with pytest.raises(Exception, match='当前主机未提供'):
        select_bundle_artifacts(
            SimpleNamespace(artifacts=artifacts, commercial_use_allowed=True),
            'rknn',
        )


def test_bundle_verification_checks_artifact_hashes(monkeypatch, tmp_path):
    monkeypatch.setattr('app.core.face_inference.runtime_capabilities', lambda: {
        'machine': 'x86_64',
        'is_rockchip': False,
        'is_jetson': False,
        'onnx_providers': ['CPUExecutionProvider'],
        'rknn_available': False,
        'torch_available': False,
        'torch_cuda_available': False,
        'preferred_backend': 'onnxruntime',
    })
    artifacts = []
    for role in ('detection', 'embedding'):
        path = tmp_path / f'{role}.onnx'
        path.write_bytes(f'{role}-model'.encode())
        artifacts.append(SimpleNamespace(
            role=role,
            runtime='onnxruntime',
            architecture='any',
            device='cpu',
            enabled=True,
            file_path=str(path),
            artifact_sha256='0' * 64,
            metadata={},
        ))
    bundle = SimpleNamespace(
        artifacts=artifacts,
        preprocess={},
        commercial_use_allowed=True,
    )

    with pytest.raises(Exception, match='SHA-256'):
        verify_bundle_artifacts(bundle)


def test_platform_selector_prefers_exact_architecture_and_device(monkeypatch):
    monkeypatch.setattr('app.core.face_inference.runtime_capabilities', lambda: {
        'machine': 'x86_64',
        'is_rockchip': False,
        'is_jetson': False,
        'onnx_providers': ['CUDAExecutionProvider', 'CPUExecutionProvider'],
        'rknn_available': False,
        'torch_cuda_available': False,
        'preferred_backend': 'onnxruntime-cuda',
    })
    artifacts = []
    for role in ('detection', 'embedding'):
        artifacts.extend([
            SimpleNamespace(role=role, runtime='onnxruntime', architecture='any', device='any', enabled=True, marker='generic'),
            SimpleNamespace(role=role, runtime='onnxruntime', architecture='amd64', device='cuda', enabled=True, marker='exact'),
            SimpleNamespace(role=role, runtime='onnxruntime', architecture='arm64', device='cuda', enabled=True, marker='wrong-arch'),
        ])
    runtime, selected, _ = select_bundle_artifacts(SimpleNamespace(artifacts=artifacts))
    assert runtime == 'onnxruntime-cuda'
    assert {item.marker for item in selected.values()} == {'exact'}


def test_platform_selector_uses_system_default_backend(monkeypatch):
    monkeypatch.setattr(face_settings, 'get_face_recognition_config', lambda: (
        face_settings.FaceRecognitionConfig(inference_backend='onnxruntime')
    ))
    monkeypatch.setattr('app.core.face_inference.runtime_capabilities', lambda: {
        'machine': 'aarch64',
        'is_rockchip': True,
        'is_jetson': False,
        'onnx_providers': ['CPUExecutionProvider'],
        'rknn_available': True,
        'torch_cuda_available': False,
        'preferred_backend': 'rknn',
    })
    artifacts = []
    for role in ('detection', 'embedding'):
        artifacts.extend([
            SimpleNamespace(role=role, runtime='rknn', architecture='arm64', device='rk3588', enabled=True),
            SimpleNamespace(role=role, runtime='onnxruntime', architecture='arm64', device='cpu', enabled=True),
        ])

    runtime, _selected, _capabilities = select_bundle_artifacts(
        SimpleNamespace(artifacts=artifacts, commercial_use_allowed=True),
        'auto',
    )

    assert runtime == 'onnxruntime'


def test_platform_selector_enforces_commercial_model_policy(monkeypatch):
    monkeypatch.setattr(face_settings, 'get_face_recognition_config', lambda: (
        face_settings.FaceRecognitionConfig(require_commercial_models=True)
    ))

    with pytest.raises(Exception, match='禁止加载未声明可商用'):
        select_bundle_artifacts(SimpleNamespace(
            artifacts=[], commercial_use_allowed=False,
        ))


def test_exact_gallery_search_aggregates_templates_by_person():
    matrix = np.asarray([
        [1.0, 0.0, 0.0],
        [0.8, 0.6, 0.0],
        [0.0, 1.0, 0.0],
    ], dtype=np.float32)
    index = GalleryIndex(
        1,
        3,
        matrix,
        [(10, 'E-10', '甲'), (10, 'E-10', '甲'), (20, 'E-20', '乙')],
    )
    matches = index.search([0.95, 0.05, 0.0])
    assert [item.person_id for item in matches] == [10, 20]
    assert matches[0].similarity > matches[1].similarity


def test_gray_zone_requires_repeated_same_person():
    gallery = SimpleNamespace(low_threshold=0.50, high_threshold=0.60)
    match = SimpleNamespace(person_id=7, similarity=0.55)
    state = {'decisions': {}}
    config = {'confirmation_window': 3, 'confirmation_hits': 2}

    assert _decision_for_track(state, 4, match, gallery, config)[0] == 'pending'
    assert _decision_for_track(state, 4, match, gallery, config)[0] == 'known'


def test_unknown_requires_full_window_of_qualified_non_matches():
    gallery = SimpleNamespace(low_threshold=0.50, high_threshold=0.60)
    state = {'decisions': {}}
    config = {'confirmation_window': 3, 'confirmation_hits': 2}

    assert _decision_for_track(state, 8, None, gallery, config)[0] == 'pending'
    assert _decision_for_track(state, 8, None, gallery, config)[0] == 'pending'
    assert _decision_for_track(state, 8, None, gallery, config)[0] == 'unknown'


def test_face_quality_rejects_small_face_before_blur_check():
    image = np.full((200, 200, 3), 128, dtype=np.uint8)
    candidate = FaceCandidate(
        box=(10, 10, 60, 60),
        confidence=0.9,
        landmarks=((20, 20), (45, 20), (32, 32), (23, 45), (42, 45)),
    )
    quality = assess_face_quality(image, candidate, min_face_size=80)
    assert quality == {'accepted': False, 'reason': 'face_too_small', 'score': 0.0}


def test_detector_accepts_canonical_combined_output(monkeypatch):
    class FakeRunner:
        def infer(self, _tensor):
            return [np.asarray([[
                10, 20, 110, 120, 0.9,
                35, 45, 75, 45, 55, 65, 40, 90, 70, 90,
            ]], dtype=np.float32)]

        def close(self):
            pass

    monkeypatch.setattr('app.core.face_inference.create_runner', lambda *_args, **_kwargs: FakeRunner())
    artifact = SimpleNamespace(
        file_path='fake.onnx',
        runtime='onnxruntime',
        metadata={
            'input_shape': '320x320',
            'input_dtype': 'uint8',
            'output_format': 'combined',
        },
    )
    detector = FaceDetector(artifact)
    faces = detector.detect(np.zeros((320, 320, 3), dtype=np.uint8))
    assert len(faces) == 1
    assert faces[0].box == pytest.approx((10, 20, 110, 120))
    assert len(faces[0].landmarks) == 5


def test_detector_accepts_unbatched_multi_face_combined_output(monkeypatch):
    rows = np.asarray([
        [10, 20, 70, 90, 0.9, 20, 35, 50, 35, 35, 50, 25, 70, 48, 70],
        [150, 30, 230, 120, 0.8, 165, 50, 210, 50, 188, 70, 170, 100, 205, 100],
    ], dtype=np.float32)

    class FakeRunner:
        def infer(self, _tensor):
            return [rows]

        def close(self):
            pass

    monkeypatch.setattr(
        'app.core.face_inference.create_runner', lambda *_args, **_kwargs: FakeRunner()
    )
    detector = FaceDetector(SimpleNamespace(
        file_path='fake.onnx', runtime='onnxruntime',
        metadata={'input_shape': '320x320', 'input_dtype': 'uint8', 'output_format': 'combined'},
    ))

    assert len(detector.detect(np.zeros((320, 320, 3), dtype=np.uint8))) == 2


def test_detector_accepts_unbatched_multi_face_separate_outputs(monkeypatch):
    boxes = np.asarray([[10, 20, 70, 90], [150, 30, 230, 120]], dtype=np.float32)
    scores = np.asarray([0.9, 0.8], dtype=np.float32)
    landmarks = np.asarray([
        [20, 35, 50, 35, 35, 50, 25, 70, 48, 70],
        [165, 50, 210, 50, 188, 70, 170, 100, 205, 100],
    ], dtype=np.float32)

    class FakeRunner:
        def infer(self, _tensor):
            return [boxes, scores, landmarks]

        def close(self):
            pass

    monkeypatch.setattr(
        'app.core.face_inference.create_runner', lambda *_args, **_kwargs: FakeRunner()
    )
    detector = FaceDetector(SimpleNamespace(
        file_path='fake.onnx', runtime='onnxruntime',
        metadata={'input_shape': '320x320', 'input_dtype': 'uint8', 'output_format': 'separate'},
    ))

    assert len(detector.detect(np.zeros((320, 320, 3), dtype=np.uint8))) == 2


def test_face_embedder_defaults_to_fixed_batch_one(monkeypatch):
    observed_batches = []

    class FixedBatchRunner:
        def infer(self, tensor):
            observed_batches.append(tensor.shape[0])
            if tensor.shape[0] != 1:
                raise ValueError('model requires batch=1')
            return [np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32)]

        def close(self):
            pass

    monkeypatch.setattr(
        'app.core.face_inference.create_runner',
        lambda *_args, **_kwargs: FixedBatchRunner(),
    )
    embedder = FaceEmbedder(SimpleNamespace(
        file_path='fixed.rknn', runtime='rknn', metadata={},
    ))

    embeddings = embedder.embed_aligned([
        np.zeros((112, 112, 3), dtype=np.uint8) for _ in range(3)
    ])

    assert len(embeddings) == 3
    assert observed_batches == [1, 1, 1]


def test_face_backend_is_rebuilt_when_gallery_bundle_changes(monkeypatch):
    previous = SimpleNamespace(cleaned=False)
    previous.cleanup = lambda: setattr(previous, 'cleaned', True)
    replacement = SimpleNamespace()
    tracker = SimpleNamespace(reset_called=False)
    tracker.reset = lambda: setattr(tracker, 'reset_called', True)
    state = {
        'bundle_id': 1,
        'model_contract': 'contract-v1',
        'requested_backend': 'auto',
        'backend_config': {'min_face_size': 80},
        'backend': previous,
        'tracker': tracker,
        'decisions': {1: object()},
        'confirmed': {1: object()},
    }
    bundle = SimpleNamespace(id=2, contract_id='contract-v2')
    monkeypatch.setattr(
        face_recognizer.FaceModelBundle, 'get_by_id', lambda _bundle_id: bundle
    )
    monkeypatch.setattr(
        face_recognizer, 'create_face_backend',
        lambda actual_bundle, backend, config: replacement,
    )

    changed = _refresh_backend_for_gallery(
        state, SimpleNamespace(model_bundle_id=2)
    )

    assert changed is True
    assert state['backend'] is replacement
    assert state['bundle_id'] == 2
    assert state['model_contract'] == 'contract-v2'
    assert tracker.reset_called is True
    assert state['decisions'] == {}
    assert state['confirmed'] == {}
    assert previous.cleaned is True


def test_confirmation_state_survives_tracker_misses_until_track_is_pruned():
    tracker = SimpleNamespace(tracks=[SimpleNamespace(track_id=7, misses=1)])
    state = {
        'tracker': tracker,
        'decisions': {7: object(), 8: object()},
        'confirmed': {7: object(), 8: object()},
    }

    _prune_track_state(state)

    assert set(state['decisions']) == {7}
    assert set(state['confirmed']) == {7}


def test_gallery_version_change_invalidates_confirmed_tracks():
    state = {
        'gallery_version': 4,
        'decisions': {7: object()},
        'confirmed': {7: object()},
    }

    assert _refresh_gallery_version(
        state, SimpleNamespace(gallery_version=5)
    ) is True
    assert state['gallery_version'] == 5
    assert state['decisions'] == {}
    assert state['confirmed'] == {}
    assert _refresh_gallery_version(
        state, SimpleNamespace(gallery_version=5)
    ) is False


def test_face_recognizer_rejects_gallery_owned_by_another_user(monkeypatch):
    gallery = SimpleNamespace(
        id=4,
        created_by='gallery-owner',
        enabled=True,
        model_bundle_id=2,
    )
    monkeypatch.setattr(
        face_recognizer.FaceGallery, 'get_by_id', lambda _gallery_id: gallery
    )

    with pytest.raises(ValueError, match='无权访问'):
        face_recognizer.init({
            'gallery_id': gallery.id,
            '_execution_owner': 'requester',
            '_execution_role': 'user',
        })


def test_face_event_uses_trusted_owner_not_script_config(monkeypatch):
    created = []
    monkeypatch.setattr(
        face_recognizer,
        'get_face_recognition_config',
        lambda: SimpleNamespace(known_retention_days=30, unknown_retention_days=7),
    )
    monkeypatch.setattr(face_recognizer, '_save_event_snapshot', lambda *_args: None)
    monkeypatch.setattr(
        face_recognizer.FaceEvent,
        'create',
        lambda **values: created.append(values),
    )
    gallery = SimpleNamespace(id=1, high_threshold=0.6, low_threshold=0.5)
    match = SimpleNamespace(
        person_id=2,
        person_code='P-2',
        person_name='人员二',
        similarity=0.9,
    )

    _record_event(
        np.zeros((32, 32, 3), dtype=np.uint8),
        {'box': [2, 2, 20, 20], 'track_id': 7, 'attributes': {}},
        'known',
        match,
        gallery,
        {
            'model_contract': 'face-v1',
            'execution_owner': 'source-owner',
            'preview_mode': False,
        },
        {'created_by': 'victim', 'save_events': True},
        inference_backend='fake',
    )

    assert created[0]['created_by'] == 'source-owner'


def test_face_event_is_not_written_in_preview_mode(monkeypatch):
    monkeypatch.setattr(
        face_recognizer.FaceEvent,
        'create',
        lambda **_values: pytest.fail('preview must not create a face event'),
    )
    monkeypatch.setattr(
        face_recognizer,
        '_save_event_snapshot',
        lambda *_args: pytest.fail('preview must not create a snapshot'),
    )

    _record_event(
        np.zeros((16, 16, 3), dtype=np.uint8),
        {'box': [1, 1, 8, 8], 'track_id': 3, 'attributes': {}},
        'unknown',
        None,
        SimpleNamespace(id=1, high_threshold=0.6, low_threshold=0.5),
        {
            'model_contract': 'face-v1',
            'execution_owner': 'owner',
            'preview_mode': True,
        },
        {'save_events': True},
    )


def test_face_recognizer_filters_detections_by_workflow_roi(monkeypatch):
    gallery = SimpleNamespace(
        id=1, enabled=True, model_bundle_id=1, gallery_version=1,
    )
    raw_detections = [
        {'box': [10, 10, 20, 20], 'attributes': {}},
        {'box': [70, 70, 90, 90], 'attributes': {}},
    ]

    class Backend:
        @staticmethod
        def infer(_frame):
            return raw_detections, [], {'backend': 'fake'}

    class Tracker:
        tracks = []

        def __init__(self):
            self.received = None

        def update(self, detections, timestamp=None):
            self.received = detections
            return []

    tracker = Tracker()
    state = {
        'gallery_id': 1,
        'gallery_version': 1,
        'model_contract': 'face-v1',
        'backend': Backend(),
        'tracker': tracker,
        'decisions': {},
        'confirmed': {},
    }
    monkeypatch.setattr(
        face_recognizer.FaceGallery, 'get_by_id', lambda _id: gallery
    )
    monkeypatch.setattr(
        face_recognizer, '_refresh_backend_for_gallery', lambda *_args: False
    )
    monkeypatch.setattr(
        face_recognizer.gallery_index_cache,
        'get',
        lambda _id: SimpleNamespace(template_count=0),
    )
    roi = [{
        'points': [[0, 0], [50, 0], [50, 50], [0, 50]],
        'mode': 'post_filter',
    }]

    result = face_recognizer.process(
        np.zeros((100, 100, 3), dtype=np.uint8),
        {},
        state,
        roi_regions=roi,
    )

    assert [item['box'] for item in tracker.received] == [[10, 10, 20, 20]]
    assert result['metadata']['detections_before_roi'] == 2
    assert result['metadata']['roi_filtered_count'] == 1


def test_face_confirmation_waits_for_event_persistence(monkeypatch):
    gallery = SimpleNamespace(
        id=1,
        enabled=True,
        model_bundle_id=1,
        gallery_version=1,
        low_threshold=0.5,
        high_threshold=0.6,
    )
    match = SimpleNamespace(
        person_id=11,
        person_code='P-11',
        person_name='测试人员',
        similarity=0.95,
    )

    class Backend:
        @staticmethod
        def infer(_frame):
            return [{'box': [10, 10, 30, 30]}], [], {'backend': 'fake'}

    class Track:
        track_id = 7
        misses = 0

        @staticmethod
        def to_detection(_label):
            return {
                'box': [10, 10, 30, 30],
                'attributes': {
                    'embedding': np.asarray([1.0, 0.0, 0.0]),
                    'quality': {'accepted': True},
                },
            }

    track = Track()

    class Tracker:
        tracks = [track]

        @staticmethod
        def update(_detections, timestamp=None):
            return [track]

    state = {
        'gallery_id': 1,
        'gallery_version': 1,
        'model_contract': 'face-v1',
        'backend': Backend(),
        'tracker': Tracker(),
        'decisions': {},
        'confirmed': {},
    }
    monkeypatch.setattr(
        face_recognizer.FaceGallery, 'get_by_id', lambda _id: gallery
    )
    monkeypatch.setattr(
        face_recognizer, '_refresh_backend_for_gallery', lambda *_args: False
    )
    monkeypatch.setattr(
        face_recognizer.gallery_index_cache,
        'get',
        lambda _id: SimpleNamespace(
            template_count=1, search=lambda *_args, **_kwargs: [match]
        ),
    )
    monkeypatch.setattr(
        face_recognizer,
        '_record_event',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('disk busy')),
    )

    with pytest.raises(OSError, match='disk busy'):
        face_recognizer.process(
            np.zeros((64, 64, 3), dtype=np.uint8), {}, state
        )
    assert state['confirmed'] == {}

    persisted = []
    monkeypatch.setattr(
        face_recognizer,
        '_record_event',
        lambda *_args, **_kwargs: persisted.append(True),
    )
    face_recognizer.process(
        np.zeros((64, 64, 3), dtype=np.uint8), {}, state
    )
    assert persisted == [True]
    assert state['confirmed'][7][0] == 'known'


def test_gallery_index_loads_encrypted_templates(monkeypatch):
    _set_face_key(monkeypatch)
    database = pw.SqliteDatabase(':memory:', pragmas={'foreign_keys': 1})
    models = [
        FaceModelBundle,
        FaceGallery,
        FacePerson,
        FaceGalleryMembership,
        FaceTemplate,
    ]
    originals = {model: model._meta.database for model in models}
    database.bind(models, bind_refs=False, bind_backrefs=False)
    database.create_tables(models)
    try:
        now = datetime.now()
        bundle = FaceModelBundle.create(
            name='faces', version='v1', contract_id='arcface-v1',
            embedding_dimension=3, input_size='112x112',
            created_at=now, updated_at=now,
        )
        gallery = FaceGallery.create(
            name='employees', model_bundle=bundle, created_at=now, updated_at=now,
        )
        person = FacePerson.create(
            person_code='E-1', name='测试人员', created_at=now, updated_at=now,
        )
        FaceGalleryMembership.create(gallery=gallery, person=person, created_at=now)
        raw_embedding = serialize_embedding([1.0, 0.0, 0.0])
        FaceTemplate.create(
            person=person,
            encrypted_image=face_crypto.encrypt_biometric(b'image', purpose=f'face-image:{person.id}'),
            encrypted_embedding=face_crypto.encrypt_biometric(
                raw_embedding, purpose=f'face-embedding:{person.id}'
            ),
            image_sha256='a' * 64,
            model_contract='arcface-v1',
            created_at=now,
        )
        index = GalleryIndexCache().get(gallery.id)
        assert index.template_count == 1
        assert index.search([1, 0, 0])[0].person_code == 'E-1'
    finally:
        database.drop_tables(models)
        database.close()
        for model, original in originals.items():
            model._meta.set_database(original)
