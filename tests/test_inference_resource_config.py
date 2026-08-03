import json
import time

import pytest

from app.core import inference_resource_config as resource_config
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


def test_normalize_inference_resource_config_validates_ranges():
    with pytest.raises(ValueError, match="queue_size"):
        normalize_inference_resource_config({"queue_size": 0}, strict=True)

    config = normalize_inference_resource_config({
        "shared_inference_enabled": "true",
        "inference_admission_enabled": "false",
        "queue_size": 8,
        "batch_wait_ms": 2.5,
    })
    assert config.shared_inference_enabled is True
    assert config.inference_admission_enabled is False
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
    assert environment["OOM_CIRCUIT_BREAKER_ENABLED"] == "false"
    assert environment["OOM_CIRCUIT_FAILURE_THRESHOLD"] == "5"
    assert environment["OOM_CIRCUIT_OPEN_SECONDS"] == "321"
    assert environment["OOM_CIRCUIT_STABLE_RESET_SECONDS"] == "654"
    assert environment["OOM_RESTART_BACKOFF_MAX_SECONDS"] == "87"
