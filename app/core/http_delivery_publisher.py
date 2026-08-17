"""Synchronous HTTP sender used by the persistent alert outbox worker."""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Callable, Dict, Optional, Tuple

import requests

from app.core.http_delivery_config import (
    HttpDeliveryConfig,
    get_http_delivery_config,
    validate_http_header_value,
    validate_http_delivery_config,
)
from app.core.node_identity import get_node_id


logger = logging.getLogger(__name__)


def build_http_headers(config: HttpDeliveryConfig, event: Dict[str, Any]) -> Dict[str, str]:
    headers = dict(config.custom_headers)
    headers["Content-Type"] = "application/json"
    if config.auth_type == "bearer":
        token = get_node_id() if config.use_node_id_as_token else config.bearer_token
        headers["Authorization"] = f"Bearer {token}"
    headers["X-VideoBA-Event-Id"] = str(event.get("event_id") or "")
    headers["X-VideoBA-Event-Type"] = str(event.get("event_type") or "")
    if event.get("test") is True:
        headers["X-VideoBA-Test"] = "true"
    for name, value in headers.items():
        validate_http_header_value(name, value)
    return headers


class HttpDeliveryPublisher:
    def __init__(
        self,
        config_provider: Optional[Callable[[], HttpDeliveryConfig]] = None,
        *,
        session: Any = None,
    ):
        self._config_provider = config_provider or get_http_delivery_config
        self._session = session or requests.Session()
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            try:
                if self._session is not None:
                    self._session.close()
            except Exception:
                pass
            self._session = None

    def publish_alert(self, alert_data: Dict[str, Any]) -> bool:
        config = self._config_provider()
        try:
            validate_http_delivery_config(config)
            headers = build_http_headers(config, alert_data)
            with self._lock:
                if self._session is None:
                    self._session = requests.Session()
                response = self._session.post(
                    config.endpoint_url,
                    headers=headers,
                    json=alert_data,
                    timeout=config.timeout_seconds,
                    allow_redirects=False,
                )
            try:
                status_code = int(response.status_code)
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            if 200 <= status_code < 300:
                logger.info(
                    "成功投递 HTTP 预警消息，endpoint=%s event_id=%s status=%s",
                    config.endpoint_url,
                    alert_data.get("event_id"),
                    status_code,
                )
                return True
            logger.error(
                "HTTP 预警投递失败，endpoint=%s event_id=%s status=%s",
                config.endpoint_url,
                alert_data.get("event_id"),
                status_code,
            )
            return False
        except (requests.Timeout, requests.ConnectionError) as exc:
            logger.error("HTTP 预警投递连接失败或超时，endpoint=%s: %s", config.endpoint_url, exc)
            return False
        except (requests.RequestException, ValueError) as exc:
            logger.error("HTTP 预警投递失败，endpoint=%s: %s", config.endpoint_url, exc)
            return False


def build_http_test_event() -> Dict[str, Any]:
    node_id = get_node_id()
    event_id = f"{node_id}:system-test:{uuid.uuid4().hex}"
    return {
        "event_id": event_id,
        "event_type": "system.test",
        "test": True,
        "source": "video-ba-pipe",
        "node_id": node_id,
        "external_alert_id": f"{node_id}-test",
        "alert_id": 0,
        "alert_type": "system_test",
        "alert_level": "info",
        "alert_message": "VideoBA HTTP 消息投递测试",
        "media_delivery_mode": "url",
        "media": {"status": "unavailable", "image": None},
    }


def test_http_delivery_connection(config: HttpDeliveryConfig) -> Tuple[bool, str]:
    publisher = HttpDeliveryPublisher(config_provider=lambda: config)
    try:
        event = build_http_test_event()
        if publisher.publish_alert(event):
            return True, f"测试事件已成功投递到 {config.endpoint_url}"
        return False, f"测试事件投递到 {config.endpoint_url} 失败"
    finally:
        publisher.close()


http_delivery_publisher = HttpDeliveryPublisher()


def reload_http_delivery_publisher() -> None:
    http_delivery_publisher.close()


def publish_alert_to_http(alert_data: Dict[str, Any]) -> bool:
    return http_delivery_publisher.publish_alert(alert_data)
