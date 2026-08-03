"""Cross-process shared Ultralytics inference service.

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
import uuid
from dataclasses import dataclass
from multiprocessing import resource_tracker, shared_memory
from multiprocessing.connection import Client, Listener
from typing import Any, Dict, Optional

import numpy as np

from app.config import (
    APP_DIR,
    SHARED_INFERENCE_BATCH_MAX_SIZE,
    SHARED_INFERENCE_BATCH_WAIT_MS,
    SHARED_INFERENCE_IDLE_SECONDS,
    SHARED_INFERENCE_QUEUE_SIZE,
    SHARED_INFERENCE_REQUEST_TIMEOUT_SECONDS,
    SHARED_INFERENCE_SOCKET_PATH,
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
    return {
        "model_id": int(model_id) if str(model_id).isdigit() else model_id,
        "model_path": os.path.abspath(model_path),
        "file_size": file_size,
        "file_mtime_ns": file_mtime_ns,
        "framework": str(model_info.get("framework") or "ultralytics"),
        "model_type": str(model_info.get("model_type") or "YOLO"),
        "input_width": int(config.get("input_width") or 640),
        "input_height": int(config.get("input_height") or 640),
    }


def model_key(spec: Dict[str, Any]) -> str:
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _inference_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "confidence": float(config.get("confidence", 0.6)),
        "nms_iou": float(config.get("nms_iou", 0.45)),
        "class_filter": list(config.get("class_filter") or []),
        "label_name": config.get("label_name"),
        "input_width": int(config.get("input_width") or 640),
        "input_height": int(config.get("input_height") or 640),
    }


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
    try:
        from app.user_scripts.common.yolo_backends import UltralyticsBackend

        model_info = {
            "id": spec.get("model_id"),
            "path": spec["model_path"],
            "file_size": spec.get("file_size"),
            "framework": spec.get("framework"),
            "model_type": spec.get("model_type"),
            "input_shape": f"{spec.get('input_width', 640)}x{spec.get('input_height', 640)}",
            "classes": {},
        }
        backend = UltralyticsBackend(spec["model_path"], model_info, base_config)
        result_queue.put({
            "kind": "worker_ready",
            "key": model_key(spec),
            "pid": os.getpid(),
        })
    except Exception as exc:
        result_queue.put({
            "kind": "worker_start_failed",
            "key": model_key(spec),
            "error": f"{type(exc).__name__}: {exc}",
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
                if len(group) > 1:
                    batch_results = backend.infer_batch(frames, configs)
                else:
                    backend.config = configs[0]
                    batch_results = [backend.infer(frames[0])]
                for (request, _frame), result in zip(group, batch_results):
                    detections, details, metadata = result
                    result_queue.put({
                        "kind": "result",
                        "request_id": request["request_id"],
                        "ok": True,
                        "detections": detections,
                        "details": details,
                        "metadata": {**metadata, "batch_size": len(group)},
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
    start_error: Optional[str] = None
    oom_kill_count_at_start: int = 0
    oom_failures: int = 0
    oom_retry_at: float = 0.0
    dead_exit_handled: bool = False


class _ModelRegistry:
    def __init__(
        self,
        queue_size: int,
        idle_seconds: float,
        worker_target=_model_worker_main,
    ):
        self.context = multiprocessing.get_context("spawn")
        self.queue_size = max(1, int(queue_size))
        self.idle_seconds = max(1.0, float(idle_seconds))
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
            elif not slot.process.is_alive():
                retry_response = self._observe_dead_slot(slot)
                if retry_response is not None:
                    return retry_response
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

    def submit(self, key: str, request: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        request_id = request["request_id"]
        pending = _PendingResult(threading.Event())
        with self.lock:
            slot = self.slots.get(key)
            if slot is None:
                return {"ok": False, "error": "model_worker_unavailable"}
            if not slot.process.is_alive():
                retry_response = self._observe_dead_slot(slot)
                if retry_response is not None:
                    return retry_response
                slot = self._restart_dead_slot(slot)
            if slot.start_error:
                return {"ok": False, "error": slot.start_error}
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
                slot.oom_failures += 1
                delay = min(15.0 * (2 ** (slot.oom_failures - 1)), 300.0)
                if slot.oom_failures >= 3:
                    delay = max(delay, 600.0)
                slot.oom_retry_at = now + delay
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
        spec = slot.spec
        base_config = slot.base_config
        self._stop_slot(slot)
        replacement = self._new_slot(spec, base_config)
        replacement.references = references
        replacement.idle_since = idle_since
        replacement.oom_failures = oom_failures
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
                    slot = self.slots.get(response.get("key"))
                    if slot is not None:
                        slot.ready = kind == "worker_ready"
                        slot.start_error = response.get("error")
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
                    "ready": slot.ready,
                    "start_error": slot.start_error,
                    "references": slot.references,
                    "queue_depth": queue_depth,
                    **memory_metrics,
                    "oom_failures": slot.oom_failures,
                    "oom_retry_in_seconds": max(
                        0.0, round(slot.oom_retry_at - time.monotonic(), 1)
                    ),
                })
            return {"ok": True, "models": models, "model_count": len(models)}

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
    ):
        self.socket_path = socket_path
        self.timeout = float(timeout)
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

    def __init__(self, socket_path: str = SHARED_INFERENCE_SOCKET_PATH):
        self.socket_path = socket_path
        self.process: Optional[subprocess.Popen] = None

    def start(self, timeout: float = 15.0) -> bool:
        if self.process is not None and self.process.poll() is None:
            return True
        entry = os.path.join(APP_DIR, "shared_inference_server.py")
        self.process = subprocess.Popen([sys.executable, "-u", entry], cwd=APP_DIR)
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
