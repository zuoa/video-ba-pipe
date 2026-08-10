import json
import types

import pytest

from app.core.mqtt_config import MqttConfig, normalize_mqtt_config, validate_mqtt_config
from app.core import mqtt_publisher


def test_mqtt_config_uses_page_defaults_and_preserves_password():
    config = normalize_mqtt_config(
        {"host": " broker.local ", "password": "", "topic_prefix": "/video/alert/"},
        existing_password="saved-secret",
    )

    assert config.host == "broker.local"
    assert config.password == "saved-secret"
    assert config.topic_prefix == "video/alert"


def test_mqtt_default_does_not_embed_broker_password():
    assert MqttConfig().password == ""


@pytest.mark.parametrize("topic", ["video/+", "video/#", "bad\x00topic", ""])
def test_mqtt_topic_prefix_rejects_invalid_values(topic):
    with pytest.raises(ValueError):
        validate_mqtt_config(MqttConfig(topic_prefix=topic))


def test_explicit_empty_host_and_topic_are_not_silently_defaulted():
    config = normalize_mqtt_config({"host": "", "topic_prefix": ""})

    with pytest.raises(ValueError, match="主机地址"):
        validate_mqtt_config(config)


def test_build_alert_topic_sanitizes_dynamic_segments():
    topic = mqtt_publisher.build_alert_topic(
        MqttConfig(topic_prefix="video/alert"),
        {"node_id": "Box/01", "alert_type": "person+#"},
    )

    assert topic == "video/alert/box-01/person--"


def test_publish_uses_qos_one_and_is_not_retained(monkeypatch):
    config = MqttConfig()
    calls = []

    class PublishInfo:
        rc = 0

        def wait_for_publish(self, timeout):
            calls.append(("wait", timeout))

        def is_published(self):
            return True

    class Client:
        def publish(self, topic, payload, qos, retain):
            calls.append((topic, json.loads(payload), qos, retain))
            return PublishInfo()

    monkeypatch.setattr(mqtt_publisher, "mqtt", types.SimpleNamespace(MQTT_ERR_SUCCESS=0))
    publisher = mqtt_publisher.MQTTPublisher(config_provider=lambda: config)
    publisher._client = Client()
    publisher._config_fingerprint = publisher._fingerprint(config)
    publisher._connected.set()

    assert publisher.publish_alert({"node_id": "box-1", "alert_type": "person"}) is True
    assert calls[0][0] == "video/alert/box-1/person"
    assert calls[0][2:] == (1, False)
    assert calls[1] == ("wait", config.publish_timeout_seconds)


def test_publish_fails_when_puback_times_out(monkeypatch):
    config = MqttConfig()

    class PublishInfo:
        rc = 0

        def wait_for_publish(self, timeout):
            return None

        def is_published(self):
            return False

    client = types.SimpleNamespace(publish=lambda *args, **kwargs: PublishInfo())
    monkeypatch.setattr(mqtt_publisher, "mqtt", types.SimpleNamespace(MQTT_ERR_SUCCESS=0))
    publisher = mqtt_publisher.MQTTPublisher(config_provider=lambda: config)
    publisher._client = client
    publisher._config_fingerprint = publisher._fingerprint(config)
    publisher._connected.set()

    assert publisher.publish_alert({"node_id": "box-1", "alert_type": "person"}) is False
