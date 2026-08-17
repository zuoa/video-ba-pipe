"""Database-backed HTTP alert delivery configuration."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlparse

from app import logger
from app.core.database_models import SystemSetting
from app.core.node_identity import get_node_id


HTTP_DELIVERY_SETTING_KEY = "http_delivery_config"
VALID_AUTH_TYPES = ("none", "bearer")
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 300
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_RESERVED_HEADERS = {
    "authorization",
    "connection",
    "content-length",
    "content-type",
    "host",
    "transfer-encoding",
    "upgrade",
    "x-videoba-event-id",
    "x-videoba-event-type",
    "x-videoba-test",
}


def validate_http_header_value(name: str, value: str) -> None:
    """Reject values that Requests/http.client cannot serialize safely."""
    if value.startswith((" ", "\t")) or "\r" in value or "\n" in value:
        raise ValueError(f"HTTP 请求头值格式无效: {name}")
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError(f"HTTP 请求头值必须可编码为 Latin-1: {name}") from exc


@dataclass(frozen=True)
class HttpDeliveryConfig:
    endpoint_url: str = ""
    auth_type: str = "bearer"
    use_node_id_as_token: bool = True
    bearer_token: str = ""
    custom_headers: Dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 10

    def to_dict(self, *, include_secrets: bool = True) -> Dict[str, Any]:
        result = asdict(self)
        if include_secrets:
            return result
        result["bearer_token_configured"] = bool(self.bearer_token)
        result["bearer_token"] = ""
        result["custom_headers"] = [
            {"name": name, "value": "", "value_configured": bool(value)}
            for name, value in self.custom_headers.items()
        ]
        return result


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return default if value is None else bool(value)


def _bounded_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(MAX_TIMEOUT_SECONDS, max(MIN_TIMEOUT_SECONDS, parsed))


def _iter_header_entries(raw_headers: Any) -> Iterable[Tuple[Any, Any]]:
    if raw_headers in (None, ""):
        return ()
    if isinstance(raw_headers, dict):
        return tuple(raw_headers.items())
    if isinstance(raw_headers, list):
        return tuple(
            (entry.get("name"), entry.get("value"))
            for entry in raw_headers
            if isinstance(entry, dict)
        )
    raise ValueError("HTTP 自定义请求头必须是对象或键值列表")


def _normalize_headers(raw_headers: Any, existing_headers: Dict[str, str]) -> Dict[str, str]:
    existing_by_name = {name.lower(): value for name, value in existing_headers.items()}
    normalized: Dict[str, str] = {}
    seen = set()
    for raw_name, raw_value in _iter_header_entries(raw_headers):
        name = str(raw_name or "").strip()
        if not name:
            continue
        lower_name = name.lower()
        if lower_name in seen:
            raise ValueError(f"HTTP 自定义请求头重复: {name}")
        if not _HEADER_NAME_RE.fullmatch(name):
            raise ValueError(f"HTTP 自定义请求头名称无效: {name}")
        if lower_name in _RESERVED_HEADERS:
            raise ValueError(f"HTTP 自定义请求头不能覆盖系统请求头: {name}")
        value = str(raw_value or "")
        if not value:
            value = existing_by_name.get(lower_name, "")
        if not value:
            raise ValueError(f"HTTP 自定义请求头必须填写值: {name}")
        validate_http_header_value(name, value)
        normalized[name] = value
        seen.add(lower_name)
    return normalized


def normalize_http_delivery_config(
    data: Optional[Dict[str, Any]],
    *,
    existing: Optional[HttpDeliveryConfig] = None,
) -> HttpDeliveryConfig:
    defaults = HttpDeliveryConfig()
    current = existing or defaults
    data = data if isinstance(data, dict) else {}
    auth_type = str(data.get("auth_type") or current.auth_type).strip().lower()
    if auth_type not in VALID_AUTH_TYPES:
        raise ValueError("HTTP 鉴权方式必须是 none 或 bearer")
    use_node_id_as_token = _safe_bool(
        data.get("use_node_id_as_token"), current.use_node_id_as_token
    )
    supplied_token = str(data.get("bearer_token") or "").strip()
    bearer_token = "" if use_node_id_as_token else (supplied_token or current.bearer_token)
    raw_headers = data.get("custom_headers", current.custom_headers)
    return HttpDeliveryConfig(
        endpoint_url=(
            current.endpoint_url
            if "endpoint_url" not in data
            else str(data.get("endpoint_url") or "").strip()
        ),
        auth_type=auth_type,
        use_node_id_as_token=use_node_id_as_token,
        bearer_token=bearer_token,
        custom_headers=_normalize_headers(raw_headers, current.custom_headers),
        timeout_seconds=_bounded_int(data.get("timeout_seconds"), current.timeout_seconds),
    )


def validate_http_delivery_config(
    config: HttpDeliveryConfig,
    *,
    require_ready: bool = True,
) -> None:
    if config.endpoint_url:
        try:
            parsed = urlparse(config.endpoint_url)
            hostname = parsed.hostname
            # Accessing ``port`` performs urllib's numeric/range validation.
            parsed.port
        except ValueError as exc:
            raise ValueError("HTTP 接收地址端口无效") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("HTTP 接收地址必须是合法的 HTTP/HTTPS URL，且不能包含 userinfo")
    elif require_ready:
        raise ValueError("启用 HTTP 投递时必须填写接收地址")
    if (
        require_ready
        and config.auth_type == "bearer"
        and not config.use_node_id_as_token
        and not config.bearer_token
    ):
        raise ValueError("使用自定义 Bearer Token 时必须填写 Token")
    if require_ready:
        node_id = get_node_id()
        validate_http_header_value("X-VideoBA-Event-Id", node_id)
        if config.auth_type == "bearer":
            token = node_id if config.use_node_id_as_token else config.bearer_token
            validate_http_header_value("Authorization", f"Bearer {token}")
        for name, value in config.custom_headers.items():
            validate_http_header_value(name, value)


def get_http_delivery_config() -> HttpDeliveryConfig:
    try:
        record = SystemSetting.get_or_none(SystemSetting.key == HTTP_DELIVERY_SETTING_KEY)
        if record and record.value:
            return normalize_http_delivery_config(json.loads(record.value))
    except Exception as exc:
        logger.warning(f"读取 HTTP 投递配置失败，使用页面初始值: {exc}")
    return HttpDeliveryConfig()


def save_http_delivery_config(
    data: Optional[Dict[str, Any]],
    *,
    updated_by: str = "system",
    require_ready: bool = False,
) -> HttpDeliveryConfig:
    if not isinstance(data, dict):
        raise ValueError("HTTP 投递配置必须是 JSON 对象")
    existing = get_http_delivery_config()
    config = normalize_http_delivery_config(data, existing=existing)
    validate_http_delivery_config(config, require_ready=require_ready)
    record, _ = SystemSetting.get_or_create(
        key=HTTP_DELIVERY_SETTING_KEY,
        defaults={
            "value": "",
            "description": "HTTP 预警投递配置",
            "updated_at": datetime.now(),
            "updated_by": updated_by,
        },
    )
    record.value = json.dumps(config.to_dict(), ensure_ascii=False)
    record.description = "HTTP 预警投递配置"
    record.updated_at = datetime.now()
    record.updated_by = updated_by
    record.save()
    return config
