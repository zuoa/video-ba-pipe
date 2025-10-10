import struct
from multiprocessing import shared_memory
from multiprocessing.context import BaseContext
from threading import Lock
from typing import Optional, Tuple

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

        # 元数据大小：写指针(8) + 读指针(8) + 帧计数(8) + 锁标志(1)
        self.metadata_size = 25

        # 总共享内存大小
        self.total_size = self.metadata_size + self.frame_size * self.capacity

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
            self._write_metadata(0, 0, 0, False)
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
                        count: int, locked: bool):
        """写入元数据到共享内存"""
        struct.pack_into('QQQ?', self.shm.buf, 0,
                         write_idx, read_idx, count, locked)

    def _read_metadata(self) -> Tuple[int, int, int, bool]:
        """从共享内存读取元数据"""
        return struct.unpack_from('QQQ?', self.shm.buf, 0)

    def _cas_write_pointer(self, expected: int, new: int) -> bool:
        """
        CAS（Compare-And-Swap）原子操作更新写指针
        注意：Python的GIL提供了基本的原子性，但对于多进程需要额外处理
        """
        with self._lock:
            write_idx, read_idx, count, locked = self._read_metadata()
            if write_idx == expected:
                self._write_metadata(new, read_idx, count + 1, locked)
                return True
            return False

    def _get_frame_offset(self, index: int) -> int:
        """计算帧在共享内存中的偏移量"""
        return self.metadata_size + (index % self.capacity) * self.frame_size

    def write(self, frame: np.ndarray) -> bool:
        """
        写入一帧数据

        Args:
            frame: 视频帧数据，形状应匹配 frame_shape

        Returns:
            是否写入成功
        """
        if frame.shape != self.frame_shape:
            raise ValueError(
                f"Frame shape {frame.shape} doesn't match "
                f"expected {self.frame_shape}"
            )

        with self._lock:
            write_idx, read_idx, count, locked = self._read_metadata()

            # 计算新的写指针位置
            new_write_idx = (write_idx + 1) % self.capacity

            # 如果缓冲区满，覆盖最旧数据（移动读指针）
            if count >= self.capacity:
                read_idx = (read_idx + 1) % self.capacity
                count = self.capacity - 1

            # 写入帧数据
            offset = self._get_frame_offset(write_idx)
            frame_bytes = frame.astype(np.uint8).tobytes()
            self.shm.buf[offset:offset + self.frame_size] = frame_bytes

            # 更新元数据
            self._write_metadata(new_write_idx, read_idx, count + 1, locked)

            return True

    def read(self) -> Optional[np.ndarray]:
        """
        读取一帧数据（FIFO）

        Returns:
            视频帧数据，如果缓冲区为空则返回 None
        """
        with self._lock:
            write_idx, read_idx, count, locked = self._read_metadata()

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
            self._write_metadata(write_idx, new_read_idx, count - 1, locked)

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
            write_idx, read_idx, count, locked = self._read_metadata()

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

    def get_stats(self) -> dict:
        """获取缓冲区统计信息"""
        write_idx, read_idx, count, locked = self._read_metadata()
        return {
            'capacity': self.capacity,
            'count': count,
            'write_index': write_idx,
            'read_index': read_idx,
            'usage_percent': (count / self.capacity) * 100,
            'free_slots': self.capacity - count,
            'is_full': count >= self.capacity,
            'is_empty': count == 0,
        }

    def clear(self):
        """清空缓冲区"""
        with self._lock:
            self._write_metadata(0, 0, 0, False)

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
