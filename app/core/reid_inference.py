"""Backend-neutral person ReID embedding inference.

The logical bundle owns the feature-space contract. Platform artifacts are only
different executable forms of that same contract.
"""

from __future__ import annotations

import hashlib
import os
import platform
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.core.cv2_compat import cv2
from app.core.face_inference import create_runner, runtime_capabilities


SUPPORTED_REID_RUNTIMES = (
    'onnxruntime', 'onnxruntime-cuda', 'tensorrt', 'torchscript', 'rknn',
)


class ReIdInferenceError(RuntimeError):
    pass


def _parse_input_size(value: Any) -> Tuple[int, int]:
    text = str(value or '256x128').lower().replace(' ', '')
    try:
        height, width = (int(part) for part in text.split('x', 1))
    except (TypeError, ValueError):
        raise ReIdInferenceError('ReID input_size 必须类似 256x128')
    if height < 16 or width < 16:
        raise ReIdInferenceError('ReID input_size 过小')
    return width, height


def _l2_normalize(value: np.ndarray) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise ReIdInferenceError('ReID 模型输出了无效特征')
    result = vector / norm
    if not np.all(np.isfinite(result)):
        raise ReIdInferenceError('ReID 模型输出包含 NaN/Inf')
    return result


def _artifact_score(artifact, runtime_name: str, capabilities: Dict[str, Any]) -> int:
    machine = str(capabilities.get('machine') or platform.machine()).lower()
    aliases = {
        'x86_64': {'x86_64', 'amd64'}, 'amd64': {'x86_64', 'amd64'},
        'aarch64': {'aarch64', 'arm64'}, 'arm64': {'aarch64', 'arm64'},
    }
    architecture = str(artifact.architecture or 'any').lower()
    if architecture not in aliases.get(machine, {machine}) | {'any', 'all', '*'}:
        return -1
    device = str(artifact.device or 'any').lower()
    generic = {'any', 'all', '*'}
    if runtime_name == 'rknn':
        wanted = {'rk3588', 'rockchip', 'npu'}
    elif runtime_name in {'tensorrt', 'onnxruntime-cuda'}:
        wanted = {'cuda', 'nvidia', 'gpu', 'jetson', 'orin', 'orin-nx'}
    elif runtime_name == 'torchscript':
        wanted = {'cuda', 'nvidia', 'gpu', 'jetson', 'orin', 'orin-nx', 'cpu'}
    else:
        wanted = {'cpu'}
    if device not in wanted | generic:
        return -1
    return (4 if architecture not in generic else 0) + (2 if device not in generic else 0)


def select_reid_artifact(
    bundle,
    requested_backend: str = 'auto',
    capabilities: Optional[Dict[str, Any]] = None,
):
    if not bundle.enabled:
        raise ReIdInferenceError('ReID 模型包已禁用')
    capabilities = dict(capabilities or runtime_capabilities())
    available = set(capabilities.get('available_runtimes') or ())
    if not available:
        available = {'onnxruntime'}
        if capabilities.get('rknn_available'):
            available.add('rknn')
        if capabilities.get('torch_available'):
            available.add('torchscript')
        if capabilities.get('onnx_cuda_available'):
            available.add('onnxruntime-cuda')
        if capabilities.get('tensorrt_available'):
            available.add('tensorrt')
    requested = str(requested_backend or 'auto').strip().lower()
    requested = {'onnx': 'onnxruntime', 'rknnlite': 'rknn', 'torch': 'torchscript'}.get(
        requested, requested
    )
    if requested == 'auto':
        order = [
            capabilities.get('preferred_backend'), 'rknn', 'tensorrt',
            'onnxruntime-cuda', 'torchscript', 'onnxruntime',
        ]
        order = [item for item in dict.fromkeys(order) if item and item in available]
    else:
        if requested not in SUPPORTED_REID_RUNTIMES:
            raise ReIdInferenceError(f'不支持的 ReID 运行时: {requested}')
        order = [requested]
    stored_alias = {'onnxruntime-cuda': 'onnxruntime'}
    enabled = [item for item in bundle.artifacts if item.enabled]
    for runtime_name in order:
        stored = stored_alias.get(runtime_name, runtime_name)
        candidates = [
            (_artifact_score(item, runtime_name, capabilities), item)
            for item in enabled if str(item.runtime).lower() == stored
        ]
        candidates = [pair for pair in candidates if pair[0] >= 0]
        if candidates:
            return runtime_name, max(candidates, key=lambda pair: pair[0])[1], capabilities
    raise ReIdInferenceError('模型包缺少当前平台可用的 ReID 制品')


def verified_reid_artifact(bundle, artifact, selected_backend: str):
    path = str(artifact.file_path or '')
    if not path or not os.path.isfile(path):
        raise ReIdInferenceError('ReID 模型制品文件不存在')
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    expected = str(artifact.artifact_sha256 or '').lower()
    if expected and digest.hexdigest() != expected:
        raise ReIdInferenceError('ReID 模型制品 SHA-256 校验失败')
    metadata = dict(bundle.preprocess or {})
    metadata.update(dict(artifact.metadata or {}))
    metadata.setdefault('input_size', bundle.input_size)
    metadata.setdefault('embedding_dimension', int(bundle.embedding_dimension))
    # The normalized artifact column is the authoritative placement contract;
    # do not allow free-form metadata to silently contradict it.
    metadata['device'] = artifact.device
    if 'providers' not in metadata:
        if selected_backend == 'onnxruntime':
            metadata['providers'] = ['CPUExecutionProvider']
        elif selected_backend == 'onnxruntime-cuda':
            metadata['providers'] = ['CUDAExecutionProvider', 'CPUExecutionProvider']
    return path, metadata


class ReIdEmbedder:
    def __init__(self, path: str, runtime: str, metadata: Dict[str, Any]):
        self.metadata = dict(metadata)
        self.runner = create_runner(path, runtime, self.metadata)
        self.runtime = runtime
        self.input_width, self.input_height = _parse_input_size(
            self.metadata.get('input_size') or self.metadata.get('input_shape')
        )
        self.dimension = int(self.metadata.get('embedding_dimension') or 512)
        self.layout = str(self.metadata.get('input_layout') or 'nchw').lower()
        self.dtype = str(self.metadata.get('input_dtype') or 'float32').lower()
        self.color = str(self.metadata.get('color') or 'rgb').lower()
        self.mean = np.asarray(
            self.metadata.get('mean', [123.675, 116.28, 103.53]), dtype=np.float32
        )
        self.std = np.asarray(
            self.metadata.get('std', [58.395, 57.12, 57.375]), dtype=np.float32
        )
        # A batch-size hint alone does not prove that an exported graph accepts
        # variable batches. Most third-party ReID exports are fixed batch 1, so
        # unknown contracts must be executed one crop at a time. Larger batches
        # require an explicit fixed or dynamic contract declaration.
        dynamic_batch = self.metadata.get('dynamic_batch') is True
        fixed_batch = self.metadata.get('fixed_batch') is True
        if dynamic_batch or fixed_batch:
            self.batch_size = max(1, int(self.metadata.get('batch_size') or 1))
            self.fixed_batch = fixed_batch and not dynamic_batch
        else:
            self.batch_size = 1
            self.fixed_batch = True

    def _tensor(self, crop_rgb: np.ndarray) -> np.ndarray:
        image = cv2.resize(
            crop_rgb, (self.input_width, self.input_height), interpolation=cv2.INTER_LINEAR
        )
        if self.color == 'bgr':
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        if self.dtype == 'uint8':
            tensor = image.astype(np.uint8)
        else:
            tensor = (image.astype(np.float32) - self.mean) / self.std
        if self.layout == 'nchw':
            tensor = np.transpose(tensor, (2, 0, 1))
        return np.ascontiguousarray(tensor)

    def embed(self, crops: Sequence[np.ndarray]) -> List[np.ndarray]:
        tensors = [self._tensor(crop) for crop in crops]
        embeddings: List[np.ndarray] = []
        for offset in range(0, len(tensors), self.batch_size):
            chunk = list(tensors[offset:offset + self.batch_size])
            actual = len(chunk)
            if self.fixed_batch and actual < self.batch_size:
                chunk.extend([chunk[-1]] * (self.batch_size - actual))
            outputs = self.runner.infer(np.stack(chunk))
            if not outputs:
                raise ReIdInferenceError('ReID 模型没有输出')
            try:
                matrix = np.asarray(outputs[0]).reshape(len(chunk), -1)
            except ValueError as exc:
                raise ReIdInferenceError('ReID 输出批大小与输入不一致') from exc
            if matrix.shape[1] != self.dimension:
                raise ReIdInferenceError(
                    f'ReID 输出维度不匹配: expected={self.dimension}, actual={matrix.shape[1]}'
                )
            embeddings.extend(_l2_normalize(row) for row in matrix[:actual])
        return embeddings

    def close(self):
        self.runner.close()


def _crop_person(frame_rgb: np.ndarray, box: Sequence[float], expansion: float):
    height, width = frame_rgb.shape[:2]
    x1, y1, x2, y2 = [float(value) for value in box[:4]]
    expand_x = max(0.0, x2 - x1) * expansion
    expand_y = max(0.0, y2 - y1) * expansion
    left = max(0, int(np.floor(x1 - expand_x)))
    top = max(0, int(np.floor(y1 - expand_y)))
    right = min(width, int(np.ceil(x2 + expand_x)))
    bottom = min(height, int(np.ceil(y2 + expand_y)))
    if right <= left or bottom <= top:
        return None
    return frame_rgb[top:bottom, left:right]


class ReIdWorkerBackend:
    name = 'reid_embedding'

    def __init__(self, bundle, backend: str = 'auto', config: Optional[Dict[str, Any]] = None):
        started = time.monotonic()
        self.bundle = bundle
        self.backend, artifact, self.capabilities = select_reid_artifact(bundle, backend)
        path, metadata = verified_reid_artifact(bundle, artifact, self.backend)
        self.embedder = ReIdEmbedder(path, self.backend, metadata)
        self.model = self.embedder
        self.config = dict(config or {})
        self.contract_id = bundle.contract_id
        self.artifact_hash = artifact.artifact_sha256
        self.startup_time_ms = (time.monotonic() - started) * 1000.0

    @staticmethod
    def _prepare(frame: np.ndarray, config: Dict[str, Any]):
        boxes = list(config.get('reid_boxes') or [])
        min_height = int(config.get('reid_min_box_height') or 48)
        expansion = float(config.get('reid_crop_expansion') or 0.05)
        crops, indexes, rejected = [], [], []
        for index, box in enumerate(boxes):
            if not isinstance(box, (list, tuple)) or len(box) < 4:
                rejected.append({'index': index, 'reason': 'invalid_box'})
                continue
            if float(box[3]) - float(box[1]) < min_height:
                rejected.append({'index': index, 'reason': 'person_too_small'})
                continue
            crop = _crop_person(frame, box, expansion)
            if crop is None or crop.size == 0:
                rejected.append({'index': index, 'reason': 'invalid_crop'})
                continue
            crops.append(crop)
            indexes.append(index)
        return boxes, crops, indexes, rejected

    def infer(self, frame: np.ndarray):
        started = time.monotonic()
        boxes, crops, indexes, rejected = self._prepare(frame, self.config)
        embeddings = self.embedder.embed(crops) if crops else []
        details = [
            {'index': index, 'embedding': embedding.tolist()}
            for index, embedding in zip(indexes, embeddings)
        ]
        return [], details, {
            'backend': self.backend,
            'model_contract': self.contract_id,
            'artifact_hash': self.artifact_hash,
            'requested_count': len(boxes),
            'embedded_count': len(details),
            'rejected': rejected,
            'inference_time_ms': (time.monotonic() - started) * 1000.0,
        }

    def warmup(self):
        """Exercise the actual embedder before a shared worker reports ready."""
        crop = np.zeros(
            (self.embedder.input_height, self.embedder.input_width, 3),
            dtype=np.uint8,
        )
        embeddings = self.embedder.embed([crop])
        if len(embeddings) != 1:
            raise ReIdInferenceError('ReID 预热未返回单个 embedding')

    def infer_batch(self, frames: List[np.ndarray], configs: List[Dict[str, Any]]):
        started = time.monotonic()
        prepared = [self._prepare(frame, dict(config or {})) for frame, config in zip(frames, configs)]
        all_crops = [crop for _boxes, crops, _indexes, _rejected in prepared for crop in crops]
        all_embeddings = self.embedder.embed(all_crops) if all_crops else []
        offset = 0
        results = []
        for boxes, crops, indexes, rejected in prepared:
            embeddings = all_embeddings[offset:offset + len(crops)]
            offset += len(crops)
            details = [
                {'index': index, 'embedding': embedding.tolist()}
                for index, embedding in zip(indexes, embeddings)
            ]
            results.append(([], details, {
                'backend': self.backend,
                'model_contract': self.contract_id,
                'artifact_hash': self.artifact_hash,
                'requested_count': len(boxes),
                'embedded_count': len(details),
                'rejected': rejected,
                'inference_time_ms': (time.monotonic() - started) * 1000.0,
                'crop_batch_size': len(all_crops),
            }))
        return results

    def cleanup(self):
        self.embedder.close()
        self.model = None


class SharedReIdBackend:
    name = 'reid_embedding'

    def __init__(self, bundle, backend: str, config: Dict[str, Any]):
        from app.core.shared_inference import SharedInferenceClient, build_reid_model_spec

        self.config = dict(config or {})
        self.client = SharedInferenceClient(
            spec=build_reid_model_spec(bundle, backend), config=self.config
        )
        self.model = self.client

    def infer(self, frame: np.ndarray, boxes: Sequence[Sequence[float]]):
        config = {**self.config, 'reid_boxes': [list(box[:4]) for box in boxes]}
        return self.client.infer(frame, config)

    def cleanup(self):
        if self.client is not None:
            self.client.close()
            self.client = None
        self.model = None


class LocalReIdBackend:
    name = 'reid_embedding'

    def __init__(self, bundle, backend: str, config: Dict[str, Any]):
        self.worker = ReIdWorkerBackend(bundle, backend, config)
        self.model = self.worker.model

    def infer(self, frame: np.ndarray, boxes: Sequence[Sequence[float]]):
        self.worker.config = {**self.worker.config, 'reid_boxes': [list(box[:4]) for box in boxes]}
        detections, details, metadata = self.worker.infer(frame)
        return {'ok': True, 'detections': detections, 'details': details, 'metadata': metadata}

    def cleanup(self):
        self.worker.cleanup()
        self.model = None


def create_reid_backend(bundle, backend: str = 'auto', config: Optional[Dict[str, Any]] = None):
    shared = os.getenv('SHARED_INFERENCE_ENABLED', 'false').lower() in {'true', '1', 'yes', 'on'}
    in_worker = os.getenv('SHARED_INFERENCE_WORKER', 'false').lower() in {'true', '1', 'yes', 'on'}
    if shared and not in_worker:
        return SharedReIdBackend(bundle, backend, dict(config or {}))
    return LocalReIdBackend(bundle, backend, dict(config or {}))
