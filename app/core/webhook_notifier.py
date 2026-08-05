"""Outbound alert webhook adapters and bounded asynchronous delivery."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse

import requests

from app import logger
from app.core.dingtalk_notifier import build_signed_webhook_url
from app.core.public_media_config import build_public_media_url, get_public_media_config


SUPPORTED_WEBHOOK_PROVIDERS = {"generic", "dingtalk", "bark"}
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")
_BLOCKED_HEADERS = {
    "content-length",
    "host",
    "connection",
    "transfer-encoding",
    "upgrade",
}
_MAX_REQUEST_BYTES = 512 * 1024
_MAX_RESPONSE_BYTES = 64 * 1024
_DEFAULT_ALLOWED_WEBHOOK_HOSTS = "oapi.dingtalk.com,api.day.app"


class WebhookConfigError(ValueError):
    """Raised when a webhook node configuration is invalid."""


class WebhookDeliveryError(RuntimeError):
    """Provider-level delivery failure with retry classification."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class PreparedWebhookRequest:
    url: str
    headers: Dict[str, str]
    payload: Dict[str, Any]


def _get_dotted_value(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            raise WebhookConfigError(f"Webhook 模板字段不存在: {path}")
        current = current[segment]
    return current


def render_webhook_template(value: Any, event: Mapping[str, Any]) -> Any:
    """Recursively render ``{{dotted.path}}`` placeholders.

    A string containing only one placeholder preserves the referenced JSON type.
    Embedded placeholders are converted to text.
    """
    if isinstance(value, dict):
        return {str(key): render_webhook_template(item, event) for key, item in value.items()}
    if isinstance(value, list):
        return [render_webhook_template(item, event) for item in value]
    if not isinstance(value, str):
        return value

    full_match = _PLACEHOLDER_RE.fullmatch(value)
    if full_match:
        return _get_dotted_value(event, full_match.group(1))

    return _PLACEHOLDER_RE.sub(
        lambda match: _stringify_template_value(_get_dotted_value(event, match.group(1))),
        value,
    )


def _stringify_template_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _canonical_hostname(hostname: str) -> str:
    try:
        return hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise WebhookConfigError("Webhook Host 格式无效") from exc


def _allowed_webhook_destinations() -> set[str]:
    configured = os.getenv("WEBHOOK_ALLOWED_HOSTS", _DEFAULT_ALLOWED_WEBHOOK_HOSTS)
    return {
        item.strip().rstrip(".").lower()
        for item in configured.split(",")
        if item.strip()
    }


def _validate_webhook_destination(parsed: Any) -> None:
    hostname = _canonical_hostname(parsed.hostname or "")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise WebhookConfigError("Webhook 端点端口无效") from exc

    allowed = _allowed_webhook_destinations()
    default_port = 443 if parsed.scheme == "https" else 80
    host_allowed = hostname in allowed and port == default_port
    host_port_allowed = f"{hostname}:{port}" in allowed
    if not host_allowed and not host_port_allowed:
        raise WebhookConfigError(
            "Webhook 目标不在管理员白名单 WEBHOOK_ALLOWED_HOSTS 中"
        )


def _validate_http_url(url: str, field_name: str = "endpoint_url") -> str:
    normalized = str(url or "").strip()
    parsed = urlparse(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise WebhookConfigError(f"{field_name} 必须是合法的 HTTP/HTTPS 地址，且不能包含 userinfo")
    if field_name == "endpoint_url":
        _validate_webhook_destination(parsed)
    return normalized


def _normalize_headers(raw_headers: Any) -> Dict[str, str]:
    if raw_headers in (None, ""):
        return {}
    if isinstance(raw_headers, dict):
        entries = [
            {"name": name, "value": value}
            for name, value in raw_headers.items()
        ]
    elif isinstance(raw_headers, list):
        entries = raw_headers
    else:
        raise WebhookConfigError("headers 必须是对象或请求头数组")

    headers: Dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise WebhookConfigError("请求头配置项必须是对象")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise WebhookConfigError("请求头名称不能为空")
        if name.lower() in _BLOCKED_HEADERS:
            raise WebhookConfigError(f"不允许配置请求头: {name}")
        if "\r" in name or "\n" in name:
            raise WebhookConfigError("请求头名称包含非法字符")
        value = str(entry.get("value") or "")
        if "\r" in value or "\n" in value:
            raise WebhookConfigError(f"请求头 {name} 包含非法字符")
        headers[name] = value
    return headers


def validate_webhook_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(config, Mapping):
        raise WebhookConfigError("Webhook config 必须是对象")

    normalized = dict(config)
    provider = str(normalized.get("provider") or "generic").strip().lower()
    if provider not in SUPPORTED_WEBHOOK_PROVIDERS:
        raise WebhookConfigError(f"不支持的 Webhook 协议: {provider}")
    normalized["provider"] = provider

    endpoint_url = _validate_http_url(normalized.get("endpoint_url"))
    normalized["endpoint_url"] = endpoint_url

    timeout_seconds = float(normalized.get("timeout_seconds") or 5)
    if timeout_seconds < 1 or timeout_seconds > 30:
        raise WebhookConfigError("timeout_seconds 必须在 1 到 30 秒之间")
    normalized["timeout_seconds"] = timeout_seconds

    max_attempts = int(normalized.get("max_attempts") or 3)
    if max_attempts < 1 or max_attempts > 5:
        raise WebhookConfigError("max_attempts 必须在 1 到 5 之间")
    normalized["max_attempts"] = max_attempts

    retry_backoff = float(normalized.get("retry_backoff_seconds") or 1)
    if retry_backoff < 0.1 or retry_backoff > 30:
        raise WebhookConfigError("retry_backoff_seconds 必须在 0.1 到 30 秒之间")
    normalized["retry_backoff_seconds"] = retry_backoff

    normalized["headers"] = _normalize_headers(normalized.get("headers"))
    payload_template = normalized.get("payload_template")
    if payload_template not in (None, {}) and not isinstance(payload_template, dict):
        raise WebhookConfigError("payload_template 必须是 JSON 对象")

    provider_options = normalized.get("provider_options") or {}
    if not isinstance(provider_options, dict):
        raise WebhookConfigError("provider_options 必须是对象")
    normalized["provider_options"] = provider_options

    if provider == "dingtalk":
        # Reuse the existing official-host validation and signing implementation.
        build_signed_webhook_url(
            endpoint_url,
            str(provider_options.get("signing_secret") or ""),
            int(time.time() * 1000),
        )
    elif provider == "bark" and not str(provider_options.get("device_key") or "").strip():
        raise WebhookConfigError("Bark 协议必须配置 device_key")

    public_base_url = str(normalized.get("public_base_url") or "").strip()
    if public_base_url:
        normalized["public_base_url"] = _validate_http_url(public_base_url, "public_base_url").rstrip("/")
    else:
        normalized["public_base_url"] = ""

    return normalized


def _absolute_media_url(
    base_url: str,
    media_kind: str,
    relative_path: Optional[str],
    *,
    use_global_default: bool = False,
) -> Optional[str]:
    if not relative_path or (not base_url and not use_global_default):
        return None
    media_config = get_public_media_config()
    resolved_base_url = base_url or (media_config.public_base_url if use_global_default else "")
    if not resolved_base_url:
        return None
    return build_public_media_url(
        media_kind,
        relative_path,
        public_base_url=resolved_base_url,
        config=media_config,
    )


def build_alert_webhook_event(
    alert: Any,
    detection_result: Optional[Mapping[str, Any]],
    *,
    public_base_url: str = "",
    include_media_urls: bool = True,
) -> Dict[str, Any]:
    detections = list((detection_result or {}).get("detections") or [])
    metadata = (detection_result or {}).get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {"value": metadata}

    video_source = getattr(alert, "video_source", None)
    workflow = getattr(alert, "workflow", None)
    image_path = getattr(alert, "alert_image", None)
    original_image_path = getattr(alert, "alert_image_ori", None)
    video_path = getattr(alert, "alert_video", None)
    occurred_at = getattr(alert, "alert_time", None)
    if hasattr(occurred_at, "isoformat"):
        occurred_at = occurred_at.isoformat()
    else:
        occurred_at = str(occurred_at or datetime.now().isoformat())

    resolved_base = public_base_url if include_media_urls else ""
    return {
        "schema_version": "1.0",
        "event_id": f"alert:{getattr(alert, 'id', '')}",
        "event_type": "alert.created",
        "occurred_at": occurred_at,
        "source": {
            "id": getattr(video_source, "id", None),
            "name": getattr(video_source, "name", "") or "",
            "code": getattr(video_source, "source_code", "") or "",
        },
        "workflow": {
            "id": getattr(workflow, "id", None),
            "name": getattr(workflow, "name", "") or "",
        },
        "alert": {
            "id": getattr(alert, "id", None),
            "type": getattr(alert, "alert_type", "") or "",
            "level": getattr(alert, "alert_level", "") or "",
            "message": getattr(alert, "alert_message", "") or "",
            "detection_count": len(detections) if detections else int(getattr(alert, "detection_count", 0) or 0),
        },
        "detection": {
            "has_detection": True,
            "detections": detections,
            "metadata": metadata,
        },
        "media": {
            "image_path": image_path,
            "image_url": _absolute_media_url(resolved_base, "image", image_path),
            "original_image_path": original_image_path,
            "original_image_url": _absolute_media_url(resolved_base, "image", original_image_path),
            "video_path": video_path,
            "video_url": _absolute_media_url(resolved_base, "video", video_path),
        },
    }


def apply_public_media_urls(
    event: Mapping[str, Any],
    *,
    public_base_url: str = "",
    include_media_urls: bool = True,
) -> Dict[str, Any]:
    """Return an event copy with absolute media URLs resolved for one node."""
    result = deepcopy(dict(event))
    media = result.setdefault("media", {})
    resolved_base = public_base_url if include_media_urls else ""
    use_global_default = include_media_urls and not public_base_url
    media["image_url"] = _absolute_media_url(
        resolved_base,
        "image",
        media.get("image_path"),
        use_global_default=use_global_default,
    )
    media["original_image_url"] = _absolute_media_url(
        resolved_base,
        "image",
        media.get("original_image_path"),
        use_global_default=use_global_default,
    )
    media["video_url"] = _absolute_media_url(
        resolved_base,
        "video",
        media.get("video_path"),
        use_global_default=use_global_default,
    )
    return result


def prepare_webhook_request(config: Mapping[str, Any], event: Mapping[str, Any]) -> PreparedWebhookRequest:
    normalized = validate_webhook_config(config)
    provider = normalized["provider"]
    headers = dict(normalized.get("headers") or {})
    headers.setdefault("Content-Type", "application/json; charset=utf-8")
    headers["X-VideoBA-Event-Id"] = str(event.get("event_id") or "")

    title_template = normalized.get("title_template") or "【{{alert.level}}】{{alert.type}}"
    body_template = normalized.get("body_template") or "{{alert.message}}"
    title = str(render_webhook_template(title_template, event))
    body = str(render_webhook_template(body_template, event))

    if provider == "generic":
        template = normalized.get("payload_template") or dict(event)
        payload = render_webhook_template(template, event)
        return PreparedWebhookRequest(normalized["endpoint_url"], headers, payload)

    if provider == "dingtalk":
        secret = str(normalized["provider_options"].get("signing_secret") or "")
        url = build_signed_webhook_url(normalized["endpoint_url"], secret, int(time.time() * 1000))
        content_parts = [title, body]
        media = event.get("media") if isinstance(event.get("media"), Mapping) else {}
        for label, field in (
            ("告警图片", "image_url"),
            ("原始图片", "original_image_url"),
            ("告警录像", "video_url"),
        ):
            media_url = str(media.get(field) or "")
            if media_url and media_url not in "\n".join(content_parts):
                content_parts.append(f"{label}: {media_url}")
        payload = {
            "msgtype": "text",
            "text": {"content": "\n".join(part for part in content_parts if part).strip()},
            "at": {"isAtAll": False},
        }
        return PreparedWebhookRequest(url, headers, payload)

    options = normalized["provider_options"]
    endpoint = normalized["endpoint_url"].rstrip("/")
    url = endpoint if endpoint.endswith("/push") else f"{endpoint}/push"
    payload: Dict[str, Any] = {
        "device_key": str(options["device_key"]),
        "title": title,
        "body": body,
        "group": str(options.get("group") or "VideoBA"),
        "level": str(options.get("level") or "active"),
    }
    for key in ("sound", "icon"):
        if options.get(key):
            payload[key] = options[key]
    media = event.get("media") if isinstance(event.get("media"), Mapping) else {}
    if media.get("image_url"):
        payload["image"] = media["image_url"]
    if media.get("video_url") or media.get("image_url"):
        payload["url"] = media.get("video_url") or media.get("image_url")
    return PreparedWebhookRequest(url, headers, payload)


def deliver_webhook_once(
    config: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    session: Any = requests,
) -> None:
    normalized = validate_webhook_config(config)
    prepared = prepare_webhook_request(normalized, event)
    encoded_payload = json.dumps(prepared.payload, ensure_ascii=False, default=str).encode("utf-8")
    if len(encoded_payload) > _MAX_REQUEST_BYTES:
        raise WebhookDeliveryError("Webhook 请求体超过 512 KiB 限制", retryable=False)
    try:
        response = session.post(
            prepared.url,
            headers=prepared.headers,
            json=prepared.payload,
            timeout=normalized["timeout_seconds"],
            allow_redirects=False,
            stream=True,
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise WebhookDeliveryError("Webhook 网络连接失败或超时", retryable=True) from exc
    except requests.RequestException as exc:
        raise WebhookDeliveryError("Webhook 请求失败", retryable=False) from exc

    try:
        if response.status_code == 429 or response.status_code >= 500:
            raise WebhookDeliveryError(f"Webhook 返回 HTTP {response.status_code}", retryable=True)
        if not 200 <= response.status_code < 300:
            raise WebhookDeliveryError(f"Webhook 返回 HTTP {response.status_code}", retryable=False)

        provider = normalized["provider"]
        if provider == "generic":
            return
        response_body = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            response_body.extend(chunk)
            if len(response_body) > _MAX_RESPONSE_BYTES:
                raise WebhookDeliveryError("Webhook 响应超过 64 KiB 限制", retryable=False)
        try:
            result = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebhookDeliveryError("Webhook 返回了无效 JSON", retryable=False) from exc
        if provider == "dingtalk" and result.get("errcode") != 0:
            raise WebhookDeliveryError(
                str(result.get("errmsg") or f"钉钉返回错误码 {result.get('errcode')}"),
                retryable=False,
            )
        if provider == "bark" and result.get("code") not in (None, 200):
            raise WebhookDeliveryError(
                str(result.get("message") or f"Bark 返回错误码 {result.get('code')}"),
                retryable=False,
            )
    finally:
        response.close()


class WebhookDispatcher:
    """A bounded, best-effort in-process webhook delivery queue."""

    def __init__(self, *, max_workers: int = 4, max_queue_size: int = 100):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="webhook")
        self._slots = threading.BoundedSemaphore(max_queue_size)

    def submit(self, config: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
        if not self._slots.acquire(blocking=False):
            logger.error("Webhook 队列已满，丢弃事件 %s", event.get("event_id"))
            return False
        future = self._executor.submit(self._deliver_with_retry, dict(config), dict(event))
        future.add_done_callback(lambda _future: self._slots.release())
        return True

    @staticmethod
    def _deliver_with_retry(config: Mapping[str, Any], event: Mapping[str, Any]) -> None:
        normalized = validate_webhook_config(config)
        attempts = normalized["max_attempts"]
        delay = normalized["retry_backoff_seconds"]
        event_id = event.get("event_id")
        for attempt in range(1, attempts + 1):
            try:
                deliver_webhook_once(normalized, event)
                logger.info("Webhook 推送成功: event=%s attempt=%s", event_id, attempt)
                return
            except WebhookDeliveryError as exc:
                if not exc.retryable or attempt >= attempts:
                    logger.error("Webhook 推送失败: event=%s attempt=%s error=%s", event_id, attempt, exc)
                    return
                logger.warning("Webhook 推送待重试: event=%s attempt=%s error=%s", event_id, attempt, exc)
                time.sleep(delay * (2 ** (attempt - 1)))
            except Exception as exc:  # pragma: no cover - defensive worker boundary
                logger.error("Webhook 推送异常: event=%s error=%s", event_id, exc)
                return


webhook_dispatcher = WebhookDispatcher()
