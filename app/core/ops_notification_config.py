"""数据库持久化的运维通知配置。"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from app import logger
from app.core.database_models import SystemSetting


OPS_NOTIFICATION_SETTING_KEY = "ops_notification_config"
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class OpsNotificationConfig:
    enabled: bool = False
    webhook_url: str = ""
    secret: str = ""
    notify_disk_pressure: bool = True
    notify_cleanup_failure: bool = True
    notify_alert_growth: bool = True
    alert_growth_window_minutes: int = 5
    alert_growth_threshold: int = 100
    cooldown_minutes: int = 30

    def to_dict(self, *, include_secret: bool = True) -> Dict[str, Any]:
        result = asdict(self)
        if not include_secret:
            result["secret_configured"] = bool(self.secret)
            result["secret"] = ""
        return result


_cached_config = OpsNotificationConfig()


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


def normalize_ops_notification_config(
    data: Optional[Dict[str, Any]],
    *,
    existing_secret: str = "",
) -> OpsNotificationConfig:
    defaults = OpsNotificationConfig()
    data = data if isinstance(data, dict) else {}
    supplied_secret = str(data.get("secret") or "").strip()
    return OpsNotificationConfig(
        enabled=_safe_bool(data.get("enabled"), defaults.enabled),
        webhook_url=str(data.get("webhook_url") or "").strip(),
        secret=supplied_secret or existing_secret,
        notify_disk_pressure=_safe_bool(
            data.get("notify_disk_pressure"), defaults.notify_disk_pressure
        ),
        notify_cleanup_failure=_safe_bool(
            data.get("notify_cleanup_failure"), defaults.notify_cleanup_failure
        ),
        notify_alert_growth=_safe_bool(
            data.get("notify_alert_growth"), defaults.notify_alert_growth
        ),
        alert_growth_window_minutes=_bounded_int(
            data.get("alert_growth_window_minutes"), 5, 1, 1440
        ),
        alert_growth_threshold=_bounded_int(
            data.get("alert_growth_threshold"), 100, 1, 1_000_000
        ),
        cooldown_minutes=_bounded_int(data.get("cooldown_minutes"), 30, 1, 1440),
    )


def get_ops_notification_config() -> OpsNotificationConfig:
    global _cached_config
    try:
        record = SystemSetting.get_or_none(
            SystemSetting.key == OPS_NOTIFICATION_SETTING_KEY
        )
        if record and record.value:
            config = normalize_ops_notification_config(json.loads(record.value))
            with _cache_lock:
                _cached_config = config
            return config
    except Exception as exc:
        logger.warning(f"读取运维通知配置失败，使用进程内最近有效配置: {exc}")
        with _cache_lock:
            return _cached_config
    with _cache_lock:
        _cached_config = OpsNotificationConfig()
        return _cached_config


def save_ops_notification_config(
    data: Optional[Dict[str, Any]],
    updated_by: str = "system",
) -> OpsNotificationConfig:
    global _cached_config
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")

    existing = get_ops_notification_config()
    config = normalize_ops_notification_config(data, existing_secret=existing.secret)
    if config.enabled and not config.webhook_url:
        raise ValueError("启用运维通知时必须填写钉钉 Webhook")
    if config.webhook_url:
        from app.core.dingtalk_notifier import validate_dingtalk_webhook_url

        validate_dingtalk_webhook_url(config.webhook_url)

    record, _ = SystemSetting.get_or_create(
        key=OPS_NOTIFICATION_SETTING_KEY,
        defaults={
            "value": "",
            "description": "钉钉运维通知配置",
            "updated_at": datetime.now(),
            "updated_by": updated_by,
        },
    )
    record.value = json.dumps(config.to_dict(), ensure_ascii=False)
    record.description = "钉钉运维通知配置"
    record.updated_at = datetime.now()
    record.updated_by = updated_by
    record.save()
    with _cache_lock:
        _cached_config = config
    return config
