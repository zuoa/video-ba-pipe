"""平台节点身份模块

提供当前实例的唯一编码（node_id），用于集群部署和 MQ 推送时标记来源机器。

解析优先级（见 get_node_id）：
1. 环境变量 NODE_ID（显式配置，集群部署推荐）
2. 持久化文件 NODE_ID_FILE（保证重启后身份不变）
3. 当前实例可用的 MAC 地址（并写入持久化文件）
4. 自动生成 UUID（MAC 不可用时，并写入持久化文件）
5. hostname（文件不可写时的最终回退）

node_id 会做清洗：去除首尾空白并把 '.' 替换为 '-'，避免污染 AMQP topic
routing_key 的分段（routing_key 形如 video.alert.{node_id}.{alert_type}）。
"""

import json
import logging
import os
import socket
import uuid
from contextlib import contextmanager

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux 容器运行时始终可用
    fcntl = None

from app.config import NODE_ID, NODE_ID_FILE

logger = logging.getLogger(__name__)

_cached_node_id = None
_cached_node_id_source = None
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


def _load_from_file() -> tuple[str, str]:
    """从持久化文件读取 node_id 和来源，读取失败或不存在时返回空值。"""
    try:
        if not os.path.exists(NODE_ID_FILE):
            return '', ''
        with open(NODE_ID_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        node_id = _sanitize(str(data.get('node_id') or ''))
        source = str(data.get('source') or 'persistent_file').strip()
        return node_id, source
    except Exception as e:
        logger.warning(f"读取 node_id 文件 {NODE_ID_FILE} 失败: {e}")
        return '', ''


def _persist_to_file(node_id: str, source: str) -> bool:
    """原子写入 node_id 到持久化文件，成功返回 True。"""
    try:
        directory = os.path.dirname(NODE_ID_FILE) or '.'
        os.makedirs(directory, exist_ok=True)
        payload = {
            'node_id': node_id,
            'source': source,
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


@contextmanager
def _identity_file_lock():
    """让共享数据卷上的多个服务只生成一次节点身份。"""
    directory = os.path.dirname(NODE_ID_FILE) or '.'
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as e:
        logger.warning(f"无法创建 node_id 目录 {directory}，跳过文件锁: {e}")
        yield
        return
    if fcntl is None:
        yield
        return
    lock_path = f'{NODE_ID_FILE}.lock'
    try:
        lock_file = open(lock_path, 'a', encoding='utf-8')
    except OSError as e:
        logger.warning(f"无法打开 node_id 锁文件 {lock_path}，继续无锁解析: {e}")
        yield
        return
    with lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except OSError as e:
            logger.warning(f"无法锁定 node_id 文件 {lock_path}，继续无锁解析: {e}")
            yield
            return
        try:
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _get_mac_address() -> str:
    """返回系统首选 MAC；uuid.getnode 随机回退值会被识别并忽略。"""
    try:
        value = uuid.getnode()
        first_octet = (value >> 40) & 0xFF
        # uuid.getnode 无法取得网卡地址时会生成带 multicast 位的随机值。
        if value <= 0 or value >= (1 << 48) or first_octet & 0x01:
            return ''
        return ':'.join(f'{(value >> shift) & 0xFF:02x}' for shift in range(40, -1, -8))
    except Exception as e:
        logger.warning(f"读取 MAC 地址失败: {e}")
        return ''


def get_node_id() -> str:
    """
    返回当前实例的唯一编码。

    优先级：NODE_ID 环境变量 → 持久化文件 → MAC 地址 → 自动生成 UUID → hostname 兜底。
    进程内缓存，重复调用无额外 IO 开销。
    """
    global _cached_node_id, _cached_node_id_source
    if _cached_node_id is not None:
        return _cached_node_id

    # 1. 显式环境变量
    if NODE_ID:
        _cached_node_id = _sanitize(NODE_ID)
        _cached_node_id_source = 'environment'
        logger.info(f"节点身份使用环境变量 NODE_ID: {_cached_node_id}")
        return _cached_node_id

    with _identity_file_lock():
        # 2. 持久化文件。锁内再次读取，避免多个容器首次启动时各自生成不同身份。
        persisted, persisted_source = _load_from_file()
        if persisted:
            _cached_node_id = persisted
            _cached_node_id_source = persisted_source
            logger.info(f"节点身份使用持久化文件: {_cached_node_id}")
            return _cached_node_id

        # 3. 优先使用 MAC 地址并持久化，保证身份可识别且容器重启后保持不变。
        mac_address = _get_mac_address()
        if mac_address and _persist_to_file(mac_address, 'mac'):
            _cached_node_id = mac_address
            _cached_node_id_source = 'mac'
            logger.info(f"节点身份使用 MAC 地址并已持久化: {_cached_node_id}")
            return _cached_node_id

        # 4. MAC 不可用时自动生成 UUID 并持久化
        generated = _sanitize(str(uuid.uuid4()))
        if _persist_to_file(generated, 'uuid'):
            _cached_node_id = generated
            _cached_node_id_source = 'uuid'
            logger.info(
                f"节点身份自动生成并持久化: {_cached_node_id}"
                "（集群部署建议在 .env 显式设置 NODE_ID 以便可读可追溯）"
            )
        else:
            # 5. 文件不可写 → hostname 兜底（集群下可能不唯一）
            _cached_node_id = _sanitize(get_hostname())
            _cached_node_id_source = 'hostname'
            logger.warning(
                f"节点身份回退到 hostname: {_cached_node_id}（集群下可能不唯一，建议显式设置 NODE_ID）"
            )
    return _cached_node_id


def get_node_identity() -> dict[str, str]:
    """返回适合运维界面展示的当前节点身份信息。"""
    node_id = get_node_id()
    return {
        'node_id': node_id,
        'source': _cached_node_id_source or 'unknown',
        'hostname': get_hostname(),
    }
