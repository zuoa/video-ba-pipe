"""Database-backed MQTT alert publishing configuration."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from app import logger
from app.core.database_models import SystemSetting


MQTT_SETTING_KEY = "mqtt_config"
MIN_PORT = 1
MAX_PORT = 65535
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 300
MIN_KEEPALIVE_SECONDS = 5
MAX_KEEPALIVE_SECONDS = 3600


@dataclass(frozen=True)
class MqttConfig:
    host: str = "mqtt"
    port: int = 1883
    username: str = "video-ba"
    password: str = ""
    topic_prefix: str = "video/alert"
    connection_timeout_seconds: int = 10
    publish_timeout_seconds: int = 10
    keepalive_seconds: int = 60

    def to_dict(self, *, include_password: bool = True) -> Dict[str, Any]:
        result = asdict(self)
        if not include_password:
            result["password_configured"] = bool(self.password)
            result["password"] = ""
        return result


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def normalize_mqtt_config(
    data: Optional[Dict[str, Any]],
    *,
    existing_password: str = "",
) -> MqttConfig:
    defaults = MqttConfig()
    data = data if isinstance(data, dict) else {}
    supplied_password = str(data.get("password") or "")
    host = defaults.host if "host" not in data else str(data.get("host") or "").strip()
    username = defaults.username if "username" not in data else str(data.get("username") or "").strip()
    topic_prefix = (
        defaults.topic_prefix
        if "topic_prefix" not in data
        else str(data.get("topic_prefix") or "").strip().strip("/")
    )

    return MqttConfig(
        host=host,
        port=_bounded_int(data.get("port"), defaults.port, MIN_PORT, MAX_PORT),
        username=username,
        password=supplied_password or existing_password,
        topic_prefix=topic_prefix,
        connection_timeout_seconds=_bounded_int(
            data.get("connection_timeout_seconds"),
            defaults.connection_timeout_seconds,
            MIN_TIMEOUT_SECONDS,
            MAX_TIMEOUT_SECONDS,
        ),
        publish_timeout_seconds=_bounded_int(
            data.get("publish_timeout_seconds"),
            defaults.publish_timeout_seconds,
            MIN_TIMEOUT_SECONDS,
            MAX_TIMEOUT_SECONDS,
        ),
        keepalive_seconds=_bounded_int(
            data.get("keepalive_seconds"),
            defaults.keepalive_seconds,
            MIN_KEEPALIVE_SECONDS,
            MAX_KEEPALIVE_SECONDS,
        ),
    )


def validate_mqtt_config(config: MqttConfig) -> None:
    if not config.host:
        raise ValueError("启用 MQTT 时必须填写主机地址")
    if not config.topic_prefix:
        raise ValueError("MQTT 主题前缀不能为空")
    if any(character in config.topic_prefix for character in ("+", "#", "\x00")):
        raise ValueError("MQTT 主题前缀不能包含 +、# 或空字符")


def get_mqtt_config() -> MqttConfig:
    try:
        record = SystemSetting.get_or_none(SystemSetting.key == MQTT_SETTING_KEY)
        if record and record.value:
            return normalize_mqtt_config(json.loads(record.value))
    except Exception as exc:
        logger.warning(f"读取 MQTT 配置失败，使用页面初始值: {exc}")
    return MqttConfig()


def save_mqtt_config(
    data: Optional[Dict[str, Any]],
    updated_by: str = "system",
) -> MqttConfig:
    if not isinstance(data, dict):
        raise ValueError("MQTT 配置必须是 JSON 对象")

    existing = get_mqtt_config()
    config = normalize_mqtt_config(data, existing_password=existing.password)
    validate_mqtt_config(config)
    record, _ = SystemSetting.get_or_create(
        key=MQTT_SETTING_KEY,
        defaults={
            "value": "",
            "description": "MQTT 预警发布配置",
            "updated_at": datetime.now(),
            "updated_by": updated_by,
        },
    )
    record.value = json.dumps(config.to_dict(), ensure_ascii=False)
    record.description = "MQTT 预警发布配置"
    record.updated_at = datetime.now()
    record.updated_by = updated_by
    record.save()
    return config


def test_mqtt_connection(config: MqttConfig) -> Tuple[bool, str]:
    validate_mqtt_config(config)
    try:
        from app.core.mqtt_publisher import MQTTPublisher
    except ImportError:
        return False, "paho-mqtt 未安装，无法测试 MQTT 连接"

    publisher = MQTTPublisher(config_provider=lambda: config)
    try:
        if publisher.connect():
            return True, f"成功连接到 MQTT {config.host}:{config.port}"
        return False, f"连接 MQTT {config.host}:{config.port} 失败"
    except Exception as exc:
        return False, f"连接 MQTT 失败: {exc}"
    finally:
        publisher.disconnect()
