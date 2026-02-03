"""
Filtering and NMS helpers.
"""

from typing import Any, Dict, List, Sequence

from app.core.utils import calculate_iou


def _get_score(det: Dict[str, Any]) -> float:
    if 'confidence' in det:
        return float(det.get('confidence') or 0.0)
    if 'score' in det:
        return float(det.get('score') or 0.0)
    return 0.0


def _get_box(det: Dict[str, Any]):
    return det.get('box') or det.get('bbox')


def filter_by_score(detections: List[Dict[str, Any]], min_score: float) -> List[Dict[str, Any]]:
    return [d for d in detections or [] if _get_score(d) >= float(min_score)]


def filter_by_labels(detections: List[Dict[str, Any]], allowed_labels: Sequence[Any]) -> List[Dict[str, Any]]:
    if not allowed_labels:
        return detections or []

    allowed_set = set(allowed_labels)

    filtered = []
    for det in detections or []:
        label = det.get('label') or det.get('label_name') or det.get('class_name')
        cls = det.get('class')
        if label in allowed_set or cls in allowed_set:
            filtered.append(det)
    return filtered


def nms(detections: List[Dict[str, Any]], iou_threshold: float) -> List[Dict[str, Any]]:
    if not detections:
        return []

    sorted_dets = sorted(detections, key=_get_score, reverse=True)
    keep: List[Dict[str, Any]] = []

    for det in sorted_dets:
        box = _get_box(det)
        if not box or len(box) < 4:
            continue

        should_keep = True
        for kept in keep:
            kept_box = _get_box(kept)
            if not kept_box or len(kept_box) < 4:
                continue
            if calculate_iou(box, kept_box) >= float(iou_threshold):
                should_keep = False
                break

        if should_keep:
            keep.append(det)

    return keep

