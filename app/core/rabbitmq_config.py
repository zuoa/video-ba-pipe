"""数据库持久化的 RabbitMQ 预警发布配置。

沿用 recording_storage_config / ops_notification_config 的既定模式：
dataclass 字段默认值取自 app.config 的环境变量常量，首次无 DB 行时回退到环境变量；
password 为敏感字段，读取时脱敏（to_dict(include_password=False)），保存时空白则保留旧值。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from app import logger
from app.config import (
    RABBITMQ_ALERT_EXCHANGE,
    RABBITMQ_ALERT_QUEUE,
    RABBITMQ_ALERT_ROUTING_KEY,
    RABBITMQ_CONNECTION_TIMEOUT,
    RABBITMQ_ENABLED,
    RABBITMQ_EXCHANGE_TYPE,
    RABBITMQ_HOST,
    RABBITMQ_PASSWORD,
    RABBITMQ_PORT,
    RABBITMQ_USER,
    RABBITMQ_VHOST,
)
from app.core.database_models import SystemSetting


RABBITMQ_SETTING_KEY = "rabbitmq_config"
MIN_PORT = 1
MAX_PORT = 65535
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 300
VALID_EXCHANGE_TYPES = ("topic", "direct")


@dataclass(frozen=True)
class RabbitMqConfig:
    enabled: bool = RABBITMQ_ENABLED
    host: str = RABBITMQ_HOST
    port: int = RABBITMQ_PORT
    username: str = RABBITMQ_USER
    password: str = RABBITMQ_PASSWORD
    vhost: str = RABBITMQ_VHOST
    alert_queue: str = RABBITMQ_ALERT_QUEUE
    alert_exchange: str = RABBITMQ_ALERT_EXCHANGE
    alert_routing_key: str = RABBITMQ_ALERT_ROUTING_KEY
    exchange_type: str = RABBITMQ_EXCHANGE_TYPE
    connection_timeout_seconds: int = RABBITMQ_CONNECTION_TIMEOUT

    def to_dict(self, *, include_password: bool = True) -> Dict[str, Any]:
        result = asdict(self)
        if not include_password:
            result["password_configured"] = bool(self.password)
            result["password"] = ""
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


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def normalize_rabbitmq_config(
    data: Optional[Dict[str, Any]],
    *,
    existing_password: str = "",
) -> RabbitMqConfig:
    """把前端传入的 dict 规整为 RabbitMqConfig；空白 password 保留旧值。"""
    defaults = RabbitMqConfig()
    data = data if isinstance(data, dict) else {}
    supplied_password = str(data.get("password") or "").strip()

    exchange_type = str(data.get("exchange_type") or defaults.exchange_type).strip().lower()
    if exchange_type not in VALID_EXCHANGE_TYPES:
        exchange_type = defaults.exchange_type

    return RabbitMqConfig(
        enabled=_safe_bool(data.get("enabled"), defaults.enabled),
        host=str(data.get("host") or defaults.host).strip(),
        port=_bounded_int(data.get("port"), defaults.port, MIN_PORT, MAX_PORT),
        username=str(data.get("username") or defaults.username).strip(),
        password=supplied_password or existing_password,
        vhost=str(data.get("vhost") or defaults.vhost).strip(),
        alert_queue=str(data.get("alert_queue") or defaults.alert_queue).strip(),
        alert_exchange=str(data.get("alert_exchange") or defaults.alert_exchange).strip(),
        alert_routing_key=str(
            data.get("alert_routing_key") or defaults.alert_routing_key
        ).strip(),
        exchange_type=exchange_type,
        connection_timeout_seconds=_bounded_int(
            data.get("connection_timeout_seconds"),
            defaults.connection_timeout_seconds,
            MIN_TIMEOUT_SECONDS,
            MAX_TIMEOUT_SECONDS,
        ),
    )


def get_rabbitmq_config() -> RabbitMqConfig:
    """读取持久化配置；DB 不可用或无配置行时默认不启用。

    关键：回退分支强制 enabled=False（不沿用环境变量默认值）。否则在
    RABBITMQ_ENABLED=true 的部署里，worker 进程一旦 DB 读取失败就会回退成
    "已启用"，出现"系统设置里已关闭却仍在尝试发送"的问题。DB 不可读时，
    宁可不发，也不要误发。
    """
    try:
        record = SystemSetting.get_or_none(SystemSetting.key == RABBITMQ_SETTING_KEY)
        if record and record.value:
            return normalize_rabbitmq_config(json.loads(record.value))
    except Exception as exc:
        logger.warning(f"读取 RabbitMQ 配置失败，回退为未启用: {exc}")
    return replace(RabbitMqConfig(), enabled=False)


def save_rabbitmq_config(
    data: Optional[Dict[str, Any]],
    updated_by: str = "system",
) -> RabbitMqConfig:
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")

    existing = get_rabbitmq_config()
    config = normalize_rabbitmq_config(data, existing_password=existing.password)

    if config.enabled:
        if not config.host:
            raise ValueError("启用 RabbitMQ 时必须填写主机地址")
        if not config.username:
            raise ValueError("启用 RabbitMQ 时必须填写用户名")
        if not config.alert_exchange:
            raise ValueError("启用 RabbitMQ 时必须填写交换机名称")

    record, _ = SystemSetting.get_or_create(
        key=RABBITMQ_SETTING_KEY,
        defaults={
            "value": "",
            "description": "RabbitMQ 预警发布配置",
            "updated_at": datetime.now(),
            "updated_by": updated_by,
        },
    )
    record.value = json.dumps(config.to_dict(), ensure_ascii=False)
    record.description = "RabbitMQ 预警发布配置"
    record.updated_at = datetime.now()
    record.updated_by = updated_by
    record.save()
    return config


def test_rabbitmq_connection(config: RabbitMqConfig) -> Tuple[bool, str]:
    """用给定配置尝试连接 broker 并声明交换机，不发布任何消息。

    Returns:
        (ok, message)
    """
    try:
        import pika
    except ImportError:
        return False, "pika 未安装，无法测试 RabbitMQ 连接"

    credentials = pika.PlainCredentials(config.username, config.password)
    parameters = pika.ConnectionParameters(
        host=config.host,
        port=config.port,
        virtual_host=config.vhost,
        credentials=credentials,
        connection_attempts=1,
        retry_delay=1,
        socket_timeout=config.connection_timeout_seconds,
        blocked_connection_timeout=config.connection_timeout_seconds,
    )
    connection = None
    channel = None
    try:
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        channel.exchange_declare(
            exchange=config.alert_exchange,
            exchange_type=config.exchange_type,
            durable=True,
        )
        return True, f"成功连接到 RabbitMQ {config.host}:{config.port} 并声明交换机 {config.alert_exchange}"
    except Exception as exc:  # pika 异常类型较多，统一捕获后转可读消息
        return False, f"连接 RabbitMQ 失败: {exc}"
    finally:
        try:
            if channel is not None and not channel.is_closed:
                channel.close()
        except Exception:
            pass
        try:
            if connection is not None and not connection.is_closed:
                connection.close()
        except Exception:
            pass
