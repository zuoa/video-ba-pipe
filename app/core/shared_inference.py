"""Cross-process shared Ultralytics and RKNN inference service.

Source workflow hosts exchange frame descriptors over a Unix socket.  Pixels live
in short-lived POSIX shared-memory segments, while exactly one model process is
kept for each stable model key.  Queues are deliberately bounded: overload drops
analysis work instead of consuming unbounded Jetson unified memory.
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
        "ultralytics": "ultralytics",
    }
    requested = str(config.get("backend") or "auto").strip().lower()
    if requested in aliases:
        return aliases[requested]
    framework = str(model_info.get("framework") or "").lower()
    extension = os.path.splitext(model_path)[1].lower()
    if extension == ".rknn" or "rknn" in framework:
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


def model_key(spec: Dict[str, Any]) -> str:
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _inference_config(config: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "confidence": float(config.get("confidence", 0.6)),
        "nms_iou": float(config.get("nms_iou", 0.45)),
        "class_filter": list(config.get("class_filter") or []),
        "label_name": config.get("label_name"),
        "input_width": int(config.get("input_width") or 640),
        "input_height": int(config.get("input_height") or 640),
    }
    for key in (
        "backend",
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


def _create_model_worker_backend(
    spec: Dict[str, Any],
    model_info: Dict[str, Any],
    base_config: Dict[str, Any],
):
    from app.user_scripts.common.yolo_backends import RKNNBackend, UltralyticsBackend

    backend_name = spec.get("backend") or _selected_backend_name(
        spec["model_path"], model_info, base_config
    )
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
) -> None:
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
        warmup_frame = np.zeros((warmup_height, warmup_width, 3), dtype=np.uint8)
        backend.infer(warmup_frame)
        predictor = getattr(backend.model, "predictor", None)
        device = getattr(predictor, "device", None)
        result_queue.put({
            "kind": "worker_ready",
            "key": model_key(spec),
            "pid": os.getpid(),
            "startup_time_ms": (time.monotonic() - startup_started_at) * 1000.0,
            "device": str(device) if device is not None else None,
            "backend": backend.name,
        })
    except Exception as exc:
        result_queue.put({
            "kind": "worker_start_failed",
            "key": model_key(spec),
            "error": f"{type(exc).__name__}: {exc}",
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
                        },
                    })
            except Exception as exc:
                for request, _frame in group:
                    result_queue.put({
                        "kind": "result",
                        "request_id": request["request_id"],
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    })

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
    startup_time_ms: Optional[float] = None
    device: Optional[str] = None
    backend: Optional[str] = None
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

    def _new_slot(self, spec: Dict[str, Any], config: Dict[str, Any]) -> _ModelSlot:
        key = model_key(spec)
        request_queue = self.context.Queue(maxsize=self.queue_size)
        process = self.context.Process(
            target=self.worker_target,
            args=(spec, config, request_queue, self.result_queue),
            name=f"shared-model-{key[:8]}",
            daemon=True,
        )
        process.start()
        slot = _ModelSlot(
            key=key,
            spec=dict(spec),
            process=process,
            request_queue=request_queue,
            base_config=dict(config),
            oom_kill_count_at_start=read_cgroup_oom_kill_count(),
        )
        self.slots[key] = slot
        return slot

    def acquire(self, spec: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        key = model_key(spec)
        with self.lock:
            slot = self.slots.get(key)
            if slot is None:
                slot = self._new_slot(spec, config)
            elif slot.start_error:
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
                slot = self._restart_dead_slot(slot)
            slot.references += 1
            slot.idle_since = None
            return {"ok": True, "model_key": key, "pid": slot.process.pid}

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
            if slot.start_error:
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
                slot = self._restart_dead_slot(slot)
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
        spec = slot.spec
        base_config = slot.base_config
        self._stop_slot(slot)
        replacement = self._new_slot(spec, base_config)
        replacement.references = references
        replacement.idle_since = idle_since
        replacement.oom_failures = oom_failures
        replacement.last_oom_at = last_oom_at
        return replacement

    def _dispatch_results(self) -> None:
        while self.running:
            try:
                response = self.result_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            kind = response.get("kind")
            if kind in ("worker_ready", "worker_start_failed"):
                with self.lock:
                    key = response.get("key")
                    slot = self.slots.get(key)
                    if slot is not None:
                        slot.ready = kind == "worker_ready"
                        slot.start_error = response.get("error")
                        slot.startup_time_ms = response.get("startup_time_ms")
                        slot.device = response.get("device")
                        slot.backend = response.get("backend") or slot.spec.get("backend")
                        slot.ready_event.set()
                    if kind == "worker_start_failed":
                        # worker 起不来时,已入队但永远不会被处理的请求必须立即置败,
                        # 否则首个请求会傻等满超时被误报成 inference_timeout,
                        # 而真实的启动错误(如 ultralytics/torch import 失败)被吞掉。
                        error = response.get("error") or "model_worker_start_failed"
                        failure_traceback = response.get("traceback")
                        print(
                            "[SharedInference] 模型子进程启动失败: "
                            f"model_key={key}, error={error}",
                            file=sys.stderr,
                            flush=True,
                        )
                        if failure_traceback:
                            print(failure_traceback, file=sys.stderr, flush=True)
                        for rid, pending in list(self.pending.items()):
                            if pending.key == key:
                                pending.response = {"ok": False, "error": error}
                                pending.event.set()
                                self.pending.pop(rid, None)
                continue
            request_id = response.get("request_id")
            with self.lock:
                pending = self.pending.pop(request_id, None)
            if pending is not None:
                pending.response = response
                pending.event.set()

    def _reap_idle(self) -> None:
        while self.running:
            time.sleep(1.0)
            now = time.monotonic()
            with self.lock:
                expired = [
                    slot for slot in self.slots.values()
                    if slot.references == 0
                    and slot.idle_since is not None
                    and now - slot.idle_since >= self.idle_seconds
                ]
                for slot in expired:
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

    def stats(self) -> Dict[str, Any]:
        with self.lock:
            models = []
            for slot in self.slots.values():
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
                    "model_path": slot.spec.get("model_path"),
                    "pid": slot.process.pid,
                    "alive": slot.process.is_alive(),
                    "exitcode": slot.process.exitcode,
                    "ready": slot.ready,
                    "start_error": slot.start_error,
                    "startup_time_ms": slot.startup_time_ms,
                    "device": slot.device,
                    "backend": slot.backend or slot.spec.get("backend"),
                    "references": slot.references,
                    "queue_depth": queue_depth,
                    **memory_metrics,
                    "oom_failures": slot.oom_failures,
                    "oom_retry_in_seconds": max(
                        0.0, round(slot.oom_retry_at - time.monotonic(), 1)
                    ),
                })
            return {
                "ok": True,
                "models": models,
                "model_count": len(models),
                "oom_policy": self.oom_policy(),
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


class SharedInferenceClient:
    def __init__(
        self,
        model_path: str,
        model_info: Dict[str, Any],
        config: Dict[str, Any],
        *,
        socket_path: str = SHARED_INFERENCE_SOCKET_PATH,
        timeout: float = SHARED_INFERENCE_REQUEST_TIMEOUT_SECONDS,
        startup_timeout: float = SHARED_INFERENCE_STARTUP_TIMEOUT_SECONDS,
    ):
        self.socket_path = socket_path
        self.timeout = float(timeout)
        self.startup_timeout = float(startup_timeout)
        self.spec = build_model_spec(model_path, model_info, config)
        self.config = _inference_config(config)
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
            raise SharedInferenceError(response.get("error") or "model_acquire_failed")
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
                "config": _inference_config(config),
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
                raise SharedInferenceError(response.get("error") or "shared inference failed")
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
