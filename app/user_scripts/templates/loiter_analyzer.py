"""Loiter / dwell analyzer.

Consumes upstream tracks, keeps per-track first-seen time, and only emits
objects that have stayed at least min_dwell_seconds. Optional radius filter
drops objects that are just passing through.
"""

from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.user_scripts.common.result import build_result, collect_upstream_detections
from app.user_scripts.common.tracker import center_of

SCRIPT_METADATA = {
    "name": "目标徘徊",
    "version": "v1.0",
    "description": "接在目标追踪之后：同一 track_id 停留达到阈值才输出。向导间隔最低 0.1 秒。",
    "author": "system",
    "category": "tracking",
    "tags": ["loiter", "dwell", "wander", "tracking"],
    "config_schema": {
        "min_dwell_seconds": {
            "type": "float",
            "label": "最短停留（秒）",
            "required": True,
            "default": 8,
            "min": 1,
            "max": 600,
            "step": 1,
            "description": "同一目标持续出现达到该时长才输出，避免把路过当成徘徊",
        },
        "max_displace_px": {
            "type": "int",
            "label": "活动半径上限（像素）",
            "min": 1,
            "max": 4000,
            "step": 10,
            "description": "留空只看停留时长；填写后轨迹需缩在该半径内才算徘徊，用于过滤穿行",
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


def _identity_key(config: dict, frame_width: Optional[int], frame_height: Optional[int]):
    return (
        config.get("source_id"),
        int(frame_width or 0),
        int(frame_height or 0),
    )


def _box_center(det: dict) -> Optional[Tuple[float, float]]:
    box = det.get("box") or det.get("bbox")
    if not isinstance(box, (list, tuple)) or len(box) < 4:
        return None
    return center_of(box)


def _track_id(det: dict) -> Optional[int]:
    raw = det.get("track_id")
    if raw is None and isinstance(det.get("attributes"), dict):
        raw = det["attributes"].get("track_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _max_radius(points: List[Tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    cx = sum(point[0] for point in points) / len(points)
    cy = sum(point[1] for point in points) / len(points)
    return max(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in points)


def init(config: dict) -> Dict[str, Any]:
    history_size = _optional_number(config, "history_size", int) or 128
    return {
        "tracks": {},
        "identity_key": None,
        "history_size": max(8, int(history_size)),
        "label_filter": {item.lower() for item in _parse_label_filter(config.get("label_filter"))},
        "min_dwell_seconds": float(_optional_number(config, "min_dwell_seconds", float) or 8),
        "max_displace_px": _optional_number(config, "max_displace_px", float),
        "disappear_seconds": float(_optional_number(config, "disappear_seconds", float) or 3),
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
    if "tracks" not in state:
        state.update(init(config))

    identity_key = _identity_key(config, frame_width, frame_height)
    if state.get("identity_key") not in (None, identity_key):
        state["tracks"] = {}
    state["identity_key"] = identity_key

    now = float(frame_timestamp) if frame_timestamp is not None else 0.0
    detections = collect_upstream_detections(upstream_results)
    label_filter = state.get("label_filter") or set()
    history_size = int(state.get("history_size") or 128)
    min_dwell = float(state.get("min_dwell_seconds") or 8)
    max_displace = state.get("max_displace_px")
    disappear_seconds = float(state.get("disappear_seconds") or 3)

    live_ids = set()
    emitted: List[dict] = []
    skipped_no_id = 0

    for det in detections:
        if label_filter:
            label = str(det.get("label") or det.get("label_name") or det.get("class_name") or "").strip().lower()
            if label not in label_filter:
                continue
        track_id = _track_id(det)
        if track_id is None:
            skipped_no_id += 1
            continue
        center = _box_center(det)
        if center is None:
            continue

        record = state["tracks"].get(track_id)
        if record is None:
            record = {
                "first_seen_ts": now,
                "last_seen_ts": now,
                "points": deque(maxlen=history_size),
            }
            state["tracks"][track_id] = record
        if record["points"].maxlen != history_size:
            record["points"] = deque(record["points"], maxlen=history_size)
        record["last_seen_ts"] = now
        record["points"].append((now, center[0], center[1]))
        live_ids.add(track_id)

        dwell = max(0.0, now - float(record["first_seen_ts"]))
        xy_points = [(cx, cy) for _, cx, cy in record["points"]]
        displace = _max_radius(xy_points)
        if dwell < min_dwell:
            continue
        if max_displace is not None and displace > float(max_displace):
            continue

        output = dict(det)
        attributes = dict(output.get("attributes") or {})
        attributes.update({
            "dwell_seconds": dwell,
            "first_seen_ts": float(record["first_seen_ts"]),
            "last_seen_ts": now,
            "displace_px": displace,
        })
        attributes["history"] = [
            {"ts": float(ts), "cx": float(cx), "cy": float(cy)}
            for ts, cx, cy in record["points"]
        ]
        output["track_id"] = track_id
        output["attributes"] = attributes
        emitted.append(output)

    stale_ids = []
    for track_id, record in state["tracks"].items():
        if track_id in live_ids:
            continue
        gap = now - float(record.get("last_seen_ts") or now)
        if gap > disappear_seconds:
            stale_ids.append(track_id)
    for track_id in stale_ids:
        state["tracks"].pop(track_id, None)

    return build_result(
        emitted,
        metadata={
            "loiter_count": len(emitted),
            "upstream_count": len(detections),
            "live_tracks": len(live_ids),
            "skipped_no_track_id": skipped_no_id,
            "min_dwell_seconds": min_dwell,
        },
    )


def cleanup(state: Optional[dict] = None) -> None:
    if state is not None:
        state["tracks"] = {}
