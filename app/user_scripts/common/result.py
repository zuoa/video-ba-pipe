"""
Result construction and validation helpers.

Detection schema (required fields):
- box: [x1, y1, x2, y2]  # absolute pixel coordinates
- confidence: float
- label: str (or label_name / class_name as fallback)
Optional fields: class, track_id, mask, attributes

Aliases accepted:
- bbox -> box
- score -> confidence
"""

from typing import Any, Dict, Iterable, List, Optional

from app import logger


def _normalize_detection(det: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a single detection dict to the canonical keys."""
    if not isinstance(det, dict):
        return {}

    normalized = dict(det)

    if 'box' not in normalized and 'bbox' in normalized:
        normalized['box'] = normalized.get('bbox')

    if 'confidence' not in normalized and 'score' in normalized:
        normalized['confidence'] = normalized.get('score')

    if 'label' not in normalized:
        if 'label_name' in normalized:
            normalized['label'] = normalized.get('label_name')
        elif 'class_name' in normalized:
            normalized['label'] = normalized.get('class_name')

    return normalized


def validate_detections(detections: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize and validate detection list.

    Returns a cleaned list; invalid entries are dropped.
    """
    cleaned: List[Dict[str, Any]] = []
    for det in detections or []:
        normalized = _normalize_detection(det)
        box = normalized.get('box')
        if not isinstance(box, (list, tuple)) or len(box) < 4:
            continue
        if normalized.get('confidence') is None:
            normalized['confidence'] = 1.0
        if normalized.get('label') is None:
            normalized['label'] = 'object'
        cleaned.append(normalized)
    return cleaned


def build_result(
    detections: Optional[Iterable[Dict[str, Any]]],
    frame: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
    upstream_node_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build a standard result dict for the pipeline.
    """
    clean = validate_detections(detections)
    result: Dict[str, Any] = {
        'detections': clean,
        'metadata': metadata or {}
    }
    if frame is not None:
        result['frame'] = frame
    if upstream_node_id is not None:
        result['upstream_node_id'] = upstream_node_id
    return result

