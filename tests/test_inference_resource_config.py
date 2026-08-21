import json
import time

import pytest

from app.core import inference_resource_config as resource_config
from app.core.gpu_placement import GpuDeviceSnapshot
from app.core.inference_resource_config import (
    InferenceResourceConfig,
    effective_inference_resource_config,
    get_inference_resource_status,
    load_inference_resource_config,
    normalize_inference_resource_config,
    save_inference_resource_config,
)
from app.core.shared_inference import SharedInferenceServiceController


class _FakeRecord:
    def __init__(self, value=""):
        self.value = value
        self.description = None
        self.updated_at = None
        self.updated_by = None
        self.saved = False

    def save(self):
        self.saved = True


class _CapabilityGpuProvider:
    def __init__(self):
        self.error = None
        self.snapshots = [
            GpuDeviceSnapshot(0, "GPU-0", "GPU 0", 24000, 0, 24000, 0),
            GpuDeviceSnapshot(1, "GPU-1", "GPU 1", 24000, 0, 24000, 0),
        ]

    def devices(self):
        if self.error is not None:
            raise self.error
        return list(self.snapshots)

    def close(self):
        pass


def test_normalize_inference_resource_config_validates_ranges():
    with pytest.raises(ValueError, match="queue_size"):
        normalize_inference_resource_config({"queue_size": 0}, strict=True)

    config = normalize_inference_resource_config({
        "shared_inference_enabled": "true",
        "gpu_scheduling_enabled": "true",
        "gpu_allowed_devices": ["GPU-a", "1"],
        "gpu_memory_reserve_mb": 1536,
        "gpu_failure_mode": "reject",
        "inference_admission_enabled": "false",
        "queue_size": 8,
        "batch_wait_ms": 2.5,
    })
    assert config.shared_inference_enabled is True
    assert config.inference_admission_enabled is False
    assert config.gpu_scheduling_enabled is True
    assert config.gpu_allowed_devices == ("GPU-a", "1")
    assert config.gpu_memory_reserve_mb == 1536
    assert config.queue_size == 8
    assert config.batch_wait_ms == 2.5


@pytest.mark.parametrize("invalid_value", ["tru", "disabled", 2, [], {}])
def test_strict_normalization_rejects_invalid_booleans(invalid_value):
    with pytest.raises(ValueError, match="shared_inference_enabled 必须是布尔值"):
        normalize_inference_resource_config(
            {"shared_inference_enabled": invalid_value},
            strict=True,
        )


def test_save_rejects_invalid_boolean_before_database_write(monkeypatch):
    database_called = False

    def unexpected_database_write(**_kwargs):
        nonlocal database_called
        database_called = True
        return _FakeRecord(), True

    monkeypatch.setattr(
        resource_config.SystemSetting,
        "get_or_create",
        unexpected_database_write,
    )
    with pytest.raises(ValueError, match="inference_admission_enabled 必须是布尔值"):
        save_inference_resource_config({
            "inference_admission_enabled": "tru",
        })
    assert database_called is False


def test_effective_config_auto_downgrades_unsupported_features():
    requested = InferenceResourceConfig(
        shared_inference_enabled=True,
        inference_admission_enabled=True,
        oom_circuit_breaker_enabled=True,
    )
    effective = effective_inference_resource_config(requested, {
        "shared_ultralytics": False,
        "memory_admission": True,
        "oom_detection": False,
    })
    assert effective.shared_inference_enabled is False
    assert effective.inference_admission_enabled is True
    assert effective.oom_circuit_breaker_enabled is False
    assert effective.gpu_scheduling_enabled is False


def test_effective_config_enables_gpu_scheduler_only_with_shared_multi_gpu():
    requested = InferenceResourceConfig(
        shared_inference_enabled=True,
        gpu_scheduling_enabled=True,
    )

    effective = effective_inference_resource_config(requested, {
        "shared_ultralytics": True,
        "shared_ocr": True,
        "rknn_shared": False,
        "gpu_scheduling": True,
        "memory_admission": True,
        "oom_detection": False,
    })

    assert effective.shared_inference_enabled is True
    assert effective.gpu_scheduling_enabled is True


def test_effective_gpu_scheduler_implicitly_enables_shared_service():
    requested = InferenceResourceConfig(
        shared_inference_enabled=False,
        gpu_scheduling_enabled=True,
    )

    effective = effective_inference_resource_config(requested, {
        "shared_ultralytics": True,
        "shared_ocr": True,
        "rknn_shared": False,
        "gpu_scheduling": True,
        "memory_admission": True,
        "oom_detection": False,
    })

    assert effective.shared_inference_enabled is True
    assert effective.gpu_scheduling_enabled is True


def test_effective_gpu_scheduler_does_not_enable_shared_on_single_gpu():
    requested = InferenceResourceConfig(
        shared_inference_enabled=False,
        gpu_scheduling_enabled=True,
    )

    effective = effective_inference_resource_config(requested, {
        "shared_ultralytics": True,
        "shared_ocr": True,
        "rknn_shared": False,
        "gpu_scheduling": False,
        "memory_admission": True,
        "oom_detection": False,
    })

    assert effective.shared_inference_enabled is False
    assert effective.gpu_scheduling_enabled is False


def test_capability_refresh_preserves_last_gpu_topology_on_nvml_failure(
    monkeypatch,
):
    provider = _CapabilityGpuProvider()
    monkeypatch.setattr(resource_config.platform, "system", lambda: "Linux")
    monkeypatch.setattr(resource_config.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(resource_config, "_device_model", lambda: "")
    monkeypatch.setattr(resource_config, "_device_tree_compatible", lambda: "")
    monkeypatch.setattr(resource_config, "NvmlGpuProvider", lambda: provider)
    monkeypatch.setattr(resource_config, "_LAST_VISIBLE_NVIDIA_GPUS", None)

    initial = resource_config.detect_inference_capabilities()
    provider.error = RuntimeError("temporary NVML failure")
    refreshed = resource_config.detect_inference_capabilities()

    assert initial["gpu_scheduling"] is True
    assert refreshed["gpu_scheduling"] is True
    assert refreshed["nvidia_gpu_count"] == 2
    assert refreshed["nvidia_gpu_snapshot_stale"] is True
    assert "temporary NVML failure" in refreshed["nvidia_gpu_detection_error"]

    provider.error = None
    provider.snapshots = []
    removed = resource_config.detect_inference_capabilities()

    assert removed["gpu_scheduling"] is False
    assert removed["nvidia_gpu_count"] == 0
    assert removed["nvidia_gpu_snapshot_stale"] is False


def test_effective_config_allows_shared_service_for_ocr_only_host():
    requested = InferenceResourceConfig(shared_inference_enabled=True)

    effective = effective_inference_resource_config(requested, {
        "shared_ultralytics": False,
        "rknn_shared": False,
        "shared_ocr": True,
        "memory_admission": True,
        "oom_detection": False,
    })

    assert effective.shared_inference_enabled is True


def test_effective_config_allows_shared_service_for_rknn_only_host():
    requested = InferenceResourceConfig(shared_inference_enabled=True)

    effective = effective_inference_resource_config(requested, {
        "shared_ultralytics": False,
        "rknn_shared": True,
        "memory_admission": True,
        "oom_detection": False,
    })

    assert effective.shared_inference_enabled is True


def test_capabilities_detect_rk3588_from_device_tree_compatible(monkeypatch):
    monkeypatch.setattr(resource_config.platform, "system", lambda: "Linux")
    monkeypatch.setattr(resource_config.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(resource_config, "_device_model", lambda: "FriendlyElec NanoPC-T6")
    monkeypatch.setattr(
        resource_config,
        "_device_tree_compatible",
        lambda: "friendlyarm,nanopc-t6,rockchip,rk3588",
    )
    monkeypatch.setattr(resource_config, "SHARED_RKNN_ENABLED", True)

    capabilities = resource_config.detect_inference_capabilities()

    assert capabilities["platform"] == "rk3588"
    assert capabilities["rknn_shared"] is True
    assert capabilities["device_compatible"].endswith("rockchip,rk3588")


def test_rknn_shared_capability_is_disabled_without_explicit_opt_in(monkeypatch):
    monkeypatch.setattr(resource_config.platform, "system", lambda: "Linux")
    monkeypatch.setattr(resource_config.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(resource_config, "_device_model", lambda: "RK3588")
    monkeypatch.setattr(resource_config, "_device_tree_compatible", lambda: "rockchip,rk3588")
    monkeypatch.setattr(resource_config, "SHARED_RKNN_ENABLED", False)

    capabilities = resource_config.detect_inference_capabilities()

    assert capabilities["platform"] == "rk3588"
    assert capabilities["rknn_shared"] is False


def test_load_uses_environment_when_database_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        resource_config.SystemSetting,
        "get_or_none",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    config, source, database_available = load_inference_resource_config()
    assert config == resource_config.environment_inference_resource_config()
    assert source == "environment_fallback"
    assert database_available is False


def test_first_load_persists_environment_defaults(monkeypatch):
    record = _FakeRecord()
    monkeypatch.setattr(
        resource_config.SystemSetting,
        "get_or_none",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        resource_config.SystemSetting,
        "get_or_create",
        lambda **_kwargs: (record, True),
    )
    config, source, database_available = load_inference_resource_config(
        initialize=True
    )
    assert json.loads(record.value) == config.to_dict()
    assert record.saved is True
    assert source == "environment_initialized"
    assert database_available is True


def test_save_writes_normalized_database_config(monkeypatch):
    record = _FakeRecord()
    monkeypatch.setattr(
        resource_config.SystemSetting,
        "get_or_create",
        lambda **_kwargs: (record, False),
    )
    config = save_inference_resource_config({
        "shared_inference_enabled": True,
        "queue_size": 6,
    }, updated_by="admin")
    assert json.loads(record.value) == config.to_dict()
    assert record.updated_by == "admin"
    assert record.saved is True


def test_status_marks_stale_worker_offline(monkeypatch):
    record = _FakeRecord(json.dumps({
        "reported_at_epoch": time.time() - 30,
        "service_running": True,
    }))
    monkeypatch.setattr(
        resource_config.SystemSetting,
        "get_or_none",
        lambda *_args, **_kwargs: record,
    )
    status = get_inference_resource_status()
    assert status["worker_online"] is False
    assert status["status_age_seconds"] >= 29


def test_shared_service_controller_exports_database_runtime_values():
    controller = SharedInferenceServiceController(
        "/tmp/test-inference.sock",
        queue_size=7,
        batch_max_size=3,
        batch_wait_ms=8.5,
        request_timeout_seconds=42,
        idle_seconds=99,
        gpu_scheduling_enabled=True,
        gpu_allowed_devices=("GPU-a", "GPU-b"),
        gpu_memory_reserve_mb=1536,
        gpu_new_model_default_mb=3072,
        gpu_model_memory_margin_percent=35,
        gpu_oom_cooldown_seconds=75,
        gpu_nvml_stale_seconds=45,
        gpu_failure_mode="reject",
        oom_circuit_enabled=False,
        oom_failure_threshold=5,
        oom_open_seconds=321,
        oom_stable_reset_seconds=654,
        oom_backoff_cap_seconds=87,
    )
    environment = controller._service_environment()
    assert environment["SHARED_INFERENCE_ENABLED"] == "true"
    assert environment["SHARED_INFERENCE_QUEUE_SIZE"] == "7"
    assert environment["SHARED_INFERENCE_BATCH_MAX_SIZE"] == "3"
    assert environment["SHARED_INFERENCE_BATCH_WAIT_MS"] == "8.5"
    assert environment["SHARED_INFERENCE_REQUEST_TIMEOUT_SECONDS"] == "42.0"
    assert environment["SHARED_INFERENCE_IDLE_SECONDS"] == "99"
    assert environment["GPU_SCHEDULING_ENABLED"] == "true"
    assert environment["GPU_ALLOWED_DEVICES"] == "GPU-a,GPU-b"
    assert environment["GPU_MEMORY_RESERVE_MB"] == "1536"
    assert environment["GPU_NEW_MODEL_DEFAULT_MB"] == "3072"
    assert environment["GPU_MODEL_MEMORY_MARGIN_PERCENT"] == "35.0"
    assert environment["GPU_OOM_COOLDOWN_SECONDS"] == "75"
    assert environment["GPU_NVML_STALE_SECONDS"] == "45"
    assert environment["GPU_SCHEDULING_FAILURE_MODE"] == "reject"
    assert environment["OOM_CIRCUIT_BREAKER_ENABLED"] == "false"
    assert environment["OOM_CIRCUIT_FAILURE_THRESHOLD"] == "5"
    assert environment["OOM_CIRCUIT_OPEN_SECONDS"] == "321"
    assert environment["OOM_CIRCUIT_STABLE_RESET_SECONDS"] == "654"
    assert environment["OOM_RESTART_BACKOFF_MAX_SECONDS"] == "87"
