"""Validation and runtime helpers for workflow detection-size filters."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Tuple


DIMENSIONS = {"height", "width"}
UNITS = {"pixel", "ratio"}
COMPARISONS = {"gte", "lte"}
DETECTION_RESULT_NODE_TYPES = {
    "algorithm",
    "external_api",
    "externalApi",
    "function",
    "detection_filter",
    "detectionFilter",
}


class DetectionFilterValidationError(ValueError):
    """Raised when a detection-filter configuration is invalid."""


def normalize_detection_filter_config(config: Any) -> Dict[str, Any]:
    if not isinstance(config, dict):
        raise DetectionFilterValidationError("目标尺寸筛选配置必须是对象")

    dimension = config.get("dimension", "height")
    unit = config.get("unit", "pixel")
    comparison = config.get("comparison", "gte")
    threshold = config.get("threshold", 0)

    if dimension not in DIMENSIONS:
        raise DetectionFilterValidationError("检测维度必须是 height 或 width")
    if unit not in UNITS:
        raise DetectionFilterValidationError("尺寸单位必须是 pixel 或 ratio")
    if comparison not in COMPARISONS:
        raise DetectionFilterValidationError("比较方式必须是 gte 或 lte")
    if isinstance(threshold, bool):
        raise DetectionFilterValidationError("尺寸阈值必须是数字")
    try:
        threshold = float(threshold)
    except (TypeError, ValueError) as exc:
        raise DetectionFilterValidationError("尺寸阈值必须是数字") from exc
    if not math.isfinite(threshold) or threshold < 0:
        raise DetectionFilterValidationError("尺寸阈值必须是有限的非负数")
    if unit == "ratio" and threshold > 1:
        raise DetectionFilterValidationError("比例阈值必须在 0 到 1 之间")

    return {
        "dimension": dimension,
        "unit": unit,
        "comparison": comparison,
        "threshold": threshold,
    }


def _box_dimension(box: Any, dimension: str) -> float | None:
    if not isinstance(box, (list, tuple)) or len(box) < 4:
        return None
    try:
        x1, y1, x2, y2 = (float(box[index]) for index in range(4))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
        return None

    width = x2 - x1
    height = y2 - y1
    if width <= 0 or height <= 0:
        return None
    return height if dimension == "height" else width


def filter_detections_by_size(
    detections: Iterable[Dict[str, Any]] | None,
    config: Dict[str, Any],
    *,
    frame_width: int | float | None,
    frame_height: int | float | None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return matching detections and deterministic filter statistics."""
    normalized = normalize_detection_filter_config(config)
    source = [item for item in (detections or []) if isinstance(item, dict)]
    dimension = normalized["dimension"]
    unit = normalized["unit"]
    comparison = normalized["comparison"]
    threshold = normalized["threshold"]

    frame_dimension = frame_height if dimension == "height" else frame_width
    try:
        frame_dimension = float(frame_dimension) if frame_dimension is not None else 0.0
    except (TypeError, ValueError):
        frame_dimension = 0.0
    if not math.isfinite(frame_dimension) or frame_dimension <= 0:
        frame_dimension = 0.0

    kept: List[Dict[str, Any]] = []
    invalid_box_count = 0
    for detection in source:
        value = _box_dimension(
            detection.get("box", detection.get("bbox", detection.get("xyxy"))),
            dimension,
        )
        if value is None or (unit == "ratio" and frame_dimension <= 0):
            invalid_box_count += 1
            continue
        comparable_value = value if unit == "pixel" else value / frame_dimension
        matches = comparable_value >= threshold if comparison == "gte" else comparable_value <= threshold
        if matches:
            kept.append(detection)

    stats = {
        **normalized,
        "input_count": len(source),
        "output_count": len(kept),
        "filtered_count": len(source) - len(kept),
        "invalid_box_count": invalid_box_count,
    }
    return kept, stats


def validate_workflow_detection_filter_nodes(workflow_data: Any) -> Tuple[bool, str | None]:
    """Validate configuration and the single-upstream contract for all filter nodes."""
    if not isinstance(workflow_data, dict):
        return True, None

    nodes = {
        str(node.get("id")): node
        for node in workflow_data.get("nodes", [])
        if isinstance(node, dict) and node.get("id")
    }
    incoming: Dict[str, List[str]] = {}
    for connection in workflow_data.get("connections", []) or []:
        if not isinstance(connection, dict):
            continue
        source = connection.get("from") or connection.get("from_node_id")
        target = connection.get("to") or connection.get("to_node_id")
        if source and target:
            incoming.setdefault(str(target), []).append(str(source))

    for node_id, node in nodes.items():
        if node.get("type") not in {"detection_filter", "detectionFilter"}:
            continue
        display_name = node.get("name") or node_id
        config = node.get("config")
        required_fields = {"dimension", "unit", "comparison", "threshold"}
        if not isinstance(config, dict) or not required_fields.issubset(config):
            return False, f"目标尺寸筛选节点 {display_name} 缺少完整的尺寸规则配置"
        try:
            normalize_detection_filter_config(config)
        except DetectionFilterValidationError as exc:
            return False, f"目标尺寸筛选节点 {display_name} 配置无效：{exc}"

        upstream_ids = incoming.get(str(node_id), [])
        if len(upstream_ids) != 1:
            return False, f"目标尺寸筛选节点 {display_name} 必须且只能连接一个上游结果节点"
        upstream = nodes.get(upstream_ids[0])
        if not upstream or upstream.get("type") not in DETECTION_RESULT_NODE_TYPES:
            return False, f"目标尺寸筛选节点 {display_name} 的上游必须是检测结果节点"

    return True, None
