"""
时间窗口检测器 - 纯内存实现
用于在时间窗口内统计检测结果，避免单次误报

支持的抑制模式：
1. simple: 简单时间抑制（X秒内只触发1次）
2. window: 时间窗口检测（窗口内检测比例/连续次数达到阈值）
"""
import time
from collections import deque
from typing import Dict, Tuple, Optional

from app import logger


class WindowDetector:
    """纯内存时间窗口检测器"""

    def __init__(self):
        # 检测记录缓冲区: {(source_id, node_id): deque([(timestamp, has_detection, image_path), ...])}
        self.buffers: Dict[Tuple[int, str], deque] = {}

        # 触发条件配置: {(source_id, node_id): trigger_config_dict}
        self.configs: Dict[Tuple[int, str], dict] = {}

        # 告警抑制配置: {(source_id, node_id): suppression_config_dict}
        self.suppression_configs: Dict[Tuple[int, str], dict] = {}

        # 最后触发时间（用于抑制）: {(source_id, node_id): last_trigger_time}
        self.last_trigger_times: Dict[Tuple[int, str], float] = {}

        # 统计缓存（避免重复计算）: {(source_id, node_id): (cache_time, stats)}
        self.stats_cache: Dict[Tuple[int, str], Tuple[float, dict]] = {}
        self.cache_ttl = 0.5  # 缓存有效期0.5秒

        # 清理任务
        self.cleanup_interval = 60  # 60秒清理一次
        self.last_cleanup = time.time()

        # 内存限制
        self.max_records_per_buffer = 3000  # 每个缓冲区最多3000条记录

    def load_trigger_condition(self, source_id: int, node_id: str, trigger_config: dict):
        """
        加载触发条件配置（窗口检测）

        Args:
            source_id: 视频源ID
            node_id: 节点ID（Alert 节点）
            trigger_config: 触发条件配置，包含:
                - enable: 是否启用窗口检测
                - window_size: 时间窗口大小（秒）
                - mode: 检测模式 ('ratio', 'consecutive', 'count')
                - threshold: 检测阈值
        """
        key = (source_id, node_id)

        if not trigger_config or not trigger_config.get('enable', False):
            # 未启用窗口检测，使用默认通过配置
            self.configs[key] = {
                'enable': False,
                'type': 'trigger_condition',
                'mode': 'ratio',
                'window_size': 30,
                'threshold': 0.3,
            }
            logger.info(f"[WindowDetector] 节点 {node_id} 未启用窗口检测，默认通过所有检测")
        else:
            self.configs[key] = {
                'enable': True,
                'type': 'trigger_condition',
                'mode': trigger_config.get('mode', 'ratio'),
                'window_size': trigger_config.get('window_size', 30),
                'threshold': trigger_config.get('threshold', 0.3),
            }
            logger.info(f"[WindowDetector] 加载触发条件配置 Source={source_id}, Node={node_id}: {self.configs[key]}")

    def load_suppression(self, source_id: int, node_id: str, suppression_config: dict):
        """
        加载告警抑制配置（触发后冷却期）

        Args:
            source_id: 视频源ID
            node_id: 节点ID（Alert 节点）
            suppression_config: 抑制配置，包含:
                - enable: 是否启用抑制
                - seconds: 抑制时长（秒）
        """
        key = (source_id, node_id)

        if not suppression_config or not suppression_config.get('enable', False):
            # 未启用抑制，删除抑制配置
            if key in self.suppression_configs:
                del self.suppression_configs[key]
                logger.info(f"[WindowDetector] 节点 {node_id} 禁用告警抑制")
        else:
            self.suppression_configs[key] = {
                'enable': True,
                'seconds': suppression_config.get('seconds', 60),
            }
            logger.info(f"[WindowDetector] 加载告警抑制配置 Source={source_id}, Node={node_id}: {self.suppression_configs[key]}")

    def add_record(self, source_id: int, node_id: str, timestamp: float, has_detection: bool, image_path: str = None):
        """
        添加检测记录（轻量级操作）

        Args:
            source_id: 视频源ID
            node_id: 节点ID
            timestamp: 帧时间戳
            has_detection: 是否检测到目标
            image_path: 检测图片路径（仅在检测到目标时提供）
        """
        key = (source_id, node_id)

        # 初始化缓冲区
        if key not in self.buffers:
            self.buffers[key] = deque(maxlen=self.max_records_per_buffer)

        # 添加记录（包含图片路径）
        record = (timestamp, has_detection, image_path)
        self.buffers[key].append(record)

        # 清除该节点的缓存
        if key in self.stats_cache:
            del self.stats_cache[key]

        # 定期清理
        self._periodic_cleanup()

    def check_condition(self, source_id: int, node_id: str, current_time: float) -> Tuple[bool, Optional[dict]]:
        """
        检查是否满足触发条件（窗口检测）

        Args:
            source_id: 视频源ID
            node_id: 节点ID
            current_time: 当前时间戳

        Returns:
            (是否通过, 窗口统计信息)
        """
        key = (source_id, node_id)

        # 未配置时，默认通过（不进行窗口检测）
        if key not in self.configs:
            logger.warning(f"[WindowDetector] 节点 {node_id} 未配置触发条件，默认通过")
            return True, None

        config = self.configs[key]

        # 未启用窗口检测，直接返回True
        if not config.get('enable', False):
            return True, None

        # 获取窗口统计
        stats = self._get_window_stats(key, current_time, config)

        # 根据模式判断
        mode = config['mode']
        threshold = config['threshold']

        if mode == 'count':
            passed = stats['detection_count'] >= threshold
        elif mode == 'ratio':
            passed = stats['detection_ratio'] >= threshold
        elif mode == 'consecutive':
            passed = stats['max_consecutive'] >= threshold
        else:
            logger.warning(f"[WindowDetector] 未知的窗口模式: {mode}，默认不通过")
            passed = False

        return passed, stats

    def check_suppression(self, source_id: int, node_id: str, current_time: float) -> Tuple[bool, Optional[dict]]:
        """
        检查是否在抑制期内（触发后冷却期）

        Args:
            source_id: 视频源ID
            node_id: 节点ID
            current_time: 当前时间戳

        Returns:
            (是否不在抑制期, 抑制状态信息)
        """
        key = (source_id, node_id)

        # 未配置抑制，默认不抑制
        if key not in self.suppression_configs:
            return True, None

        suppression = self.suppression_configs[key]

        # 未启用抑制，直接通过
        if not suppression.get('enable', False):
            return True, None

        # 检查是否在抑制期内
        last_trigger = self.last_trigger_times.get(key, 0)
        cooldown_seconds = suppression['seconds']

        if current_time - last_trigger < cooldown_seconds:
            # 在抑制期内
            time_since_trigger = current_time - last_trigger
            logger.info(
                f"[WindowDetector] 告警抑制中 Source={source_id}, Node={node_id}, "
                f"已过{time_since_trigger:.2f}秒，还需{cooldown_seconds - time_since_trigger:.2f}秒"
            )
            return False, {
                'suppressed': True,
                'cooldown_remaining': cooldown_seconds - time_since_trigger,
                'last_trigger_time': last_trigger
            }

        # 不在抑制期内，通过
        return True, None

    def record_trigger(self, source_id: int, node_id: str, trigger_time: float):
        """
        记录告警触发时间（用于抑制计算）

        Args:
            source_id: 视频源ID
            node_id: 节点ID
            trigger_time: 触发时间戳
        """
        key = (source_id, node_id)
        self.last_trigger_times[key] = trigger_time

        # 获取抑制配置用于日志
        if key in self.suppression_configs:
            cooldown = self.suppression_configs[key]['seconds']
            logger.info(f"[WindowDetector] 记录告警触发 Source={source_id}, Node={node_id}，开始{cooldown}秒抑制期")
        else:
            logger.info(f"[WindowDetector] 记录告警触发 Source={source_id}, Node={node_id}，未配置抑制")

    def _get_window_stats(self, key: Tuple[int, str], current_time: float, config: dict) -> dict:
        """获取窗口统计（带缓存）"""
        
        # 检查缓存
        if key in self.stats_cache:
            cache_time, cached_stats = self.stats_cache[key]
            if current_time - cache_time < self.cache_ttl:
                return cached_stats
        
        # 计算统计
        stats = self._calculate_stats(key, current_time, config)
        
        # 更新缓存
        self.stats_cache[key] = (current_time, stats)
        
        return stats
    
    def _calculate_stats(self, key: Tuple[int, str], current_time: float, config: dict) -> dict:
        """计算窗口统计"""
        if key not in self.buffers:
            return self._empty_stats()
        
        window_size = config['window_size']
        window_start = current_time - window_size
        
        records = self.buffers[key]
        
        # 过滤窗口内的记录
        window_records = [
            (ts, detected, img_path) for ts, detected, img_path in records
            if ts >= window_start
        ]
        
        if not window_records:
            return self._empty_stats()
        
        # 统计
        total_count = len(window_records)
        detection_count = sum(1 for _, detected, _ in window_records if detected)
        detection_ratio = detection_count / total_count if total_count > 0 else 0
        
        # 计算最大连续检测数
        max_consecutive = self._calc_max_consecutive(window_records)
        
        return {
            'total_count': total_count,
            'detection_count': detection_count,
            'detection_ratio': detection_ratio,
            'max_consecutive': max_consecutive,
            'window_start': window_start,
            'window_end': current_time,
            'window_size': window_size
        }
    
    def _calc_max_consecutive(self, records: list) -> int:
        """计算最大连续检测次数"""
        max_count = 0
        current_count = 0
        
        for _, detected, _ in records:
            if detected:
                current_count += 1
                max_count = max(max_count, current_count)
            else:
                current_count = 0
        
        return max_count
    
    def _periodic_cleanup(self):
        """定期清理过期数据"""
        current_time = time.time()
        if current_time - self.last_cleanup < self.cleanup_interval:
            return
        
        # 清理过期的统计缓存
        expired_keys = [
            key for key, (cache_time, _) in self.stats_cache.items()
            if current_time - cache_time > 60  # 缓存超过60秒清理
        ]
        for key in expired_keys:
            del self.stats_cache[key]
        
        if expired_keys:
            logger.debug(f"[WindowDetector] 清理了 {len(expired_keys)} 个过期统计缓存")
        
        self.last_cleanup = current_time
    
    def get_stats(self, source_id: int, node_id: str, current_time: Optional[float] = None) -> dict:
        """
        获取当前窗口统计（用于监控面板）

        Args:
            source_id: 视频源ID
            node_id: 节点ID
            current_time: 当前时间戳，None则使用当前时间

        Returns:
            窗口统计信息
        """
        if current_time is None:
            current_time = time.time()

        key = (source_id, node_id)

        # 未配置时返回空统计
        if key not in self.configs:
            return self._empty_stats()

        config = self.configs[key]
        stats = self._get_window_stats(key, current_time, config)

        # 添加配置信息
        stats['config'] = config

        return stats

    def clear_buffer(self, source_id: int, node_id: str):
        """清空指定的缓冲区"""
        key = (source_id, node_id)
        if key in self.buffers:
            self.buffers[key].clear()
            logger.info(f"[WindowDetector] 清空缓冲区 Source={source_id}, Node={node_id}")
        if key in self.stats_cache:
            del self.stats_cache[key]
        if key in self.configs:
            del self.configs[key]
        if key in self.suppression_configs:
            del self.suppression_configs[key]
            logger.info(f"[WindowDetector] 清空抑制配置 Source={source_id}, Node={node_id}")
        if key in self.last_trigger_times:
            del self.last_trigger_times[key]
            logger.info(f"[WindowDetector] 清空触发时间记录 Source={source_id}, Node={node_id}")
    
    def get_memory_usage(self) -> dict:
        """获取内存使用情况"""
        buffer_count = len(self.buffers)
        total_records = sum(len(buf) for buf in self.buffers.values())
        
        # 估算内存使用（每条记录约9字节）
        estimated_memory_bytes = total_records * 9
        estimated_memory_mb = estimated_memory_bytes / (1024 * 1024)
        
        return {
            'buffer_count': buffer_count,
            'total_records': total_records,
            'estimated_memory_mb': round(estimated_memory_mb, 2),
            'cache_count': len(self.stats_cache)
        }
    
    def get_window_records(self, source_id: int, node_id: str, current_time: float) -> list:
        """
        获取窗口内的所有检测记录

        Args:
            source_id: 视频源ID
            node_id: 节点ID
            current_time: 当前时间戳

        Returns:
            窗口内的检测记录列表 [(timestamp, has_detection, image_path), ...]
        """
        key = (source_id, node_id)

        if key not in self.buffers:
            return []

        # 确保配置已加载
        if key not in self.configs:
            return []

        config = self.configs[key]
        window_size = config['window_size']
        window_start = current_time - window_size

        records = self.buffers[key]

        # 过滤窗口内的记录
        window_records = [
            (ts, detected, img_path) for ts, detected, img_path in records
            if ts >= window_start
        ]

        return window_records

    def get_detection_records(self, source_id: int, node_id: str, current_time: float) -> list:
        """
        获取窗口内检测到目标的记录（用于保存图片）

        Args:
            source_id: 视频源ID
            node_id: 节点ID
            current_time: 当前时间戳

        Returns:
            检测到目标的记录列表 [(timestamp, has_detection, image_path), ...]
        """
        window_records = self.get_window_records(source_id, node_id, current_time)

        # 只返回检测到目标的记录
        detection_records = [
            (ts, detected, img_path) for ts, detected, img_path in window_records
            if detected
        ]

        return detection_records

    def update_last_image_path(self, source_id: int, node_id: str, image_path: str):
        """
        更新最后一条记录的图片路径

        用于在检测到目标后，更新该帧的图片路径

        Args:
            source_id: 视频源ID
            node_id: 节点ID
            image_path: 检测图片路径
        """
        key = (source_id, node_id)

        if key not in self.buffers or len(self.buffers[key]) == 0:
            return

        # 更新最后一条记录的图片路径
        buffer = self.buffers[key]
        timestamp, has_detection, _ = buffer[-1]
        buffer[-1] = (timestamp, has_detection, image_path)

        # 清除该节点的缓存
        if key in self.stats_cache:
            del self.stats_cache[key]

    def _empty_stats(self) -> dict:
        """返回空统计"""
        return {
            'total_count': 0,
            'detection_count': 0,
            'detection_ratio': 0.0,
            'max_consecutive': 0,
            'window_start': 0,
            'window_end': 0,
            'window_size': 0
        }


# 全局单例
_window_detector_instance = None


def get_window_detector() -> WindowDetector:
    """获取全局WindowDetector实例（单例模式）"""
    global _window_detector_instance
    if _window_detector_instance is None:
        _window_detector_instance = WindowDetector()
        logger.info("[WindowDetector] 初始化全局实例")
    return _window_detector_instance

