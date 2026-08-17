"""Global public media URL configuration and expiring URL signatures."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import quote, urlparse

from app import logger
from app.core.database_models import SystemSetting


PUBLIC_MEDIA_SETTING_KEY = "public_media_config"
VALID_DELIVERY_MODES = {"url", "inline", "object_storage"}


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
    delivery_mode: str = "url"
    inline_max_bytes: int = 512 * 1024
    inline_max_edge: int = 1280
    inline_jpeg_quality: int = 80
    object_storage_endpoint_url: str = ""
    object_storage_region: str = ""
    object_storage_bucket: str = ""
    object_storage_access_key_id: str = ""
    object_storage_secret_access_key: str = ""
    object_storage_key_prefix: str = "alerts"
    object_storage_force_path_style: bool = False
    object_storage_verify_ssl: bool = True
    object_storage_presigned_url_ttl_hours: int = 24
    async_max_attempts: int = 10
    async_initial_backoff_seconds: int = 2
    async_max_backoff_seconds: int = 300
    config_source: str = "environment"

    def to_dict(self, *, include_secret: bool = False) -> Dict[str, Any]:
        result = {
            "public_base_url": self.public_base_url,
            "public_base_url_override": self.public_base_url_override,
            "sign_media_urls": self.sign_media_urls,
            "media_url_ttl_hours": self.media_url_ttl_hours,
            "delivery_mode": self.delivery_mode,
            "inline": {
                "max_bytes": self.inline_max_bytes,
                "max_edge": self.inline_max_edge,
                "jpeg_quality": self.inline_jpeg_quality,
            },
            "object_storage": {
                "endpoint_url": self.object_storage_endpoint_url,
                "region": self.object_storage_region,
                "bucket": self.object_storage_bucket,
                "access_key_id": self.object_storage_access_key_id,
                "secret_access_key": self.object_storage_secret_access_key if include_secret else "",
                "secret_configured": bool(self.object_storage_secret_access_key),
                "key_prefix": self.object_storage_key_prefix,
                "force_path_style": self.object_storage_force_path_style,
                "verify_ssl": self.object_storage_verify_ssl,
                "presigned_url_ttl_hours": self.object_storage_presigned_url_ttl_hours,
            },
            "async_delivery": {
                "max_attempts": self.async_max_attempts,
                "initial_backoff_seconds": self.async_initial_backoff_seconds,
                "max_backoff_seconds": self.async_max_backoff_seconds,
            },
            "config_source": self.config_source,
        }
        result["signing_available"] = bool(_signing_secret())
        return result


def normalize_public_media_config(
    data: Optional[Dict[str, Any]],
    *,
    config_source: str = "database",
    existing_secret: str = "",
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

    delivery_mode = str(data.get("delivery_mode") or "url").strip().lower()
    if delivery_mode not in VALID_DELIVERY_MODES:
        raise ValueError("媒体交付模式必须是 url、inline 或 object_storage")
    inline = data.get("inline") if isinstance(data.get("inline"), dict) else {}
    object_storage = data.get("object_storage") if isinstance(data.get("object_storage"), dict) else {}
    async_delivery = data.get("async_delivery") if isinstance(data.get("async_delivery"), dict) else {}
    endpoint_url = str(object_storage.get("endpoint_url") or "").strip().rstrip("/")
    if endpoint_url:
        parsed_endpoint = urlparse(endpoint_url)
        if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.hostname:
            raise ValueError("对象存储 Endpoint 必须是合法的 HTTP/HTTPS 地址")
    supplied_secret = str(object_storage.get("secret_access_key") or "")
    bucket = str(object_storage.get("bucket") or "").strip()
    access_key_id = str(object_storage.get("access_key_id") or "").strip()
    if delivery_mode == "object_storage":
        if not endpoint_url:
            raise ValueError("对象存储模式必须填写 Endpoint")
        if not bucket:
            raise ValueError("对象存储模式必须填写 Bucket")
        if not access_key_id:
            raise ValueError("对象存储模式必须填写 Access Key ID")
        if not (supplied_secret or existing_secret):
            raise ValueError("对象存储模式必须填写 Secret Access Key")
    initial_backoff_seconds = _bounded_int(
        async_delivery.get("initial_backoff_seconds"), 2, 1, 300
    )
    max_backoff_seconds = max(
        initial_backoff_seconds,
        _bounded_int(async_delivery.get("max_backoff_seconds"), 300, 1, 86400),
    )

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
        delivery_mode=delivery_mode,
        inline_max_bytes=_bounded_int(inline.get("max_bytes"), 512 * 1024, 32 * 1024, 8 * 1024 * 1024),
        inline_max_edge=_bounded_int(inline.get("max_edge"), 1280, 320, 4096),
        inline_jpeg_quality=_bounded_int(inline.get("jpeg_quality"), 80, 30, 95),
        object_storage_endpoint_url=endpoint_url,
        object_storage_region=str(object_storage.get("region") or "").strip(),
        object_storage_bucket=bucket,
        object_storage_access_key_id=access_key_id,
        object_storage_secret_access_key=supplied_secret or existing_secret,
        object_storage_key_prefix=str(object_storage.get("key_prefix") or "alerts").strip().strip("/"),
        object_storage_force_path_style=_safe_bool(object_storage.get("force_path_style"), False),
        object_storage_verify_ssl=_safe_bool(object_storage.get("verify_ssl"), True),
        object_storage_presigned_url_ttl_hours=_bounded_int(
            object_storage.get("presigned_url_ttl_hours"), 24, 1, 168
        ),
        async_max_attempts=_bounded_int(async_delivery.get("max_attempts"), 10, 1, 100),
        async_initial_backoff_seconds=initial_backoff_seconds,
        async_max_backoff_seconds=max_backoff_seconds,
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
    existing = get_public_media_config()
    config = normalize_public_media_config(
        data,
        config_source="database",
        existing_secret=existing.object_storage_secret_access_key,
    )
    record, _ = SystemSetting.get_or_create(
        key=PUBLIC_MEDIA_SETTING_KEY,
        defaults={
            "value": "",
            "description": "公共媒体访问与签名配置",
            "updated_at": datetime.now(),
            "updated_by": updated_by,
        },
    )
    persisted = config.to_dict(include_secret=True)
    persisted.pop("public_base_url", None)
    persisted.pop("config_source", None)
    persisted.pop("signing_available", None)
    persisted["object_storage"].pop("secret_configured", None)
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
