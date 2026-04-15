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
        buffer.write(frame, timestamp=123.456)
        loaded_frame, timestamp = buffer.peek_with_timestamp(-1)

        assert timestamp == 123.456
        assert loaded_frame.shape == frame.shape
        assert np.array_equal(loaded_frame, frame)
    finally:
        buffer.close()
        buffer.unlink()
