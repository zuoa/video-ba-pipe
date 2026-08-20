"""Compatibility shim: loiter as a standalone node.

New workflows should set 目标追踪 → 输出事件=徘徊 instead of chaining this script.
Kept so existing 目标徘徊 algorithms keep working.
"""

from typing import Any, Dict, List, Optional

import numpy as np

from app.user_scripts.common.result import build_result, collect_upstream_detections
from app.user_scripts.common.track_events import EVENT_LOITER, apply_event, init_event_state

SCRIPT_METADATA = {
    "name": "目标徘徊",
    "version": "v1.1",
    "description": "兼容旧编排：请改用目标追踪的「输出事件=徘徊」。接在追踪之后时行为不变。",
    "author": "system",
    "category": "tracking",
    "tags": ["loiter", "dwell", "wander", "tracking", "legacy"],
    "config_schema": {
        "min_dwell_seconds": {
            "type": "float",
            "label": "最短时长（秒）",
            "required": True,
            "default": 8,
            "min": 0.5,
            "max": 600,
            "step": 0.5,
            "description": "同一目标持续出现达到该时长才输出",
        },
        "min_displace_px": {
            "type": "int",
            "label": "最小位移（像素）",
            "default": 0,
            "min": 0,
            "max": 4000,
            "step": 1,
            "description": "活动半径低于该值不算徘徊；0 表示不限制",
        },
        "max_displace_px": {
            "type": "int",
            "label": "最大位移（像素）",
            "min": 0,
            "max": 4000,
            "step": 1,
            "description": "活动半径超过该值不算；留空不限制",
        },
        "disappear_seconds": {
            "type": "float",
            "label": "消失后遗忘（秒）",
            "default": 3,
            "min": 0.5,
            "max": 120,
            "step": 0.5,
            "description": "目标丢失超过该时间后重新计时",
        },
        "history_size": {
            "type": "int",
            "label": "轨迹点数",
            "default": 128,
            "min": 8,
            "max": 1024,
            "step": 8,
            "description": "写入 attributes.history，供告警图画轨迹",
        },
        "label_filter": {
            "type": "string",
            "label": "类别过滤",
            "default": "",
            "placeholder": "person",
            "description": "留空表示全部；多个类别用逗号分隔",
        },
    },
    "performance": {
        "timeout": 5,
        "memory_limit_mb": 64,
        "gpu_required": False,
        "estimated_time_ms": 3,
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
    return [part.strip() for part in text.split(",") if part.strip()]


def init(config: dict) -> Dict[str, Any]:
    return {
        "event": init_event_state({**config, "event": EVENT_LOITER}),
        "label_filter": {item.lower() for item in _parse_label_filter(config.get("label_filter"))},
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
        return build_result([], metadata={"error": "loiter_state_missing"})
    if "event" not in state:
        state.update(init(config))

    detections = collect_upstream_detections(upstream_results)
    label_filter = state.get("label_filter") or set()
    if label_filter:
        detections = [
            det for det in detections
            if str(det.get("label") or det.get("label_name") or det.get("class_name") or "").strip().lower()
            in label_filter
        ]

    height = int(frame_height or 0)
    width = int(frame_width or 0)
    if (height <= 0 or width <= 0) and frame is not None and getattr(frame, "ndim", 0) == 3 and frame.shape[-1] in (3, 4):
        height = int(frame.shape[0])
        width = int(frame.shape[1])

    emitted, metadata = apply_event(
        detections,
        config={**config, "event": EVENT_LOITER},
        state=state["event"],
        timestamp=frame_timestamp,
        roi_regions=roi_regions,
        frame_width=width,
        frame_height=height,
    )
    return build_result(emitted, metadata=metadata)


def cleanup(state: Optional[dict] = None) -> None:
    if state is not None:
        state["event"] = init_event_state({"event": EVENT_LOITER})
