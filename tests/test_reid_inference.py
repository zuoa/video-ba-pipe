from types import SimpleNamespace

import numpy as np
import pytest

from app.core import reid_inference
from app.core.reid_inference import (
    ReIdEmbedder,
    ReIdInferenceError,
    ReIdWorkerBackend,
    select_reid_artifact,
    verified_reid_artifact,
)


def _artifact(runtime, architecture='any', device='any'):
    return SimpleNamespace(
        runtime=runtime, architecture=architecture, device=device, enabled=True,
    )


def _bundle(*artifacts):
    return SimpleNamespace(enabled=True, artifacts=list(artifacts))


def test_rknn_platform_selects_rknn_artifact():
    artifact = _artifact('rknn', 'arm64', 'rk3588')
    runtime, selected, _ = select_reid_artifact(
        _bundle(artifact, _artifact('onnxruntime')),
        'auto',
        {
            'machine': 'aarch64', 'available_runtimes': ['rknn', 'onnxruntime'],
            'preferred_backend': 'rknn',
        },
    )
    assert runtime == 'rknn'
    assert selected is artifact


def test_cuda_alias_uses_stored_onnx_artifact():
    artifact = _artifact('onnxruntime', 'amd64', 'cuda')
    runtime, selected, _ = select_reid_artifact(
        _bundle(artifact), 'onnxruntime-cuda',
        {'machine': 'x86_64', 'available_runtimes': ['onnxruntime-cuda']},
    )
    assert runtime == 'onnxruntime-cuda'
    assert selected is artifact


def test_wrong_architecture_is_rejected():
    with pytest.raises(ReIdInferenceError, match='缺少当前平台'):
        select_reid_artifact(
            _bundle(_artifact('rknn', 'x86_64', 'rk3588')), 'rknn',
            {'machine': 'aarch64', 'available_runtimes': ['rknn']},
        )


def test_worker_batch_combines_crops_across_frames():
    class FakeEmbedder:
        def __init__(self):
            self.batch_lengths = []

        def embed(self, crops):
            self.batch_lengths.append(len(crops))
            return [np.asarray([1.0, 0.0], dtype=np.float32) for _ in crops]

    backend = ReIdWorkerBackend.__new__(ReIdWorkerBackend)
    backend.embedder = FakeEmbedder()
    backend.backend = 'onnxruntime'
    backend.contract_id = 'test-contract'
    backend.artifact_hash = 'a' * 64
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    results = backend.infer_batch(
        [frame, frame],
        [
            {'reid_boxes': [[0, 0, 20, 80]]},
            {'reid_boxes': [[10, 0, 30, 80], [40, 0, 60, 80]]},
        ],
    )
    assert backend.embedder.batch_lengths == [3]
    assert [len(result[1]) for result in results] == [1, 2]
    assert results[0][2]['crop_batch_size'] == 3


def test_unknown_batch_contract_defaults_to_fixed_batch_one(monkeypatch):
    class FixedBatchOneRunner:
        def __init__(self):
            self.batch_lengths = []

        def infer(self, tensor):
            self.batch_lengths.append(tensor.shape[0])
            assert tensor.shape[0] == 1
            return [np.ones((1, 2), dtype=np.float32)]

        def close(self):
            pass

    runner = FixedBatchOneRunner()
    monkeypatch.setattr(reid_inference, 'create_runner', lambda *_args: runner)
    embedder = ReIdEmbedder('model.onnx', 'onnxruntime', {
        'input_size': '32x16',
        'embedding_dimension': 2,
        # Legacy UI metadata had this hint but no actual batch contract.
        'batch_size': 32,
    })

    embeddings = embedder.embed([
        np.zeros((40, 20, 3), dtype=np.uint8) for _ in range(3)
    ])

    assert len(embeddings) == 3
    assert runner.batch_lengths == [1, 1, 1]


def test_explicit_dynamic_batch_contract_combines_crops(monkeypatch):
    class DynamicRunner:
        def __init__(self):
            self.batch_lengths = []

        def infer(self, tensor):
            self.batch_lengths.append(tensor.shape[0])
            return [np.ones((tensor.shape[0], 2), dtype=np.float32)]

        def close(self):
            pass

    runner = DynamicRunner()
    monkeypatch.setattr(reid_inference, 'create_runner', lambda *_args: runner)
    embedder = ReIdEmbedder('model.onnx', 'onnxruntime', {
        'input_size': '32x16', 'embedding_dimension': 2,
        'batch_size': 32, 'dynamic_batch': True,
    })

    embedder.embed([np.zeros((40, 20, 3), dtype=np.uint8) for _ in range(3)])

    assert runner.batch_lengths == [3]


def test_verified_artifact_carries_declared_device(tmp_path):
    model = tmp_path / 'reid.pt'
    model.write_bytes(b'torchscript')
    artifact = SimpleNamespace(
        file_path=str(model), artifact_sha256='',
        metadata={'device': 'cuda'}, device='cpu',
    )
    bundle = SimpleNamespace(
        preprocess={}, input_size='256x128', embedding_dimension=2,
    )

    _path, metadata = verified_reid_artifact(bundle, artifact, 'torchscript')

    assert metadata['device'] == 'cpu'


def test_reid_worker_warmup_exercises_embedder():
    class FakeEmbedder:
        input_height = 256
        input_width = 128

        def __init__(self):
            self.shapes = []

        def embed(self, crops):
            self.shapes.extend(crop.shape for crop in crops)
            return [np.asarray([1.0, 0.0], dtype=np.float32)]

    backend = ReIdWorkerBackend.__new__(ReIdWorkerBackend)
    backend.embedder = FakeEmbedder()

    backend.warmup()

    assert backend.embedder.shapes == [(256, 128, 3)]
