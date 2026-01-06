"""
时间窗口检测器 - 纯内存实现
用于在时间窗口内统计检测结果，避免单次误报
"""
import time
from collections import deque
from typing import Dict, Tuple, Optional

from app import logger
from app.core.database_models import Algorithm


class WindowDetector:
    """纯内存时间窗口检测器"""
    
    def __init__(self):
        # 检测记录缓冲区: {(source_id, algo_id): deque([(timestamp, has_detection), ...])}
        self.buffers: Dict[Tuple[int, str], deque] = {}
        
        # 配置缓存: {(source_id, algo_id): config_dict}
        self.configs: Dict[Tuple[int, str], dict] = {}
        
        # 统计缓存（避免重复计算）: {(source_id, algo_id): (cache_time, stats)}
        self.stats_cache: Dict[Tuple[int, str], Tuple[float, dict]] = {}
        self.cache_ttl = 0.5  # 缓存有效期0.5秒
        
        # 清理任务
        self.cleanup_interval = 60  # 60秒清理一次
        self.last_cleanup = time.time()
        
        # 内存限制
        self.max_records_per_buffer = 3000  # 每个缓冲区最多3000条记录
        
    def load_config(self, source_id: int, algorithm_id: str):
        """
        从数据库加载窗口配置
        从Algorithm表读取时间窗口配置
        """
        key = (source_id, algorithm_id)
        
        try:
            # 获取Algorithm配置
            algorithm = Algorithm.get_by_id(algorithm_id)
            
            # 从算法表读取配置
            config = {
                'enable': algorithm.enable_window_check,
                'window_size': algorithm.window_size,
                'mode': algorithm.window_mode,
                'threshold': algorithm.window_threshold,
            }
            
            self.configs[key] = config
            logger.info(f"[WindowDetector] 加载配置 Source={source_id}, Algo={algorithm_id}: {config}")
            
        except Exception as e:
            logger.error(f"[WindowDetector] 加载配置失败 Source={source_id}, Algo={algorithm_id}: {e}")
            # 使用默认配置
            self.configs[key] = {
                'enable': False,
                'window_size': 30,
                'mode': 'ratio',
                'threshold': 0.3
            }
    
    def add_record(self, source_id: int, algorithm_id: str, timestamp: float, has_detection: bool, image_path: str = None):
        """
        添加检测记录（轻量级操作）
        
        Args:
            source_id: 视频源ID
            algorithm_id: 算法ID
            timestamp: 帧时间戳
            has_detection: 是否检测到目标
            image_path: 检测图片路径（仅在检测到目标时提供）
        """
        key = (source_id, algorithm_id)
        
        # 初始化缓冲区
        if key not in self.buffers:
            self.buffers[key] = deque(maxlen=self.max_records_per_buffer)
        
        # 添加记录（包含图片路径）
        record = (timestamp, has_detection, image_path)
        self.buffers[key].append(record)
        
        # 清除该算法的缓存
        if key in self.stats_cache:
            del self.stats_cache[key]
        
        # 定期清理
        self._periodic_cleanup()
    
    def check_condition(self, source_id: int, algorithm_id: str, current_time: float) -> Tuple[bool, Optional[dict]]:
        """
        检查是否满足窗口条件
        
        Args:
            source_id: 视频源ID
            algorithm_id: 算法ID
            current_time: 当前时间戳
            
        Returns:
            (是否通过, 窗口统计信息)
        """
        key = (source_id, algorithm_id)
        
        # 确保配置已加载
        if key not in self.configs:
            self.load_config(source_id, algorithm_id)
        
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
    
    def get_stats(self, source_id: int, algorithm_id: str, current_time: Optional[float] = None) -> dict:
        """
        获取当前窗口统计（用于监控面板）
        
        Args:
            source_id: 视频源ID
            algorithm_id: 算法ID
            current_time: 当前时间戳，None则使用当前时间
            
        Returns:
            窗口统计信息
        """
        if current_time is None:
            current_time = time.time()
        
        key = (source_id, algorithm_id)
        
        # 确保配置已加载
        if key not in self.configs:
            self.load_config(source_id, algorithm_id)
        
        config = self.configs[key]
        stats = self._get_window_stats(key, current_time, config)
        
        # 添加配置信息
        stats['config'] = config
        
        return stats
    
    def clear_buffer(self, source_id: int, algorithm_id: str):
        """清空指定的缓冲区"""
        key = (source_id, algorithm_id)
        if key in self.buffers:
            self.buffers[key].clear()
            logger.info(f"[WindowDetector] 清空缓冲区 Source={source_id}, Algo={algorithm_id}")
        if key in self.stats_cache:
            del self.stats_cache[key]
        if key in self.configs:
            del self.configs[key]
    
    def reload_config(self, source_id: int, algorithm_id: str):
        """重新加载配置（配置变更时调用）"""
        key = (source_id, algorithm_id)
        if key in self.configs:
            del self.configs[key]
        if key in self.stats_cache:
            del self.stats_cache[key]
        self.load_config(source_id, algorithm_id)
    
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
    
    def get_window_records(self, source_id: int, algorithm_id: str, current_time: float) -> list:
        """
        获取窗口内的所有检测记录
        
        Args:
            source_id: 视频源ID
            algorithm_id: 算法ID
            current_time: 当前时间戳
            
        Returns:
            窗口内的检测记录列表 [(timestamp, has_detection), ...]
        """
        key = (source_id, algorithm_id)
        
        if key not in self.buffers:
            return []
        
        # 确保配置已加载
        if key not in self.configs:
            self.load_config(source_id, algorithm_id)
        
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
    
    def get_detection_records(self, source_id: int, algorithm_id: str, current_time: float) -> list:
        """
        获取窗口内检测到目标的记录（用于保存图片）
        
        Args:
            source_id: 视频源ID
            algorithm_id: 算法ID
            current_time: 当前时间戳
            
        Returns:
            检测到目标的记录列表 [(timestamp, has_detection), ...]
        """
        window_records = self.get_window_records(source_id, algorithm_id, current_time)
        
        # 只返回检测到目标的记录
        detection_records = [
            (ts, detected, img_path) for ts, detected, img_path in window_records
            if detected
        ]
        
        return detection_records
    
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

