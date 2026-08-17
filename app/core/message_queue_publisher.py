"""Single-provider alert message queue dispatcher."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict

from app.core.message_queue_config import get_message_queue_config
from app.core.http_delivery_publisher import publish_alert_to_http, reload_http_delivery_publisher
from app.core.mqtt_publisher import publish_alert_to_mqtt, reload_mqtt_publisher
from app.core.rabbitmq_publisher import publish_alert_to_rabbitmq, reload_rabbitmq_publisher


logger = logging.getLogger(__name__)
_selector_lock = threading.Lock()
_last_selector_fingerprint = None


def _apply_selector_transition(enabled: bool, provider: str) -> None:
    """Disconnect inactive clients when this process observes selector changes."""
    global _last_selector_fingerprint
    fingerprint = (enabled, provider)
    with _selector_lock:
        if fingerprint == _last_selector_fingerprint:
            return
        if not enabled:
            reload_mqtt_publisher()
            reload_rabbitmq_publisher()
            reload_http_delivery_publisher()
        elif provider == "mqtt":
            reload_rabbitmq_publisher()
            reload_http_delivery_publisher()
        elif provider == "rabbitmq":
            reload_mqtt_publisher()
            reload_http_delivery_publisher()
        elif provider == "http":
            reload_mqtt_publisher()
            reload_rabbitmq_publisher()
        _last_selector_fingerprint = fingerprint


def publish_alert_to_mq(alert_data: Dict[str, Any]) -> bool:
    config = get_message_queue_config()
    _apply_selector_transition(config.enabled, config.provider)
    if not config.enabled:
        logger.debug("消息队列发布未启用")
        return False
    if config.provider == "mqtt":
        return publish_alert_to_mqtt(alert_data)
    if config.provider == "rabbitmq":
        return publish_alert_to_rabbitmq(alert_data)
    if config.provider == "http":
        return publish_alert_to_http(alert_data)
    logger.error("不支持的消息投递通道: %s", config.provider)
    return False


def reload_message_queue_publishers() -> None:
    global _last_selector_fingerprint
    reload_mqtt_publisher()
    reload_rabbitmq_publisher()
    reload_http_delivery_publisher()
    with _selector_lock:
        _last_selector_fingerprint = None
