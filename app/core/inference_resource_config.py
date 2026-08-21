"""Database-backed inference resource policy and worker status.

Environment variables remain the bootstrap/fallback defaults.  Once the
database is available, the API and worker share one SystemSetting record so a
stale compose file cannot silently disable runtime protection.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from multiprocessing import shared_memory
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import psutil

from app import logger
from app.config import (
    GPU_ALLOWED_DEVICES,
    GPU_MEMORY_RESERVE_MB,
    GPU_MODEL_MEMORY_MARGIN_PERCENT,
    GPU_NEW_MODEL_DEFAULT_MB,
    GPU_NVML_STALE_SECONDS,
    GPU_OOM_COOLDOWN_SECONDS,
    GPU_SCHEDULING_ENABLED,
    GPU_SCHEDULING_FAILURE_MODE,
    GPU_SCHEDULING_POLICY,
    INFERENCE_ADMISSION_ENABLED,
    INFERENCE_MODEL_MEMORY_MARGIN_PERCENT,
    INFERENCE_NEW_MODEL_DEFAULT_MB,
    INFERENCE_SYSTEM_RESERVE_MB,
    INFERENCE_SYSTEM_RESERVE_PERCENT,
    OOM_CIRCUIT_BREAKER_ENABLED,
    OOM_CIRCUIT_FAILURE_THRESHOLD,
    OOM_CIRCUIT_OPEN_SECONDS,
    OOM_CIRCUIT_STABLE_RESET_SECONDS,
    OOM_RESTART_BACKOFF_MAX_SECONDS,
    SHARED_INFERENCE_BATCH_MAX_SIZE,
    SHARED_INFERENCE_BATCH_WAIT_MS,
    SHARED_INFERENCE_ENABLED,
    SHARED_INFERENCE_IDLE_SECONDS,
    SHARED_INFERENCE_QUEUE_SIZE,
    SHARED_INFERENCE_REQUEST_TIMEOUT_SECONDS,
    SHARED_RKNN_ENABLED,
)
from app.core.database_models import SystemSetting
from app.core.gpu_placement import NvmlGpuProvider


INFERENCE_RESOURCE_SETTING_KEY = "inference_resource_config"
INFERENCE_RESOURCE_STATUS_KEY = "inference_resource_status"
INFERENCE_RESOURCE_CONFIG_REFRESH_SECONDS = 5.0
INFERENCE_RESOURCE_STATUS_STALE_SECONDS = 20.0

# Capability refresh is structural discovery, not live telemetry.  Keep the
# last successful topology so a transient NVML read failure cannot disable GPU
# scheduling and rebuild every active shared-inference process.  A successful
# empty result still replaces the cache and represents a genuine topology
# change; runtime metric staleness remains governed by GpuPlacementBroker.
_GPU_CAPABILITY_CACHE_LOCK = threading.Lock()
_LAST_VISIBLE_NVIDIA_GPUS: Optional[Tuple[Any, ...]] = None


@dataclass(frozen=True)
class InferenceResourceConfig:
    shared_inference_enabled: bool = False
    gpu_scheduling_enabled: bool = True
    gpu_scheduling_policy: str = "balanced"
    gpu_allowed_devices: Tuple[str, ...] = ()
    gpu_memory_reserve_mb: int = 1024
    gpu_new_model_default_mb: int = 2048
    gpu_model_memory_margin_percent: float = 25.0
    gpu_oom_cooldown_seconds: int = 60
    gpu_nvml_stale_seconds: int = 60
    gpu_failure_mode: str = "reject"
    inference_admission_enabled: bool = False
    system_reserve_mb: int = 2048
    system_reserve_percent: float = 15.0
    new_model_default_mb: int = 1024
    model_memory_margin_percent: float = 25.0
    queue_size: int = 2
    batch_max_size: int = 4
    batch_wait_ms: float = 5.0
    request_timeout_seconds: float = 30.0
    model_idle_seconds: int = 120
    oom_circuit_breaker_enabled: bool = True
    oom_failure_threshold: int = 3
    oom_circuit_open_seconds: int = 600
    oom_stable_reset_seconds: int = 600
    oom_restart_backoff_max_seconds: int = 300

    def to_dict(self) -> Dict[str, Any]:
        values = asdict(self)
        values["gpu_allowed_devices"] = list(self.gpu_allowed_devices)
        return values

    @property
    def revision(self) -> str:
        payload = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    @property
    def service_fingerprint(self) -> Tuple[Any, ...]:
        return (
            self.shared_inference_enabled,
            self.gpu_scheduling_enabled,
            self.gpu_scheduling_policy,
            self.gpu_allowed_devices,
            self.gpu_memory_reserve_mb,
            self.gpu_new_model_default_mb,
            self.gpu_model_memory_margin_percent,
            self.gpu_oom_cooldown_seconds,
            self.gpu_nvml_stale_seconds,
            self.gpu_failure_mode,
            self.queue_size,
            self.batch_max_size,
            self.batch_wait_ms,
            self.request_timeout_seconds,
            self.model_idle_seconds,
        )

    @property
    def oom_fingerprint(self) -> Tuple[Any, ...]:
        return (
            self.oom_circuit_breaker_enabled,
            self.oom_failure_threshold,
            self.oom_circuit_open_seconds,
            self.oom_stable_reset_seconds,
            self.oom_restart_backoff_max_seconds,
        )


def environment_inference_resource_config() -> InferenceResourceConfig:
    return InferenceResourceConfig(
        shared_inference_enabled=SHARED_INFERENCE_ENABLED,
        gpu_scheduling_enabled=GPU_SCHEDULING_ENABLED,
        gpu_scheduling_policy=GPU_SCHEDULING_POLICY,
        gpu_allowed_devices=GPU_ALLOWED_DEVICES,
        gpu_memory_reserve_mb=GPU_MEMORY_RESERVE_MB,
        gpu_new_model_default_mb=GPU_NEW_MODEL_DEFAULT_MB,
        gpu_model_memory_margin_percent=GPU_MODEL_MEMORY_MARGIN_PERCENT,
        gpu_oom_cooldown_seconds=GPU_OOM_COOLDOWN_SECONDS,
        gpu_nvml_stale_seconds=GPU_NVML_STALE_SECONDS,
        gpu_failure_mode=GPU_SCHEDULING_FAILURE_MODE,
        inference_admission_enabled=INFERENCE_ADMISSION_ENABLED,
        system_reserve_mb=INFERENCE_SYSTEM_RESERVE_MB,
        system_reserve_percent=INFERENCE_SYSTEM_RESERVE_PERCENT,
        new_model_default_mb=INFERENCE_NEW_MODEL_DEFAULT_MB,
        model_memory_margin_percent=INFERENCE_MODEL_MEMORY_MARGIN_PERCENT,
        queue_size=SHARED_INFERENCE_QUEUE_SIZE,
        batch_max_size=SHARED_INFERENCE_BATCH_MAX_SIZE,
        batch_wait_ms=SHARED_INFERENCE_BATCH_WAIT_MS,
        request_timeout_seconds=SHARED_INFERENCE_REQUEST_TIMEOUT_SECONDS,
        model_idle_seconds=SHARED_INFERENCE_IDLE_SECONDS,
        oom_circuit_breaker_enabled=OOM_CIRCUIT_BREAKER_ENABLED,
        oom_failure_threshold=OOM_CIRCUIT_FAILURE_THRESHOLD,
        oom_circuit_open_seconds=OOM_CIRCUIT_OPEN_SECONDS,
        oom_stable_reset_seconds=OOM_CIRCUIT_STABLE_RESET_SECONDS,
        oom_restart_backoff_max_seconds=OOM_RESTART_BACKOFF_MAX_SECONDS,
    )


def _parse_bool(
    value: Any,
    default: bool,
    *,
    key: str,
    strict: bool = False,
) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if strict and value not in (0, 1):
            raise ValueError(f"{key} 必须是布尔值")
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off", ""}:
        return False
    if strict:
        raise ValueError(f"{key} 必须是布尔值")
    return default


def _number(
    data: Dict[str, Any],
    key: str,
    default: Any,
    *,
    minimum: float,
    maximum: float,
    integer: bool = False,
    strict: bool = False,
) -> Any:
    value = data.get(key, default)
    try:
        parsed = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        if strict:
            raise ValueError(f"{key} 必须是数字") from exc
        parsed = default
    if parsed < minimum or parsed > maximum:
        if strict:
            raise ValueError(f"{key} 必须在 {minimum:g} 到 {maximum:g} 之间")
        parsed = min(max(parsed, minimum), maximum)
    return int(parsed) if integer else float(parsed)


def normalize_inference_resource_config(
    data: Optional[Dict[str, Any]],
    *,
    defaults: Optional[InferenceResourceConfig] = None,
    strict: bool = False,
) -> InferenceResourceConfig:
    if strict and not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")
    data = data if isinstance(data, dict) else {}
    defaults = defaults or environment_inference_resource_config()
    allowed_devices_value = data.get("gpu_allowed_devices", defaults.gpu_allowed_devices)
    if isinstance(allowed_devices_value, str):
        allowed_devices = tuple(
            value.strip() for value in allowed_devices_value.split(",") if value.strip()
        )
    elif isinstance(allowed_devices_value, (list, tuple)):
        allowed_devices = tuple(
            str(value).strip() for value in allowed_devices_value if str(value).strip()
        )
    elif strict:
        raise ValueError("gpu_allowed_devices 必须是字符串数组")
    else:
        allowed_devices = defaults.gpu_allowed_devices

    gpu_policy = str(
        data.get("gpu_scheduling_policy", defaults.gpu_scheduling_policy) or "balanced"
    ).strip().lower()
    if gpu_policy not in {"balanced"}:
        if strict:
            raise ValueError("gpu_scheduling_policy 目前仅支持 balanced")
        gpu_policy = "balanced"
    gpu_failure_mode = str(
        data.get("gpu_failure_mode", defaults.gpu_failure_mode) or "reject"
    ).strip().lower()
    if gpu_failure_mode not in {"reject", "legacy"}:
        if strict:
            raise ValueError("gpu_failure_mode 仅支持 reject 或 legacy")
        gpu_failure_mode = "reject"

    return InferenceResourceConfig(
        shared_inference_enabled=_parse_bool(
            data.get("shared_inference_enabled"),
            defaults.shared_inference_enabled,
            key="shared_inference_enabled",
            strict=strict,
        ),
        gpu_scheduling_enabled=_parse_bool(
            data.get("gpu_scheduling_enabled"),
            defaults.gpu_scheduling_enabled,
            key="gpu_scheduling_enabled",
            strict=strict,
        ),
        gpu_scheduling_policy=gpu_policy,
        gpu_allowed_devices=allowed_devices,
        gpu_memory_reserve_mb=_number(
            data, "gpu_memory_reserve_mb", defaults.gpu_memory_reserve_mb,
            minimum=0, maximum=1048576, integer=True, strict=strict,
        ),
        gpu_new_model_default_mb=_number(
            data, "gpu_new_model_default_mb", defaults.gpu_new_model_default_mb,
            minimum=128, maximum=1048576, integer=True, strict=strict,
        ),
        gpu_model_memory_margin_percent=_number(
            data,
            "gpu_model_memory_margin_percent",
            defaults.gpu_model_memory_margin_percent,
            minimum=0,
            maximum=100,
            strict=strict,
        ),
        gpu_oom_cooldown_seconds=_number(
            data, "gpu_oom_cooldown_seconds", defaults.gpu_oom_cooldown_seconds,
            minimum=1, maximum=86400, integer=True, strict=strict,
        ),
        gpu_nvml_stale_seconds=_number(
            data, "gpu_nvml_stale_seconds", defaults.gpu_nvml_stale_seconds,
            minimum=1, maximum=3600, integer=True, strict=strict,
        ),
        gpu_failure_mode=gpu_failure_mode,
        inference_admission_enabled=_parse_bool(
            data.get("inference_admission_enabled"),
            defaults.inference_admission_enabled,
            key="inference_admission_enabled",
            strict=strict,
        ),
        system_reserve_mb=_number(
            data, "system_reserve_mb", defaults.system_reserve_mb,
            minimum=256, maximum=1048576, integer=True, strict=strict,
        ),
        system_reserve_percent=_number(
            data, "system_reserve_percent", defaults.system_reserve_percent,
            minimum=0, maximum=50, strict=strict,
        ),
        new_model_default_mb=_number(
            data, "new_model_default_mb", defaults.new_model_default_mb,
            minimum=128, maximum=1048576, integer=True, strict=strict,
        ),
        model_memory_margin_percent=_number(
            data,
            "model_memory_margin_percent",
            defaults.model_memory_margin_percent,
            minimum=0,
            maximum=100,
            strict=strict,
        ),
        queue_size=_number(
            data, "queue_size", defaults.queue_size,
            minimum=1, maximum=64, integer=True, strict=strict,
        ),
        batch_max_size=_number(
            data, "batch_max_size", defaults.batch_max_size,
            minimum=1, maximum=64, integer=True, strict=strict,
        ),
        batch_wait_ms=_number(
            data, "batch_wait_ms", defaults.batch_wait_ms,
            minimum=0, maximum=1000, strict=strict,
        ),
        request_timeout_seconds=_number(
            data,
            "request_timeout_seconds",
            defaults.request_timeout_seconds,
            minimum=1,
            maximum=1800,
            strict=strict,
        ),
        model_idle_seconds=_number(
            data, "model_idle_seconds", defaults.model_idle_seconds,
            minimum=10, maximum=86400, integer=True, strict=strict,
        ),
        oom_circuit_breaker_enabled=_parse_bool(
            data.get("oom_circuit_breaker_enabled"),
            defaults.oom_circuit_breaker_enabled,
            key="oom_circuit_breaker_enabled",
            strict=strict,
        ),
        oom_failure_threshold=_number(
            data,
            "oom_failure_threshold",
            defaults.oom_failure_threshold,
            minimum=1,
            maximum=100,
            integer=True,
            strict=strict,
        ),
        oom_circuit_open_seconds=_number(
            data,
            "oom_circuit_open_seconds",
            defaults.oom_circuit_open_seconds,
            minimum=30,
            maximum=86400,
            integer=True,
            strict=strict,
        ),
        oom_stable_reset_seconds=_number(
            data,
            "oom_stable_reset_seconds",
            defaults.oom_stable_reset_seconds,
            minimum=60,
            maximum=86400,
            integer=True,
            strict=strict,
        ),
        oom_restart_backoff_max_seconds=_number(
            data,
            "oom_restart_backoff_max_seconds",
            defaults.oom_restart_backoff_max_seconds,
            minimum=30,
            maximum=86400,
            integer=True,
            strict=strict,
        ),
    )


def load_inference_resource_config(
    *, initialize: bool = False
) -> Tuple[InferenceResourceConfig, str, bool]:
    """Return config, source and database availability."""
    fallback = environment_inference_resource_config()
    try:
        record = SystemSetting.get_or_none(
            SystemSetting.key == INFERENCE_RESOURCE_SETTING_KEY
        )
        if record and record.value:
            return (
                normalize_inference_resource_config(
                    json.loads(record.value), defaults=fallback
                ),
                "database",
                True,
            )
        if initialize:
            record, created = SystemSetting.get_or_create(
                key=INFERENCE_RESOURCE_SETTING_KEY,
                defaults={
                    "value": json.dumps(fallback.to_dict(), ensure_ascii=False),
                    "description": "推理资源保护配置",
                    "updated_at": datetime.now(),
                    "updated_by": "bootstrap",
                },
            )
            if not created and record.value:
                return (
                    normalize_inference_resource_config(
                        json.loads(record.value), defaults=fallback
                    ),
                    "database",
                    True,
                )
            if not record.value:
                record.value = json.dumps(fallback.to_dict(), ensure_ascii=False)
                record.description = "推理资源保护配置"
                record.updated_at = datetime.now()
                record.updated_by = "bootstrap"
                record.save()
            return fallback, "environment_initialized", True
        return fallback, "environment_default", True
    except Exception as exc:
        logger.warning(f"读取推理资源配置失败，使用环境默认值: {exc}")
        return fallback, "environment_fallback", False


def save_inference_resource_config(
    data: Optional[Dict[str, Any]], updated_by: str = "system"
) -> InferenceResourceConfig:
    config = normalize_inference_resource_config(data, strict=True)
    now = datetime.now()
    record, _ = SystemSetting.get_or_create(
        key=INFERENCE_RESOURCE_SETTING_KEY,
        defaults={
            "value": "",
            "description": "推理资源保护配置",
            "updated_at": now,
            "updated_by": updated_by,
        },
    )
    record.value = json.dumps(config.to_dict(), ensure_ascii=False)
    record.description = "推理资源保护配置"
    record.updated_at = now
    record.updated_by = updated_by
    record.save()
    return config


def _device_model() -> str:
    for path in (Path("/proc/device-tree/model"), Path("/sys/firmware/devicetree/base/model")):
        try:
            return path.read_bytes().replace(b"\x00", b"").decode("utf-8", "ignore").strip()
        except OSError:
            continue
    return ""


def _device_tree_compatible() -> str:
    for path in (
        Path("/proc/device-tree/compatible"),
        Path("/sys/firmware/devicetree/base/compatible"),
    ):
        try:
            values = path.read_bytes().split(b"\x00")
            compatible = ",".join(
                value.decode("utf-8", "ignore").strip()
                for value in values
                if value.strip()
            )
            if compatible:
                return compatible
        except OSError:
            continue
    return ""


def _cgroup_oom_available() -> bool:
    try:
        with open("/proc/self/cgroup", "r", encoding="utf-8") as handle:
            for line in handle:
                hierarchy, controllers, path = line.strip().split(":", 2)
                if hierarchy == "0" and controllers == "":
                    events = os.path.join("/sys/fs/cgroup", path.lstrip("/"), "memory.events")
                    return os.path.isfile(events) and os.access(events, os.R_OK)
    except (OSError, ValueError):
        return False
    return False


def _visible_nvidia_gpus() -> Tuple[list, Optional[str], bool]:
    global _LAST_VISIBLE_NVIDIA_GPUS

    provider = NvmlGpuProvider()
    try:
        devices = list(provider.devices())
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        with _GPU_CAPABILITY_CACHE_LOCK:
            cached = _LAST_VISIBLE_NVIDIA_GPUS
        return list(cached or ()), error, cached is not None
    else:
        with _GPU_CAPABILITY_CACHE_LOCK:
            _LAST_VISIBLE_NVIDIA_GPUS = tuple(devices)
        return devices, None, False
    finally:
        provider.close()


def detect_inference_capabilities() -> Dict[str, Any]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    device_model = _device_model()
    device_compatible = _device_tree_compatible()
    model_lower = device_model.lower()
    compatible_lower = device_compatible.lower()
    is_jetson = (
        "jetson" in model_lower
        or "nvidia" in model_lower
        or "nvidia" in compatible_lower
    )
    is_rk3588 = (
        "rk3588" in model_lower
        or "rk3588" in machine
        or "rk3588" in compatible_lower
    )
    if is_jetson:
        platform_name = "jetson"
    elif is_rk3588:
        platform_name = "rk3588"
    elif system == "linux":
        platform_name = "linux"
    elif system == "darwin":
        platform_name = "macos"
    elif system == "windows":
        platform_name = "windows"
    else:
        platform_name = system or "unknown"

    unix_socket = hasattr(socket, "AF_UNIX") and os.name != "nt"
    posix_shared_memory = os.name == "posix" and shared_memory is not None
    cgroup_oom = system == "linux" and _cgroup_oom_available()
    if system == "linux" and not is_rk3588:
        nvidia_gpus, gpu_detection_error, gpu_snapshot_stale = (
            _visible_nvidia_gpus()
        )
    else:
        nvidia_gpus, gpu_detection_error, gpu_snapshot_stale = [], None, False
    return {
        "mode": "auto",
        "platform": platform_name,
        "system": system,
        "machine": machine,
        "device_model": device_model,
        "device_compatible": device_compatible,
        "in_docker": os.path.exists("/.dockerenv"),
        "shared_ultralytics": unix_socket and posix_shared_memory,
        "shared_ocr": unix_socket and posix_shared_memory,
        "memory_admission": True,
        "gpu_scheduling": len(nvidia_gpus) >= 2 and not is_jetson,
        "nvidia_gpu_count": len(nvidia_gpus),
        "nvidia_gpu_detection_error": gpu_detection_error,
        "nvidia_gpu_snapshot_stale": gpu_snapshot_stale,
        "nvidia_gpus": [
            {
                "index": gpu.index,
                "uuid": gpu.uuid,
                "name": gpu.name,
                "total_mb": round(gpu.total_mb, 1),
            }
            for gpu in nvidia_gpus
        ],
        "oom_detection": cgroup_oom,
        "unix_socket": unix_socket,
        "posix_shared_memory": posix_shared_memory,
        "rknn_shared": (
            SHARED_RKNN_ENABLED
            and is_rk3588
            and unix_socket
            and posix_shared_memory
        ),
        "onnx_shared": False,
    }


def effective_inference_resource_config(
    config: InferenceResourceConfig,
    capabilities: Optional[Dict[str, Any]] = None,
) -> InferenceResourceConfig:
    capabilities = capabilities or detect_inference_capabilities()
    values = config.to_dict()
    shared_runtime_available = bool(
        capabilities.get("shared_ultralytics")
        or capabilities.get("rknn_shared")
        or capabilities.get("shared_ocr")
    )
    gpu_scheduling_requested = bool(
        config.gpu_scheduling_enabled
        and capabilities.get("gpu_scheduling")
    )
    # The GPU broker lives inside the shared-inference service.  Treat GPU
    # scheduling as an implicit request for that service instead of silently
    # disabling placement when an older persisted config has
    # shared_inference_enabled=false.  This is capability-gated, so the generic
    # default gpu_scheduling_enabled=true does not turn on shared inference on
    # CPU/single-GPU hosts.
    values["shared_inference_enabled"] = bool(
        (config.shared_inference_enabled or gpu_scheduling_requested)
        and shared_runtime_available
    )
    values["gpu_scheduling_enabled"] = bool(
        gpu_scheduling_requested
        and values["shared_inference_enabled"]
    )
    values["inference_admission_enabled"] = bool(
        config.inference_admission_enabled and capabilities.get("memory_admission")
    )
    values["oom_circuit_breaker_enabled"] = bool(
        config.oom_circuit_breaker_enabled and capabilities.get("oom_detection")
    )
    return InferenceResourceConfig(**values)


def collect_portable_memory_status() -> Dict[str, float]:
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    mib = 1024.0 * 1024.0
    return {
        "total_mb": round(memory.total / mib, 1),
        "available_mb": round(memory.available / mib, 1),
        "used_mb": round(memory.used / mib, 1),
        "usage_percent": round(memory.percent, 1),
        "swap_total_mb": round(swap.total / mib, 1),
        "swap_used_mb": round(swap.used / mib, 1),
        "swap_usage_percent": round(swap.percent, 1),
    }


def publish_inference_resource_status(status: Dict[str, Any]) -> None:
    payload = dict(status)
    payload["reported_at_epoch"] = time.time()
    now = datetime.now()
    record, _ = SystemSetting.get_or_create(
        key=INFERENCE_RESOURCE_STATUS_KEY,
        defaults={
            "value": "",
            "description": "推理资源保护运行状态",
            "updated_at": now,
            "updated_by": "worker",
        },
    )
    record.value = json.dumps(payload, ensure_ascii=False, default=str)
    record.description = "推理资源保护运行状态"
    record.updated_at = now
    record.updated_by = "worker"
    record.save()


def get_inference_resource_status() -> Dict[str, Any]:
    try:
        record = SystemSetting.get_or_none(
            SystemSetting.key == INFERENCE_RESOURCE_STATUS_KEY
        )
        if not record or not record.value:
            return {"worker_online": False, "status_age_seconds": None}
        status = json.loads(record.value)
        reported_at = float(status.get("reported_at_epoch") or 0.0)
        age = max(0.0, time.time() - reported_at) if reported_at else None
        status["status_age_seconds"] = round(age, 1) if age is not None else None
        status["worker_online"] = bool(
            age is not None and age <= INFERENCE_RESOURCE_STATUS_STALE_SECONDS
        )
        return status
    except Exception as exc:
        logger.warning(f"读取推理资源运行状态失败: {exc}")
        return {
            "worker_online": False,
            "status_age_seconds": None,
            "error": str(exc),
        }
