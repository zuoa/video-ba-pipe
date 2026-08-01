"""钉钉自定义机器人通知，包含加签和事件冷却。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import socket
import threading
import time
from datetime import datetime
from typing import Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from app import logger
from app.core.ops_notification_config import (
    OpsNotificationConfig,
    get_ops_notification_config,
)


DINGTALK_WEBHOOK_HOST = "oapi.dingtalk.com"
_last_sent_at: Dict[str, float] = {}
_send_lock = threading.Lock()


def validate_dingtalk_webhook_url(webhook_url: str) -> str:
    normalized = str(webhook_url or "").strip()
    parsed = urlparse(normalized)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if (
        parsed.scheme != "https"
        or parsed.hostname != DINGTALK_WEBHOOK_HOST
        or parsed.path.rstrip("/") != "/robot/send"
        or not query.get("access_token")
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Webhook 必须是钉钉官方 https://oapi.dingtalk.com/robot/send 地址")
    return normalized


def build_signed_webhook_url(webhook_url: str, secret: str, timestamp_ms: int) -> str:
    normalized = validate_dingtalk_webhook_url(webhook_url)
    if not secret:
        return normalized
    string_to_sign = f"{timestamp_ms}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    sign = base64.b64encode(digest).decode("ascii")
    parsed = urlparse(normalized)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({"timestamp": str(timestamp_ms), "sign": sign})
    return urlunparse(parsed._replace(query=urlencode(query)))


def send_dingtalk_text(
    config: OpsNotificationConfig,
    content: str,
    *,
    timeout_seconds: float = 5.0,
) -> None:
    url = build_signed_webhook_url(
        config.webhook_url,
        config.secret,
        int(time.time() * 1000),
    )
    response = requests.post(
        url,
        json={
            "msgtype": "text",
            "text": {"content": content},
            "at": {"isAtAll": False},
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errcode") != 0:
        raise RuntimeError(payload.get("errmsg") or f"钉钉返回错误码 {payload.get('errcode')}")


def notify_ops_event(
    event_key: str,
    title: str,
    details: str,
    *,
    config: Optional[OpsNotificationConfig] = None,
    force: bool = False,
) -> bool:
    config = config or get_ops_notification_config()
    if not config.enabled:
        return False

    now = time.monotonic()
    cooldown_seconds = config.cooldown_minutes * 60
    with _send_lock:
        last_sent = _last_sent_at.get(event_key, 0.0)
        if not force and now - last_sent < cooldown_seconds:
            return False

    content = (
        f"[VideoBA运维] {title}\n"
        f"设备: {socket.gethostname()}\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"{details}"
    )
    try:
        send_dingtalk_text(config, content)
    except Exception as exc:
        logger.error(f"发送钉钉运维通知失败 [{event_key}]: {exc}")
        return False

    with _send_lock:
        _last_sent_at[event_key] = now
    return True
