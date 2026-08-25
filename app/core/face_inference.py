"""Portable face detection and embedding runtime.

The business contract is deliberately backend-neutral. Model artifacts must
emit either a combined ``N x 15`` face tensor (box, score, five landmarks) or
separate boxes/scores/landmarks tensors declared in artifact metadata.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import platform
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np


class FaceInferenceError(RuntimeError):
    pass


_RUNNER_PLUGINS = {}
_RUNNER_PLUGIN_LOCK = threading.RLock()
_RUNNER_PLUGIN_ERRORS = []
_RUNNER_PLUGINS_LOADED = False
_BUILTIN_ARTIFACT_RUNTIMES = {
    'onnxruntime', 'tensorrt', 'rknn', 'torchscript',
}


def register_face_runner(runtime: str, factory, *, extensions=()):
    """Register a deployment-specific runner without changing business code."""
    name = str(runtime or '').strip().lower()
    if not name or not name.replace('-', '').replace('_', '').isalnum():
        raise ValueError('人脸推理插件名称无效')
    if not callable(factory):
        raise TypeError('人脸推理插件 factory 必须可调用')
    normalized_extensions = {
        value if str(value).startswith('.') else f'.{value}'
        for value in (extensions or ())
    }
    with _RUNNER_PLUGIN_LOCK:
        _RUNNER_PLUGINS[name] = (factory, {
            str(value).lower() for value in normalized_extensions
        })


def _load_runner_plugins():
    global _RUNNER_PLUGINS_LOADED
    with _RUNNER_PLUGIN_LOCK:
        if _RUNNER_PLUGINS_LOADED:
            return list(_RUNNER_PLUGIN_ERRORS)
        modules = [
            value.strip()
            for value in os.getenv('FACE_INFERENCE_PLUGIN_MODULES', '').split(',')
            if value.strip()
        ]
        for module_name in modules:
            try:
                importlib.import_module(module_name)
            except Exception as exc:
                _RUNNER_PLUGIN_ERRORS.append(f'{module_name}: {exc}')
        _RUNNER_PLUGINS_LOADED = True
        return list(_RUNNER_PLUGIN_ERRORS)


def supported_face_runtimes():
    """Return artifact runtime names accepted for model uploads."""
    errors = _load_runner_plugins()
    with _RUNNER_PLUGIN_LOCK:
        runtimes = _BUILTIN_ARTIFACT_RUNTIMES | set(_RUNNER_PLUGINS)
    return sorted(runtimes), errors


def _host_available_face_backends(capabilities: Dict[str, Any]) -> List[str]:
    """Return execution backends that the current host can actually start."""
    available = set()
    providers = set(capabilities.get('onnx_providers') or [])
    if 'CPUExecutionProvider' in providers:
        available.add('onnxruntime')
    if 'CUDAExecutionProvider' in providers:
        available.add('onnxruntime-cuda')
    if 'TensorrtExecutionProvider' in providers:
        available.add('tensorrt')
    if capabilities.get('is_rockchip') and capabilities.get('rknn_available'):
        available.add('rknn')
    if capabilities.get('torch_available') or capabilities.get('torch_cuda_available'):
        available.add('torchscript')
    _load_runner_plugins()
    with _RUNNER_PLUGIN_LOCK:
        available.update(_RUNNER_PLUGINS)
    return sorted(available)


def available_face_runtimes():
    """Return host-runnable backends, separate from uploadable artifact types."""
    capabilities = runtime_capabilities()
    runtimes = (
        capabilities.get('available_runtimes')
        or _host_available_face_backends(capabilities)
    )
    return list(runtimes), list(capabilities.get('plugin_errors') or [])


def face_runtime_extensions(runtime: str):
    name = str(runtime or '').strip().lower()
    builtin = {
        'onnxruntime': {'.onnx'},
        'tensorrt': {'.onnx'},
        'rknn': {'.rknn'},
        'torchscript': {'.pt', '.pth'},
    }
    if name in builtin:
        return builtin[name]
    _load_runner_plugins()
    with _RUNNER_PLUGIN_LOCK:
        registered = _RUNNER_PLUGINS.get(name)
    return set(registered[1]) if registered else set()


def _l2_normalize(vector) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(value))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise FaceInferenceError('特征模型返回了无效向量')
    return np.ascontiguousarray(value / norm, dtype=np.float32)


def _parse_shape(value: Any, default: Tuple[int, int]) -> Tuple[int, int]:
    if isinstance(value, str):
        parts = value.lower().replace(' ', '').split('x')
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            return int(parts[0]), int(parts[1])
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return int(value[-1]), int(value[-2])
    return default


def _letterbox(image: np.ndarray, width: int, height: int):
    source_h, source_w = image.shape[:2]
    scale = min(width / source_w, height / source_h)
    resized_w = max(1, int(round(source_w * scale)))
    resized_h = max(1, int(round(source_h * scale)))
    resized = cv2.resize(image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    pad_x = (width - resized_w) // 2
    pad_y = (height - resized_h) // 2
    canvas[pad_y:pad_y + resized_h, pad_x:pad_x + resized_w] = resized
    return canvas, scale, pad_x, pad_y


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> List[int]:
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes.T
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        index = int(order[0])
        keep.append(index)
        if order.size == 1:
            break
        remaining = order[1:]
        xx1 = np.maximum(x1[index], x1[remaining])
        yy1 = np.maximum(y1[index], y1[remaining])
        xx2 = np.minimum(x2[index], x2[remaining])
        yy2 = np.minimum(y2[index], y2[remaining])
        intersection = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        union = areas[index] + areas[remaining] - intersection
        iou = np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)
        order = remaining[iou <= threshold]
    return keep


class ModelRunner:
    runtime = 'base'

    def infer(self, tensor: np.ndarray) -> List[np.ndarray]:
        raise NotImplementedError

    def close(self):
        return None


class OnnxRunner(ModelRunner):
    runtime = 'onnxruntime'

    def __init__(self, path: str, metadata: Dict[str, Any]):
        try:
            import onnxruntime as ort
        except Exception as exc:
            raise FaceInferenceError(f'ONNX Runtime 不可用: {exc}') from exc
        requested = metadata.get('providers')
        if not requested:
            requested = _preferred_onnx_providers(ort.get_available_providers())
        self.session = ort.InferenceSession(path, providers=list(requested))
        self.input_name = metadata.get('input_name') or self.session.get_inputs()[0].name
        self.providers = self.session.get_providers()

    def infer(self, tensor: np.ndarray) -> List[np.ndarray]:
        return [np.asarray(item) for item in self.session.run(None, {self.input_name: tensor})]

    def close(self):
        self.session = None


class RknnRunner(ModelRunner):
    runtime = 'rknn'
    _native_lock = threading.RLock()

    def __init__(self, path: str, metadata: Dict[str, Any]):
        try:
            from rknnlite.api import RKNNLite
        except Exception as exc:
            raise FaceInferenceError(f'RKNNLite 不可用: {exc}') from exc
        self.model = RKNNLite()
        result = self.model.load_rknn(path)
        if result != 0:
            raise FaceInferenceError(f'RKNN 模型加载失败: code={result}')
        core_mask = str(metadata.get('core_mask') or 'auto').lower()
        mask_map = {
            'auto': RKNNLite.NPU_CORE_AUTO,
            'core_0': RKNNLite.NPU_CORE_0,
            'core_1': RKNNLite.NPU_CORE_1,
            'core_2': RKNNLite.NPU_CORE_2,
        }
        result = self.model.init_runtime(core_mask=mask_map.get(core_mask, RKNNLite.NPU_CORE_AUTO))
        if result != 0:
            self.model.release()
            raise FaceInferenceError(f'RKNN runtime 初始化失败: code={result}')

    def infer(self, tensor: np.ndarray) -> List[np.ndarray]:
        with self._native_lock:
            return [np.asarray(item) for item in self.model.inference(inputs=[tensor])]

    def close(self):
        if self.model is not None:
            with self._native_lock:
                self.model.release()
            self.model = None


class TorchScriptRunner(ModelRunner):
    runtime = 'torchscript'

    def __init__(self, path: str, metadata: Dict[str, Any]):
        try:
            import torch
        except Exception as exc:
            raise FaceInferenceError(f'PyTorch 不可用: {exc}') from exc
        requested = str(metadata.get('device') or 'auto').lower()
        self.device = 'cuda' if requested == 'auto' and torch.cuda.is_available() else requested
        if self.device == 'auto':
            self.device = 'cpu'
        self.torch = torch
        self.model = torch.jit.load(path, map_location=self.device).eval()

    def infer(self, tensor: np.ndarray) -> List[np.ndarray]:
        value = self.torch.from_numpy(tensor).to(self.device)
        with self.torch.inference_mode():
            outputs = self.model(value)
        if not isinstance(outputs, (list, tuple)):
            outputs = [outputs]
        return [item.detach().float().cpu().numpy() for item in outputs]

    def close(self):
        self.model = None


def _preferred_onnx_providers(available: Iterable[str]) -> List[str]:
    available_set = set(available)
    preferred = [
        'TensorrtExecutionProvider',
        'CUDAExecutionProvider',
        'CPUExecutionProvider',
    ]
    selected = [provider for provider in preferred if provider in available_set]
    return selected or list(available)


def create_runner(path: str, runtime: str, metadata: Optional[Dict[str, Any]] = None) -> ModelRunner:
    runtime_name = str(runtime or '').lower()
    metadata = dict(metadata or {})
    _load_runner_plugins()
    with _RUNNER_PLUGIN_LOCK:
        plugin = _RUNNER_PLUGINS.get(runtime_name)
    if plugin is not None:
        return plugin[0](path, metadata)
    if runtime_name in {'onnx', 'onnxruntime', 'onnxruntime-cuda', 'tensorrt'}:
        if runtime_name == 'tensorrt' and 'providers' not in metadata:
            metadata['providers'] = ['TensorrtExecutionProvider', 'CUDAExecutionProvider']
        return OnnxRunner(path, metadata)
    if runtime_name in {'rknn', 'rknnlite'}:
        return RknnRunner(path, metadata)
    if runtime_name in {'torch', 'torchscript', 'pytorch'}:
        return TorchScriptRunner(path, metadata)
    raise FaceInferenceError(f'不支持的人脸推理后端: {runtime}')


def _verified_artifact(bundle, artifact, role: str, selected_backend: str):
    path = str(artifact.file_path or '')
    if not path or not os.path.isfile(path):
        raise FaceInferenceError(f'{role} 模型制品文件不存在')
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    expected = str(artifact.artifact_sha256 or '').lower()
    if expected and digest.hexdigest() != expected:
        raise FaceInferenceError(f'{role} 模型制品 SHA-256 校验失败')

    preprocess = dict(bundle.preprocess or {})
    metadata = {}
    common = preprocess.get('common')
    if isinstance(common, dict):
        metadata.update(common)
    role_config = preprocess.get(role)
    if isinstance(role_config, dict):
        metadata.update(role_config)
    # A flat object is also accepted for simple bundles.
    if not any(key in preprocess for key in ('common', 'detection', 'embedding')):
        metadata.update(preprocess)
    metadata.update(dict(artifact.metadata or {}))
    if 'providers' not in metadata:
        if selected_backend == 'onnxruntime':
            metadata['providers'] = ['CPUExecutionProvider']
        elif selected_backend == 'onnxruntime-cuda':
            metadata['providers'] = [
                'CUDAExecutionProvider', 'CPUExecutionProvider'
            ]
    return type('VerifiedFaceArtifact', (), {
        'file_path': path,
        'runtime': selected_backend,
        'metadata': metadata,
    })()


@dataclass(frozen=True)
class FaceCandidate:
    box: Tuple[float, float, float, float]
    confidence: float
    landmarks: Tuple[Tuple[float, float], ...]


class FaceDetector:
    def __init__(self, artifact):
        self.metadata = dict(artifact.metadata)
        self.runner = create_runner(artifact.file_path, artifact.runtime, self.metadata)
        self.input_width, self.input_height = _parse_shape(
            self.metadata.get('input_shape'), (320, 320)
        )
        self.input_layout = str(self.metadata.get('input_layout') or 'nchw').lower()
        self.input_dtype = str(self.metadata.get('input_dtype') or 'float32').lower()
        self.color = str(self.metadata.get('color') or 'rgb').lower()
        self.mean = np.asarray(self.metadata.get('mean', [127.5, 127.5, 127.5]), dtype=np.float32)
        self.std = np.asarray(self.metadata.get('std', [128.0, 128.0, 128.0]), dtype=np.float32)

    def _tensor(self, frame_rgb: np.ndarray):
        image, scale, pad_x, pad_y = _letterbox(
            frame_rgb, self.input_width, self.input_height
        )
        if self.color == 'bgr':
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        if self.input_dtype == 'uint8':
            tensor = image.astype(np.uint8)
        else:
            tensor = (image.astype(np.float32) - self.mean) / self.std
        if self.input_layout == 'nchw':
            tensor = np.transpose(tensor, (2, 0, 1))
        return np.expand_dims(np.ascontiguousarray(tensor), 0), scale, pad_x, pad_y

    def detect(self, frame_rgb: np.ndarray, confidence: float = 0.6, nms_iou: float = 0.4):
        tensor, scale, pad_x, pad_y = self._tensor(frame_rgb)
        outputs = self.runner.infer(tensor)
        boxes, scores, landmarks = self._parse_outputs(outputs)
        scores = scores.reshape(-1)
        boxes = boxes.reshape(-1, 4)
        landmarks = landmarks.reshape(-1, 5, 2)
        count = min(len(boxes), len(scores), len(landmarks))
        boxes, scores, landmarks = boxes[:count], scores[:count], landmarks[:count]
        selected = np.isfinite(scores) & (scores >= float(confidence))
        boxes, scores, landmarks = boxes[selected], scores[selected], landmarks[selected]
        if not bool(self.metadata.get('coordinates_are_absolute', True)):
            boxes[:, [0, 2]] *= self.input_width
            boxes[:, [1, 3]] *= self.input_height
            landmarks[:, :, 0] *= self.input_width
            landmarks[:, :, 1] *= self.input_height
        boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad_x) / scale
        boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad_y) / scale
        landmarks[:, :, 0] = (landmarks[:, :, 0] - pad_x) / scale
        landmarks[:, :, 1] = (landmarks[:, :, 1] - pad_y) / scale
        height, width = frame_rgb.shape[:2]
        boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, width - 1)
        boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, height - 1)
        keep = _nms(boxes, scores, float(nms_iou))
        return [
            FaceCandidate(
                box=tuple(float(v) for v in boxes[index]),
                confidence=float(scores[index]),
                landmarks=tuple(tuple(float(v) for v in point) for point in landmarks[index]),
            )
            for index in keep
        ]

    def _parse_outputs(self, outputs: Sequence[np.ndarray]):
        output_format = str(self.metadata.get('output_format') or 'auto').lower()
        arrays = []
        for value in outputs:
            array = np.asarray(value)
            if array.ndim > 0 and array.shape[0] == 1:
                array = array[0]
            arrays.append(array)
        if output_format in {'combined', 'auto'}:
            combined = next(
                (
                    value.reshape(-1, value.shape[-1])
                    for value in arrays
                    if value.ndim >= 1 and value.shape[-1] >= 15
                ),
                None,
            )
            if combined is not None:
                return combined[:, :4], combined[:, 4], combined[:, 5:15]
        indexes = self.metadata.get('output_indexes') or {'boxes': 0, 'scores': 1, 'landmarks': 2}
        try:
            boxes = arrays[int(indexes['boxes'])]
            scores = arrays[int(indexes['scores'])]
            landmarks = arrays[int(indexes['landmarks'])]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise FaceInferenceError('检测模型输出与人脸模型契约不匹配') from exc
        if scores.ndim > 1 and scores.shape[-1] > 1:
            scores = scores[..., -1]
        return boxes, scores, landmarks

    def close(self):
        self.runner.close()


_ARCFACE_REFERENCE = np.asarray([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float32)


def align_face(frame_rgb: np.ndarray, landmarks: Sequence[Sequence[float]]) -> np.ndarray:
    source = np.asarray(landmarks, dtype=np.float32).reshape(5, 2)
    transform, _ = cv2.estimateAffinePartial2D(source, _ARCFACE_REFERENCE, method=cv2.LMEDS)
    if transform is None:
        raise FaceInferenceError('人脸五点对齐失败')
    return cv2.warpAffine(frame_rgb, transform, (112, 112), borderValue=0)


def _embedding_batch_policy(metadata: Dict[str, Any]) -> Tuple[Optional[int], bool]:
    """Return (batch limit, requires exact batch shape).

    A missing declaration defaults to one because fixed-batch RKNN and edge
    ONNX artifacts are common. Dynamic batching must be declared explicitly.
    """
    dynamic_tokens = {'dynamic', 'auto', '-1', '0', 'none'}
    for key in ('batch_size', 'input_batch_size'):
        raw = metadata.get(key)
        if raw is None:
            continue
        if str(raw).strip().lower() in dynamic_tokens:
            return None, False
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed, True

    shape = metadata.get('input_shape')
    first_dimension = None
    if isinstance(shape, str):
        parts = shape.lower().replace(' ', '').split('x')
        if len(parts) >= 4:
            first_dimension = parts[0]
    elif isinstance(shape, (list, tuple)) and len(shape) >= 4:
        first_dimension = shape[0]
    if first_dimension is not None:
        if str(first_dimension).strip().lower() in dynamic_tokens:
            return None, False
        try:
            parsed = int(first_dimension)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            return parsed, True

    try:
        maximum = int(metadata.get('max_batch_size') or 0)
    except (TypeError, ValueError):
        maximum = 0
    if maximum > 0:
        return maximum, False
    dynamic_batch = metadata.get('dynamic_batch')
    if dynamic_batch is True or str(dynamic_batch).strip().lower() in {
        'true', '1', 'yes', 'on'
    }:
        return None, False
    return 1, True


class FaceEmbedder:
    def __init__(self, artifact):
        self.metadata = dict(artifact.metadata)
        self.runner = create_runner(artifact.file_path, artifact.runtime, self.metadata)
        self.input_layout = str(self.metadata.get('input_layout') or 'nchw').lower()
        self.input_dtype = str(self.metadata.get('input_dtype') or 'float32').lower()
        self.color = str(self.metadata.get('color') or 'rgb').lower()
        self.mean = np.asarray(self.metadata.get('mean', [127.5, 127.5, 127.5]), dtype=np.float32)
        self.std = np.asarray(self.metadata.get('std', [127.5, 127.5, 127.5]), dtype=np.float32)
        self.batch_size, self.fixed_batch = _embedding_batch_policy(self.metadata)

    def embed_aligned(self, aligned_faces: Sequence[np.ndarray]) -> List[np.ndarray]:
        if not aligned_faces:
            return []
        tensors = []
        for face in aligned_faces:
            image = cv2.resize(face, (112, 112), interpolation=cv2.INTER_LINEAR)
            if self.color == 'bgr':
                image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            if self.input_dtype == 'uint8':
                tensor = image.astype(np.uint8)
            else:
                tensor = (image.astype(np.float32) - self.mean) / self.std
            if self.input_layout == 'nchw':
                tensor = np.transpose(tensor, (2, 0, 1))
            tensors.append(np.ascontiguousarray(tensor))
        embeddings = []
        batch_limit = self.batch_size or len(tensors)
        for offset in range(0, len(tensors), batch_limit):
            chunk = list(tensors[offset:offset + batch_limit])
            actual_count = len(chunk)
            inference_tensors = list(chunk)
            if self.fixed_batch and self.batch_size and actual_count < self.batch_size:
                inference_tensors.extend(
                    [chunk[-1]] * (self.batch_size - actual_count)
                )
            outputs = self.runner.infer(np.stack(inference_tensors))
            if not outputs:
                raise FaceInferenceError('特征模型没有输出')
            try:
                matrix = np.asarray(outputs[0]).reshape(len(inference_tensors), -1)
            except ValueError as exc:
                raise FaceInferenceError('特征模型输出批大小与输入契约不匹配') from exc
            embeddings.extend(
                _l2_normalize(row) for row in matrix[:actual_count]
            )
        return embeddings

    def close(self):
        self.runner.close()


def assess_face_quality(frame_rgb: np.ndarray, candidate: FaceCandidate, min_face_size: int = 80):
    x1, y1, x2, y2 = [int(round(value)) for value in candidate.box]
    width, height = max(0, x2 - x1), max(0, y2 - y1)
    if min(width, height) < int(min_face_size):
        return {'accepted': False, 'reason': 'face_too_small', 'score': 0.0}
    crop = frame_rgb[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
    if crop.size == 0:
        return {'accepted': False, 'reason': 'invalid_crop', 'score': 0.0}
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    brightness = float(gray.mean())
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness_score = max(0.0, 1.0 - abs(brightness - 127.5) / 127.5)
    blur_score = min(1.0, blur / 100.0)
    score = 0.45 * brightness_score + 0.55 * blur_score
    if brightness < 35 or brightness > 225:
        return {'accepted': False, 'reason': 'exposure', 'score': score, 'brightness': brightness, 'blur': blur}
    if blur < 25:
        return {'accepted': False, 'reason': 'blur', 'score': score, 'brightness': brightness, 'blur': blur}
    return {'accepted': True, 'reason': 'ok', 'score': score, 'brightness': brightness, 'blur': blur}


def runtime_capabilities() -> Dict[str, Any]:
    machine = platform.machine().lower()
    is_rockchip = False
    is_jetson = False
    try:
        with open('/proc/device-tree/compatible', 'rb') as handle:
            compatible = handle.read().replace(b'\x00', b' ').decode('utf-8', errors='ignore').lower()
        is_rockchip = 'rockchip' in compatible
        is_jetson = 'nvidia' in compatible or 'tegra' in compatible
    except OSError:
        compatible = ''
    onnx_providers = []
    try:
        import onnxruntime as ort
        onnx_providers = ort.get_available_providers()
    except Exception:
        pass
    rknn_available = False
    try:
        import rknnlite.api  # noqa: F401
        rknn_available = True
    except Exception:
        pass
    torch_available = False
    torch_cuda = False
    try:
        import torch
        torch_available = True
        torch_cuda = bool(torch.cuda.is_available())
    except Exception:
        pass
    plugin_runtimes, plugin_errors = supported_face_runtimes()
    capabilities = {
        'machine': machine,
        'compatible': compatible,
        'is_rockchip': is_rockchip,
        'is_jetson': is_jetson,
        'onnx_providers': onnx_providers,
        'rknn_available': rknn_available,
        'torch_available': torch_available,
        'torch_cuda_available': torch_cuda,
        'supported_runtimes': plugin_runtimes,
        'plugin_errors': plugin_errors,
    }
    available = _host_available_face_backends(capabilities)
    preferred = next((backend for backend in (
        'rknn' if is_rockchip else None,
        'tensorrt' if is_jetson else None,
        'onnxruntime-cuda',
        'onnxruntime',
        'torchscript',
        *available,
    ) if backend and backend in available), None)
    capabilities['available_runtimes'] = available
    capabilities['preferred_backend'] = preferred
    return capabilities


def select_bundle_artifacts(bundle, requested_backend: str = 'auto'):
    from app.core.face_settings import get_face_recognition_config

    system_config = get_face_recognition_config()
    if (
        system_config.require_commercial_models
        and not bool(getattr(bundle, 'commercial_use_allowed', False))
    ):
        raise FaceInferenceError('当前部署禁止加载未声明可商用的人脸模型包')
    capabilities = runtime_capabilities()
    requested = str(requested_backend or 'auto').strip().lower()
    if requested == 'auto':
        requested = system_config.inference_backend
    requested = {
        'onnx': 'onnxruntime',
        'rknnlite': 'rknn',
        'torch': 'torchscript',
    }.get(requested, requested)
    aliases = {
        'onnxruntime-cuda': 'onnxruntime',
    }
    artifacts = [item for item in bundle.artifacts if item.enabled]
    available_backends = set(
        capabilities.get('available_runtimes')
        or _host_available_face_backends(capabilities)
    )

    if requested == 'auto':
        runtime_order = [
            capabilities.get('preferred_backend'),
            'rknn',
            'tensorrt',
            'onnxruntime-cuda',
            'onnxruntime',
            'torchscript',
            *sorted(available_backends),
        ]
        runtime_order = [
            runtime for runtime in runtime_order
            if runtime and runtime in available_backends
        ]
    else:
        if requested not in available_backends:
            raise FaceInferenceError(f'当前主机未提供人脸推理后端: {requested}')
        runtime_order = [requested]
    runtime_order = list(dict.fromkeys(runtime_order))

    machine = capabilities['machine']
    architecture_aliases = {
        'x86_64': {'x86_64', 'amd64'},
        'amd64': {'x86_64', 'amd64'},
        'aarch64': {'aarch64', 'arm64'},
        'arm64': {'aarch64', 'arm64'},
    }
    valid_architectures = architecture_aliases.get(machine, {machine}) | {'any', 'all', '*'}

    def artifact_score(artifact, runtime_name):
        architecture = str(artifact.architecture or 'any').lower()
        if architecture not in valid_architectures:
            return -1
        device = str(artifact.device or 'any').lower()
        generic = {'any', 'all', '*'}
        builtin_runtimes = {
            'rknn', 'tensorrt', 'torchscript',
            'onnxruntime-cuda', 'onnxruntime', 'onnx',
        }
        if runtime_name not in builtin_runtimes:
            return (
                (4 if architecture not in {'any', 'all', '*'} else 0)
                + (2 if device not in generic else 0)
            )
        if runtime_name == 'rknn':
            wanted = {'rk3588', 'rockchip', 'npu'}
        elif capabilities['is_jetson'] and runtime_name in {
            'tensorrt', 'torchscript', 'onnxruntime-cuda'
        }:
            wanted = {'jetson', 'orin', 'orin-nx', 'cuda', 'gpu'}
        elif runtime_name == 'torchscript':
            wanted = (
                {'cuda', 'nvidia', 'gpu'}
                if capabilities.get('torch_cuda_available')
                else {'cpu'}
            )
        elif runtime_name in {'tensorrt', 'onnxruntime-cuda'}:
            wanted = {'cuda', 'nvidia', 'gpu'}
        else:
            wanted = {'cpu'}
        if device not in wanted | generic:
            return -1
        return (
            (4 if architecture not in {'any', 'all', '*'} else 0)
            + (2 if device not in generic else 0)
        )

    for runtime_name in runtime_order:
        stored_runtime = aliases.get(runtime_name, runtime_name)
        selected = {}
        for role in ('detection', 'embedding'):
            candidates = [
                (artifact_score(item, runtime_name), item)
                for item in artifacts
                if item.role == role and item.runtime == stored_runtime
            ]
            candidates = [
                item
                for score, item in sorted(
                    candidates, key=lambda pair: pair[0], reverse=True
                )
                if score >= 0
            ]
            if candidates:
                selected[role] = candidates[0]
        if len(selected) == 2:
            return runtime_name, selected, capabilities

    raise FaceInferenceError(
        '模型包缺少当前平台可用的检测/特征制品组合: '
        + ', '.join(runtime_order)
    )


def verify_bundle_artifacts(bundle, requested_backend: str = 'auto'):
    """Validate selected artifact availability and integrity without loading models."""
    backend, artifacts, capabilities = select_bundle_artifacts(
        bundle, requested_backend
    )
    for role in ('detection', 'embedding'):
        _verified_artifact(bundle, artifacts[role], role, backend)
    return backend, artifacts, capabilities


class FacePipeline:
    def __init__(self, bundle, backend: str = 'auto'):
        started = time.monotonic()
        self.backend, artifacts, self.capabilities = select_bundle_artifacts(bundle, backend)
        detector_artifact = _verified_artifact(
            bundle, artifacts['detection'], 'detection', self.backend
        )
        embedding_artifact = _verified_artifact(
            bundle, artifacts['embedding'], 'embedding', self.backend
        )
        self.detector = FaceDetector(detector_artifact)
        try:
            self.embedder = FaceEmbedder(embedding_artifact)
        except Exception:
            self.detector.close()
            raise
        self.contract_id = bundle.contract_id
        self.artifact_hash = hashlib.sha256(
            json.dumps(
                {role: item.artifact_sha256 for role, item in artifacts.items()},
                sort_keys=True,
            ).encode('utf-8')
        ).hexdigest()
        self.startup_time_ms = (time.monotonic() - started) * 1000.0

    def warmup(self):
        frame = np.zeros((320, 320, 3), dtype=np.uint8)
        self.detector.detect(frame)
        self.embedder.embed_aligned([np.zeros((112, 112, 3), dtype=np.uint8)])

    def close(self):
        self.detector.close()
        self.embedder.close()


class FaceWorkerBackend:
    """Adapter used by the existing bounded shared-inference worker."""

    name = 'face_pipeline'

    def __init__(self, bundle, backend: str = 'auto', config: Optional[Dict[str, Any]] = None):
        self.pipeline = FacePipeline(bundle, backend)
        self.config = dict(config or {})
        self.model = self.pipeline

    def infer(self, frame: np.ndarray):
        started = time.monotonic()
        confidence = float(self.config.get('face_detection_confidence', 0.6))
        nms_iou = float(self.config.get('face_nms_iou', 0.4))
        minimum = int(self.config.get('min_face_size', 80))
        candidates = self.pipeline.detector.detect(frame, confidence, nms_iou)
        details = []
        aligned = []
        aligned_indexes = []
        for candidate in candidates:
            quality = assess_face_quality(frame, candidate, minimum)
            detail = {
                'box': list(candidate.box),
                'confidence': candidate.confidence,
                'landmarks': [list(point) for point in candidate.landmarks],
                'quality': quality,
            }
            details.append(detail)
            if quality.get('accepted'):
                aligned.append(align_face(frame, candidate.landmarks))
                aligned_indexes.append(len(details) - 1)
        embeddings = self.pipeline.embedder.embed_aligned(aligned)
        for index, embedding in zip(aligned_indexes, embeddings):
            details[index]['embedding'] = embedding.tolist()
        detections = [
            {
                'box': item['box'],
                'confidence': item['confidence'],
                'label': 'face',
                'landmarks': item['landmarks'],
                'attributes': {
                    'quality': item['quality'],
                    **({'embedding': item['embedding']} if item.get('embedding') is not None else {}),
                },
            }
            for item in details
        ]
        return detections, details, {
            'backend': self.pipeline.backend,
            'model_contract': self.pipeline.contract_id,
            'artifact_hash': self.pipeline.artifact_hash,
            'face_count': len(detections),
            'qualified_face_count': len(embeddings),
            'inference_time_ms': (time.monotonic() - started) * 1000.0,
        }

    def infer_batch(self, frames: List[np.ndarray], configs: List[Dict[str, Any]]):
        results = []
        for frame, config in zip(frames, configs):
            self.config = dict(config or {})
            results.append(self.infer(frame))
        return results

    def cleanup(self):
        self.pipeline.close()
        self.model = None


class SharedFaceBackend:
    """Client-side proxy; pixels travel through existing POSIX shared memory."""

    name = 'face_pipeline'

    def __init__(self, bundle, backend: str, config: Dict[str, Any]):
        from app.core.shared_inference import (
            SharedInferenceClient,
            build_face_model_spec,
        )

        self.config = dict(config or {})
        self.spec = build_face_model_spec(bundle, backend)
        self.client = SharedInferenceClient(spec=self.spec, config=self.config)
        self.model = self.client

    def infer(self, frame: np.ndarray):
        response = self.client.infer(frame, self.config)
        return (
            response.get('detections') or [],
            response.get('details') or [],
            response.get('metadata') or {},
        )

    def cleanup(self):
        if self.client is not None:
            self.client.close()
            self.client = None
        self.model = None


def create_face_backend(bundle, backend: str = 'auto', config: Optional[Dict[str, Any]] = None):
    shared = os.getenv('SHARED_INFERENCE_ENABLED', 'false').lower() in {
        'true', '1', 'yes', 'on'
    }
    if shared and os.getenv('SHARED_INFERENCE_WORKER', 'false').lower() not in {
        'true', '1', 'yes', 'on'
    }:
        return SharedFaceBackend(bundle, backend, dict(config or {}))
    return FaceWorkerBackend(bundle, backend, dict(config or {}))
