"""
Type contracts (optional).

Keep aligned with result.py requirements.
"""

from typing import Any, Dict, List, Optional, TypedDict


class Detection(TypedDict, total=False):
    box: List[float]
    confidence: float
    label: str
    class_id: int
    label_name: str
    class_name: str
    track_id: int
    mask: Any
    attributes: Dict[str, Any]


class Result(TypedDict, total=False):
    detections: List[Detection]
    metadata: Dict[str, Any]
    frame: Any
    upstream_node_id: Optional[str]
