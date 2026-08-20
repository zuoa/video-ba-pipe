"""CPU post-process helpers for RKNN PPOCR detection and recognition."""

from __future__ import annotations

import os
from typing import List, Optional, Sequence, Tuple

import numpy as np

from app.core.cv2_compat import cv2, require_cv2


DEFAULT_DET_SIZE = (480, 480)  # width, height
DEFAULT_REC_SIZE = (320, 48)  # width, height
_DEFAULT_KEYS_PATH = os.path.join(
    os.path.dirname(__file__),
    "ocr_data",
    "ppocr_keys_v1.txt",
)


def default_character_dict_path() -> str:
    return _DEFAULT_KEYS_PATH


def load_character_dict(path: Optional[str] = None, use_space_char: bool = True) -> List[str]:
    dict_path = path or _DEFAULT_KEYS_PATH
    characters: List[str] = []
    with open(dict_path, "r", encoding="utf-8") as handle:
        for line in handle:
            char = line.rstrip("\r\n")
            if char:
                characters.append(char)
    if use_space_char and " " not in characters:
        characters.append(" ")
    return characters


def parse_input_size(value: object, default: Tuple[int, int]) -> Tuple[int, int]:
    if value in (None, ""):
        return default
    if isinstance(value, (list, tuple)):
        dimensions = [int(item) for item in value]
    else:
        text = str(value).strip().lower().replace(" ", "")
        for token in ("[", "]", "(", ")"):
            text = text.replace(token, "")
        separator = next((item for item in ("x", ",", "*") if item in text), None)
        if separator is None:
            raise ValueError(f"无法解析 OCR 输入尺寸: {value}")
        dimensions = [int(item) for item in text.split(separator) if item]

    if len(dimensions) >= 4:
        # Common metadata is NCHW or NHWC. Remove batch/channel dimensions.
        if dimensions[-1] in (1, 3, 4):
            height, width = dimensions[-3], dimensions[-2]
        else:
            height, width = dimensions[-2], dimensions[-1]
        return int(width), int(height)
    if len(dimensions) == 3:
        if dimensions[0] in (1, 3, 4):
            height, width = dimensions[-2], dimensions[-1]
        elif dimensions[-1] in (1, 3, 4):
            height, width = dimensions[0], dimensions[1]
        else:
            height, width = dimensions[-2], dimensions[-1]
        return int(width), int(height)
    if len(dimensions) == 2:
        first, second = dimensions
        # OCR metadata commonly uses either WxH or HxW. The shorter side is H.
        if first <= second:
            return int(second), int(first)
        return int(first), int(second)
    raise ValueError(f"无法解析 OCR 输入尺寸: {value}")


def squeeze_hw_map(output: np.ndarray) -> np.ndarray:
    array = np.asarray(output)
    if array.ndim == 4 and array.shape[0] == 1:
        array = array[0]
    if array.ndim == 3:
        if array.shape[0] == 1:
            array = array[0]
        elif array.shape[-1] == 1:
            array = array[..., 0]
    if array.ndim != 2:
        raise ValueError(f"OCR 检测输出维度无效: {np.asarray(output).shape}")
    return array.astype(np.float32)


def resize_detection_image(image: np.ndarray, width: int, height: int) -> np.ndarray:
    require_cv2()
    if image.shape[1] == width and image.shape[0] == height:
        return image
    return cv2.resize(image, (int(width), int(height)))


def prepare_recognition_image(
    image: np.ndarray,
    width: int,
    height: int,
    pad_value: int = 0,
) -> np.ndarray:
    require_cv2()
    source_h, source_w = image.shape[:2]
    if source_h < 1 or source_w < 1:
        return np.zeros((int(height), int(width), 3), dtype=np.uint8)
    scale = float(height) / float(source_h)
    resized_width = max(1, min(int(width), int(round(source_w * scale))))
    resized = cv2.resize(image, (resized_width, int(height)))
    canvas = np.full((int(height), int(width), 3), pad_value, dtype=np.uint8)
    canvas[:, :resized_width] = resized
    return canvas


def get_rotate_crop_image(image: np.ndarray, points: Sequence[Sequence[float]]) -> np.ndarray:
    require_cv2()
    box = np.array(points, dtype=np.float32)
    if box.shape != (4, 2):
        raise ValueError("OCR 裁剪需要 4 个顶点")
    width = int(
        max(
            np.linalg.norm(box[0] - box[1]),
            np.linalg.norm(box[2] - box[3]),
        )
    )
    height = int(
        max(
            np.linalg.norm(box[0] - box[3]),
            np.linalg.norm(box[1] - box[2]),
        )
    )
    width = max(width, 1)
    height = max(height, 1)
    destination = np.array(
        [[0, 0], [width, 0], [width, height], [0, height]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(box, destination)
    cropped = cv2.warpPerspective(
        image,
        matrix,
        (width, height),
        borderMode=cv2.BORDER_REPLICATE,
    )
    if cropped.shape[0] >= cropped.shape[1] * 1.5:
        cropped = np.rot90(cropped)
    return cropped


def _box_score(probability: np.ndarray, box: np.ndarray) -> float:
    require_cv2()
    height, width = probability.shape[:2]
    clip = np.clip(np.round(box), 0, [width - 1, height - 1])
    xmin = max(int(np.min(clip[:, 0])), 0)
    xmax = min(int(np.max(clip[:, 0])), width - 1)
    ymin = max(int(np.min(clip[:, 1])), 0)
    ymax = min(int(np.max(clip[:, 1])), height - 1)
    if xmax <= xmin or ymax <= ymin:
        return 0.0
    mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
    shifted = clip - np.array([xmin, ymin], dtype=np.float32)
    cv2.fillPoly(mask, [shifted.astype(np.int32)], 1)
    return float(cv2.mean(probability[ymin : ymax + 1, xmin : xmax + 1], mask)[0])


def _polygon_area(points: np.ndarray) -> float:
    x = points[:, 0]
    y = points[:, 1]
    return float(0.5 * np.abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))


def _polygon_length(points: np.ndarray) -> float:
    rolled = np.roll(points, -1, axis=0)
    return float(np.sum(np.linalg.norm(rolled - points, axis=1)))


def unclip_box(box: np.ndarray, unclip_ratio: float) -> np.ndarray:
    import pyclipper

    area = abs(_polygon_area(box))
    length = _polygon_length(box)
    if area < 1e-6 or length < 1e-6:
        return box
    distance = area * float(unclip_ratio) / length
    offset = pyclipper.PyclipperOffset()
    offset.AddPath(box.astype(np.int32).tolist(), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    expanded = offset.Execute(distance)
    if not expanded:
        return box
    return np.array(expanded[0], dtype=np.float32)


def _clockwise_min_box(contour: np.ndarray) -> np.ndarray:
    require_cv2()
    rectangle = cv2.minAreaRect(contour)
    box = cv2.boxPoints(rectangle)
    start = int(np.argmin(np.sum(box, axis=1)))
    box = np.roll(box, -start, axis=0)
    if np.linalg.norm(box[0] - box[1]) < np.linalg.norm(box[1] - box[2]):
        box = np.array([box[1], box[2], box[3], box[0]], dtype=np.float32)
    return box.astype(np.float32)


def db_detect_polygons(
    probability_map: np.ndarray,
    source_width: int,
    source_height: int,
    *,
    thresh: float = 0.3,
    box_thresh: float = 0.6,
    unclip_ratio: float = 1.5,
    max_candidates: int = 1000,
    min_size: int = 3,
) -> List[Tuple[List[List[float]], float]]:
    require_cv2()
    heatmap = squeeze_hw_map(probability_map)
    mask = (heatmap > float(thresh)).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[: int(max_candidates)]
    map_h, map_w = heatmap.shape[:2]
    scale_x = float(source_width) / float(map_w)
    scale_y = float(source_height) / float(map_h)

    detections: List[Tuple[List[List[float]], float]] = []
    for contour in contours:
        if cv2.contourArea(contour) < min_size:
            continue
        box = _clockwise_min_box(contour.astype(np.float32))
        if min(np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[1] - box[2])) < min_size:
            continue
        score = _box_score(heatmap, box)
        if score < float(box_thresh):
            continue
        expanded = unclip_box(box, unclip_ratio)
        if expanded.shape[0] < 4:
            continue
        expanded = _clockwise_min_box(expanded.reshape(-1, 1, 2).astype(np.float32))
        expanded[:, 0] = np.clip(expanded[:, 0] * scale_x, 0, source_width - 1)
        expanded[:, 1] = np.clip(expanded[:, 1] * scale_y, 0, source_height - 1)
        width = np.linalg.norm(expanded[0] - expanded[1])
        height = np.linalg.norm(expanded[0] - expanded[3])
        if min(width, height) < min_size:
            continue
        detections.append((expanded.tolist(), float(score)))
    return detections


def sort_text_polygons(
    detections: Sequence[Tuple[List[List[float]], float]],
) -> List[Tuple[List[List[float]], float]]:
    """Sort detected text lines from top to bottom and left to right."""
    ordered = sorted(
        detections,
        key=lambda item: (float(item[0][0][1]), float(item[0][0][0])),
    )
    for index in range(1, len(ordered)):
        current = index
        while current > 0:
            left = ordered[current - 1][0][0]
            right = ordered[current][0][0]
            if abs(float(right[1]) - float(left[1])) >= 10 or float(right[0]) >= float(left[0]):
                break
            ordered[current - 1], ordered[current] = ordered[current], ordered[current - 1]
            current -= 1
    return ordered


def ctc_greedy_decode(
    logits: np.ndarray,
    characters: Sequence[str],
) -> Tuple[str, float]:
    array = np.asarray(logits)
    if array.ndim == 3:
        array = array[0]
    if array.ndim != 2:
        raise ValueError(f"OCR 识别输出维度无效: {np.asarray(logits).shape}")
    # Accept [T, C] or [C, T]; character axis is the longer class dimension.
    if array.shape[0] == len(characters) + 1 and array.shape[1] != len(characters) + 1:
        array = array.T
    class_count = array.shape[1]
    indexes = array.argmax(axis=1)
    probs = array.max(axis=1)
    blank = 0
    texts: List[str] = []
    confidences: List[float] = []
    previous = blank
    for index, score in zip(indexes.tolist(), probs.tolist()):
        if index == previous or index == blank or index >= class_count:
            previous = index
            continue
        character_index = index - 1
        if 0 <= character_index < len(characters):
            texts.append(characters[character_index])
            confidences.append(float(score))
        previous = index
    if not texts:
        return "", 0.0
    return "".join(texts), float(sum(confidences) / len(confidences))


def find_character_dict_path(model_path: str) -> Optional[str]:
    candidates = []
    if os.path.isdir(model_path):
        search_roots = [model_path]
    else:
        search_roots = [os.path.dirname(model_path)]
    names = {"ppocr_keys_v1.txt", "keys.txt", "dict.txt", "character.txt"}
    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        for filename in os.listdir(root):
            lower = filename.lower()
            if lower in names or (lower.endswith(".txt") and "key" in lower):
                candidates.append(os.path.join(root, filename))
    return candidates[0] if candidates else None


def resolve_rknn_model_path(path: str) -> str:
    if os.path.isfile(path) and path.lower().endswith(".rknn"):
        return path
    if os.path.isdir(path):
        matches = []
        for root, _directories, filenames in os.walk(path):
            for name in filenames:
                if name.lower().endswith(".rknn"):
                    matches.append(os.path.join(root, name))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"OCR 模型目录包含多个 .rknn: {path}")
    return path
