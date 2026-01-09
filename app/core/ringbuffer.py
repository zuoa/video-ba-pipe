import struct
import time
from multiprocessing import shared_memory
from multiprocessing.context import BaseContext
from threading import Lock
from typing import Optional, Tuple, List

import numpy as np


class VideoRingBuffer:
    """
    基于共享内存的视频环形缓冲区
    - 支持零拷贝操作
    - 使用原子操作保证线程安全
    - FIFO溢出策略
    """

    def __init__(
            self,
            name: str,
            frame_shape: Tuple[int, int, int] = (1080, 1920, 3),
            fps: int = 30,
            duration_seconds: int = 10,
            create: bool = True,
            mp_context: Optional[BaseContext] = None
    ):
        """
        初始化环形缓冲区

        Args:
            name: 共享内存名称
            frame_shape: 单帧形状 (height, width, channels)
            fps: 帧率
            duration_seconds: 缓冲时长（秒）
            create: 是否创建新缓冲区（False则连接已存在的）
        """
        self.name = name
        self.frame_shape = frame_shape
        self.fps = fps
        self.capacity = fps * duration_seconds  # 总帧数

        # 计算单帧大小（字节）
        self.frame_size = int(np.prod(frame_shape))

        # 元数据大小：写指针(8) + 读指针(8) + 帧计数(8) + 锁标志(1) + 最后写入时间(8) + 连续错误数(8)
        self.metadata_size = 41
        
        # 时间戳数组大小：每帧一个double (8字节)
        self.timestamp_size = 8 * self.capacity

        # 总共享内存大小：元数据 + 时间戳数组 + 帧数据
        self.total_size = self.metadata_size + self.timestamp_size + self.frame_size * self.capacity

        # 创建或连接共享内存
        if create:
            try:
                # 尝试删除已存在的
                existing = shared_memory.SharedMemory(name=name)
                existing.close()
                existing.unlink()
            except FileNotFoundError:
                pass

            self.shm = shared_memory.SharedMemory(
                name=name,
                create=True,
                size=self.total_size
            )
            # 初始化元数据
            self._write_metadata(0, 0, 0, False, 0.0, 0)
        else:
            self.shm = shared_memory.SharedMemory(name=name)

        # 本地锁（用于单进程内的线程安全）
        if mp_context:
            # 如果在多进程环境中使用，创建一个进程锁
            self._lock = mp_context.Lock()
        else:
            # 否则，使用线程锁
            self._lock = Lock()

    def _write_metadata(self, write_idx: int, read_idx: int,
                        count: int, locked: bool, last_write_time: float = 0.0,
                        consecutive_errors: int = 0):
        """写入元数据到共享内存"""
        struct.pack_into('QQQ?dd', self.shm.buf, 0,
                         write_idx, read_idx, count, locked,
                         last_write_time, consecutive_errors)

    def _read_metadata(self) -> Tuple[int, int, int, bool, float, int]:
        """从共享内存读取元数据"""
        return struct.unpack_from('QQQ?dd', self.shm.buf, 0)

    def _cas_write_pointer(self, expected: int, new: int) -> bool:
        """
        CAS（Compare-And-Swap）原子操作更新写指针
        注意：Python的GIL提供了基本的原子性，但对于多进程需要额外处理
        """
        with self._lock:
            write_idx, read_idx, count, locked, last_write, errors = self._read_metadata()
            if write_idx == expected:
                self._write_metadata(new, read_idx, count + 1, locked, last_write, errors)
                return True
            return False

    def _get_frame_offset(self, index: int) -> int:
        """计算帧在共享内存中的偏移量"""
        return self.metadata_size + self.timestamp_size + (index % self.capacity) * self.frame_size
    
    def _get_timestamp_offset(self, index: int) -> int:
        """计算时间戳在共享内存中的偏移量"""
        return self.metadata_size + (index % self.capacity) * 8
    
    def _write_timestamp(self, index: int, timestamp: float):
        """写入时间戳"""
        offset = self._get_timestamp_offset(index)
        struct.pack_into('d', self.shm.buf, offset, timestamp)
    
    def _read_timestamp(self, index: int) -> float:
        """读取时间戳"""
        offset = self._get_timestamp_offset(index)
        return struct.unpack_from('d', self.shm.buf, offset)[0]

    def write(self, frame: np.ndarray, timestamp: Optional[float] = None) -> bool:
        """
        写入一帧数据

        Args:
            frame: 视频帧数据，形状应匹配 frame_shape
            timestamp: 帧的时间戳（秒），如果为None则使用当前时间

        Returns:
            是否写入成功
        """
        if frame.shape != self.frame_shape:
            raise ValueError(
                f"Frame shape {frame.shape} doesn't match "
                f"expected {self.frame_shape}"
            )

        if timestamp is None:
            timestamp = time.time()

        with self._lock:
            write_idx, read_idx, count, locked, last_write, errors = self._read_metadata()

            # 计算新的写指针位置
            new_write_idx = (write_idx + 1) % self.capacity

            # 如果缓冲区满，覆盖最旧数据（移动读指针）
            if count >= self.capacity:
                read_idx = (read_idx + 1) % self.capacity
                count = self.capacity - 1

            # 写入帧数据
            offset = self._get_frame_offset(write_idx)

            # 确保帧是uint8类型且是C连续的（内存布局连续）
            frame_data = np.ascontiguousarray(frame, dtype=np.uint8)

            # Validate size
            expected_size = int(np.prod(self.frame_shape))
            actual_size = frame_data.size
            if actual_size != expected_size:
                raise ValueError(
                    f"Frame size {actual_size} doesn't match "
                    f"expected {expected_size}. Frame shape: {frame.shape}, "
                    f"expected shape: {self.frame_shape}"
                )

            # 使用numpy数组视图直接写入共享内存
            # 创建一个指向共享内存的numpy数组视图
            shm_array = np.ndarray(
                shape=(self.frame_size,),
                dtype=np.uint8,
                buffer=self.shm.buf,
                offset=offset
            )
            # 将帧数据展平并复制
            shm_array[:] = frame_data.ravel()

            # 写入时间戳
            self._write_timestamp(write_idx, timestamp)

            # 更新元数据
            self._write_metadata(new_write_idx, read_idx, count + 1, locked,
                                 timestamp, 0)  # 重置连续错误计数

            return True

    def read(self) -> Optional[np.ndarray]:
        """
        读取一帧数据（FIFO）

        Returns:
            视频帧数据，如果缓冲区为空则返回 None
        """
        with self._lock:
            write_idx, read_idx, count, locked, last_write, errors = self._read_metadata()

            if count == 0:
                return None

            # 读取帧数据
            offset = self._get_frame_offset(read_idx)
            frame_bytes = bytes(self.shm.buf[offset:offset + self.frame_size])
            frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
                self.frame_shape
            )

            # 更新读指针
            new_read_idx = (read_idx + 1) % self.capacity
            self._write_metadata(write_idx, new_read_idx, count - 1, locked,
                                 last_write, errors)

            return frame.copy()  # 返回副本以避免数据竞争

    def peek(self, index: int = 0) -> Optional[np.ndarray]:
        """
        查看缓冲区中的帧但不移除

        Args:
            index: 相对于当前读位置的偏移（0=最旧，-1=最新）

        Returns:
            视频帧数据
        """
        with self._lock:
            write_idx, read_idx, count, locked, _, _ = self._read_metadata()

            if count == 0 or abs(index) >= count:
                return None

            # 计算实际索引
            if index < 0:
                # 负索引：从写位置往回数
                actual_idx = (write_idx + index) % self.capacity
            else:
                # 正索引：从读位置往前数
                actual_idx = (read_idx + index) % self.capacity

            offset = self._get_frame_offset(actual_idx)
            frame_bytes = bytes(self.shm.buf[offset:offset + self.frame_size])
            frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
                self.frame_shape
            )

            return frame.copy()

    def peek_with_timestamp(self, index: int = 0) -> Optional[Tuple[np.ndarray, float]]:
        """
        查看缓冲区中的帧及其时间戳但不移除

        Args:
            index: 相对于当前读位置的偏移（0=最旧，-1=最新）

        Returns:
            (视频帧数据, 时间戳) 或 None
        """
        with self._lock:
            write_idx, read_idx, count, locked, _, _ = self._read_metadata()

            if count == 0 or abs(index) >= count:
                return None

            # 计算实际索引
            if index < 0:
                # 负索引：从写位置往回数
                actual_idx = (write_idx + index) % self.capacity
            else:
                # 正索引：从读位置往前数
                actual_idx = (read_idx + index) % self.capacity

            # 读取帧数据
            offset = self._get_frame_offset(actual_idx)
            frame_bytes = bytes(self.shm.buf[offset:offset + self.frame_size])
            frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
                self.frame_shape
            )

            # 读取时间戳
            timestamp = self._read_timestamp(actual_idx)

            return frame.copy(), timestamp

    def get_recent_frames(self, seconds: float) -> List[Tuple[np.ndarray, float]]:
        """
        获取最近N秒的所有帧

        Args:
            seconds: 要获取的时间范围（秒）

        Returns:
            [(帧, 时间戳), ...] 按时间从旧到新排序
        """
        with self._lock:
            write_idx, read_idx, count, locked, _, _ = self._read_metadata()

            if count == 0:
                return []

            # 获取最新帧的时间戳
            latest_idx = (write_idx - 1) % self.capacity
            latest_timestamp = self._read_timestamp(latest_idx)
            cutoff_time = latest_timestamp - seconds

            frames = []

            # 从最旧的帧开始遍历
            for i in range(count):
                actual_idx = (read_idx + i) % self.capacity
                timestamp = self._read_timestamp(actual_idx)

                # 只收集在时间范围内的帧
                if timestamp >= cutoff_time:
                    offset = self._get_frame_offset(actual_idx)
                    frame_bytes = bytes(self.shm.buf[offset:offset + self.frame_size])
                    frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
                        self.frame_shape
                    )
                    frames.append((frame.copy(), timestamp))

            return frames

    def get_frames_in_time_range(self, start_time: float, end_time: float) -> List[Tuple[np.ndarray, float]]:
        """
        获取指定时间范围内的所有帧

        Args:
            start_time: 开始时间戳
            end_time: 结束时间戳

        Returns:
            [(帧, 时间戳), ...] 按时间从旧到新排序
        """
        with self._lock:
            write_idx, read_idx, count, locked, _, _ = self._read_metadata()

            if count == 0:
                return []

            frames = []

            # 遍历所有帧
            for i in range(count):
                actual_idx = (read_idx + i) % self.capacity
                timestamp = self._read_timestamp(actual_idx)

                # 只收集在时间范围内的帧
                if start_time <= timestamp <= end_time:
                    offset = self._get_frame_offset(actual_idx)
                    frame_bytes = bytes(self.shm.buf[offset:offset + self.frame_size])
                    frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
                        self.frame_shape
                    )
                    frames.append((frame.copy(), timestamp))

            return frames

    def get_stats(self) -> dict:
        """获取缓冲区统计信息"""
        write_idx, read_idx, count, locked, last_write, errors = self._read_metadata()
        return {
            'capacity': self.capacity,
            'count': count,
            'write_index': write_idx,
            'read_index': read_idx,
            'usage_percent': (count / self.capacity) * 100,
            'free_slots': self.capacity - count,
            'is_full': count >= self.capacity,
            'is_empty': count == 0,
            'last_write_time': last_write,
            'consecutive_errors': errors,
        }

    def clear(self):
        """清空缓冲区"""
        with self._lock:
            self._write_metadata(0, 0, 0, False, 0.0, 0)

    # ==================== 健康监控相关方法 ====================

    def get_last_write_time(self) -> float:
        """获取最后写入时间"""
        with self._lock:
            _, _, _, _, last_write, _ = self._read_metadata()
            return last_write

    def update_last_write_time(self, timestamp: float = None):
        """
        更新最后写入时间（用于健康监控）
        注意：此方法仅更新时间戳，不写入帧数据

        Args:
            timestamp: 时间戳，如果为None则使用当前时间
        """
        if timestamp is None:
            timestamp = time.time()

        with self._lock:
            write_idx, read_idx, count, locked, last_write, errors = self._read_metadata()
            self._write_metadata(write_idx, read_idx, count, locked, timestamp, errors)

    def get_consecutive_errors(self) -> int:
        """获取连续错误计数"""
        with self._lock:
            _, _, _, _, _, errors = self._read_metadata()
            return errors

    def increment_error_count(self):
        """增加错误计数"""
        with self._lock:
            write_idx, read_idx, count, locked, last_write, errors = self._read_metadata()
            self._write_metadata(write_idx, read_idx, count, locked, last_write, errors + 1)

    def reset_error_count(self):
        """重置错误计数"""
        with self._lock:
            write_idx, read_idx, count, locked, last_write, _ = self._read_metadata()
            self._write_metadata(write_idx, read_idx, count, locked, last_write, 0)

    def get_health_status(self) -> dict:
        """
        获取健康状态信息

        Returns:
            包含健康状态的字典
        """
        stats = self.get_stats()
        current_time = time.time()
        last_write = stats['last_write_time']

        # 如果 last_write_time 为 0，表示从未写入过帧
        if last_write == 0:
            time_since_last_frame = 0  # 未初始化，不计算时间差
        else:
            time_since_last_frame = current_time - last_write

        # 判断健康状态：
        # - 如果从未写入过帧 (frame_count == 0)，认为是初始化状态，不算不健康
        # - 如果有帧写入，检查最后写入时间
        frame_count = stats['count']
        if frame_count == 0:
            is_healthy = True  # 未初始化，默认健康
        else:
            is_healthy = time_since_last_frame < 30  # 30秒内有帧输出认为健康

        return {
            'last_write_time': last_write,
            'time_since_last_frame': time_since_last_frame,
            'consecutive_errors': stats['consecutive_errors'],
            'frame_count': frame_count,
            'is_healthy': is_healthy,
        }

    def close(self):
        """关闭共享内存连接"""
        self.shm.close()

    def unlink(self):
        """删除共享内存（仅创建者调用）"""
        self.shm.unlink()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# 使用示例
if __name__ == "__main__":
    # 创建缓冲区（生产者）
    print("=== 创建视频环形缓冲区 ===")
    buffer = VideoRingBuffer(
        name="video_buffer",
        frame_shape=(480, 640, 3),  # 较小的分辨率用于测试
        fps=30,
        duration_seconds=10,
        create=True
    )

    print(f"缓冲区容量: {buffer.capacity} 帧")
    print(f"共享内存大小: {buffer.total_size / (1024 ** 2):.2f} MB\n")

    # 写入测试数据
    print("=== 写入测试帧 ===")
    for i in range(50):
        # 生成测试帧（渐变色）
        frame = np.ones(buffer.frame_shape, dtype=np.uint8) * (i % 256)
        buffer.write(frame)

        if i % 10 == 0:
            stats = buffer.get_stats()
            print(f"写入 {i + 1} 帧 | "
                  f"缓冲区使用: {stats['count']}/{stats['capacity']} "
                  f"({stats['usage_percent']:.1f}%)")

    # 查看统计
    print("\n=== 缓冲区状态 ===")
    stats = buffer.get_stats()
    for key, value in stats.items():
        print(f"{key}: {value}")

    # 读取测试
    print("\n=== 读取测试 ===")
    for i in range(5):
        frame = buffer.read()
        if frame is not None:
            print(f"读取帧 {i + 1}: shape={frame.shape}, "
                  f"mean_value={frame.mean():.1f}")

    # Peek测试
    print("\n=== Peek测试 ===")
    oldest = buffer.peek(0)
    newest = buffer.peek(-1)
    print(f"最旧帧平均值: {oldest.mean():.1f}")
    print(f"最新帧平均值: {newest.mean():.1f}")

    # 清理
    stats = buffer.get_stats()
    print(f"\n最终缓冲区剩余: {stats['count']} 帧")

    buffer.close()
    buffer.unlink()
    print("\n缓冲区已清理")
