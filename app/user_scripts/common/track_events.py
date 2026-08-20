"""Post-track event filters.

Tracker assigns identity; this module decides which tracks to emit:
none / loiter / stay / region_cross. New behaviors should be added here
instead of new algorithm scripts.
"""

from collections import deque
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

from app.user_scripts.common.tracker import center_of

EVENT_NONE = "none"
EVENT_LOITER = "loiter"
EVENT_STAY = "stay"
EVENT_REGION_CROSS = "region_cross"
SUPPORTED_EVENTS = (EVENT_NONE, EVENT_LOITER, EVENT_STAY, EVENT_REGION_CROSS)

EVENT_LABELS = {
    EVENT_NONE: "轨迹",
    EVENT_LOITER: "徘徊",
    EVENT_STAY: "停留",
    EVENT_REGION_CROSS: "穿越",
}

CROSS_ENTER = "enter"
CROSS_EXIT = "exit"
CROSS_ANY = "cross"
CROSS_MODES = (CROSS_ENTER, CROSS_EXIT, CROSS_ANY)

DIR_ANY = "any"
DIR_L2R = "left_to_right"
DIR_R2L = "right_to_left"
DIR_T2B = "top_to_bottom"
DIR_B2T = "bottom_to_top"
CROSS_DIRECTIONS = (DIR_ANY, DIR_L2R, DIR_R2L, DIR_T2B, DIR_B2T)

STAY_DEFAULT_DISPLACE_PX = 48.0
DEFAULT_MIN_DWELL_SECONDS = 8.0
DEFAULT_DISAPPEAR_SECONDS = 3.0
DEFAULT_HISTORY_SIZE = 128


def normalize_event(value: Any) -> str:
    raw = str(value or EVENT_NONE).strip().lower()
    aliases = {
        "passthrough": EVENT_NONE,
        "identity": EVENT_NONE,
        "track": EVENT_NONE,
        "dwell": EVENT_LOITER,
        "wander": EVENT_LOITER,
        "lingering": EVENT_LOITER,
        "stop": EVENT_STAY,
        "stationary": EVENT_STAY,
        "cross": EVENT_REGION_CROSS,
        "crossing": EVENT_REGION_CROSS,
        "enter": EVENT_REGION_CROSS,
        "tripwire": EVENT_REGION_CROSS,
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in SUPPORTED_EVENTS else EVENT_NONE


def _optional_number(config: dict, key: str, cast):
    value = config.get(key)
    if value is None or value == "":
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def event_settings(config: Optional[dict] = None) -> dict:
    config = config or {}
    event = normalize_event(config.get("event"))
    min_dwell = _optional_number(config, "min_dwell_seconds", float)
    max_displace = _optional_number(config, "max_displace_px", float)
    disappear = _optional_number(config, "disappear_seconds", float)
    history_size = _optional_number(config, "history_size", int)
    if event == EVENT_STAY and max_displace is None:
        max_displace = STAY_DEFAULT_DISPLACE_PX
    cross_mode = str(config.get("cross_mode") or CROSS_ENTER).strip().lower()
    if cross_mode not in CROSS_MODES:
        cross_mode = CROSS_ENTER
    cross_direction = str(config.get("cross_direction") or DIR_ANY).strip().lower()
    if cross_direction not in CROSS_DIRECTIONS:
        cross_direction = DIR_ANY
    return {
        "event": event,
        "min_dwell_seconds": float(min_dwell if min_dwell is not None else DEFAULT_MIN_DWELL_SECONDS),
        "max_displace_px": max_displace,
        "disappear_seconds": float(disappear if disappear is not None else DEFAULT_DISAPPEAR_SECONDS),
        "history_size": max(8, int(history_size if history_size is not None else DEFAULT_HISTORY_SIZE)),
        "cross_mode": cross_mode,
        "cross_direction": cross_direction,
    }


def init_event_state(config: Optional[dict] = None) -> dict:
    return {"tracks": {}, "settings": event_settings(config)}


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


def _max_radius(points: Sequence[Tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    cx = sum(point[0] for point in points) / len(points)
    cy = sum(point[1] for point in points) / len(points)
    return max(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in points)


def _window_points(
    history: Iterable[Tuple[float, float, float]],
    now: float,
    window_seconds: float,
) -> List[Tuple[float, float]]:
    if window_seconds <= 0:
        return [(cx, cy) for _, cx, cy in history]
    return [(cx, cy) for ts, cx, cy in history if now - float(ts) <= window_seconds]


def _region_points(region: dict, width: int, height: int) -> List[Tuple[float, float]]:
    raw = region.get("polygon") or region.get("points") or []
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return []
    points: List[Tuple[float, float]] = []
    if isinstance(raw[0], dict):
        for item in raw:
            try:
                points.append((float(item.get("x", 0.0)) * width, float(item.get("y", 0.0)) * height))
            except (TypeError, ValueError, AttributeError):
                continue
        return points if len(points) >= 3 else []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            points.append((float(item[0]), float(item[1])))
        except (TypeError, ValueError):
            continue
    return points if len(points) >= 3 else []


def point_in_polygon(x: float, y: float, polygon: Sequence[Tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) + 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _inside_any_roi(
    x: float,
    y: float,
    roi_regions: Optional[List[dict]],
    width: int,
    height: int,
) -> Tuple[bool, Optional[str]]:
    for index, region in enumerate(roi_regions or []):
        polygon = _region_points(region, width, height)
        if polygon and point_in_polygon(x, y, polygon):
            name = region.get("name") or region.get("label") or f"roi-{index + 1}"
            return True, str(name)
    return False, None


def _direction_ok(dx: float, dy: float, direction: str) -> bool:
    if direction == DIR_ANY:
        return True
    if direction == DIR_L2R:
        return dx > 0
    if direction == DIR_R2L:
        return dx < 0
    if direction == DIR_T2B:
        return dy > 0
    if direction == DIR_B2T:
        return dy < 0
    return True


def _annotate(det: dict, **fields: Any) -> dict:
    output = dict(det)
    attributes = dict(output.get("attributes") or {})
    attributes.update(fields)
    output["attributes"] = attributes
    return output


def apply_event(
    detections: Optional[List[dict]],
    *,
    config: Optional[dict] = None,
    state: Optional[dict] = None,
    timestamp: Optional[float] = None,
    roi_regions: Optional[List[dict]] = None,
    frame_width: Optional[int] = None,
    frame_height: Optional[int] = None,
) -> Tuple[List[dict], dict]:
    settings = event_settings(config)
    event = settings["event"]
    metadata = {
        "event": event,
        "event_count": 0,
        "upstream_count": len(detections or []),
        "live_tracks": 0,
        "skipped_no_track_id": 0,
    }
    if event == EVENT_NONE:
        metadata["event_count"] = len(detections or [])
        return list(detections or []), metadata

    if not isinstance(state, dict):
        return [], {**metadata, "error": "event_state_missing"}
    if "tracks" not in state or not isinstance(state["tracks"], dict):
        state["tracks"] = {}

    now = float(timestamp) if timestamp is not None else 0.0
    width = int(frame_width or 0)
    height = int(frame_height or 0)
    history_size = settings["history_size"]
    disappear_seconds = settings["disappear_seconds"]
    live_ids = set()
    emitted: List[dict] = []
    skipped_no_id = 0

    for det in detections or []:
        if not isinstance(det, dict):
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
                "inside": None,
                "last_center": None,
            }
            state["tracks"][track_id] = record
        points: Deque = record["points"]
        if getattr(points, "maxlen", history_size) != history_size:
            record["points"] = deque(points, maxlen=history_size)
            points = record["points"]
        record["last_seen_ts"] = now
        points.append((now, center[0], center[1]))
        live_ids.add(track_id)

        dwell = max(0.0, now - float(record["first_seen_ts"]))
        xy_points = _window_points(points, now, settings["min_dwell_seconds"])
        if len(xy_points) < 2:
            xy_points = [(cx, cy) for _, cx, cy in points]
        displace = _max_radius(xy_points or [(center[0], center[1])])
        history = [{"ts": float(ts), "cx": float(cx), "cy": float(cy)} for ts, cx, cy in points]

        matched = False
        extra: Dict[str, Any] = {
            "event": event,
            "event_label": EVENT_LABELS[event],
            "dwell_seconds": dwell,
            "first_seen_ts": float(record["first_seen_ts"]),
            "last_seen_ts": now,
            "displace_px": displace,
            "history": history,
        }

        if event in (EVENT_LOITER, EVENT_STAY):
            if dwell >= settings["min_dwell_seconds"]:
                max_displace = settings["max_displace_px"]
                if max_displace is None or displace <= float(max_displace):
                    matched = True
        elif event == EVENT_REGION_CROSS:
            if width <= 0 or height <= 0 or not roi_regions:
                metadata["roi_missing"] = True
            else:
                inside, roi_name = _inside_any_roi(center[0], center[1], roi_regions, width, height)
                prev_inside = record.get("inside")
                prev_center = record.get("last_center")
                dx = center[0] - prev_center[0] if prev_center else 0.0
                dy = center[1] - prev_center[1] if prev_center else 0.0
                record["inside"] = inside
                record["last_center"] = center
                if prev_inside is None:
                    continue
                entered = (not prev_inside) and inside
                exited = prev_inside and (not inside)
                mode = settings["cross_mode"]
                edge = (
                    (mode == CROSS_ENTER and entered)
                    or (mode == CROSS_EXIT and exited)
                    or (mode == CROSS_ANY and (entered or exited))
                )
                if edge and _direction_ok(dx, dy, settings["cross_direction"]):
                    matched = True
                    extra.update({
                        "cross_mode": "enter" if entered else "exit",
                        "cross_direction": settings["cross_direction"],
                        "roi_name": roi_name,
                    })

        if matched:
            output = _annotate(det, **extra)
            output["track_id"] = track_id
            emitted.append(output)

    stale = []
    for track_id, record in state["tracks"].items():
        if track_id in live_ids:
            continue
        gap = now - float(record.get("last_seen_ts") or now)
        if gap > disappear_seconds:
            stale.append(track_id)
    for track_id in stale:
        state["tracks"].pop(track_id, None)

    metadata.update({
        "event_count": len(emitted),
        "live_tracks": len(live_ids),
        "skipped_no_track_id": skipped_no_id,
        "min_dwell_seconds": settings["min_dwell_seconds"],
    })
    return emitted, metadata
