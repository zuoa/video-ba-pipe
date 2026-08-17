from app.core.message_queue_config import MessageQueueConfig
from app.core import message_queue_publisher


def _reset_selector_state(monkeypatch):
    monkeypatch.setattr(message_queue_publisher, "_last_selector_fingerprint", None)


def test_disabled_message_queue_does_not_publish(monkeypatch):
    _reset_selector_state(monkeypatch)
    calls = []
    monkeypatch.setattr(
        message_queue_publisher,
        "get_message_queue_config",
        lambda: MessageQueueConfig(enabled=False, provider="mqtt"),
    )
    monkeypatch.setattr(message_queue_publisher, "publish_alert_to_mqtt", lambda data: calls.append("mqtt"))
    monkeypatch.setattr(message_queue_publisher, "publish_alert_to_rabbitmq", lambda data: calls.append("rabbitmq"))

    assert message_queue_publisher.publish_alert_to_mq({}) is False
    assert calls == []


def test_mqtt_provider_never_double_publishes(monkeypatch):
    _reset_selector_state(monkeypatch)
    calls = []
    monkeypatch.setattr(
        message_queue_publisher,
        "get_message_queue_config",
        lambda: MessageQueueConfig(enabled=True, provider="mqtt"),
    )
    monkeypatch.setattr(message_queue_publisher, "publish_alert_to_mqtt", lambda data: calls.append("mqtt") or True)
    monkeypatch.setattr(message_queue_publisher, "publish_alert_to_rabbitmq", lambda data: calls.append("rabbitmq") or True)

    assert message_queue_publisher.publish_alert_to_mq({}) is True
    assert calls == ["mqtt"]


def test_rabbitmq_provider_never_double_publishes(monkeypatch):
    _reset_selector_state(monkeypatch)
    calls = []
    monkeypatch.setattr(
        message_queue_publisher,
        "get_message_queue_config",
        lambda: MessageQueueConfig(enabled=True, provider="rabbitmq"),
    )
    monkeypatch.setattr(message_queue_publisher, "publish_alert_to_mqtt", lambda data: calls.append("mqtt") or True)
    monkeypatch.setattr(message_queue_publisher, "publish_alert_to_rabbitmq", lambda data: calls.append("rabbitmq") or True)

    assert message_queue_publisher.publish_alert_to_mq({}) is True
    assert calls == ["rabbitmq"]


def test_http_provider_never_double_publishes(monkeypatch):
    _reset_selector_state(monkeypatch)
    calls = []
    monkeypatch.setattr(
        message_queue_publisher,
        "get_message_queue_config",
        lambda: MessageQueueConfig(enabled=True, provider="http"),
    )
    monkeypatch.setattr(message_queue_publisher, "publish_alert_to_mqtt", lambda data: calls.append("mqtt") or True)
    monkeypatch.setattr(message_queue_publisher, "publish_alert_to_rabbitmq", lambda data: calls.append("rabbitmq") or True)
    monkeypatch.setattr(message_queue_publisher, "publish_alert_to_http", lambda data: calls.append("http") or True)

    assert message_queue_publisher.publish_alert_to_mq({}) is True
    assert calls == ["http"]


def test_selector_changes_disconnect_inactive_publishers(monkeypatch):
    _reset_selector_state(monkeypatch)
    selectors = iter((
        MessageQueueConfig(enabled=True, provider="mqtt"),
        MessageQueueConfig(enabled=True, provider="rabbitmq"),
        MessageQueueConfig(enabled=False, provider="rabbitmq"),
    ))
    disconnects = []
    monkeypatch.setattr(message_queue_publisher, "get_message_queue_config", lambda: next(selectors))
    monkeypatch.setattr(message_queue_publisher, "publish_alert_to_mqtt", lambda data: True)
    monkeypatch.setattr(message_queue_publisher, "publish_alert_to_rabbitmq", lambda data: True)
    monkeypatch.setattr(message_queue_publisher, "reload_mqtt_publisher", lambda: disconnects.append("mqtt"))
    monkeypatch.setattr(message_queue_publisher, "reload_rabbitmq_publisher", lambda: disconnects.append("rabbitmq"))

    assert message_queue_publisher.publish_alert_to_mq({}) is True
    assert message_queue_publisher.publish_alert_to_mq({}) is True
    assert message_queue_publisher.publish_alert_to_mq({}) is False
    assert disconnects == ["rabbitmq", "mqtt", "mqtt", "rabbitmq"]
