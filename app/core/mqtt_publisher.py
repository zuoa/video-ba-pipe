"""MQTT QoS 1 alert publisher."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict
from typing import Any, Callable, Dict, Optional, Tuple

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - optional in lightweight test images
    mqtt = None

from app.core.mqtt_config import MqttConfig, get_mqtt_config, validate_mqtt_config
from app.core.node_identity import get_node_id


logger = logging.getLogger(__name__)


def _topic_segment(value: Any) -> str:
    normalized = str(value or "unknown").strip().lower()
    for character in ("/", "+", "#", "\x00"):
        normalized = normalized.replace(character, "-")
    return normalized or "unknown"


def build_alert_topic(config: MqttConfig, alert_data: Dict[str, Any]) -> str:
    prefix = config.topic_prefix.strip().strip("/")
    return "/".join(
        (prefix, _topic_segment(alert_data.get("node_id")), _topic_segment(alert_data.get("alert_type")))
    )


class MQTTPublisher:
    def __init__(self, config_provider: Optional[Callable[[], MqttConfig]] = None):
        self._config_provider = config_provider or get_mqtt_config
        self._client = None
        self._connected = threading.Event()
        self._lock = threading.RLock()
        self._config_fingerprint: Optional[Tuple[Any, ...]] = None

    @staticmethod
    def _fingerprint(config: MqttConfig) -> Tuple[Any, ...]:
        return tuple(asdict(config).items())

    def _new_client(self):
        client_id = f"video-ba-{_topic_segment(get_node_id())}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        if hasattr(mqtt, "CallbackAPIVersion"):
            return mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
                protocol=mqtt.MQTTv311,
            )
        return mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)

    def connect(self) -> bool:
        if mqtt is None:
            logger.warning("paho-mqtt 未安装，无法连接 MQTT")
            return False
        config = self._config_provider()
        validate_mqtt_config(config)
        fingerprint = self._fingerprint(config)

        with self._lock:
            if self._connected.is_set() and fingerprint == self._config_fingerprint:
                return True
            self.disconnect()
            self._connected.clear()
            client = self._new_client()

            def on_connect(_client, _userdata, _flags, reason_code, *args):
                if getattr(reason_code, "value", reason_code) == 0:
                    self._connected.set()
                else:
                    logger.error("MQTT 连接被拒绝，reason_code=%s", reason_code)

            def on_disconnect(_client, _userdata, *args):
                self._connected.clear()

            client.on_connect = on_connect
            client.on_disconnect = on_disconnect
            client.reconnect_delay_set(min_delay=1, max_delay=30)
            if config.username:
                client.username_pw_set(config.username, config.password)
            self._client = client
            try:
                client.connect(config.host, config.port, config.keepalive_seconds)
                client.loop_start()
                if not self._connected.wait(config.connection_timeout_seconds):
                    logger.error("连接 MQTT %s:%s 超时", config.host, config.port)
                    self.disconnect()
                    return False
                self._config_fingerprint = fingerprint
                logger.info("成功连接到 MQTT %s:%s", config.host, config.port)
                return True
            except Exception as exc:
                logger.error("连接 MQTT 失败: %s", exc)
                self.disconnect()
                return False

    def disconnect(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
            self._connected.clear()
            self._config_fingerprint = None
            if client is None:
                return
            try:
                client.disconnect()
            except Exception:
                pass
            try:
                client.loop_stop()
            except Exception:
                pass

    def publish_alert(self, alert_data: Dict[str, Any]) -> bool:
        config = self._config_provider()
        if self._fingerprint(config) != self._config_fingerprint or not self._connected.is_set():
            if not self.connect():
                return False
            config = self._config_provider()

        try:
            payload = json.dumps(alert_data, ensure_ascii=False, default=str)
            topic = build_alert_topic(config, alert_data)
            info = self._client.publish(topic, payload=payload, qos=1, retain=False)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error(
                    "MQTT 发布失败，broker=%s:%s topic=%s rc=%s",
                    config.host,
                    config.port,
                    topic,
                    info.rc,
                )
                return False
            info.wait_for_publish(timeout=config.publish_timeout_seconds)
            if not info.is_published():
                logger.error(
                    "等待 MQTT PUBACK 超时，broker=%s:%s topic=%s",
                    config.host,
                    config.port,
                    topic,
                )
                return False
            logger.info(
                "成功发布预警消息到 MQTT，broker=%s:%s topic=%s",
                config.host,
                config.port,
                topic,
            )
            return True
        except Exception as exc:
            logger.error(
                "发布 MQTT 预警消息失败，broker=%s:%s: %s",
                config.host,
                config.port,
                exc,
            )
            return False


mqtt_publisher = MQTTPublisher()


def reload_mqtt_publisher() -> None:
    mqtt_publisher.disconnect()


def publish_alert_to_mqtt(alert_data: Dict[str, Any]) -> bool:
    return mqtt_publisher.publish_alert(alert_data)
