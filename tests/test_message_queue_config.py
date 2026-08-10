import types

from app.core import message_queue_config
from app.core.message_queue_config import MessageQueueConfig, normalize_message_queue_config


def test_new_install_defaults_to_disabled_mqtt(monkeypatch):
    monkeypatch.setattr(message_queue_config.SystemSetting, "get_or_none", lambda *args, **kwargs: None)

    assert message_queue_config.get_message_queue_config() == MessageQueueConfig(
        enabled=False,
        provider="mqtt",
    )


def test_legacy_rabbitmq_config_is_selected(monkeypatch):
    records = iter((None, types.SimpleNamespace(value="{}")))
    monkeypatch.setattr(
        message_queue_config.SystemSetting,
        "get_or_none",
        lambda *args, **kwargs: next(records),
    )
    monkeypatch.setattr(
        message_queue_config,
        "get_rabbitmq_config",
        lambda: types.SimpleNamespace(enabled=True),
    )

    assert message_queue_config.get_message_queue_config() == MessageQueueConfig(
        enabled=True,
        provider="rabbitmq",
    )


def test_invalid_provider_is_rejected():
    try:
        normalize_message_queue_config({"provider": "kafka"})
    except ValueError as exc:
        assert "mqtt 或 rabbitmq" in str(exc)
    else:
        raise AssertionError("invalid provider should fail")
