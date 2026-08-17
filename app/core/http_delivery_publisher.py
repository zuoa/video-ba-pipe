"""Synchronous HTTP sender used by the persistent alert outbox worker."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
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
_MAX_ERROR_RESPONSE_CHARS = 2000


def serialize_http_event(event: Dict[str, Any]) -> bytes:
    return json.dumps(
        event,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_hmac_canonical_message(
    *,
    node_id: str,
    timestamp: str,
    nonce: str,
    event_id: str,
    event_type: str,
    test_marker: str,
    body: bytes,
) -> bytes:
    body_digest = hashlib.sha256(body).hexdigest()
    return "\n".join(
        (node_id, timestamp, nonce, event_id, event_type, test_marker, body_digest)
    ).encode("utf-8")


def build_http_headers(
    config: HttpDeliveryConfig,
    event: Dict[str, Any],
    *,
    body: Optional[bytes] = None,
    timestamp: Optional[int] = None,
    nonce: Optional[str] = None,
) -> Dict[str, str]:
    headers = dict(config.custom_headers)
    headers["Content-Type"] = "application/json"
    event_id = str(event.get("event_id") or "")
    event_type = str(event.get("event_type") or "")
    test_marker = "true" if event.get("test") is True else "false"
    headers["X-VideoBA-Event-Id"] = event_id
    headers["X-VideoBA-Event-Type"] = event_type
    headers["X-VideoBA-Test"] = test_marker
    body = body if body is not None else serialize_http_event(event)
    node_id = get_node_id()
    timestamp_text = str(int(time.time()) if timestamp is None else int(timestamp))
    nonce_text = str(nonce or uuid.uuid4().hex)
    canonical = build_hmac_canonical_message(
        node_id=node_id,
        timestamp=timestamp_text,
        nonce=nonce_text,
        event_id=event_id,
        event_type=event_type,
        test_marker=test_marker,
        body=body,
    )
    signature = hmac.new(
        config.hmac_secret.encode("utf-8"),
        canonical,
        hashlib.sha256,
    ).hexdigest()
    headers["X-VideoBA-Node-Id"] = node_id
    headers["X-VideoBA-Timestamp"] = timestamp_text
    headers["X-VideoBA-Nonce"] = nonce_text
    headers["X-VideoBA-Signature"] = f"sha256={signature}"
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
            body = serialize_http_event(alert_data)
            headers = build_http_headers(config, alert_data, body=body)
            with self._lock:
                if self._session is None:
                    self._session = requests.Session()
                response = self._session.post(
                    config.endpoint_url,
                    headers=headers,
                    data=body,
                    timeout=config.timeout_seconds,
                    allow_redirects=False,
                )
            try:
                status_code = int(response.status_code)
                response_text = str(getattr(response, "text", "") or "")
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
                "HTTP 预警投递失败，endpoint=%s event_id=%s status=%s response=%s",
                config.endpoint_url,
                alert_data.get("event_id"),
                status_code,
                response_text[:_MAX_ERROR_RESPONSE_CHARS].replace("\r", "\\r").replace("\n", "\\n"),
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
