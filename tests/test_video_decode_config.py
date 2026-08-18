import json

import pytest

import app.core.video_decode_config as video_decode_config
from app.core.video_decode_config import (
    VideoDecodeConfig,
    load_video_decode_config,
    normalize_video_decode_config,
    save_video_decode_config,
)


def test_video_decode_config_defaults_to_environment(monkeypatch):
    monkeypatch.setattr(video_decode_config, "DECODE_KEYFRAMES_ONLY", False)
    monkeypatch.setattr(
        video_decode_config.SystemSetting,
        "get_or_none",
        lambda *_args, **_kwargs: None,
    )

    config, source, database_available = load_video_decode_config()

    assert config == VideoDecodeConfig(decode_keyframes_only=False)
    assert source == "environment"
    assert database_available is True


def test_video_decode_config_loads_database_value(monkeypatch):
    record = type(
        "Setting",
        (),
        {"value": json.dumps({"decode_keyframes_only": True})},
    )()
    monkeypatch.setattr(
        video_decode_config.SystemSetting,
        "get_or_none",
        lambda *_args, **_kwargs: record,
    )

    config, source, database_available = load_video_decode_config()

    assert config.decode_keyframes_only is True
    assert source == "database"
    assert database_available is True


def test_normalize_video_decode_config_parses_boolean_strings():
    assert normalize_video_decode_config(
        {"decode_keyframes_only": "true"}
    ).decode_keyframes_only is True
    assert normalize_video_decode_config(
        {"decode_keyframes_only": "false"}
    ).decode_keyframes_only is False


@pytest.mark.parametrize(
    "data",
    [{}, {"decode_keyframes_only": "true"}, {"decode_keyframes_only": 1}],
)
def test_save_video_decode_config_requires_boolean(data):
    with pytest.raises(ValueError, match="必须是布尔值"):
        save_video_decode_config(data)


def test_save_video_decode_config_requires_json_object():
    with pytest.raises(ValueError, match="必须是 JSON 对象"):
        save_video_decode_config(None)


def test_save_video_decode_config_persists_value(monkeypatch):
    class FakeRecord:
        value = ""
        description = ""
        updated_at = None
        updated_by = ""

        def save(self):
            return 1

    record = FakeRecord()
    monkeypatch.setattr(
        video_decode_config.SystemSetting,
        "get_or_create",
        lambda **_kwargs: (record, True),
    )

    config = save_video_decode_config(
        {"decode_keyframes_only": True},
        updated_by="admin",
    )

    assert config.decode_keyframes_only is True
    assert json.loads(record.value) == {"decode_keyframes_only": True}
    assert record.updated_by == "admin"
