"""Global public media URL configuration and expiring URL signatures."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import quote, urlparse

from app import logger
from app.core.database_models import SystemSetting


PUBLIC_MEDIA_SETTING_KEY = "public_media_config"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"true", "1", "yes", "on"}


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


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def _environment_public_base_url() -> str:
    return str(os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")


def _signing_secret() -> str:
    return str(
        os.getenv("MEDIA_URL_SIGNING_SECRET")
        or os.getenv("JWT_SECRET")
        or "your-secret-key-change-in-production"
    )


@dataclass(frozen=True)
class PublicMediaConfig:
    public_base_url: str = ""
    public_base_url_override: str = ""
    sign_media_urls: bool = True
    media_url_ttl_hours: int = 24
    config_source: str = "environment"

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["signing_available"] = bool(_signing_secret())
        return result


def normalize_public_media_config(
    data: Optional[Dict[str, Any]],
    *,
    config_source: str = "database",
) -> PublicMediaConfig:
    data = data if isinstance(data, dict) else {}
    override_value = data.get("public_base_url_override")
    if override_value is None:
        override_value = data.get("public_base_url")
    public_base_url_override = str(override_value or "").strip().rstrip("/")
    public_base_url = public_base_url_override or _environment_public_base_url()
    if public_base_url:
        parsed = urlparse(public_base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("公共访问地址必须是合法的 HTTP/HTTPS 基础地址，不能包含认证信息、查询参数或片段")

    return PublicMediaConfig(
        public_base_url=public_base_url,
        public_base_url_override=public_base_url_override,
        sign_media_urls=_safe_bool(
            data.get("sign_media_urls"),
            _env_bool("MEDIA_URL_SIGNING_ENABLED", True),
        ),
        media_url_ttl_hours=_bounded_int(
            data.get("media_url_ttl_hours"),
            _bounded_int(os.getenv("MEDIA_URL_TTL_HOURS"), 24, 1, 720),
            1,
            720,
        ),
        config_source=config_source if public_base_url_override else "environment",
    )


def get_public_media_config() -> PublicMediaConfig:
    try:
        record = SystemSetting.get_or_none(SystemSetting.key == PUBLIC_MEDIA_SETTING_KEY)
        if record and record.value:
            return normalize_public_media_config(json.loads(record.value), config_source="database")
    except Exception as exc:
        logger.warning(f"读取公共媒体配置失败，回退到环境变量: {exc}")
    return normalize_public_media_config({}, config_source="environment")


def save_public_media_config(
    data: Optional[Dict[str, Any]],
    *,
    updated_by: str = "system",
) -> PublicMediaConfig:
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")
    config = normalize_public_media_config(data, config_source="database")
    record, _ = SystemSetting.get_or_create(
        key=PUBLIC_MEDIA_SETTING_KEY,
        defaults={
            "value": "",
            "description": "公共媒体访问与签名配置",
            "updated_at": datetime.now(),
            "updated_by": updated_by,
        },
    )
    persisted = {
        "public_base_url_override": config.public_base_url_override,
        "sign_media_urls": config.sign_media_urls,
        "media_url_ttl_hours": config.media_url_ttl_hours,
    }
    record.value = json.dumps(persisted, ensure_ascii=False)
    record.description = "公共媒体访问与签名配置"
    record.updated_at = datetime.now()
    record.updated_by = updated_by
    record.save()
    return config


def _media_route(media_kind: str, relative_path: str) -> str:
    clean_path = str(relative_path or "").replace("\\", "/").lstrip("/")
    if not clean_path:
        raise ValueError("媒体相对路径不能为空")
    if media_kind == "image":
        prefix = "/api/image/frames"
    elif media_kind == "video":
        prefix = "/api/video"
    else:
        raise ValueError(f"不支持的媒体类型: {media_kind}")
    return f"{prefix}/{clean_path}"


def _signature(route_path: str, expires: int) -> str:
    message = f"{route_path}\n{expires}".encode("utf-8")
    digest = hmac.new(_signing_secret().encode("utf-8"), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_public_media_url(
    media_kind: str,
    relative_path: Optional[str],
    *,
    public_base_url: Optional[str] = None,
    config: Optional[PublicMediaConfig] = None,
    now: Optional[int] = None,
) -> Optional[str]:
    if not relative_path:
        return None
    config = config or get_public_media_config()
    route_path = _media_route(media_kind, relative_path)
    encoded_route = quote(route_path, safe="/")
    base_url = (public_base_url if public_base_url is not None else config.public_base_url).rstrip("/")
    url = f"{base_url}{encoded_route}" if base_url else encoded_route
    if not config.sign_media_urls:
        return url
    expires = int(now if now is not None else time.time()) + config.media_url_ttl_hours * 3600
    return f"{url}?expires={expires}&signature={_signature(route_path, expires)}"


def add_public_media_urls_to_detection_images(
    raw_value: Any,
    *,
    config: Optional[PublicMediaConfig] = None,
) -> Any:
    """Enrich stored detection-image metadata while preserving its container type."""
    if not raw_value:
        return raw_value
    try:
        items = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except (TypeError, json.JSONDecodeError):
        return raw_value
    if not isinstance(items, list):
        return raw_value

    media_config = config or get_public_media_config()
    enriched = []
    for item in items:
        if isinstance(item, str):
            enriched.append({
                "image_path": item,
                "image_url": build_public_media_url("image", item, config=media_config),
            })
            continue
        if not isinstance(item, dict):
            enriched.append(item)
            continue
        enriched_item = dict(item)
        enriched_item["image_url"] = build_public_media_url(
            "image",
            enriched_item.get("image_path"),
            config=media_config,
        )
        enriched_item["image_ori_url"] = build_public_media_url(
            "image",
            enriched_item.get("image_ori_path"),
            config=media_config,
        )
        enriched.append(enriched_item)

    return json.dumps(enriched, ensure_ascii=False) if isinstance(raw_value, str) else enriched


def verify_public_media_signature(
    route_path: str,
    expires: Any,
    signature: Any,
    *,
    config: Optional[PublicMediaConfig] = None,
    now: Optional[int] = None,
) -> bool:
    config = config or get_public_media_config()
    if not config.sign_media_urls:
        return True
    try:
        expiry = int(expires)
    except (TypeError, ValueError):
        return False
    current_time = int(now if now is not None else time.time())
    if expiry < current_time:
        return False
    expected = _signature(route_path, expiry)
    return hmac.compare_digest(expected, str(signature or ""))
