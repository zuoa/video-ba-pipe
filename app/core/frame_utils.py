from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from app.core.cv2_compat import cv2, require_cv2


def normalize_pixel_format(pixel_format: Optional[str]) -> str:
    normalized = str(pixel_format or "nv12").strip().lower()
    aliases = {
        "rgb": "rgb24",
        "bgr": "bgr24",
        "nv21": "nv21",
        "nv12": "nv12",
        "yuv420": "yuv420p",
        "i420": "yuv420p",
    }
    return aliases.get(normalized, normalized)


def get_frame_size_bytes(width: int, height: int, pixel_format: str) -> int:
    pixel_format = normalize_pixel_format(pixel_format)
    width = int(width)
    height = int(height)

    if pixel_format in {"nv12", "nv21", "yuv420p"}:
        return width * height * 3 // 2
    if pixel_format in {"rgb24", "bgr24"}:
        return width * height * 3
    raise ValueError(f"Unsupported pixel format: {pixel_format}")


def get_storage_shape(width: int, height: int, pixel_format: str) -> Tuple[int, ...]:
    pixel_format = normalize_pixel_format(pixel_format)
    width = int(width)
    height = int(height)

    if pixel_format in {"nv12", "nv21", "yuv420p"}:
        return (height * 3 // 2, width)
    if pixel_format in {"rgb24", "bgr24"}:
        return (height, width, 3)
    raise ValueError(f"Unsupported pixel format: {pixel_format}")


def infer_frame_dimensions(
    frame: np.ndarray,
    pixel_format: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Tuple[int, int]:
    if width is not None and height is not None:
        return int(width), int(height)

    frame = np.asarray(frame)
    pixel_format = normalize_pixel_format(pixel_format)

    if pixel_format in {"rgb24", "bgr24"}:
        if frame.ndim < 2:
            raise ValueError(f"Cannot infer dimensions from frame with shape {frame.shape}")
        return int(frame.shape[1]), int(frame.shape[0])

    if pixel_format in {"nv12", "nv21", "yuv420p"}:
        if frame.ndim != 2:
            raise ValueError(f"{pixel_format} frame must be a 2D array, got {frame.shape}")
        rows, cols = frame.shape
        if rows * 2 % 3 != 0:
            raise ValueError(f"Cannot infer {pixel_format} height from frame shape {frame.shape}")
        return int(cols), int(rows * 2 // 3)

    raise ValueError(f"Unsupported pixel format: {pixel_format}")


def reshape_frame(
    frame_data: np.ndarray | bytes | bytearray | memoryview,
    width: int,
    height: int,
    pixel_format: str,
) -> np.ndarray:
    pixel_format = normalize_pixel_format(pixel_format)
    expected_size = get_frame_size_bytes(width, height, pixel_format)
    storage_shape = get_storage_shape(width, height, pixel_format)
    frame_array = np.frombuffer(frame_data, dtype=np.uint8)

    if frame_array.size != expected_size:
        raise ValueError(
            f"Frame size {frame_array.size} does not match expected {expected_size} "
            f"for {pixel_format} {width}x{height}"
        )

    return frame_array.reshape(storage_shape)


def ensure_frame_array(
    frame: np.ndarray | bytes | bytearray | memoryview,
    width: int,
    height: int,
    pixel_format: str,
) -> np.ndarray:
    if isinstance(frame, np.ndarray):
        frame_array = np.ascontiguousarray(frame, dtype=np.uint8)
        expected_shape = get_storage_shape(width, height, pixel_format)
        expected_size = get_frame_size_bytes(width, height, pixel_format)
        if frame_array.shape != expected_shape and frame_array.size != expected_size:
            raise ValueError(
                f"Frame shape {frame_array.shape} does not match expected {expected_shape} "
                f"for {normalize_pixel_format(pixel_format)} {width}x{height}"
            )
        if frame_array.shape != expected_shape:
            frame_array = frame_array.reshape(expected_shape)
        return frame_array

    return reshape_frame(frame, width, height, pixel_format)


def _bgr_to_yuv_i420(frame_bgr: np.ndarray) -> np.ndarray:
    require_cv2()
    return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2YUV_I420)


def bgr_to_nv12(frame_bgr: np.ndarray) -> np.ndarray:
    require_cv2()
    height, width = frame_bgr.shape[:2]
    if height % 2 != 0 or width % 2 != 0:
        raise ValueError(f"NV12 requires even dimensions, got {width}x{height}")

    yuv_i420 = _bgr_to_yuv_i420(frame_bgr)
    flat = yuv_i420.reshape(-1)
    y_size = width * height
    uv_plane_size = y_size // 4

    y_plane = flat[:y_size].reshape((height, width))
    u_plane = flat[y_size:y_size + uv_plane_size].reshape((height // 2, width // 2))
    v_plane = flat[y_size + uv_plane_size:].reshape((height // 2, width // 2))

    uv_plane = np.empty((height // 2, width), dtype=np.uint8)
    uv_plane[:, 0::2] = u_plane
    uv_plane[:, 1::2] = v_plane
    return np.vstack([y_plane, uv_plane])


def rgb_to_nv12(frame_rgb: np.ndarray) -> np.ndarray:
    require_cv2()
    frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
    return bgr_to_nv12(frame_bgr)


def nv12_to_bgr(
    frame_nv12: np.ndarray | bytes | bytearray | memoryview,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> np.ndarray:
    require_cv2()
    if isinstance(frame_nv12, np.ndarray):
        if width is None or height is None:
            width, height = infer_frame_dimensions(frame_nv12, pixel_format="nv12")
        frame_nv12 = ensure_frame_array(frame_nv12, width, height, "nv12")
    else:
        if width is None or height is None:
            raise ValueError("width and height are required for NV12 byte buffers")
        frame_nv12 = reshape_frame(frame_nv12, width, height, "nv12")

    return cv2.cvtColor(frame_nv12, cv2.COLOR_YUV2BGR_NV12)


def nv12_to_rgb(
    frame_nv12: np.ndarray | bytes | bytearray | memoryview,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> np.ndarray:
    require_cv2()
    frame_bgr = nv12_to_bgr(frame_nv12, width=width, height=height)
    return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
