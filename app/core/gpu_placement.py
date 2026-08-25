"""NVML-backed placement and admission for shared CUDA model workers."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


MIB = 1024.0 * 1024.0
MODEL_FILE_MEMORY_FACTOR = 4.0


class GpuPlacementError(RuntimeError):
    """A structured placement failure safe to return over the local IPC API."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details

    def to_response(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": self.code,
            "message": str(self),
            **self.details,
        }


@dataclass(frozen=True)
class GpuDeviceSnapshot:
    index: int
    uuid: str
    name: str
    total_mb: float
    used_mb: float
    free_mb: float
    utilization_percent: Optional[float] = None


@dataclass
class GpuAssignment:
    owner_key: str
    gpu_index: int
    gpu_uuid: str
    gpu_name: str
    reserved_mb: float
    state: str = "starting"
    pid: Optional[int] = None
    actual_mb: Optional[float] = None
    assigned_at: float = 0.0

    def worker_payload(self) -> Dict[str, Any]:
        return {
            "gpu_index": self.gpu_index,
            "gpu_uuid": self.gpu_uuid,
            "gpu_name": self.gpu_name,
            "reserved_mb": self.reserved_mb,
        }

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def spec_requires_cuda(spec: Dict[str, Any]) -> bool:
    """Return whether a shared worker should be isolated onto a CUDA device."""
    if isinstance(spec.get("requires_cuda"), bool):
        return spec["requires_cuda"]
    backend = str(spec.get("backend") or "").strip().lower()
    if backend == "ultralytics":
        return True
    if backend != "paddleocr":
        return False
    device = str((spec.get("backend_config") or {}).get("device") or "auto").lower()
    return device != "cpu"


class NvmlGpuProvider:
    """Small long-lived NVML adapter owned by the inference router process."""

    def __init__(self, nvml=None):
        self._nvml = nvml
        self._initialized = False
        self._handles_by_uuid: Dict[str, Any] = {}

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        if self._nvml is None:
            import pynvml

            self._nvml = pynvml
        self._nvml.nvmlInit()
        self._initialized = True

    @staticmethod
    def _text(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def devices(self) -> List[GpuDeviceSnapshot]:
        self._ensure_initialized()
        devices = []
        handles = {}
        for index in range(int(self._nvml.nvmlDeviceGetCount())):
            handle = self._nvml.nvmlDeviceGetHandleByIndex(index)
            uuid = self._text(self._nvml.nvmlDeviceGetUUID(handle))
            name = self._text(self._nvml.nvmlDeviceGetName(handle))
            memory = self._nvml.nvmlDeviceGetMemoryInfo(handle)
            try:
                utilization = self._nvml.nvmlDeviceGetUtilizationRates(handle)
                utilization_percent = float(getattr(utilization, "gpu", 0.0))
            except Exception:
                utilization_percent = None
            devices.append(GpuDeviceSnapshot(
                index=index,
                uuid=uuid,
                name=name,
                total_mb=float(memory.total) / MIB,
                used_mb=float(memory.used) / MIB,
                free_mb=float(memory.free) / MIB,
                utilization_percent=utilization_percent,
            ))
            handles[uuid] = handle
        self._handles_by_uuid = handles
        return devices

    def process_used_mb(self, gpu_uuid: str, pid: int) -> Optional[float]:
        self._ensure_initialized()
        handle = self._handles_by_uuid.get(gpu_uuid)
        if handle is None:
            self.devices()
            handle = self._handles_by_uuid.get(gpu_uuid)
        if handle is None:
            return None

        process_reader = None
        for name in (
            "nvmlDeviceGetComputeRunningProcesses_v3",
            "nvmlDeviceGetComputeRunningProcesses_v2",
            "nvmlDeviceGetComputeRunningProcesses",
        ):
            candidate = getattr(self._nvml, name, None)
            if callable(candidate):
                process_reader = candidate
                break
        if process_reader is None:
            return None

        total_bytes = 0
        found = False
        try:
            processes = process_reader(handle)
        except Exception:
            return None
        for process in processes or ():
            if int(getattr(process, "pid", -1)) != int(pid):
                continue
            used = getattr(process, "usedGpuMemory", None)
            if not isinstance(used, (int, float)) or used < 0 or used >= 2 ** 63:
                continue
            found = True
            total_bytes += int(used)
        return total_bytes / MIB if found else None

    def close(self) -> None:
        if not self._initialized:
            return
        try:
            self._nvml.nvmlShutdown()
        except Exception:
            pass
        self._initialized = False
        self._handles_by_uuid.clear()


class GpuPlacementBroker:
    """Select GPUs, reserve cold-start capacity, and expose placement telemetry."""

    def __init__(
        self,
        *,
        enabled: bool,
        allowed_devices: Sequence[str] = (),
        reserve_mb: int = 1024,
        default_model_mb: int = 2048,
        margin_percent: float = 25.0,
        oom_cooldown_seconds: float = 60.0,
        stale_seconds: float = 60.0,
        failure_mode: str = "reject",
        provider=None,
        time_func=time.monotonic,
    ):
        self.enabled = bool(enabled)
        self.allowed_devices = tuple(
            str(value).strip() for value in allowed_devices if str(value).strip()
        )
        self.reserve_mb = max(0.0, float(reserve_mb))
        self.default_model_mb = max(1.0, float(default_model_mb))
        self.margin_percent = max(0.0, float(margin_percent))
        self.oom_cooldown_seconds = max(1.0, float(oom_cooldown_seconds))
        self.stale_seconds = max(1.0, float(stale_seconds))
        self.failure_mode = str(failure_mode or "reject").strip().lower()
        self.provider = provider or NvmlGpuProvider()
        self._time = time_func
        self._lock = threading.RLock()
        self.assignments: Dict[str, GpuAssignment] = {}
        self.observed_model_mb: Dict[str, float] = {}
        self.cooldowns: Dict[str, float] = {}
        self._last_devices: List[GpuDeviceSnapshot] = []
        self._last_devices_at = float("-inf")
        self._last_error: Optional[str] = None
        self._degraded_to_legacy = False
        self._tie_cursor = 0

    def estimate_model_mb(self, owner_key: str, spec: Dict[str, Any]) -> float:
        observed = self.observed_model_mb.get(owner_key)
        if observed is not None and observed > 0:
            base_mb = observed
        else:
            model_bytes = max(0.0, float(spec.get("file_size") or 0))
            model_bytes += max(
                0.0,
                float(spec.get("recognition_file_size") or 0),
            )
            file_mb = model_bytes / MIB
            base_mb = max(self.default_model_mb, file_mb * MODEL_FILE_MEMORY_FACTOR)
        return round(base_mb * (1.0 + self.margin_percent / 100.0), 1)

    def _read_devices(self) -> Tuple[List[GpuDeviceSnapshot], bool]:
        now = self._time()
        try:
            devices = list(self.provider.devices())
            self._last_devices = devices
            self._last_devices_at = now
            self._last_error = None
            return devices, False
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            if self._last_devices and now - self._last_devices_at <= self.stale_seconds:
                return list(self._last_devices), True
            raise GpuPlacementError(
                "gpu_metrics_unavailable",
                "无法读取可用 GPU 的 NVML 指标",
                metrics_error=self._last_error,
            ) from exc

    def _allowed(self, device: GpuDeviceSnapshot) -> bool:
        if not self.allowed_devices:
            return True
        return device.uuid in self.allowed_devices or str(device.index) in self.allowed_devices

    def _pending_by_gpu(self, *, include_ready: bool = False) -> Dict[str, float]:
        pending: Dict[str, float] = {}
        for assignment in self.assignments.values():
            # When per-PID usage cannot be read, retain the reservation even after
            # readiness so a temporary NVML visibility gap cannot oversubscribe VRAM.
            if (
                include_ready
                or assignment.state == "starting"
                or assignment.actual_mb is None
            ):
                pending[assignment.gpu_uuid] = (
                    pending.get(assignment.gpu_uuid, 0.0) + assignment.reserved_mb
                )
        return pending

    def reserve(
        self,
        owner_key: str,
        spec: Dict[str, Any],
        *,
        exclude_gpu_uuids: Iterable[str] = (),
    ) -> Optional[GpuAssignment]:
        if not self.enabled or not spec_requires_cuda(spec):
            return None
        with self._lock:
            existing = self.assignments.get(owner_key)
            if existing is not None:
                return existing
            try:
                devices, stale = self._read_devices()
            except GpuPlacementError:
                if self.failure_mode == "legacy":
                    self._degraded_to_legacy = True
                    return None
                raise

            excluded = set(exclude_gpu_uuids)
            now = self._time()
            self.cooldowns = {
                uuid: deadline for uuid, deadline in self.cooldowns.items()
                if deadline > now
            }
            pending = self._pending_by_gpu(include_ready=stale)
            estimated_mb = self.estimate_model_mb(owner_key, spec)
            candidates = []
            gpu_details = []
            for device in devices:
                pending_mb = pending.get(device.uuid, 0.0)
                available_mb = device.free_mb - pending_mb - self.reserve_mb
                detail = {
                    "index": device.index,
                    "uuid": device.uuid,
                    "name": device.name,
                    "total_mb": round(device.total_mb, 1),
                    "used_mb": round(device.used_mb, 1),
                    "free_mb": round(device.free_mb, 1),
                    "pending_mb": round(pending_mb, 1),
                    "available_after_reserve_mb": round(available_mb, 1),
                    "cooldown_seconds": round(
                        max(0.0, self.cooldowns.get(device.uuid, 0.0) - now), 1
                    ),
                }
                gpu_details.append(detail)
                if (
                    not self._allowed(device)
                    or device.uuid in excluded
                    or device.uuid in self.cooldowns
                    or available_mb < estimated_mb
                ):
                    continue
                projected_ratio = (
                    device.used_mb + pending_mb + estimated_mb
                ) / max(1.0, device.total_mb)
                candidates.append((device, projected_ratio))

            if not candidates:
                raise GpuPlacementError(
                    "gpu_capacity_exhausted",
                    "没有 GPU 具备足够的安全显存来加载模型",
                    estimated_mb=estimated_mb,
                    reserve_mb=self.reserve_mb,
                    gpus=gpu_details,
                )

            ordered = sorted(candidates, key=lambda item: item[0].index)
            rotated = ordered[self._tie_cursor % len(ordered):] + ordered[:self._tie_cursor % len(ordered)]
            device, _score = min(
                rotated,
                key=lambda item: (
                    round(item[1], 6),
                    item[0].utilization_percent
                    if item[0].utilization_percent is not None
                    else 0.0,
                    rotated.index(item),
                ),
            )
            self._tie_cursor = (self._tie_cursor + 1) % max(1, len(ordered))
            assignment = GpuAssignment(
                owner_key=owner_key,
                gpu_index=device.index,
                gpu_uuid=device.uuid,
                gpu_name=device.name,
                reserved_mb=estimated_mb,
                assigned_at=now,
            )
            self.assignments[owner_key] = assignment
            self._degraded_to_legacy = False
            return assignment

    def mark_ready(self, owner_key: str, pid: int) -> Optional[GpuAssignment]:
        with self._lock:
            assignment = self.assignments.get(owner_key)
            if assignment is None:
                return None
            assignment.pid = int(pid)
            assignment.state = "ready"
            actual_mb = self.provider.process_used_mb(assignment.gpu_uuid, pid)
            if actual_mb is not None and actual_mb > 0:
                assignment.actual_mb = round(float(actual_mb), 1)
                self.observed_model_mb[owner_key] = max(
                    self.observed_model_mb.get(owner_key, 0.0),
                    float(actual_mb),
                )
            return assignment

    def refresh_usage(self, owner_key: str, pid: int) -> Optional[float]:
        with self._lock:
            assignment = self.assignments.get(owner_key)
            if assignment is None:
                return None
            actual_mb = self.provider.process_used_mb(assignment.gpu_uuid, pid)
            if actual_mb is not None and actual_mb > 0:
                assignment.actual_mb = round(float(actual_mb), 1)
                self.observed_model_mb[owner_key] = max(
                    self.observed_model_mb.get(owner_key, 0.0),
                    float(actual_mb),
                )
            return assignment.actual_mb

    def release(self, owner_key: str) -> Optional[GpuAssignment]:
        with self._lock:
            return self.assignments.pop(owner_key, None)

    def fail(self, owner_key: str, *, cooldown: bool = False) -> Optional[GpuAssignment]:
        with self._lock:
            assignment = self.assignments.pop(owner_key, None)
            if assignment is not None and cooldown:
                self.cooldowns[assignment.gpu_uuid] = (
                    self._time() + self.oom_cooldown_seconds
                )
            return assignment

    def status(self) -> Dict[str, Any]:
        with self._lock:
            stale = False
            try:
                devices, stale = self._read_devices() if self.enabled else ([], False)
            except GpuPlacementError:
                devices = list(self._last_devices)
                stale = True
            pending = self._pending_by_gpu(include_ready=stale)
            allocation_count: Dict[str, int] = {}
            for assignment in self.assignments.values():
                allocation_count[assignment.gpu_uuid] = (
                    allocation_count.get(assignment.gpu_uuid, 0) + 1
                )
            return {
                "enabled": self.enabled,
                "failure_mode": self.failure_mode,
                "degraded_to_legacy": self._degraded_to_legacy,
                "metrics_stale": stale,
                "metrics_error": self._last_error,
                "reserve_mb": self.reserve_mb,
                "allowed_devices": list(self.allowed_devices),
                "gpus": [
                    {
                        **asdict(device),
                        "pending_reserved_mb": round(pending.get(device.uuid, 0.0), 1),
                        "assignment_count": allocation_count.get(device.uuid, 0),
                        "cooldown_seconds": round(max(
                            0.0,
                            self.cooldowns.get(device.uuid, 0.0) - self._time(),
                        ), 1),
                    }
                    for device in devices
                    if self._allowed(device)
                ],
            }

    def close(self) -> None:
        with self._lock:
            self.assignments.clear()
            self.provider.close()
