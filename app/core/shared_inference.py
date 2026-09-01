"""Cross-process shared Ultralytics, RKNN, and PaddleOCR inference service.

Source workflow hosts exchange frame descriptors over a Unix socket.  Pixels live
in short-lived POSIX shared-memory segments, while exactly one model process is
kept for each stable model key.  OCR uses one process per det+rec pair.
Queues are deliberately bounded: overload drops analysis work instead of
consuming unbounded Jetson unified memory.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from multiprocessing import resource_tracker, shared_memory
from multiprocessing.connection import Client, Listener
from typing import Any, Dict, Optional

import numpy as np

from app.config import (
    APP_DIR,
    GPU_ALLOWED_DEVICES,
    GPU_MEMORY_RESERVE_MB,
    GPU_MODEL_MEMORY_MARGIN_PERCENT,
    GPU_NEW_MODEL_DEFAULT_MB,
    GPU_NVML_STALE_SECONDS,
    GPU_OOM_COOLDOWN_SECONDS,
    GPU_SCHEDULING_ENABLED,
    GPU_SCHEDULING_FAILURE_MODE,
    OOM_CIRCUIT_BREAKER_ENABLED,
    OOM_CIRCUIT_FAILURE_THRESHOLD,
    OOM_CIRCUIT_OPEN_SECONDS,
    OOM_CIRCUIT_STABLE_RESET_SECONDS,
    OOM_RESTART_BACKOFF_MAX_SECONDS,
    SHARED_INFERENCE_BATCH_MAX_SIZE,
    SHARED_INFERENCE_BATCH_WAIT_MS,
    SHARED_INFERENCE_IDLE_SECONDS,
    SHARED_INFERENCE_QUEUE_SIZE,
    SHARED_INFERENCE_REQUEST_TIMEOUT_SECONDS,
    SHARED_INFERENCE_SOCKET_PATH,
    SHARED_INFERENCE_STARTUP_TIMEOUT_SECONDS,
)
from app.core.inference_budget import (
    read_cgroup_oom_kill_count,
    read_process_memory_metrics,
)
from app.core.gpu_placement import (
    GpuAssignment,
    GpuPlacementBroker,
    GpuPlacementError,
)


_AUTHKEY = b"video-ba-pipe-local-inference-v1"


class SharedInferenceError(RuntimeError):
    pass


class SharedInferenceOverloaded(SharedInferenceError):
    pass


def _selected_backend_name(
    model_path: str,
    model_info: Dict[str, Any],
    config: Dict[str, Any],
) -> str:
    aliases = {
        "onnx": "onnxruntime",
        "onnxruntime": "onnxruntime",
        "rknn": "rknn",
        "rknnlite": "rknn",
        "rknn_ocr": "rknn_ocr",
        "ultralytics": "ultralytics",
        "paddleocr": "paddleocr",
        "ocr": "paddleocr",
    }
    requested = str(config.get("backend") or "auto").strip().lower()
    if requested in aliases:
        return aliases[requested]
    framework = str(model_info.get("framework") or "").lower()
    model_type = str(model_info.get("model_type") or "").lower()
    extension = os.path.splitext(model_path)[1].lower()
    recognition_path = str(model_info.get("recognition_model_path") or config.get("recognition_model_path") or "")
    is_ocr = framework in {"paddleocr", "paddle"} or model_type == "ocr"
    is_rknn = (
        extension == ".rknn"
        or "rknn" in framework
        or recognition_path.lower().endswith(".rknn")
    )
    if is_ocr:
        return "rknn_ocr" if is_rknn else "paddleocr"
    if is_rknn:
        return "rknn"
    if extension == ".onnx" or framework == "onnx":
        return "onnxruntime"
    return "ultralytics"


def build_model_spec(model_path: str, model_info: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    try:
        stat_result = os.stat(model_path)
        file_size = int(stat_result.st_size)
        file_mtime_ns = int(stat_result.st_mtime_ns)
    except OSError:
        file_size = int(model_info.get("file_size") or 0)
        file_mtime_ns = 0
    model_id = model_info.get("id")
    if model_id is None:
        model_id = config.get("model_id")
    static_config_keys = (
        "rknn_core_mask",
        "rknn_input_format",
        "postprocess_profile",
        "postprocess_layout",
        "postprocess_bbox_format",
        "postprocess_score_mode",
        "postprocess_apply_sigmoid",
        "postprocess_strides",
        "postprocess_anchors",
        "postprocess_anchor_count",
        "postprocess_reg_max",
        "postprocess_num_classes",
        "model_postprocess",
    )
    return {
        "model_id": int(model_id) if str(model_id).isdigit() else model_id,
        "model_path": os.path.abspath(model_path),
        "file_size": file_size,
        "file_mtime_ns": file_mtime_ns,
        "framework": str(model_info.get("framework") or "ultralytics"),
        "model_type": str(model_info.get("model_type") or "YOLO"),
        "backend": _selected_backend_name(model_path, model_info, config),
        "classes": {
            str(key): str(value)
            for key, value in (model_info.get("classes") or {}).items()
        },
        "model_postprocess": model_info.get("model_postprocess") or {},
        "input_shape": model_info.get("input_shape"),
        "input_width": int(config.get("input_width") or 640),
        "input_height": int(config.get("input_height") or 640),
        "backend_config": {
            key: config.get(key)
            for key in static_config_keys
            if config.get(key) not in (None, "")
        },
    }


def build_face_model_spec(bundle, requested_backend: str = 'auto') -> Dict[str, Any]:
    """Build a stable shared-worker key for a logical face model bundle."""
    from app.core.face_inference import select_bundle_artifacts

    selected_backend, artifacts, _capabilities = select_bundle_artifacts(
        bundle, requested_backend
    )
    file_size = sum(int(item.file_size or 0) for item in artifacts.values())
    artifact_metadata = [dict(item.metadata or {}) for item in artifacts.values()]
    requires_cuda = selected_backend in {'onnxruntime-cuda', 'tensorrt'}
    if selected_backend == 'torchscript':
        # TorchScript's default "auto" device chooses CUDA when this runtime
        # is selected on a CUDA-capable node. Only an explicit CPU declaration
        # on every artifact opts the pipeline out of GPU placement.
        requires_cuda = any(
            str(metadata.get('device') or 'auto').strip().lower() != 'cpu'
            for metadata in artifact_metadata
        )
    requires_cuda = requires_cuda or any(
        metadata.get('requires_cuda') is True for metadata in artifact_metadata
    )
    return {
        'model_id': f'face-bundle:{bundle.id}',
        'bundle_id': int(bundle.id),
        'bundle_version': bundle.version,
        'contract_id': bundle.contract_id,
        'backend': 'face_pipeline',
        'face_runtime': selected_backend,
        'requires_cuda': requires_cuda,
        'model_path': artifacts['detection'].file_path,
        'recognition_model_path': artifacts['embedding'].file_path,
        'file_size': file_size,
        'artifact_sha256': {
            role: artifact.artifact_sha256 for role, artifact in artifacts.items()
        },
        'framework': 'face_pipeline',
        'model_type': 'FACE',
        'input_width': int(artifacts['detection'].metadata.get('input_width') or 320),
        'input_height': int(artifacts['detection'].metadata.get('input_height') or 320),
        'backend_config': {},
    }


def build_reid_model_spec(bundle, requested_backend: str = 'auto') -> Dict[str, Any]:
    """Build a stable worker key for a logical ReID embedding bundle."""
    from app.core.reid_inference import select_reid_artifact

    selected_backend, artifact, _capabilities = select_reid_artifact(
        bundle, requested_backend
    )
    metadata = dict(artifact.metadata or {})
    requires_cuda = selected_backend in {'onnxruntime-cuda', 'tensorrt'}
    if selected_backend == 'torchscript':
        declared_device = str(
            artifact.device or metadata.get('device') or 'auto'
        ).strip().lower()
        requires_cuda = declared_device != 'cpu'
    return {
        'model_id': f'reid-bundle:{bundle.id}',
        'bundle_id': int(bundle.id),
        'bundle_version': bundle.version,
        'contract_id': bundle.contract_id,
        'backend': 'reid_embedding',
        'reid_runtime': selected_backend,
        'requires_cuda': requires_cuda or metadata.get('requires_cuda') is True,
        'model_path': artifact.file_path,
        'file_size': int(artifact.file_size or 0),
        'artifact_sha256': artifact.artifact_sha256,
        'framework': 'reid_embedding',
        'model_type': 'REID',
        'input_width': int(metadata.get('input_width') or 128),
        'input_height': int(metadata.get('input_height') or 256),
        'backend_config': {},
    }


def model_key(spec: Dict[str, Any]) -> str:
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _inference_config(config: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "confidence": float(config.get("confidence", 0.6)),
        "nms_iou": float(config.get("nms_iou", 0.7)),
        "class_filter": list(config.get("class_filter") or []),
        "label_name": config.get("label_name"),
        "input_width": int(config.get("input_width") or 640),
        "input_height": int(config.get("input_height") or 640),
    }
    for key in (
        "backend",
        "face_detection_confidence",
        "face_nms_iou",
        "min_face_size",
        "rknn_core_mask",
        "rknn_input_format",
        "postprocess_profile",
        "postprocess_layout",
        "postprocess_bbox_format",
        "postprocess_score_mode",
        "postprocess_apply_sigmoid",
        "postprocess_strides",
        "postprocess_anchors",
        "postprocess_anchor_count",
        "postprocess_reg_max",
        "postprocess_num_classes",
        "model_postprocess",
    ):
        if config.get(key) not in (None, ""):
            result[key] = config.get(key)
    return result


def _worker_device(backend) -> Optional[str]:
    device = getattr(backend, "device", None)
    if device is not None:
        return str(device)
    predictor = getattr(getattr(backend, "model", None), "predictor", None)
    device = getattr(predictor, "device", None)
    return str(device) if device is not None else None


def _is_cuda_oom(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return (
        "outofmemory" in name
        or (
            "resourceexhausted" in name
            and "out of memory" in message
        )
        or "cuda out of memory" in message
        or "cuda error: out of memory" in message
        or "out of memory error on gpu" in message
        or "cudnn_status_alloc_failed" in message
    )


def _create_model_worker_backend(
    spec: Dict[str, Any],
    model_info: Dict[str, Any],
    base_config: Dict[str, Any],
):
    backend_name = spec.get("backend") or _selected_backend_name(
        spec["model_path"], model_info, base_config
    )
    if backend_name == "face_pipeline":
        from app.core.database_models import FaceModelBundle
        from app.core.face_inference import FaceWorkerBackend

        bundle = FaceModelBundle.get_by_id(int(spec["bundle_id"]))
        return FaceWorkerBackend(
            bundle,
            backend=spec.get("face_runtime") or "auto",
            config=base_config,
        )
    if backend_name == "reid_embedding":
        from app.core.database_models import ReIdModelBundle
        from app.core.reid_inference import ReIdWorkerBackend

        bundle = ReIdModelBundle.get_by_id(int(spec["bundle_id"]))
        return ReIdWorkerBackend(
            bundle,
            backend=spec.get("reid_runtime") or "auto",
            config=base_config,
        )
    if backend_name == "paddleocr":
        from app.core.ocr_backend import PaddleOCRBackend

        return PaddleOCRBackend.from_worker_spec(spec, base_config)
    if backend_name == "rknn_ocr":
        from app.core.ocr_backend import RKNNOcrBackend

        return RKNNOcrBackend.from_worker_spec(spec, base_config)
    from app.user_scripts.common.yolo_backends import RKNNBackend, UltralyticsBackend

    if backend_name == "rknn":
        return RKNNBackend(spec["model_path"], model_info, base_config)
    if backend_name == "ultralytics":
        return UltralyticsBackend(spec["model_path"], model_info, base_config)
    raise SharedInferenceError(f"共享推理暂不支持后端: {backend_name}")


def _untrack_attached_shared_memory(segment: shared_memory.SharedMemory) -> None:
    # Attaching processes must not unlink a segment owned by the source client.
    name = segment.name if os.name == "nt" else f"/{segment.name}"
    try:
        resource_tracker.unregister(name, "shared_memory")
    except Exception:
        pass


def _model_worker_main(
    spec: Dict[str, Any],
    base_config: Dict[str, Any],
    request_queue,
    result_queue,
    gpu_assignment: Optional[Dict[str, Any]] = None,
) -> None:
    if gpu_assignment:
        # This must happen before importing Ultralytics/Paddle/PyTorch.  The
        # assigned physical GPU then becomes the worker's private cuda:0.
        os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_assignment["gpu_uuid"])
    os.environ["SHARED_INFERENCE_WORKER"] = "true"
    startup_started_at = time.monotonic()
    try:
        model_info = {
            "id": spec.get("model_id"),
            "path": spec["model_path"],
            "file_size": spec.get("file_size"),
            "framework": spec.get("framework"),
            "model_type": spec.get("model_type"),
            "input_shape": spec.get("input_shape") or (
                f"{spec.get('input_width', 640)}x{spec.get('input_height', 640)}"
            ),
            "classes": spec.get("classes") or {},
            "model_postprocess": spec.get("model_postprocess") or {},
        }
        worker_config = {
            **(spec.get("backend_config") or {}),
            **base_config,
            "backend": spec.get("backend") or base_config.get("backend"),
        }
        backend = _create_model_worker_backend(spec, model_info, worker_config)

        # YOLO(model_path) does not necessarily initialize CUDA or move all
        # weights to the target device.  Complete one warm-up before announcing
        # readiness so the first real frame is governed only by the steady-state
        # inference timeout.
        warmup_height = max(1, int(spec.get("input_height") or 640))
        warmup_width = max(1, int(spec.get("input_width") or 640))
        warmup = getattr(backend, "warmup", None)
        if callable(warmup):
            warmup()
        else:
            warmup_frame = np.zeros((warmup_height, warmup_width, 3), dtype=np.uint8)
            backend.infer(warmup_frame)
        result_queue.put({
            "kind": "worker_ready",
            "key": model_key(spec),
            "pid": os.getpid(),
            "startup_time_ms": (time.monotonic() - startup_started_at) * 1000.0,
            "device": _worker_device(backend),
            "backend": backend.name,
            "gpu_index": gpu_assignment.get("gpu_index") if gpu_assignment else None,
            "gpu_uuid": gpu_assignment.get("gpu_uuid") if gpu_assignment else None,
        })
    except Exception as exc:
        result_queue.put({
            "kind": "worker_start_failed",
            "key": model_key(spec),
            "pid": os.getpid(),
            "error": f"{type(exc).__name__}: {exc}",
            "failure_kind": "cuda_oom" if _is_cuda_oom(exc) else "startup_error",
            "gpu_index": gpu_assignment.get("gpu_index") if gpu_assignment else None,
            "gpu_uuid": gpu_assignment.get("gpu_uuid") if gpu_assignment else None,
            "traceback": traceback.format_exc(),
        })
        return

    stop_after_batch = False
    while not stop_after_batch:
        first_request = request_queue.get()
        if first_request is None:
            break
        requests = [first_request]
        batch_deadline = time.monotonic() + SHARED_INFERENCE_BATCH_WAIT_MS / 1000.0
        while len(requests) < SHARED_INFERENCE_BATCH_MAX_SIZE:
            remaining = batch_deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                next_request = request_queue.get(timeout=remaining)
            except queue.Empty:
                break
            if next_request is None:
                stop_after_batch = True
                break
            requests.append(next_request)

        prepared = []
        for request in requests:
            segment = None
            try:
                segment = shared_memory.SharedMemory(name=request["shm_name"], create=False)
                _untrack_attached_shared_memory(segment)
                frame_view = np.ndarray(
                    tuple(request["shape"]),
                    dtype=np.dtype(request["dtype"]),
                    buffer=segment.buf,
                )
                prepared.append((request, np.array(frame_view, copy=True)))
            except Exception as exc:
                result_queue.put({
                    "kind": "result",
                    "request_id": request["request_id"],
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                })
            finally:
                if segment is not None:
                    segment.close()

        # Requests with different NMS parameters cannot share one Ultralytics
        # predict call. Group compatible requests, while still draining one
        # bounded queue batch to avoid head-of-line memory growth.
        groups: Dict[str, list] = {}
        for request, frame in prepared:
            config = request["config"]
            signature = json.dumps(
                {
                    "confidence": config.get("confidence"),
                    "nms_iou": config.get("nms_iou"),
                    "class_filter": config.get("class_filter") or [],
                },
                sort_keys=True,
            )
            groups.setdefault(signature, []).append((request, frame))

        fatal_worker_error = False
        for group in groups.values():
            try:
                configs = [item[0]["config"] for item in group]
                frames = [item[1] for item in group]
                infer_batch = getattr(backend, "infer_batch", None)
                if len(group) > 1 and callable(infer_batch):
                    batch_results = backend.infer_batch(frames, configs)
                    effective_batch_size = len(group)
                else:
                    batch_results = []
                    for frame, config in zip(frames, configs):
                        backend.config = config
                        if getattr(backend, "output_adapter", None) is not None:
                            backend.output_adapter.config = config
                        batch_results.append(backend.infer(frame))
                    effective_batch_size = 1
                for (request, _frame), result in zip(group, batch_results):
                    detections, details, metadata = result
                    result_queue.put({
                        "kind": "result",
                        "request_id": request["request_id"],
                        "ok": True,
                        "detections": detections,
                        "details": details,
                        "metadata": {
                            **metadata,
                            "batch_size": effective_batch_size,
                            "shared_backend": backend.name,
                            "gpu_index": (
                                gpu_assignment.get("gpu_index")
                                if gpu_assignment else None
                            ),
                            "gpu_uuid": (
                                gpu_assignment.get("gpu_uuid")
                                if gpu_assignment else None
                            ),
                        },
                    })
            except Exception as exc:
                cuda_oom = _is_cuda_oom(exc)
                for request, _frame in group:
                    result_queue.put({
                        "kind": "result",
                        "request_id": request["request_id"],
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "failure_kind": "cuda_oom" if cuda_oom else "inference_error",
                    })
                if cuda_oom:
                    result_queue.put({
                        "kind": "worker_runtime_failed",
                        "key": model_key(spec),
                        "pid": os.getpid(),
                        "error": f"{type(exc).__name__}: {exc}",
                        "failure_kind": "cuda_oom",
                        "gpu_index": (
                            gpu_assignment.get("gpu_index")
                            if gpu_assignment else None
                        ),
                        "gpu_uuid": (
                            gpu_assignment.get("gpu_uuid")
                            if gpu_assignment else None
                        ),
                    })
                    fatal_worker_error = True
                    stop_after_batch = True
                    break
        if fatal_worker_error:
            break

    try:
        backend.cleanup()
    except Exception:
        pass


@dataclass
class _PendingResult:
    event: threading.Event
    response: Optional[Dict[str, Any]] = None
    # 该请求所属的 model key;用于 worker 启动失败时把对应 pending 请求立即置败,
    # 而不是让首个请求傻等满超时被误报成 inference_timeout。
    key: str = ""


@dataclass
class _ModelSlot:
    key: str
    spec: Dict[str, Any]
    process: Any
    request_queue: Any
    base_config: Dict[str, Any]
    references: int = 0
    idle_since: Optional[float] = None
    ready: bool = False
    ready_event: threading.Event = field(default_factory=threading.Event)
    start_error: Optional[str] = None
    start_failure_kind: Optional[str] = None
    startup_time_ms: Optional[float] = None
    device: Optional[str] = None
    backend: Optional[str] = None
    gpu_assignment: Optional[GpuAssignment] = None
    attempted_gpu_uuids: set = field(default_factory=set)
    gpu_retry_count: int = 0
    oom_kill_count_at_start: int = 0
    oom_failures: int = 0
    last_oom_at: Optional[float] = None
    oom_retry_at: float = 0.0
    dead_exit_handled: bool = False


class _ModelRegistry:
    def __init__(
        self,
        queue_size: int,
        idle_seconds: float,
        oom_circuit_enabled: bool = OOM_CIRCUIT_BREAKER_ENABLED,
        oom_failure_threshold: int = OOM_CIRCUIT_FAILURE_THRESHOLD,
        oom_open_seconds: float = OOM_CIRCUIT_OPEN_SECONDS,
        oom_stable_reset_seconds: float = OOM_CIRCUIT_STABLE_RESET_SECONDS,
        oom_backoff_cap_seconds: float = OOM_RESTART_BACKOFF_MAX_SECONDS,
        gpu_scheduling_enabled: bool = False,
        gpu_allowed_devices=(),
        gpu_memory_reserve_mb: int = GPU_MEMORY_RESERVE_MB,
        gpu_new_model_default_mb: int = GPU_NEW_MODEL_DEFAULT_MB,
        gpu_model_memory_margin_percent: float = GPU_MODEL_MEMORY_MARGIN_PERCENT,
        gpu_oom_cooldown_seconds: float = GPU_OOM_COOLDOWN_SECONDS,
        gpu_nvml_stale_seconds: float = GPU_NVML_STALE_SECONDS,
        gpu_failure_mode: str = GPU_SCHEDULING_FAILURE_MODE,
        gpu_broker=None,
        worker_target=_model_worker_main,
    ):
        self.context = multiprocessing.get_context("spawn")
        self.queue_size = max(1, int(queue_size))
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.oom_circuit_enabled = bool(oom_circuit_enabled)
        self.oom_failure_threshold = max(1, int(oom_failure_threshold))
        self.oom_open_seconds = max(1.0, float(oom_open_seconds))
        self.oom_stable_reset_seconds = max(
            1.0, float(oom_stable_reset_seconds)
        )
        self.oom_backoff_cap_seconds = max(
            1.0, float(oom_backoff_cap_seconds)
        )
        self.result_queue = self.context.Queue()
        self.worker_target = worker_target
        self.gpu_broker = gpu_broker or GpuPlacementBroker(
            enabled=gpu_scheduling_enabled,
            allowed_devices=gpu_allowed_devices,
            reserve_mb=gpu_memory_reserve_mb,
            default_model_mb=gpu_new_model_default_mb,
            margin_percent=gpu_model_memory_margin_percent,
            oom_cooldown_seconds=gpu_oom_cooldown_seconds,
            stale_seconds=gpu_nvml_stale_seconds,
            failure_mode=gpu_failure_mode,
        )
        self.slots: Dict[str, _ModelSlot] = {}
        self.pending: Dict[str, _PendingResult] = {}
        self.lock = threading.RLock()
        self.running = True
        self.dispatcher = threading.Thread(target=self._dispatch_results, daemon=True)
        self.reaper = threading.Thread(target=self._reap_idle, daemon=True)
        self.dispatcher.start()
        self.reaper.start()

    def configure_oom_policy(self, policy: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            self.oom_circuit_enabled = bool(policy.get(
                "enabled", self.oom_circuit_enabled
            ))
            self.oom_failure_threshold = max(
                1,
                int(policy.get("failure_threshold", self.oom_failure_threshold)),
            )
            self.oom_open_seconds = max(
                1.0, float(policy.get("open_seconds", self.oom_open_seconds))
            )
            self.oom_stable_reset_seconds = max(
                1.0,
                float(policy.get(
                    "stable_reset_seconds", self.oom_stable_reset_seconds
                )),
            )
            self.oom_backoff_cap_seconds = max(
                1.0,
                float(policy.get(
                    "backoff_cap_seconds", self.oom_backoff_cap_seconds
                )),
            )
            if not self.oom_circuit_enabled:
                for slot in self.slots.values():
                    slot.oom_retry_at = 0.0
            return {
                "ok": True,
                "oom_policy": self.oom_policy(),
            }

    def oom_policy(self) -> Dict[str, Any]:
        return {
            "enabled": self.oom_circuit_enabled,
            "failure_threshold": self.oom_failure_threshold,
            "open_seconds": self.oom_open_seconds,
            "stable_reset_seconds": self.oom_stable_reset_seconds,
            "backoff_cap_seconds": self.oom_backoff_cap_seconds,
        }

    def _start_model_process(
        self,
        spec: Dict[str, Any],
        config: Dict[str, Any],
        request_queue,
        assignment: Optional[GpuAssignment],
    ):
        key = model_key(spec)
        process = self.context.Process(
            target=self.worker_target,
            args=(
                spec,
                config,
                request_queue,
                self.result_queue,
                assignment.worker_payload() if assignment is not None else None,
            ),
            name=f"shared-model-{key[:8]}",
            daemon=True,
        )
        process.start()
        return process

    def _new_slot(
        self,
        spec: Dict[str, Any],
        config: Dict[str, Any],
        *,
        exclude_gpu_uuids=(),
    ) -> _ModelSlot:
        key = model_key(spec)
        assignment = self.gpu_broker.reserve(
            key,
            spec,
            exclude_gpu_uuids=exclude_gpu_uuids,
        )
        request_queue = self.context.Queue(maxsize=self.queue_size)
        try:
            process = self._start_model_process(
                spec, config, request_queue, assignment
            )
        except Exception:
            self.gpu_broker.release(key)
            raise
        slot = _ModelSlot(
            key=key,
            spec=dict(spec),
            process=process,
            request_queue=request_queue,
            base_config=dict(config),
            gpu_assignment=assignment,
            attempted_gpu_uuids=(
                {assignment.gpu_uuid} if assignment is not None else set()
            ),
            oom_kill_count_at_start=read_cgroup_oom_kill_count(),
        )
        self.slots[key] = slot
        if assignment is not None:
            print(
                "[SharedInference] 共享模型已分配 GPU: "
                f"model_key={key}, model_id={spec.get('model_id')}, "
                f"gpu_index={assignment.gpu_index}, gpu_uuid={assignment.gpu_uuid}, "
                f"reserved_gpu_mb={assignment.reserved_mb:.1f}",
                file=sys.stderr,
                flush=True,
            )
        return slot

    def acquire(self, spec: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        key = model_key(spec)
        with self.lock:
            slot = self.slots.get(key)
            if slot is None:
                try:
                    slot = self._new_slot(spec, config)
                except GpuPlacementError as exc:
                    return exc.to_response()
            elif slot.start_error and slot.start_failure_kind != "cuda_oom":
                # Preserve the actual import/model-load failure. Restarting the
                # dead process first would discard start_error and turn every
                # following frame into another opaque inference_timeout.
                return {"ok": False, "error": slot.start_error}
            elif not slot.process.is_alive():
                retry_response = self._observe_dead_slot(slot)
                if retry_response is not None:
                    return retry_response
                if not slot.ready and slot.oom_failures == 0:
                    return {
                        "ok": False,
                        "error": (
                            "model_worker_start_exited:"
                            f"exitcode={slot.process.exitcode}"
                        ),
                    }
                try:
                    slot = self._restart_dead_slot(slot)
                except GpuPlacementError as exc:
                    return exc.to_response()
            slot.references += 1
            slot.idle_since = None
            return {
                "ok": True,
                "model_key": key,
                "pid": slot.process.pid,
                "gpu_index": (
                    slot.gpu_assignment.gpu_index
                    if slot.gpu_assignment is not None else None
                ),
                "gpu_uuid": (
                    slot.gpu_assignment.gpu_uuid
                    if slot.gpu_assignment is not None else None
                ),
            }

    def release(self, key: str) -> Dict[str, Any]:
        with self.lock:
            slot = self.slots.get(key)
            if slot is None:
                return {"ok": True, "released": False}
            slot.references = max(0, slot.references - 1)
            if slot.references == 0:
                slot.idle_since = time.monotonic()
            return {"ok": True, "released": True, "references": slot.references}

    def submit(
        self,
        key: str,
        request: Dict[str, Any],
        timeout: float,
        startup_timeout: float = SHARED_INFERENCE_STARTUP_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        request_id = request["request_id"]
        pending = _PendingResult(threading.Event())
        pending.key = key

        # A slot is published immediately after Process.start(), while importing
        # PyTorch, loading weights and CUDA warm-up happen inside that process.
        # Do not charge that cold-start time to the per-frame inference timeout.
        with self.lock:
            slot = self.slots.get(key)
            if slot is None:
                return {"ok": False, "error": "model_worker_unavailable"}
            if slot.start_error and slot.start_failure_kind != "cuda_oom":
                return {"ok": False, "error": slot.start_error}
            if not slot.process.is_alive():
                retry_response = self._observe_dead_slot(slot)
                if retry_response is not None:
                    return retry_response
                if not slot.ready and slot.oom_failures == 0:
                    return {
                        "ok": False,
                        "error": (
                            "model_worker_start_exited:"
                            f"exitcode={slot.process.exitcode}"
                        ),
                    }
                try:
                    slot = self._restart_dead_slot(slot)
                except GpuPlacementError as exc:
                    return exc.to_response()
            ready_event = slot.ready_event

        if not ready_event.wait(timeout=max(0.1, float(startup_timeout))):
            with self.lock:
                current_slot = self.slots.get(key)
                if current_slot is not slot:
                    return {"ok": False, "error": "model_worker_restarted"}
                if slot.start_error:
                    return {"ok": False, "error": slot.start_error}
                if not slot.process.is_alive():
                    return {"ok": False, "error": "model_worker_unavailable"}
            return {"ok": False, "error": "model_worker_start_timeout"}

        with self.lock:
            current_slot = self.slots.get(key)
            if current_slot is not slot:
                return {"ok": False, "error": "model_worker_restarted"}
            if slot.start_error:
                return {"ok": False, "error": slot.start_error}
            if not slot.ready:
                return {"ok": False, "error": "model_worker_unavailable"}
            self.pending[request_id] = pending
            try:
                slot.request_queue.put_nowait(request)
            except queue.Full:
                self.pending.pop(request_id, None)
                return {"ok": False, "overloaded": True, "error": "model_queue_full"}

        if not pending.event.wait(timeout=max(0.1, float(timeout))):
            with self.lock:
                self.pending.pop(request_id, None)
            return {"ok": False, "error": "inference_timeout"}
        return pending.response or {"ok": False, "error": "missing_inference_result"}

    def _observe_dead_slot(self, slot: _ModelSlot) -> Optional[Dict[str, Any]]:
        """Record a dead worker once and enforce its OOM retry deadline."""
        now = time.monotonic()
        current_oom_count = read_cgroup_oom_kill_count()
        if not slot.dead_exit_handled:
            slot.dead_exit_handled = True
            if (
                slot.process.exitcode == -signal.SIGKILL
                and current_oom_count > slot.oom_kill_count_at_start
            ):
                if (
                    slot.last_oom_at is not None
                    and now - slot.last_oom_at >= self.oom_stable_reset_seconds
                ):
                    slot.oom_failures = 0
                slot.oom_failures += 1
                slot.last_oom_at = now
                if self.oom_circuit_enabled:
                    delay = min(
                        15.0 * (2 ** (slot.oom_failures - 1)),
                        self.oom_backoff_cap_seconds,
                    )
                    if slot.oom_failures >= self.oom_failure_threshold:
                        delay = max(delay, self.oom_open_seconds)
                    slot.oom_retry_at = now + delay
                else:
                    slot.oom_retry_at = 0.0
        if now < slot.oom_retry_at:
            return {
                "ok": False,
                "error": "model_worker_oom_backoff",
                "retry_in_seconds": round(slot.oom_retry_at - now, 1),
                "oom_failures": slot.oom_failures,
            }
        return None

    def _restart_dead_slot(self, slot: _ModelSlot) -> _ModelSlot:
        """Replace a dead process without discarding its references/OOM history."""
        references = slot.references
        idle_since = slot.idle_since
        oom_failures = slot.oom_failures
        last_oom_at = slot.last_oom_at
        gpu_retry_count = slot.gpu_retry_count
        spec = slot.spec
        base_config = slot.base_config
        self._stop_slot(slot)
        replacement = self._new_slot(spec, base_config)
        replacement.references = references
        replacement.idle_since = idle_since
        replacement.oom_failures = oom_failures
        replacement.last_oom_at = last_oom_at
        replacement.gpu_retry_count = gpu_retry_count
        return replacement

    def _fail_pending_for_key(self, key: str, error: str) -> None:
        for request_id, pending in list(self.pending.items()):
            if pending.key != key:
                continue
            pending.response = {"ok": False, "error": error}
            pending.event.set()
            self.pending.pop(request_id, None)

    @staticmethod
    def _stop_process_only(slot: _ModelSlot) -> None:
        try:
            slot.request_queue.put_nowait(None)
        except Exception:
            pass
        slot.process.join(timeout=1)
        if slot.process.is_alive():
            slot.process.terminate()
            slot.process.join(timeout=1)

    def _retry_on_other_gpu(self, slot: _ModelSlot) -> bool:
        assignment = slot.gpu_assignment
        if assignment is None or slot.gpu_retry_count >= 1:
            return False

        failed_uuid = assignment.gpu_uuid
        slot.attempted_gpu_uuids.add(failed_uuid)
        self.gpu_broker.fail(slot.key, cooldown=True)
        self._stop_process_only(slot)
        try:
            replacement_assignment = self.gpu_broker.reserve(
                slot.key,
                slot.spec,
                exclude_gpu_uuids=slot.attempted_gpu_uuids,
            )
        except GpuPlacementError:
            return False
        if replacement_assignment is None:
            return False

        request_queue = self.context.Queue(maxsize=self.queue_size)
        try:
            process = self._start_model_process(
                slot.spec,
                slot.base_config,
                request_queue,
                replacement_assignment,
            )
        except Exception:
            self.gpu_broker.release(slot.key)
            return False

        slot.process = process
        slot.request_queue = request_queue
        slot.gpu_assignment = replacement_assignment
        slot.attempted_gpu_uuids.add(replacement_assignment.gpu_uuid)
        slot.gpu_retry_count += 1
        slot.ready = False
        slot.ready_event.clear()
        slot.start_error = None
        slot.start_failure_kind = None
        slot.startup_time_ms = None
        slot.device = None
        slot.backend = None
        slot.dead_exit_handled = False
        slot.oom_kill_count_at_start = read_cgroup_oom_kill_count()
        return True

    def _dispatch_results(self) -> None:
        while self.running:
            try:
                response = self.result_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            kind = response.get("kind")
            if kind in ("worker_ready", "worker_start_failed", "worker_runtime_failed"):
                with self.lock:
                    key = response.get("key")
                    slot = self.slots.get(key)
                    if slot is None:
                        continue
                    response_pid = response.get("pid")
                    if response_pid is not None and int(response_pid) != slot.process.pid:
                        continue

                    if kind == "worker_ready":
                        slot.ready = True
                        slot.start_error = None
                        slot.start_failure_kind = None
                        slot.startup_time_ms = response.get("startup_time_ms")
                        slot.device = response.get("device")
                        slot.backend = response.get("backend") or slot.spec.get("backend")
                        if slot.gpu_assignment is not None:
                            try:
                                self.gpu_broker.mark_ready(key, slot.process.pid)
                            except Exception as exc:
                                print(
                                    "[SharedInference] 无法读取模型进程显存: "
                                    f"model_key={key}, error={exc}",
                                    file=sys.stderr,
                                    flush=True,
                                )
                        # A successful warm-up begins a fresh one-retry allowance
                        # for any future runtime CUDA OOM incident.
                        slot.gpu_retry_count = 0
                        slot.oom_retry_at = 0.0
                        slot.attempted_gpu_uuids = (
                            {slot.gpu_assignment.gpu_uuid}
                            if slot.gpu_assignment is not None else set()
                        )
                        slot.ready_event.set()
                        continue

                    error = response.get("error") or "model_worker_failed"
                    cuda_oom = response.get("failure_kind") == "cuda_oom"
                    slot.ready = False
                    slot.oom_failures += 1 if cuda_oom else 0
                    slot.last_oom_at = time.monotonic() if cuda_oom else slot.last_oom_at
                    self._fail_pending_for_key(key, error)

                    if cuda_oom and self._retry_on_other_gpu(slot):
                        print(
                            "[SharedInference] CUDA OOM，模型切换到其他 GPU 重试: "
                            f"model_key={key}, pid={slot.process.pid}, "
                            f"gpu_uuid={slot.gpu_assignment.gpu_uuid}",
                            file=sys.stderr,
                            flush=True,
                        )
                        continue

                    if slot.gpu_assignment is not None:
                        self.gpu_broker.fail(key, cooldown=cuda_oom)
                    if cuda_oom:
                        # The failed worker cannot safely continue after a CUDA
                        # allocator failure.  Keep the error for the in-flight
                        # caller, but mark it retryable so future calls can
                        # restart after backoff/capacity/NVML recovery.
                        self._stop_process_only(slot)
                        if self.oom_circuit_enabled:
                            delay = min(
                                15.0 * (2 ** max(0, slot.oom_failures - 1)),
                                self.oom_backoff_cap_seconds,
                            )
                            if slot.oom_failures >= self.oom_failure_threshold:
                                delay = max(delay, self.oom_open_seconds)
                            slot.oom_retry_at = time.monotonic() + delay
                        else:
                            slot.oom_retry_at = 0.0
                    slot.start_error = error
                    slot.start_failure_kind = response.get("failure_kind")
                    slot.ready_event.set()

                    if kind == "worker_start_failed":
                        # worker 起不来时,已入队但永远不会被处理的请求必须立即置败,
                        # 否则首个请求会傻等满超时被误报成 inference_timeout,
                        # 而真实的启动错误(如 ultralytics/torch import 失败)被吞掉。
                        failure_traceback = response.get("traceback")
                        print(
                            "[SharedInference] 模型子进程启动失败: "
                            f"model_key={key}, error={error}",
                            file=sys.stderr,
                            flush=True,
                        )
                        if failure_traceback:
                            print(failure_traceback, file=sys.stderr, flush=True)
                continue
            request_id = response.get("request_id")
            with self.lock:
                pending = self.pending.pop(request_id, None)
            if pending is not None:
                pending.response = response
                pending.event.set()

    def _idle_slots_to_reap(self, now: float) -> list[_ModelSlot]:
        idle_slots = [
            slot for slot in self.slots.values()
            if slot.references == 0 and slot.idle_since is not None
        ]
        selected = {
            slot.key: slot
            for slot in idle_slots
            if now - slot.idle_since >= self.idle_seconds
        }

        # 空闲时间是正常热缓存策略；GPU 已跌破安全预留时则按 LRU 提前回收。
        # 每张受压 GPU 每轮只回收一个，下一秒重新读取 NVML，避免一次抖动清空缓存。
        try:
            gpu_status = self.gpu_broker.status()
        except Exception:
            gpu_status = {}
        if not gpu_status.get('metrics_stale'):
            reserve_mb = float(gpu_status.get('reserve_mb') or 0.0)
            pressured_gpu_uuids = {
                gpu.get('uuid')
                for gpu in gpu_status.get('gpus', [])
                if gpu.get('uuid')
                and float(gpu.get('free_mb') or 0.0) < reserve_mb
            }
            for gpu_uuid in pressured_gpu_uuids:
                candidates = sorted(
                    (
                        slot for slot in idle_slots
                        if slot.gpu_assignment is not None
                        and slot.gpu_assignment.gpu_uuid == gpu_uuid
                    ),
                    key=lambda slot: slot.idle_since,
                )
                if candidates:
                    selected[candidates[0].key] = candidates[0]

        return sorted(selected.values(), key=lambda slot: slot.idle_since)

    def _reap_idle(self) -> None:
        while self.running:
            time.sleep(1.0)
            now = time.monotonic()
            with self.lock:
                for slot in self._idle_slots_to_reap(now):
                    self._stop_slot(slot)
                    self.slots.pop(slot.key, None)

    def _stop_slot(self, slot: _ModelSlot) -> None:
        try:
            slot.request_queue.put_nowait(None)
        except Exception:
            pass
        slot.process.join(timeout=3)
        if slot.process.is_alive():
            slot.process.terminate()
            slot.process.join(timeout=2)
        self.gpu_broker.release(slot.key)

    def stats(self) -> Dict[str, Any]:
        with self.lock:
            models = []
            for slot in self.slots.values():
                if (
                    slot.gpu_assignment is not None
                    and slot.process.is_alive()
                    and slot.ready
                ):
                    try:
                        self.gpu_broker.refresh_usage(slot.key, slot.process.pid)
                    except Exception:
                        pass
                memory_metrics = (
                    read_process_memory_metrics(slot.process.pid)
                    if slot.process.is_alive()
                    else {}
                )
                try:
                    queue_depth = slot.request_queue.qsize()
                except (NotImplementedError, OSError):
                    queue_depth = None
                models.append({
                    "model_key": slot.key,
                    "model_id": slot.spec.get("model_id"),
                    "recognition_model_id": slot.spec.get("recognition_model_id"),
                    "model_path": slot.spec.get("model_path"),
                    "recognition_model_path": slot.spec.get("recognition_model_path"),
                    "pid": slot.process.pid,
                    "alive": slot.process.is_alive(),
                    "exitcode": slot.process.exitcode,
                    "ready": slot.ready,
                    "start_error": slot.start_error,
                    "startup_time_ms": slot.startup_time_ms,
                    "device": slot.device,
                    "gpu_index": (
                        slot.gpu_assignment.gpu_index
                        if slot.gpu_assignment is not None else None
                    ),
                    "gpu_uuid": (
                        slot.gpu_assignment.gpu_uuid
                        if slot.gpu_assignment is not None else None
                    ),
                    "gpu_name": (
                        slot.gpu_assignment.gpu_name
                        if slot.gpu_assignment is not None else None
                    ),
                    "reserved_gpu_mb": (
                        slot.gpu_assignment.reserved_mb
                        if slot.gpu_assignment is not None else None
                    ),
                    "actual_gpu_mb": (
                        slot.gpu_assignment.actual_mb
                        if slot.gpu_assignment is not None else None
                    ),
                    "gpu_retry_count": slot.gpu_retry_count,
                    "backend": slot.backend or slot.spec.get("backend"),
                    "references": slot.references,
                    "queue_depth": queue_depth,
                    **memory_metrics,
                    "oom_failures": slot.oom_failures,
                    "oom_retry_in_seconds": max(
                        0.0, round(slot.oom_retry_at - time.monotonic(), 1)
                    ),
                })
            gpu_status = self.gpu_broker.status()
            return {
                "ok": True,
                "models": models,
                "model_count": len(models),
                "oom_policy": self.oom_policy(),
                "gpu_scheduler": {
                    key: value for key, value in gpu_status.items() if key != "gpus"
                },
                "gpus": gpu_status.get("gpus", []),
            }

    def close(self) -> None:
        self.running = False
        with self.lock:
            for slot in list(self.slots.values()):
                self._stop_slot(slot)
            self.slots.clear()
            for pending in self.pending.values():
                pending.response = {"ok": False, "error": "service_stopping"}
                pending.event.set()
            self.pending.clear()
        self.gpu_broker.close()


class SharedInferenceServer:
    def __init__(self, socket_path: str = SHARED_INFERENCE_SOCKET_PATH):
        self.socket_path = socket_path
        self.running = True
        self.registry = _ModelRegistry(
            queue_size=SHARED_INFERENCE_QUEUE_SIZE,
            idle_seconds=SHARED_INFERENCE_IDLE_SECONDS,
            oom_circuit_enabled=OOM_CIRCUIT_BREAKER_ENABLED,
            oom_failure_threshold=OOM_CIRCUIT_FAILURE_THRESHOLD,
            oom_open_seconds=OOM_CIRCUIT_OPEN_SECONDS,
            oom_stable_reset_seconds=OOM_CIRCUIT_STABLE_RESET_SECONDS,
            oom_backoff_cap_seconds=OOM_RESTART_BACKOFF_MAX_SECONDS,
            gpu_scheduling_enabled=GPU_SCHEDULING_ENABLED,
            gpu_allowed_devices=GPU_ALLOWED_DEVICES,
            gpu_memory_reserve_mb=GPU_MEMORY_RESERVE_MB,
            gpu_new_model_default_mb=GPU_NEW_MODEL_DEFAULT_MB,
            gpu_model_memory_margin_percent=GPU_MODEL_MEMORY_MARGIN_PERCENT,
            gpu_oom_cooldown_seconds=GPU_OOM_COOLDOWN_SECONDS,
            gpu_nvml_stale_seconds=GPU_NVML_STALE_SECONDS,
            gpu_failure_mode=GPU_SCHEDULING_FAILURE_MODE,
        )
        self.listener = None

    def _handle_connection(self, connection) -> None:
        acquired_keys = set()
        try:
            while self.running:
                request = connection.recv()
                action = request.get("action")
                if action == "ping":
                    response = {"ok": True, "pid": os.getpid()}
                elif action == "shutdown":
                    connection.send({"ok": True})
                    threading.Thread(target=self.close, daemon=True).start()
                    return
                elif action == "stats":
                    response = self.registry.stats()
                elif action == "configure_oom":
                    response = self.registry.configure_oom_policy(
                        request.get("policy") or {}
                    )
                elif action == "acquire":
                    response = self.registry.acquire(request["spec"], request["config"])
                    if response.get("ok") and response.get("model_key"):
                        acquired_keys.add(response["model_key"])
                elif action == "release":
                    response = self.registry.release(request["model_key"])
                    acquired_keys.discard(request["model_key"])
                elif action == "infer":
                    response = self.registry.submit(
                        request["model_key"],
                        request["request"],
                        request.get("timeout", SHARED_INFERENCE_REQUEST_TIMEOUT_SECONDS),
                        request.get(
                            "startup_timeout",
                            SHARED_INFERENCE_STARTUP_TIMEOUT_SECONDS,
                        ),
                    )
                else:
                    response = {"ok": False, "error": f"unsupported_action:{action}"}
                connection.send(response)
        except (EOFError, BrokenPipeError, ConnectionResetError):
            pass
        finally:
            for key in acquired_keys:
                self.registry.release(key)
            connection.close()

    def serve_forever(self) -> None:
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        os.makedirs(os.path.dirname(self.socket_path) or ".", exist_ok=True)
        self.listener = Listener(self.socket_path, family="AF_UNIX", authkey=_AUTHKEY)
        os.chmod(self.socket_path, 0o600)
        while self.running:
            try:
                connection = self.listener.accept()
            except (OSError, EOFError):
                break
            threading.Thread(
                target=self._handle_connection,
                args=(connection,),
                daemon=True,
            ).start()

    def close(self) -> None:
        self.running = False
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass
        self.registry.close()
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass


def _client_request_config(spec: Dict[str, Any], config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if spec.get("backend") in {"paddleocr", "rknn_ocr"}:
        return {}
    if spec.get("backend") == "reid_embedding":
        source = config or {}
        return {
            key: source.get(key)
            for key in (
                "reid_boxes", "reid_min_box_height", "reid_crop_expansion",
            )
            if source.get(key) is not None
        }
    return _inference_config(config or {})


def _service_error_message(response: Dict[str, Any], fallback: str) -> str:
    code = str(response.get("error") or fallback)
    message = str(response.get("message") or "").strip()
    parts = [code]
    if message and message != code:
        parts.append(message)
    if response.get("estimated_mb") is not None:
        parts.append(f"estimated_gpu_mb={response['estimated_mb']}")
    gpu_summaries = []
    for gpu in response.get("gpus") or ():
        gpu_summaries.append(
            "gpu{index}(free={free_mb}MB,pending={pending_mb}MB,cooldown={cooldown_seconds}s)".format(
                index=gpu.get("index", "?"),
                free_mb=gpu.get("free_mb", "?"),
                pending_mb=gpu.get("pending_mb", "?"),
                cooldown_seconds=gpu.get("cooldown_seconds", 0),
            )
        )
    if gpu_summaries:
        parts.append(";".join(gpu_summaries))
    return ": ".join(parts)


class SharedInferenceClient:
    def __init__(
        self,
        model_path: str = "",
        model_info: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        *,
        spec: Optional[Dict[str, Any]] = None,
        socket_path: str = SHARED_INFERENCE_SOCKET_PATH,
        timeout: float = SHARED_INFERENCE_REQUEST_TIMEOUT_SECONDS,
        startup_timeout: float = SHARED_INFERENCE_STARTUP_TIMEOUT_SECONDS,
    ):
        self.socket_path = socket_path
        self.timeout = float(timeout)
        self.startup_timeout = float(startup_timeout)
        resolved_config = config or {}
        self.spec = spec if spec is not None else build_model_spec(
            model_path, model_info or {}, resolved_config
        )
        self.config = _client_request_config(self.spec, resolved_config)
        self.connection = None
        self.model_key = None
        self.lock = threading.Lock()
        self.closed = False
        self._connect_and_acquire()

    def _connect(self) -> None:
        self.connection = Client(self.socket_path, family="AF_UNIX", authkey=_AUTHKEY)

    def _connect_and_acquire(self) -> None:
        self._connect()
        self.connection.send({"action": "acquire", "spec": self.spec, "config": self.config})
        response = self.connection.recv()
        if not response.get("ok"):
            self.connection.close()
            self.connection = None
            raise SharedInferenceError(
                _service_error_message(response, "model_acquire_failed")
            )
        self.model_key = response["model_key"]

    def infer(self, frame: np.ndarray, config: Dict[str, Any]) -> Dict[str, Any]:
        contiguous = np.ascontiguousarray(frame)
        segment = shared_memory.SharedMemory(create=True, size=contiguous.nbytes)
        try:
            shared_array = np.ndarray(contiguous.shape, dtype=contiguous.dtype, buffer=segment.buf)
            shared_array[...] = contiguous
            request = {
                "request_id": uuid.uuid4().hex,
                "shm_name": segment.name,
                "shape": tuple(contiguous.shape),
                "dtype": contiguous.dtype.str,
                "config": _client_request_config(self.spec, config),
            }
            with self.lock:
                if self.closed:
                    raise SharedInferenceError("shared inference client is closed")
                try:
                    self.connection.send({
                        "action": "infer",
                        "model_key": self.model_key,
                        "request": request,
                        "timeout": self.timeout,
                        "startup_timeout": self.startup_timeout,
                    })
                    response = self.connection.recv()
                except (EOFError, BrokenPipeError, ConnectionResetError, OSError):
                    try:
                        self.connection.close()
                    except Exception:
                        pass
                    self._connect_and_acquire()
                    self.connection.send({
                        "action": "infer",
                        "model_key": self.model_key,
                        "request": request,
                        "timeout": self.timeout,
                        "startup_timeout": self.startup_timeout,
                    })
                    response = self.connection.recv()
            if response.get("overloaded"):
                raise SharedInferenceOverloaded(response.get("error") or "model queue full")
            if not response.get("ok"):
                raise SharedInferenceError(
                    _service_error_message(response, "shared inference failed")
                )
            return response
        finally:
            segment.close()
            try:
                segment.unlink()
            except FileNotFoundError:
                pass

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.connection is not None:
                try:
                    self.connection.send({"action": "release", "model_key": self.model_key})
                    self.connection.recv()
                except Exception:
                    pass
                self.connection.close()
                self.connection = None


def request_service_stats(socket_path: str = SHARED_INFERENCE_SOCKET_PATH) -> Dict[str, Any]:
    try:
        connection = Client(socket_path, family="AF_UNIX", authkey=_AUTHKEY)
        connection.send({"action": "stats"})
        response = connection.recv()
        connection.close()
        return response
    except (OSError, EOFError, ConnectionError):
        return {"ok": False, "models": [], "error": "service_unavailable"}


class SharedInferenceServiceController:
    """Own the router process from the orchestrator without importing CUDA there."""

    def __init__(
        self,
        socket_path: str = SHARED_INFERENCE_SOCKET_PATH,
        *,
        queue_size: int = SHARED_INFERENCE_QUEUE_SIZE,
        batch_max_size: int = SHARED_INFERENCE_BATCH_MAX_SIZE,
        batch_wait_ms: float = SHARED_INFERENCE_BATCH_WAIT_MS,
        request_timeout_seconds: float = SHARED_INFERENCE_REQUEST_TIMEOUT_SECONDS,
        idle_seconds: int = SHARED_INFERENCE_IDLE_SECONDS,
        gpu_scheduling_enabled: bool = GPU_SCHEDULING_ENABLED,
        gpu_allowed_devices=GPU_ALLOWED_DEVICES,
        gpu_memory_reserve_mb: int = GPU_MEMORY_RESERVE_MB,
        gpu_new_model_default_mb: int = GPU_NEW_MODEL_DEFAULT_MB,
        gpu_model_memory_margin_percent: float = GPU_MODEL_MEMORY_MARGIN_PERCENT,
        gpu_oom_cooldown_seconds: int = GPU_OOM_COOLDOWN_SECONDS,
        gpu_nvml_stale_seconds: int = GPU_NVML_STALE_SECONDS,
        gpu_failure_mode: str = GPU_SCHEDULING_FAILURE_MODE,
        oom_circuit_enabled: bool = OOM_CIRCUIT_BREAKER_ENABLED,
        oom_failure_threshold: int = OOM_CIRCUIT_FAILURE_THRESHOLD,
        oom_open_seconds: float = OOM_CIRCUIT_OPEN_SECONDS,
        oom_stable_reset_seconds: float = OOM_CIRCUIT_STABLE_RESET_SECONDS,
        oom_backoff_cap_seconds: float = OOM_RESTART_BACKOFF_MAX_SECONDS,
    ):
        self.socket_path = socket_path
        self.queue_size = int(queue_size)
        self.batch_max_size = int(batch_max_size)
        self.batch_wait_ms = float(batch_wait_ms)
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.idle_seconds = int(idle_seconds)
        self.gpu_scheduling_enabled = bool(gpu_scheduling_enabled)
        self.gpu_allowed_devices = tuple(gpu_allowed_devices or ())
        self.gpu_memory_reserve_mb = int(gpu_memory_reserve_mb)
        self.gpu_new_model_default_mb = int(gpu_new_model_default_mb)
        self.gpu_model_memory_margin_percent = float(
            gpu_model_memory_margin_percent
        )
        self.gpu_oom_cooldown_seconds = int(gpu_oom_cooldown_seconds)
        self.gpu_nvml_stale_seconds = int(gpu_nvml_stale_seconds)
        self.gpu_failure_mode = str(gpu_failure_mode)
        self.oom_circuit_enabled = bool(oom_circuit_enabled)
        self.oom_failure_threshold = int(oom_failure_threshold)
        self.oom_open_seconds = float(oom_open_seconds)
        self.oom_stable_reset_seconds = float(oom_stable_reset_seconds)
        self.oom_backoff_cap_seconds = float(oom_backoff_cap_seconds)
        self.process: Optional[subprocess.Popen] = None

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _service_environment(self) -> Dict[str, str]:
        environment = os.environ.copy()
        environment.update({
            "SHARED_INFERENCE_ENABLED": "true",
            "SHARED_INFERENCE_SOCKET_PATH": self.socket_path,
            "SHARED_INFERENCE_QUEUE_SIZE": str(self.queue_size),
            "SHARED_INFERENCE_BATCH_MAX_SIZE": str(self.batch_max_size),
            "SHARED_INFERENCE_BATCH_WAIT_MS": str(self.batch_wait_ms),
            "SHARED_INFERENCE_REQUEST_TIMEOUT_SECONDS": str(
                self.request_timeout_seconds
            ),
            "SHARED_INFERENCE_IDLE_SECONDS": str(self.idle_seconds),
            "GPU_SCHEDULING_ENABLED": (
                "true" if self.gpu_scheduling_enabled else "false"
            ),
            "GPU_ALLOWED_DEVICES": ",".join(self.gpu_allowed_devices),
            "GPU_MEMORY_RESERVE_MB": str(self.gpu_memory_reserve_mb),
            "GPU_NEW_MODEL_DEFAULT_MB": str(self.gpu_new_model_default_mb),
            "GPU_MODEL_MEMORY_MARGIN_PERCENT": str(
                self.gpu_model_memory_margin_percent
            ),
            "GPU_OOM_COOLDOWN_SECONDS": str(self.gpu_oom_cooldown_seconds),
            "GPU_NVML_STALE_SECONDS": str(self.gpu_nvml_stale_seconds),
            "GPU_SCHEDULING_FAILURE_MODE": self.gpu_failure_mode,
            "OOM_CIRCUIT_BREAKER_ENABLED": (
                "true" if self.oom_circuit_enabled else "false"
            ),
            "OOM_CIRCUIT_FAILURE_THRESHOLD": str(self.oom_failure_threshold),
            "OOM_CIRCUIT_OPEN_SECONDS": str(int(self.oom_open_seconds)),
            "OOM_CIRCUIT_STABLE_RESET_SECONDS": str(
                int(self.oom_stable_reset_seconds)
            ),
            "OOM_RESTART_BACKOFF_MAX_SECONDS": str(
                int(self.oom_backoff_cap_seconds)
            ),
        })
        return environment

    def update_oom_policy(
        self,
        *,
        enabled: bool,
        failure_threshold: int,
        open_seconds: float,
        stable_reset_seconds: float,
        backoff_cap_seconds: float,
    ) -> bool:
        self.oom_circuit_enabled = bool(enabled)
        self.oom_failure_threshold = int(failure_threshold)
        self.oom_open_seconds = float(open_seconds)
        self.oom_stable_reset_seconds = float(stable_reset_seconds)
        self.oom_backoff_cap_seconds = float(backoff_cap_seconds)
        if not self.is_running:
            return False
        try:
            connection = Client(
                self.socket_path, family="AF_UNIX", authkey=_AUTHKEY
            )
            connection.send({
                "action": "configure_oom",
                "policy": {
                    "enabled": self.oom_circuit_enabled,
                    "failure_threshold": self.oom_failure_threshold,
                    "open_seconds": self.oom_open_seconds,
                    "stable_reset_seconds": self.oom_stable_reset_seconds,
                    "backoff_cap_seconds": self.oom_backoff_cap_seconds,
                },
            })
            response = connection.recv()
            connection.close()
            return bool(response.get("ok"))
        except (OSError, EOFError, ConnectionError):
            return False

    def start(self, timeout: float = 15.0) -> bool:
        if self.process is not None and self.process.poll() is None:
            return True
        entry = os.path.join(APP_DIR, "shared_inference_server.py")
        self.process = subprocess.Popen(
            [sys.executable, "-u", entry],
            cwd=APP_DIR,
            env=self._service_environment(),
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return False
            try:
                connection = Client(self.socket_path, family="AF_UNIX", authkey=_AUTHKEY)
                connection.send({"action": "ping"})
                response = connection.recv()
                connection.close()
                if response.get("ok"):
                    return True
            except (OSError, EOFError, ConnectionError):
                time.sleep(0.1)
        return False

    def ensure_running(self) -> bool:
        if self.process is not None and self.process.poll() is None:
            return True
        return self.start()

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                connection = Client(self.socket_path, family="AF_UNIX", authkey=_AUTHKEY)
                connection.send({"action": "shutdown"})
                connection.recv()
                connection.close()
            except (OSError, EOFError, ConnectionError):
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.process = None


def run_server() -> None:
    server = SharedInferenceServer()

    def stop_server(*_args):
        server.close()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    try:
        server.serve_forever()
    finally:
        server.close()
