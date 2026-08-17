"""Protocol selection for database-backed alert message queue delivery."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from app import logger
from app.core.database_models import SystemSetting
from app.core.rabbitmq_config import RABBITMQ_SETTING_KEY, get_rabbitmq_config


MESSAGE_QUEUE_SETTING_KEY = "message_queue_config"
VALID_PROVIDERS = ("mqtt", "rabbitmq", "http")


@dataclass(frozen=True)
class MessageQueueConfig:
    enabled: bool = False
    provider: str = "mqtt"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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


def normalize_message_queue_config(data: Optional[Dict[str, Any]]) -> MessageQueueConfig:
    defaults = MessageQueueConfig()
    data = data if isinstance(data, dict) else {}
    provider = str(data.get("provider") or defaults.provider).strip().lower()
    if provider not in VALID_PROVIDERS:
        raise ValueError("消息投递提供方必须是 mqtt、rabbitmq 或 http")
    return MessageQueueConfig(
        enabled=_safe_bool(data.get("enabled"), defaults.enabled),
        provider=provider,
    )


def get_message_queue_config() -> MessageQueueConfig:
    """Read selector; transparently retain legacy RabbitMQ installations."""
    try:
        record = SystemSetting.get_or_none(SystemSetting.key == MESSAGE_QUEUE_SETTING_KEY)
        if record and record.value:
            return normalize_message_queue_config(json.loads(record.value))

        legacy = SystemSetting.get_or_none(SystemSetting.key == RABBITMQ_SETTING_KEY)
        if legacy is not None:
            rabbitmq = get_rabbitmq_config()
            return MessageQueueConfig(enabled=rabbitmq.enabled, provider="rabbitmq")
    except Exception as exc:
        logger.warning(f"读取消息队列配置失败，回退为关闭: {exc}")
    return MessageQueueConfig()


def save_message_queue_config(
    data: Optional[Dict[str, Any]],
    updated_by: str = "system",
) -> MessageQueueConfig:
    if not isinstance(data, dict):
        raise ValueError("消息队列配置必须是 JSON 对象")
    config = normalize_message_queue_config(data)
    record, _ = SystemSetting.get_or_create(
        key=MESSAGE_QUEUE_SETTING_KEY,
        defaults={
            "value": "",
            "description": "消息投递通道配置",
            "updated_at": datetime.now(),
            "updated_by": updated_by,
        },
    )
    record.value = json.dumps(config.to_dict(), ensure_ascii=False)
    record.description = "消息投递通道配置"
    record.updated_at = datetime.now()
    record.updated_by = updated_by
    record.save()
    return config
