"""
ROI handling helpers.

Rules:
- mode == "pre_mask": apply mask before inference
- mode == "crop_infer": crop ROI bounds for local inference
- mode == "post_filter": filter detections after inference
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from app.core.algorithm import BaseAlgorithm


ROI_MODE_PRE_MASK = "pre_mask"
ROI_MODE_CROP_INFER = "crop_infer"
ROI_MODE_POST_FILTER = "post_filter"


def normalize_roi_mode(mode: Any, default: str = ROI_MODE_POST_FILTER) -> str:
    normalized = str(mode or "").strip().lower()
    aliases = {
        "premask": ROI_MODE_PRE_MASK,
        "pre_mask": ROI_MODE_PRE_MASK,
        "postfilter": ROI_MODE_POST_FILTER,
        "post_filter": ROI_MODE_POST_FILTER,
        "cropinfer": ROI_MODE_CROP_INFER,
        "crop_infer": ROI_MODE_CROP_INFER,
    }
    return aliases.get(normalized, default)


def split_regions(
    roi_regions: Optional[List[Dict[str, Any]]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    pre_mask = []
    crop_infer = []
    post_filter = []
    for region in roi_regions or []:
        mode = normalize_roi_mode(region.get("mode"))
        if mode == ROI_MODE_PRE_MASK:
            pre_mask.append(region)
        elif mode == ROI_MODE_CROP_INFER:
            crop_infer.append(region)
        else:
            post_filter.append(region)
    return pre_mask, crop_infer, post_filter


def _clip_crop_box(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    width: int,
    height: int,
) -> Optional[List[int]]:
    x1 = max(0, min(int(x1), width - 1))
    y1 = max(0, min(int(y1), height - 1))
    x2 = max(0, min(int(x2), width))
    y2 = max(0, min(int(y2), height))
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def normalize_region_points(region: Dict[str, Any], frame_shape: Sequence[int]) -> List[List[int]]:
    height, width = int(frame_shape[0]), int(frame_shape[1])
    raw_points = region.get("polygon", region.get("points", [])) or []
    if len(raw_points) < 3:
        return []

    points: List[List[int]] = []
    if isinstance(raw_points[0], dict):
        for point in raw_points:
            x = int(round(float(point.get("x", 0.0)) * width))
            y = int(round(float(point.get("y", 0.0)) * height))
            points.append([x, y])
    else:
        for point in raw_points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            points.append([int(round(float(point[0]))), int(round(float(point[1])))])

    return points if len(points) >= 3 else []


def build_crop_plans(
    frame_shape: Sequence[int],
    roi_regions: Optional[List[Dict[str, Any]]],
    padding: int = 0,
    strategy: str = "per_region",
    union_area_threshold: float = 0.6,
) -> List[Dict[str, Any]]:
    height, width = int(frame_shape[0]), int(frame_shape[1])
    normalized_regions = []
    for region in roi_regions or []:
        points = normalize_region_points(region, frame_shape)
        if not points:
            continue
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        crop_box = _clip_crop_box(
            min(xs) - padding,
            min(ys) - padding,
            max(xs) + padding + 1,
            max(ys) + padding + 1,
            width,
            height,
        )
        if crop_box is None:
            continue
        normalized_regions.append({
            "region": region,
            "points": points,
            "box": crop_box,
        })

    if not normalized_regions:
        return []

    strategy_normalized = str(strategy or "per_region").strip().lower()
    if strategy_normalized == "auto":
        if len(normalized_regions) == 1:
            strategy_normalized = "per_region"
        else:
            union_x1 = min(item["box"][0] for item in normalized_regions)
            union_y1 = min(item["box"][1] for item in normalized_regions)
            union_x2 = max(item["box"][2] for item in normalized_regions)
            union_y2 = max(item["box"][3] for item in normalized_regions)
            union_area = max(0, union_x2 - union_x1) * max(0, union_y2 - union_y1)
            frame_area = max(1, width * height)
            strategy_normalized = "union" if union_area / float(frame_area) <= union_area_threshold else "per_region"

    if strategy_normalized == "union":
        union_x1 = min(item["box"][0] for item in normalized_regions)
        union_y1 = min(item["box"][1] for item in normalized_regions)
        union_x2 = max(item["box"][2] for item in normalized_regions)
        union_y2 = max(item["box"][3] for item in normalized_regions)
        return [{
            "box": [union_x1, union_y1, union_x2, union_y2],
            "regions": [item["region"] for item in normalized_regions],
        }]

    return [{
        "box": item["box"],
        "regions": [item["region"]],
    } for item in normalized_regions]


def crop_frame(frame: np.ndarray, crop_box: Sequence[int]) -> np.ndarray:
    x1, y1, x2, y2 = [int(value) for value in crop_box[:4]]
    return frame[y1:y2, x1:x2]


def remap_detections_to_full_frame(
    items: Optional[List[Dict[str, Any]]],
    crop_box: Sequence[int],
) -> List[Dict[str, Any]]:
    if not items:
        return []

    offset_x = float(crop_box[0])
    offset_y = float(crop_box[1])
    remapped = []
    for item in items:
        box = BaseAlgorithm._get_detection_box(item)
        if box is None:
            continue
        mapped_box = [
            float(box[0]) + offset_x,
            float(box[1]) + offset_y,
            float(box[2]) + offset_x,
            float(box[3]) + offset_y,
        ]
        mapped_item = dict(item)
        if "box" in mapped_item:
            mapped_item["box"] = mapped_box
        if "bbox" in mapped_item:
            mapped_item["bbox"] = mapped_box
        remapped.append(mapped_item)
    return remapped


def _mask_keep(box: Sequence[float], mask: np.ndarray, metric: str, threshold: float) -> bool:
    height, width = mask.shape[:2]
    normalized_box = BaseAlgorithm._normalize_box_for_canvas(box, width, height)
    if normalized_box is None:
        return False

    x1, y1, x2, y2 = normalized_box
    if metric == "bottom_center":
        point_x = int(round((x1 + x2) / 2.0))
        point_y = y2
        return bool(mask[point_y, point_x] > 0)

    if metric == "ioa":
        x2_inclusive = min(x2 + 1, width)
        y2_inclusive = min(y2 + 1, height)
        if x2_inclusive <= x1 or y2_inclusive <= y1:
            return False
        area = float((x2_inclusive - x1) * (y2_inclusive - y1))
        if area <= 0:
            return False
        roi_pixels = float(np.count_nonzero(mask[y1:y2_inclusive, x1:x2_inclusive]))
        return roi_pixels / area >= float(threshold)

    point_x = int(round((x1 + x2) / 2.0))
    point_y = int(round((y1 + y2) / 2.0))
    return bool(mask[point_y, point_x] > 0)


def filter_items_by_regions(
    items: Optional[List[Dict[str, Any]]],
    frame_shape: Sequence[int],
    roi_regions: Optional[List[Dict[str, Any]]],
    metric: str = "center",
    threshold: float = 0.3,
) -> List[Dict[str, Any]]:
    if not items or not roi_regions:
        return list(items or [])

    mask = BaseAlgorithm.create_roi_mask(tuple(frame_shape), roi_regions)
    metric_normalized = str(metric or "center").strip().lower()
    filtered = []
    for item in items:
        box = BaseAlgorithm._get_detection_box(item)
        if box is None:
            continue
        if _mask_keep(box, mask, metric_normalized, threshold):
            filtered.append(item)
    return filtered


def global_nms(
    detections: Optional[List[Dict[str, Any]]],
    details: Optional[List[Dict[str, Any]]] = None,
    score_threshold: float = 0.0,
    nms_threshold: float = 0.45,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    detections = list(detections or [])
    details = list(details or [])
    if not detections:
        return [], []

    class_groups: Dict[Any, List[int]] = {}
    for idx, det in enumerate(detections):
        class_key = det.get("class")
        if class_key is None:
            class_key = det.get("label") or det.get("label_name") or det.get("class_name")
        class_groups.setdefault(class_key, []).append(idx)

    kept_source_indices = []
    for class_key in class_groups:
        group_indices = class_groups[class_key]
        boxes = []
        scores = []
        valid_indices = []
        for source_idx in group_indices:
            det = detections[source_idx]
            box = BaseAlgorithm._get_detection_box(det)
            confidence = BaseAlgorithm._get_detection_confidence(det, 0.0)
            if box is None:
                continue
            x1, y1, x2, y2 = [float(value) for value in box[:4]]
            boxes.append([x1, y1, max(1.0, x2 - x1), max(1.0, y2 - y1)])
            scores.append(float(confidence))
            valid_indices.append(source_idx)

        if not boxes:
            continue

        indices = cv2.dnn.NMSBoxes(
            bboxes=boxes,
            scores=scores,
            score_threshold=float(score_threshold),
            nms_threshold=float(nms_threshold),
        )
        if len(indices) == 0:
            continue

        if isinstance(indices, np.ndarray):
            flat_indices = indices.flatten().tolist()
        else:
            flat_indices = [int(item[0]) if isinstance(item, (list, tuple, np.ndarray)) else int(item) for item in indices]

        for local_idx in flat_indices:
            kept_source_indices.append(valid_indices[int(local_idx)])

    kept_source_indices = sorted(set(kept_source_indices))

    merged_detections = []
    merged_details = []
    for source_idx in kept_source_indices:
        merged_detections.append(detections[source_idx])
        if source_idx < len(details):
            merged_details.append(details[source_idx])

    return merged_detections, merged_details


def apply_roi(frame: np.ndarray, detections: List[Dict[str, Any]], roi_regions: List[Dict[str, Any]]):
    """
    Apply ROI rules to frame and detections.
    Returns (frame, detections).
    """
    if roi_regions is None:
        return frame, detections

    pre_mask_regions, _, post_filter_regions = split_regions(roi_regions)

    if pre_mask_regions:
        mask = BaseAlgorithm.create_roi_mask(frame.shape, pre_mask_regions)
        frame = BaseAlgorithm.apply_roi_mask(frame, mask)

    if post_filter_regions and detections:
        detections = filter_items_by_regions(
            detections,
            frame_shape=frame.shape,
            roi_regions=post_filter_regions,
            metric="center",
            threshold=0.0,
        )

    return frame, detections
