import time

import numpy as np
import pytest

from app.core.frame_utils import get_frame_size_bytes, get_storage_shape
from app.core.ringbuffer import VideoRingBuffer


def test_nv12_helpers_report_expected_size_and_shape():
    width = 640
    height = 480

    assert get_frame_size_bytes(width, height, "nv12") == width * height * 3 // 2
    assert get_storage_shape(width, height, "nv12") == (height * 3 // 2, width)


def test_video_ringbuffer_roundtrips_nv12_frame():
    width = 8
    height = 4
    buffer_name = f"nv12_{time.time_ns() % 1000000}"
    frame = np.arange(width * height * 3 // 2, dtype=np.uint8).reshape(
        (height * 3 // 2, width)
    )

    try:
        buffer = VideoRingBuffer(
            name=buffer_name,
            width=width,
            height=height,
            pixel_format="nv12",
            fps=2,
            duration_seconds=2,
            create=True,
        )
    except PermissionError:
        pytest.skip("shared_memory create is not permitted in this sandbox")

    try:
        buffer.write(frame, timestamp=123.456)
        loaded_frame, timestamp = buffer.peek_with_timestamp(-1)
        view_frame, view_timestamp = buffer.peek_view_with_timestamp(-1)

        assert timestamp == 123.456
        assert loaded_frame.shape == frame.shape
        assert np.array_equal(loaded_frame, frame)
        assert loaded_frame.flags.writeable is True
        assert view_timestamp == 123.456
        assert view_frame.shape == frame.shape
        assert view_frame.flags.writeable is False
        assert np.array_equal(view_frame, frame)

        loaded_frame[0, 0] = 255
        assert view_frame[0, 0] == frame[0, 0]
    finally:
        buffer.close()
        buffer.unlink()


def test_peek_if_newer_skips_frame_copy_until_timestamp_changes(monkeypatch):
    width = 8
    height = 4
    buffer_name = f"nv12_newer_{time.time_ns() % 1000000}"
    frame = np.arange(width * height * 3 // 2, dtype=np.uint8).reshape((height * 3 // 2, width))

    try:
        buffer = VideoRingBuffer(
            name=buffer_name,
            width=width,
            height=height,
            pixel_format="nv12",
            fps=2,
            duration_seconds=2,
            create=True,
        )
    except PermissionError:
        pytest.skip("shared_memory create is not permitted in this sandbox")

    try:
        buffer.write(frame, timestamp=100.0)
        original_copy = buffer._frame_from_shm
        copy_offsets = []

        def track_copy(offset):
            copy_offsets.append(offset)
            return original_copy(offset)

        monkeypatch.setattr(buffer, "_frame_from_shm", track_copy)

        loaded_frame, timestamp = buffer.peek_if_newer_with_timestamp(None)
        assert timestamp == 100.0
        assert np.array_equal(loaded_frame, frame)
        assert len(copy_offsets) == 1

        assert buffer.peek_if_newer_with_timestamp(timestamp) is None
        assert len(copy_offsets) == 1

        updated_frame = np.full_like(frame, 7)
        buffer.write(updated_frame, timestamp=101.0)
        loaded_frame, timestamp = buffer.peek_if_newer_with_timestamp(timestamp)
        assert timestamp == 101.0
        assert np.array_equal(loaded_frame, updated_frame)
        assert len(copy_offsets) == 2
    finally:
        buffer.close()
        buffer.unlink()


def test_peek_if_newer_can_return_read_only_shared_memory_view():
    width = 8
    height = 4
    buffer_name = f"nv12_newer_view_{time.time_ns() % 1000000}"
    frame = np.zeros((height * 3 // 2, width), dtype=np.uint8)

    try:
        buffer = VideoRingBuffer(
            name=buffer_name,
            width=width,
            height=height,
            pixel_format="nv12",
            fps=2,
            duration_seconds=2,
            create=True,
        )
    except PermissionError:
        pytest.skip("shared_memory create is not permitted in this sandbox")

    try:
        buffer.write(frame, timestamp=123.0)
        view, timestamp = buffer.peek_if_newer_with_timestamp(None, copy=False)

        assert timestamp == 123.0
        assert view.flags.writeable is False
        shared_array = np.ndarray(
            shape=buffer.frame_shape,
            dtype=np.uint8,
            buffer=buffer.shm.buf,
            offset=buffer._get_frame_offset(0),
        )
        assert np.shares_memory(view, shared_array)
    finally:
        buffer.close()
        buffer.unlink()
