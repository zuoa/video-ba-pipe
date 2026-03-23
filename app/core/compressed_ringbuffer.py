import os
import struct
import time
from contextlib import contextmanager
from multiprocessing import shared_memory
from multiprocessing.context import BaseContext
from threading import Lock
from typing import List, Optional, Tuple

import cv2
import numpy as np

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


class CompressedVideoRingBuffer:
    """
    基于共享内存的压缩视频环形缓冲区。

    用固定大小 slot 保存 JPEG 编码后的单帧，显著降低录制链路的内存占用。
    对外暴露与 VideoRingBuffer 基本一致的读取接口，返回 RGB 帧和时间戳。
    """

    METADATA_FORMAT = '<QQQ?dd'
    TIMESTAMP_FORMAT = '<d'
    LENGTH_FORMAT = '<I'

    def __init__(
        self,
        name: str,
        frame_shape: Tuple[int, int, int] = (1080, 1920, 3),
        fps: int = 10,
        duration_seconds: int = 10,
        create: bool = True,
        max_frame_bytes: int = 1024 * 1024,
        jpeg_quality: int = 85,
        mp_context: Optional[BaseContext] = None,
    ):
        self.name = name
        self.frame_shape = frame_shape
        self.fps = fps
        self.capacity = fps * duration_seconds
        self.max_frame_bytes = max_frame_bytes
        self.jpeg_quality = jpeg_quality

        self.metadata_size = struct.calcsize(self.METADATA_FORMAT)
        self.timestamp_size = struct.calcsize(self.TIMESTAMP_FORMAT) * self.capacity
        self.length_size = struct.calcsize(self.LENGTH_FORMAT) * self.capacity
        self.total_size = (
            self.metadata_size
            + self.timestamp_size
            + self.length_size
            + self.max_frame_bytes * self.capacity
        )

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

        if mp_context:
            self._lock = mp_context.Lock()
        else:
            self._lock = Lock()

        lock_dir = '/tmp/video_ba_pipe_locks'
        os.makedirs(lock_dir, exist_ok=True)
        safe_lock_name = self.name.replace('/', '_')
        self._lock_file_path = os.path.join(lock_dir, f'{safe_lock_name}.lock')
        self._lock_file = open(self._lock_file_path, 'a+b')

    @contextmanager
    def _guard(self):
        with self._lock:
            if fcntl is not None:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)

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

    def _get_timestamp_offset(self, index: int) -> int:
        return self.metadata_size + (index % self.capacity) * 8

    def _get_length_offset(self, index: int) -> int:
        return self.metadata_size + self.timestamp_size + (index % self.capacity) * 4

    def _get_frame_offset(self, index: int) -> int:
        return (
            self.metadata_size
            + self.timestamp_size
            + self.length_size
            + (index % self.capacity) * self.max_frame_bytes
        )

    def _write_timestamp(self, index: int, timestamp: float):
        struct.pack_into(self.TIMESTAMP_FORMAT, self.shm.buf, self._get_timestamp_offset(index), timestamp)

    def _read_timestamp(self, index: int) -> float:
        return struct.unpack_from(self.TIMESTAMP_FORMAT, self.shm.buf, self._get_timestamp_offset(index))[0]

    def _write_length(self, index: int, length: int):
        struct.pack_into(self.LENGTH_FORMAT, self.shm.buf, self._get_length_offset(index), length)

    def _read_length(self, index: int) -> int:
        return struct.unpack_from(self.LENGTH_FORMAT, self.shm.buf, self._get_length_offset(index))[0]

    def _encode_frame(self, frame: np.ndarray) -> bytes:
        if frame.shape != self.frame_shape:
            raise ValueError(f"Frame shape {frame.shape} doesn't match expected {self.frame_shape}")

        bgr_frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        ok, encoded = cv2.imencode(
            '.jpg',
            bgr_frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), int(self.jpeg_quality)],
        )
        if not ok:
            raise RuntimeError('JPEG 编码失败')

        payload = encoded.tobytes()
        if len(payload) > self.max_frame_bytes:
            raise ValueError(
                f"Encoded frame size {len(payload)} exceeds max_frame_bytes={self.max_frame_bytes}"
            )
        return payload

    def _decode_frame(self, payload: bytes) -> np.ndarray:
        arr = np.frombuffer(payload, dtype=np.uint8)
        frame_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame_bgr is None:
            raise RuntimeError('JPEG 解码失败')
        return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

    def write(self, frame: np.ndarray, timestamp: Optional[float] = None) -> bool:
        if timestamp is None:
            timestamp = time.time()

        payload = self._encode_frame(frame)

        with self._guard():
            write_idx, read_idx, count, locked, last_write, errors = self._read_metadata()
            new_write_idx = (write_idx + 1) % self.capacity

            if count >= self.capacity:
                read_idx = (read_idx + 1) % self.capacity
                count = self.capacity - 1

            frame_offset = self._get_frame_offset(write_idx)
            slot = memoryview(self.shm.buf)[frame_offset:frame_offset + self.max_frame_bytes]
            slot[:len(payload)] = payload
            self._write_length(write_idx, len(payload))
            self._write_timestamp(write_idx, timestamp)
            self._write_metadata(new_write_idx, read_idx, count + 1, locked, timestamp, 0)
            return True

    def _read_frame_at(self, index: int) -> np.ndarray:
        length = self._read_length(index)
        if length <= 0:
            raise RuntimeError(f'Compressed frame slot {index} is empty')
        offset = self._get_frame_offset(index)
        payload = bytes(self.shm.buf[offset:offset + length])
        return self._decode_frame(payload)

    def peek_with_timestamp(self, index: int = 0) -> Optional[Tuple[np.ndarray, float]]:
        with self._guard():
            write_idx, read_idx, count, _, _, _ = self._read_metadata()
            if count == 0 or abs(index) >= count:
                return None

            actual_idx = (write_idx + index) % self.capacity if index < 0 else (read_idx + index) % self.capacity
            timestamp = self._read_timestamp(actual_idx)
            frame = self._read_frame_at(actual_idx)
            return frame, timestamp

    def peek(self, index: int = 0) -> Optional[np.ndarray]:
        result = self.peek_with_timestamp(index)
        if result is None:
            return None
        return result[0]

    def get_recent_frames(self, seconds: float) -> List[Tuple[np.ndarray, float]]:
        with self._guard():
            write_idx, read_idx, count, _, _, _ = self._read_metadata()
            if count == 0:
                return []

            latest_idx = (write_idx - 1) % self.capacity
            latest_timestamp = self._read_timestamp(latest_idx)
            cutoff_time = latest_timestamp - seconds

            frames: List[Tuple[np.ndarray, float]] = []
            for i in range(count):
                actual_idx = (read_idx + i) % self.capacity
                timestamp = self._read_timestamp(actual_idx)
                if timestamp >= cutoff_time:
                    frames.append((self._read_frame_at(actual_idx), timestamp))
            return frames

    def get_frames_in_time_range(self, start_time: float, end_time: float) -> List[Tuple[np.ndarray, float]]:
        with self._guard():
            write_idx, read_idx, count, _, _, _ = self._read_metadata()
            if count == 0:
                return []

            frames: List[Tuple[np.ndarray, float]] = []
            for i in range(count):
                actual_idx = (read_idx + i) % self.capacity
                timestamp = self._read_timestamp(actual_idx)
                if start_time <= timestamp <= end_time:
                    frames.append((self._read_frame_at(actual_idx), timestamp))
            return frames

    def get_stats(self) -> dict:
        with self._guard():
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
                'max_frame_bytes': self.max_frame_bytes,
                'jpeg_quality': self.jpeg_quality,
            }

    def update_last_write_time(self, timestamp: float = None):
        if timestamp is None:
            timestamp = time.time()
        with self._guard():
            write_idx, read_idx, count, locked, _, errors = self._read_metadata()
            self._write_metadata(write_idx, read_idx, count, locked, timestamp, errors)

    def increment_error_count(self):
        with self._guard():
            write_idx, read_idx, count, locked, last_write, errors = self._read_metadata()
            self._write_metadata(write_idx, read_idx, count, locked, last_write, errors + 1)

    def get_health_status(self) -> dict:
        stats = self.get_stats()
        current_time = time.time()
        last_write = stats['last_write_time']
        if last_write == 0:
            time_since_last_frame = 0
        else:
            time_since_last_frame = current_time - last_write

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
        try:
            self._lock_file.close()
        except Exception:
            pass
        self.shm.close()

    def unlink(self):
        self.shm.unlink()
