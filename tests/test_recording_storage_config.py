import pytest

from app.core.recording_storage_config import (
    MAX_RECORDING_FPS,
    MIN_STORAGE_LIMIT_GB,
    RecordingStorageConfig,
    load_recording_storage_config_with_status,
    normalize_recording_storage_config,
    save_recording_storage_config,
)


def test_recording_defaults_are_safe():
    config = RecordingStorageConfig()

    assert config.recording_enabled is False
    assert config.video_max_gb > 0
    assert config.image_max_gb > 0
    assert config.min_free_gb > 0
    assert config.stop_recording_percent == 80
    assert config.metadata_only_percent == 90


def test_normalize_recording_storage_config_clamps_unsafe_values():
    config = normalize_recording_storage_config({
        "recording_enabled": True,
        "pre_alert_seconds": -1,
        "post_alert_seconds": 999,
        "recording_fps": 100,
        "video_max_gb": 0,
        "image_max_gb": "invalid",
        "min_free_gb": 0,
        "stop_recording_percent": 0,
        "metadata_only_percent": 100,
    })

    assert config.recording_enabled is True
    assert config.pre_alert_seconds == 0
    assert config.post_alert_seconds == 300
    assert config.recording_fps == MAX_RECORDING_FPS
    assert config.video_max_gb == MIN_STORAGE_LIMIT_GB
    assert config.image_max_gb == RecordingStorageConfig().image_max_gb
    assert config.min_free_gb == MIN_STORAGE_LIMIT_GB
    assert config.stop_recording_percent == 1
    assert config.metadata_only_percent == 99


def test_normalize_recording_storage_config_parses_false_string():
    config = normalize_recording_storage_config({"recording_enabled": "false"})

    assert config.recording_enabled is False


def test_load_recording_storage_config_reports_persisted_config(monkeypatch):
    persisted = RecordingStorageConfig(video_max_gb=100).to_dict()
    monkeypatch.setattr(
        "app.core.recording_storage_config.SystemSetting.get_or_none",
        lambda *_args, **_kwargs: type(
            "Setting",
            (),
            {"value": __import__("json").dumps(persisted)},
        )(),
    )

    config, database_available = load_recording_storage_config_with_status()

    assert database_available is True
    assert config.video_max_gb == 100


def test_load_recording_storage_config_reports_database_failure(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        "app.core.recording_storage_config.SystemSetting.get_or_none",
        fail,
    )

    config, database_available = load_recording_storage_config_with_status(
        log_failure=False,
    )

    assert database_available is False
    assert config == RecordingStorageConfig()


@pytest.mark.parametrize(
    "data,error_text",
    [
        # 录像关闭时缺省所有字段：录像相关字段不再报错，最先命中始终必填的录像容量上限
        ({}, "录像容量上限"),
        # 录像启用时，录像帧率仍需校验
        ({
            "recording_enabled": True,
            "pre_alert_seconds": 5,
            "post_alert_seconds": 5,
            "recording_fps": 0,
            "video_max_gb": 20,
            "image_max_gb": 10,
            "min_free_gb": 10,
            "stop_recording_percent": 80,
            "metadata_only_percent": 90,
        }, "录像帧率"),
        ({
            "pre_alert_seconds": 5,
            "post_alert_seconds": 5,
            "recording_fps": 10,
            "video_max_gb": 20,
            "image_max_gb": 10,
            "min_free_gb": 10,
            "stop_recording_percent": 90,
            "metadata_only_percent": 80,
        }, "必须高于"),
    ],
)
def test_save_recording_storage_config_rejects_invalid_values(data, error_text):
    with pytest.raises(ValueError, match=error_text):
        save_recording_storage_config(data)


def test_save_recording_storage_config_allows_missing_recording_fields_when_disabled(monkeypatch):
    """录像关闭时缺省前/后录像时长与帧率不应报错，回退到安全默认值（回归测试）。"""

    class FakeRecord:
        def __init__(self):
            self.value = ""
            self.description = ""
            self.updated_at = None
            self.updated_by = ""

        def save(self):
            return None

    monkeypatch.setattr(
        "app.core.recording_storage_config.SystemSetting.get_or_create",
        lambda *_, **__: (FakeRecord(), False),
    )

    config = save_recording_storage_config({
        "recording_enabled": False,
        "video_max_gb": 20,
        "image_max_gb": 10,
        "min_free_gb": 10,
        "stop_recording_percent": 80,
        "metadata_only_percent": 90,
    })

    assert config.recording_enabled is False
    assert config.pre_alert_seconds == RecordingStorageConfig().pre_alert_seconds
    assert config.post_alert_seconds == RecordingStorageConfig().post_alert_seconds
    assert config.recording_fps == RecordingStorageConfig().recording_fps
