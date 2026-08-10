"""Compatibility helpers for message queue administration APIs."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.message_queue_config import save_message_queue_config
from app.core.rabbitmq_config import RabbitMqConfig, save_rabbitmq_config


def save_legacy_rabbitmq_config(
    data: Optional[Dict[str, Any]],
    *,
    updated_by: str,
) -> RabbitMqConfig:
    """Save RabbitMQ details and keep the canonical selector in sync."""
    config = save_rabbitmq_config(data, updated_by=updated_by)
    save_message_queue_config(
        {"enabled": config.enabled, "provider": "rabbitmq"},
        updated_by=updated_by,
    )
    return config
