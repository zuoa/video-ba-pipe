"""Object tracker workflow adapter.

Consumes upstream detections, assigns stable track_id values, and leaves
dwell / loiter analysis to later nodes.
"""

from typing import Any, Dict, List, Optional

import numpy as np

from app.user_scripts.common.result import build_result
from app.user_scripts.common.tracker import create_tracker

SCRIPT_METADATA = {
    "name": "目标追踪",
    "version": "v1.0",
    "description": "为上游检测框分配跨帧 track_id，可选贪心 IoU 或 ByteTrack。向导间隔最低 0.1 秒；工作流节点可设为 0 以每帧执行。",
    "author": "system",
    "category": "tracking",
    "tags": ["tracking", "iou", "bytetrack", "identity"],
    "config_schema": {
        "backend": {
            "type": "select",
            "label": "跟踪后端",
            "required": True,
            "default": "iou",
            "options": [
                {"value": "iou", "label": "贪心 IoU（静止/慢动）"},
                {"value": "bytetrack", "label": "ByteTrack（移动/遮挡）"},
            ],
            "description": "违停、占道用 IoU；人员徘徊、短暂遮挡用 ByteTrack",
        },
        "max_misses": {
            "type": "int",
            "label": "丢失保留次数",
            "min": 1,
            "max": 300,
            "step": 1,
            "description": "连续未匹配多少次后删除轨迹。留空则 IoU=3，ByteTrack=30",
        },
        "min_hits": {
            "type": "int",
            "label": "确认命中次数",
            "default": 1,
            "min": 1,
            "max": 20,
            "step": 1,
            "description": "达到该命中次数后才对外输出",
        },
        "label_filter": {
            "type": "string",
            "label": "类别过滤",
            "default": "",
            "placeholder": "car,motorcycle",
            "description": "留空表示全部；多个类别用逗号分隔",
        },
        "max_tracks": {
            "type": "int",
            "label": "最大轨迹数",
            "default": 256,
            "min": 1,
            "max": 2048,
            "step": 1,
        },
        "history_size": {
            "type": "int",
            "label": "短轨迹点数",
            "default": 32,
            "min": 1,
            "max": 256,
            "step": 1,
            "description": "写入检测 attributes.history 的中心点点数，供后续徘徊分析使用",
        },
        "match_iou": {
            "type": "float",
            "label": "关联 IoU",
            "default": 0.3,
            "min": 0.05,
            "max": 0.95,
            "step": 0.05,
            "visible_when": {"backend": "iou"},
            "description": "同类框 IoU 达到该值才视为同一目标",
        },
        "track_high_thresh": {
            "type": "float",
            "label": "高分阈值",
            "default": 0.5,
            "min": 0.05,
            "max": 1.0,
            "step": 0.05,
            "visible_when": {"backend": "bytetrack"},
        },
        "track_low_thresh": {
            "type": "float",
            "label": "低分阈值",
            "default": 0.1,
            "min": 0.0,
            "max": 0.9,
            "step": 0.05,
            "visible_when": {"backend": "bytetrack"},
            "description": "用低分框救回被遮挡轨迹",
        },
        "new_track_thresh": {
            "type": "float",
            "label": "开新轨迹最低分",
            "default": 0.6,
            "min": 0.05,
            "max": 1.0,
            "step": 0.05,
            "visible_when": {"backend": "bytetrack"},
        },
        "match_thresh": {
            "type": "float",
            "label": "ByteTrack 关联阈值",
            "default": 0.8,
            "min": 0.1,
            "max": 0.95,
            "step": 0.05,
            "visible_when": {"backend": "bytetrack"},
            "description": "原文是 1-IoU 代价上限，0.8 约等于 IoU ≥ 0.2",
        },
    },
    "performance": {
        "timeout": 5,
        "memory_limit_mb": 128,
        "gpu_required": False,
        "estimated_time_ms": 5,
    },
    "dependencies": [
        "numpy>=1.19.0",
    ],
}


def _parse_label_filter(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        import json

        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


def _optional_number(config: dict, key: str, cast):
    value = config.get(key)
    if value is None or value == "":
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _collect_upstream_detections(upstream_results: Optional[dict]) -> List[dict]:
    detections: List[dict] = []
    if not upstream_results:
        return detections
    for result in upstream_results.values():
        if not isinstance(result, dict):
            continue
        items = result.get("detections") or []
        if isinstance(items, list):
            detections.extend(item for item in items if isinstance(item, dict))
    return detections


def _tracker_kwargs(config: dict) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "label_filter": _parse_label_filter(config.get("label_filter")),
    }
    for key, cast in (
        ("max_misses", int),
        ("min_hits", int),
        ("max_tracks", int),
        ("history_size", int),
        ("match_iou", float),
        ("track_high_thresh", float),
        ("track_low_thresh", float),
        ("new_track_thresh", float),
        ("match_thresh", float),
    ):
        value = _optional_number(config, key, cast)
        if value is not None:
            kwargs[key] = value
    return kwargs


def _identity_key(config: dict, frame_width: Optional[int], frame_height: Optional[int]):
    return (
        config.get("source_id"),
        int(frame_width or 0),
        int(frame_height or 0),
    )


def init(config: dict) -> Dict[str, Any]:
    backend = str(config.get("backend") or "iou")
    tracker = create_tracker(backend, **_tracker_kwargs(config))
    return {
        "tracker": tracker,
        "backend": tracker.backend,
        "identity_key": None,
    }


def process(
    frame: np.ndarray,
    config: dict,
    roi_regions: Optional[List[dict]] = None,
    state: Optional[dict] = None,
    upstream_results: Optional[dict] = None,
    frame_width: Optional[int] = None,
    frame_height: Optional[int] = None,
    frame_timestamp: Optional[float] = None,
    pixel_format: str = "nv12",
) -> dict:
    if not isinstance(state, dict):
        return build_result([], metadata={"error": "tracker_state_missing"})
    if "tracker" not in state:
        state.update(init(config))

    identity_key = _identity_key(config, frame_width, frame_height)
    if state.get("identity_key") not in (None, identity_key):
        state["tracker"].reset()
    state["identity_key"] = identity_key

    detections = _collect_upstream_detections(upstream_results)
    tracks = state["tracker"].update(detections, timestamp=frame_timestamp)
    backend = state.get("backend") or getattr(state["tracker"], "backend", "iou")
    return build_result(
        [track.to_detection(backend) for track in tracks],
        metadata={
            "backend": backend,
            "track_count": len(tracks),
            "upstream_count": len(detections),
        },
    )


def cleanup(state: Optional[dict] = None) -> None:
    if state and state.get("tracker") is not None:
        state["tracker"].reset()
