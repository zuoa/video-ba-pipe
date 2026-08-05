"""平台节点身份模块

提供当前实例的唯一编码（node_id），用于集群部署和 MQ 推送时标记来源机器。

解析优先级（见 get_node_id）：
1. 环境变量 NODE_ID（显式配置，集群部署推荐）
2. 持久化文件 NODE_ID_FILE（首次自动生成 UUID 并写入，保证重启不变）
3. hostname（文件不可写时的最终回退）

node_id 会做清洗：去除首尾空白并把 '.' 替换为 '-'，避免污染 AMQP topic
routing_key 的分段（routing_key 形如 video.alert.{node_id}.{alert_type}）。
"""

import json
import logging
import os
import socket
import uuid

from app.config import NODE_ID, NODE_ID_FILE

logger = logging.getLogger(__name__)

_cached_node_id = None
_cached_hostname = None


def _sanitize(value: str) -> str:
    """清洗 node_id：去空白，把 '.' 换成 '-'（避免破坏 AMQP routing_key 分段）。"""
    return (value or '').strip().replace('.', '-')


def get_hostname() -> str:
    """返回当前主机名（用于溯源/展示，不保证全局唯一）。"""
    global _cached_hostname
    if _cached_hostname is None:
        try:
            _cached_hostname = (socket.gethostname() or '').strip() or 'unknown-host'
        except Exception:
            _cached_hostname = 'unknown-host'
    return _cached_hostname


def _load_from_file() -> str:
    """从持久化文件读取 node_id，读取失败或不存在时返回空串。"""
    try:
        if not os.path.exists(NODE_ID_FILE):
            return ''
        with open(NODE_ID_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return _sanitize(str(data.get('node_id') or ''))
    except Exception as e:
        logger.warning(f"读取 node_id 文件 {NODE_ID_FILE} 失败: {e}")
        return ''


def _persist_to_file(node_id: str) -> bool:
    """原子写入 node_id 到持久化文件，成功返回 True。"""
    try:
        directory = os.path.dirname(NODE_ID_FILE) or '.'
        os.makedirs(directory, exist_ok=True)
        payload = {
            'node_id': node_id,
            'hostname': get_hostname(),
        }
        tmp_path = f"{NODE_ID_FILE}.tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, NODE_ID_FILE)  # 原子替换，避免多进程并发写产生半截文件
        return True
    except Exception as e:
        logger.warning(f"持久化 node_id 到 {NODE_ID_FILE} 失败（将回退到 hostname）: {e}")
        return False


def get_node_id() -> str:
    """
    返回当前实例的唯一编码。

    优先级：NODE_ID 环境变量 → 持久化文件 → 自动生成 UUID（并尝试持久化）→ hostname 兜底。
    进程内缓存，重复调用无额外 IO 开销。
    """
    global _cached_node_id
    if _cached_node_id is not None:
        return _cached_node_id

    # 1. 显式环境变量
    if NODE_ID:
        _cached_node_id = _sanitize(NODE_ID)
        logger.info(f"节点身份使用环境变量 NODE_ID: {_cached_node_id}")
        return _cached_node_id

    # 2. 持久化文件
    persisted = _load_from_file()
    if persisted:
        _cached_node_id = persisted
        logger.info(f"节点身份使用持久化文件: {_cached_node_id}")
        return _cached_node_id

    # 3. 自动生成 UUID 并持久化
    generated = _sanitize(str(uuid.uuid4()))
    if _persist_to_file(generated):
        _cached_node_id = generated
        logger.info(
            f"节点身份自动生成并持久化: {_cached_node_id}"
            "（集群部署建议在 .env 显式设置 NODE_ID 以便可读可追溯）"
        )
    else:
        # 4. 文件不可写 → hostname 兜底（集群下可能不唯一）
        _cached_node_id = _sanitize(get_hostname())
        logger.warning(
            f"节点身份回退到 hostname: {_cached_node_id}（集群下可能不唯一，建议显式设置 NODE_ID）"
        )
    return _cached_node_id
