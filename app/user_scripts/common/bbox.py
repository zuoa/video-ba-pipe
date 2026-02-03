"""
BBox utilities.
"""

from typing import Iterable, List


def to_abs_bbox(bbox_norm: Iterable[float], width: int, height: int) -> List[float]:
    x1, y1, x2, y2 = bbox_norm
    return [x1 * width, y1 * height, x2 * width, y2 * height]


def to_norm_bbox(bbox_abs: Iterable[float], width: int, height: int) -> List[float]:
    x1, y1, x2, y2 = bbox_abs
    if width <= 0 or height <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    return [x1 / width, y1 / height, x2 / width, y2 / height]


def clip_bbox(bbox: Iterable[float], width: int, height: int) -> List[float]:
    x1, y1, x2, y2 = bbox
    x1 = max(0.0, min(float(x1), width))
    y1 = max(0.0, min(float(y1), height))
    x2 = max(0.0, min(float(x2), width))
    y2 = max(0.0, min(float(y2), height))
    return [x1, y1, x2, y2]

