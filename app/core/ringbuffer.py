import struct
import time
from multiprocessing import shared_memory
from multiprocessing.context import BaseContext
from threading import Lock
from typing import Iterator, List, Optional, Tuple

import numpy as np

from app.core.frame_utils import (
    ensure_frame_array,
    get_frame_size_bytes,
    get_storage_shape,
    infer_frame_dimensions,
    normalize_pixel_format,
    reshape_frame,
)


class VideoRingBuffer:
    """
    基于共享内存的原始视频帧环形缓冲区。

    当前主路径以 NV12 为默认像素格式，但该缓冲区保留了按像素格式
    计算 frame_size / storage_shape 的通用能力。
    """

    METADATA_FORMAT = 'QQQ?dd'

    def __init__(
        self,
        name: str,
        frame_shape: Optional[Tuple[int, ...]] = None,
        fps: int = 30,
        duration_seconds: int = 10,
        create: bool = True,
        mp_context: Optional[BaseContext] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        pixel_format: str = 'nv12',
    ):
        self.name = name
        self.pixel_format = normalize_pixel_format(pixel_format)
        self.width, self.height = self._resolve_dimensions(
            frame_shape=frame_shape,
            width=width,
            height=height,
            pixel_format=self.pixel_format,
        )
        self.frame_shape = get_storage_shape(self.width, self.height, self.pixel_format)
        self.fps = fps
        self.capacity = fps * duration_seconds

        self.frame_size = get_frame_size_bytes(self.width, self.height, self.pixel_format)
        self.metadata_size = struct.calcsize(self.METADATA_FORMAT)
        self.timestamp_size = 8 * self.capacity
        self.total_size = self.metadata_size + self.timestamp_size + self.frame_size * self.capacity

        if create:
            try:
                existing = shared_memory.SharedMemory(name=name)
                existing.close()
                existing.unlink()
            except FileNotFoundError:
                pass

            self.shm = shared_memory.SharedMemory(name=name, create=True, size=self.total_size)
            self._write_metadata(0, 0, 0, False, 0.0, 0)
        else:
            self.shm = shared_memory.SharedMemory(name=name)

        self._lock = mp_context.Lock() if mp_context else Lock()

    @staticmethod
    def _resolve_dimensions(
        frame_shape: Optional[Tuple[int, ...]],
        width: Optional[int],
        height: Optional[int],
        pixel_format: str,
    ) -> Tuple[int, int]:
        if width is not None and height is not None:
            return int(width), int(height)

        if frame_shape is None:
            raise ValueError("width/height or frame_shape must be provided")

        shape = tuple(int(value) for value in frame_shape)
        if len(shape) == 3:
            return int(shape[1]), int(shape[0])

        if len(shape) == 2 and pixel_format in {'nv12', 'nv21', 'yuv420p'}:
            return infer_frame_dimensions(
                np.empty(shape, dtype=np.uint8),
                pixel_format=pixel_format,
            )

        raise ValueError(f"Cannot infer dimensions from frame_shape={frame_shape}, pixel_format={pixel_format}")

    def _write_metadata(
        self,
        write_idx: int,
        read_idx: int,
        count: int,
        locked: bool,
        last_write_time: float = 0.0,
        consecutive_errors: int = 0,
    ):
        struct.pack_into(
            self.METADATA_FORMAT,
            self.shm.buf,
            0,
            write_idx,
            read_idx,
            count,
            locked,
            last_write_time,
            consecutive_errors,
        )

    def _read_metadata(self) -> Tuple[int, int, int, bool, float, int]:
        return struct.unpack_from(self.METADATA_FORMAT, self.shm.buf, 0)

    def _get_frame_offset(self, index: int) -> int:
        return self.metadata_size + self.timestamp_size + (index % self.capacity) * self.frame_size

    def _get_timestamp_offset(self, index: int) -> int:
        return self.metadata_size + (index % self.capacity) * 8

    def _write_timestamp(self, index: int, timestamp: float):
        struct.pack_into('d', self.shm.buf, self._get_timestamp_offset(index), timestamp)

    def _read_timestamp(self, index: int) -> float:
        return struct.unpack_from('d', self.shm.buf, self._get_timestamp_offset(index))[0]

    def _frame_from_shm(self, offset: int) -> np.ndarray:
        frame_bytes = bytes(self.shm.buf[offset:offset + self.frame_size])
        return reshape_frame(frame_bytes, self.width, self.height, self.pixel_format).copy()

    def write(self, frame: np.ndarray | bytes | bytearray | memoryview, timestamp: Optional[float] = None) -> bool:
        if timestamp is None:
            timestamp = time.time()

        frame_data = ensure_frame_array(frame, self.width, self.height, self.pixel_format)

        with self._lock:
            write_idx, read_idx, count, locked, _, _ = self._read_metadata()
            new_write_idx = (write_idx + 1) % self.capacity

            if count >= self.capacity:
                read_idx = (read_idx + 1) % self.capacity
                count = self.capacity - 1

            offset = self._get_frame_offset(write_idx)
            shm_array = np.ndarray(
                shape=(self.frame_size,),
                dtype=np.uint8,
                buffer=self.shm.buf,
                offset=offset,
            )
            shm_array[:] = frame_data.reshape(-1)

            self._write_timestamp(write_idx, timestamp)
            self._write_metadata(new_write_idx, read_idx, count + 1, locked, timestamp, 0)
            return True

    def read(self) -> Optional[np.ndarray]:
        with self._lock:
            write_idx, read_idx, count, locked, last_write, errors = self._read_metadata()
            if count == 0:
                return None

            offset = self._get_frame_offset(read_idx)
            frame = self._frame_from_shm(offset)
            new_read_idx = (read_idx + 1) % self.capacity
            self._write_metadata(write_idx, new_read_idx, count - 1, locked, last_write, errors)
            return frame

    def peek(self, index: int = 0) -> Optional[np.ndarray]:
        result = self.peek_with_timestamp(index)
        if result is None:
            return None
        return result[0]

    def peek_with_timestamp(self, index: int = 0) -> Optional[Tuple[np.ndarray, float]]:
        with self._lock:
            write_idx, read_idx, count, _, _, _ = self._read_metadata()
            if count == 0 or abs(index) >= count:
                return None

            actual_idx = (write_idx + index) % self.capacity if index < 0 else (read_idx + index) % self.capacity
            offset = self._get_frame_offset(actual_idx)
            timestamp = self._read_timestamp(actual_idx)
            return self._frame_from_shm(offset), timestamp

    def iter_frames_in_time_range(self, start_time: float, end_time: float) -> Iterator[Tuple[np.ndarray, float]]:
        matching_slots: List[Tuple[bytes, float]] = []
        with self._lock:
            _, read_idx, count, _, _, _ = self._read_metadata()
            for i in range(count):
                actual_idx = (read_idx + i) % self.capacity
                timestamp = self._read_timestamp(actual_idx)
                if start_time <= timestamp <= end_time:
                    offset = self._get_frame_offset(actual_idx)
                    matching_slots.append((bytes(self.shm.buf[offset:offset + self.frame_size]), timestamp))

        for frame_bytes, timestamp in matching_slots:
            yield reshape_frame(frame_bytes, self.width, self.height, self.pixel_format).copy(), timestamp

    def get_recent_frames(self, seconds: float) -> List[Tuple[np.ndarray, float]]:
        with self._lock:
            write_idx, _, count, _, _, _ = self._read_metadata()
            if count == 0:
                return []
            latest_idx = (write_idx - 1) % self.capacity
            latest_timestamp = self._read_timestamp(latest_idx)
        return list(self.iter_frames_in_time_range(latest_timestamp - seconds, latest_timestamp))

    def get_frames_in_time_range(self, start_time: float, end_time: float) -> List[Tuple[np.ndarray, float]]:
        return list(self.iter_frames_in_time_range(start_time, end_time))

    def get_stats(self) -> dict:
        write_idx, read_idx, count, locked, last_write, errors = self._read_metadata()
        return {
            'capacity': self.capacity,
            'count': count,
            'write_index': write_idx,
            'read_index': read_idx,
            'usage_percent': (count / self.capacity) * 100 if self.capacity else 0,
            'free_slots': self.capacity - count,
            'is_full': count >= self.capacity,
            'is_empty': count == 0,
            'last_write_time': last_write,
            'consecutive_errors': errors,
            'width': self.width,
            'height': self.height,
            'pixel_format': self.pixel_format,
            'frame_shape': self.frame_shape,
            'frame_size': self.frame_size,
        }

    def clear(self):
        with self._lock:
            self._write_metadata(0, 0, 0, False, 0.0, 0)

    def get_last_write_time(self) -> float:
        with self._lock:
            _, _, _, _, last_write, _ = self._read_metadata()
            return last_write

    def update_last_write_time(self, timestamp: float = None):
        if timestamp is None:
            timestamp = time.time()

        with self._lock:
            write_idx, read_idx, count, locked, _, errors = self._read_metadata()
            self._write_metadata(write_idx, read_idx, count, locked, timestamp, errors)

    def get_consecutive_errors(self) -> int:
        with self._lock:
            _, _, _, _, _, errors = self._read_metadata()
            return errors

    def increment_error_count(self):
        with self._lock:
            write_idx, read_idx, count, locked, last_write, errors = self._read_metadata()
            self._write_metadata(write_idx, read_idx, count, locked, last_write, errors + 1)

    def reset_error_count(self):
        with self._lock:
            write_idx, read_idx, count, locked, last_write, _ = self._read_metadata()
            self._write_metadata(write_idx, read_idx, count, locked, last_write, 0)

    def get_health_status(self) -> dict:
        stats = self.get_stats()
        current_time = time.time()
        last_write = stats['last_write_time']
        time_since_last_frame = 0 if last_write == 0 else current_time - last_write
        frame_count = stats['count']
        is_healthy = True if frame_count == 0 else time_since_last_frame < 30
        return {
            'last_write_time': last_write,
            'time_since_last_frame': time_since_last_frame,
            'consecutive_errors': stats['consecutive_errors'],
            'frame_count': frame_count,
            'is_healthy': is_healthy,
        }

    def close(self):
        self.shm.close()

    def unlink(self):
        self.shm.unlink()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


if __name__ == "__main__":
    print("=== 创建视频环形缓冲区 ===")
    buffer = VideoRingBuffer(
        name="video_buffer",
        width=640,
        height=480,
        pixel_format="nv12",
        fps=30,
        duration_seconds=10,
        create=True,
    )

    print(f"缓冲区容量: {buffer.capacity} 帧")
    print(f"共享内存大小: {buffer.total_size / (1024 ** 2):.2f} MB\n")

    print("=== 写入测试帧 ===")
    for i in range(50):
        frame = np.ones(buffer.frame_shape, dtype=np.uint8) * (i % 256)
        buffer.write(frame)

        if i % 10 == 0:
            stats = buffer.get_stats()
            print(
                f"写入 {i + 1} 帧 | 缓冲区使用: {stats['count']}/{stats['capacity']} "
                f"({stats['usage_percent']:.1f}%)"
            )

    print("\n=== 缓冲区状态 ===")
    stats = buffer.get_stats()
    for key, value in stats.items():
        print(f"{key}: {value}")

    print("\n=== 读取测试 ===")
    for i in range(5):
        frame = buffer.read()
        if frame is not None:
            print(f"读取帧 {i + 1}: shape={frame.shape}, mean_value={frame.mean():.1f}")

    print("\n=== Peek测试 ===")
    oldest = buffer.peek(0)
    newest = buffer.peek(-1)
    print(f"最旧帧平均值: {oldest.mean():.1f}")
    print(f"最新帧平均值: {newest.mean():.1f}")
