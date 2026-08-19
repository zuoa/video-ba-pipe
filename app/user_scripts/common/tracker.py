"""Object-tracking contracts and shared helpers.

Backends implement the same update/reset protocol and emit Track objects.
Downstream analyzers (dwell, loiter) should consume track_id only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

import numpy as np

try:
    from scipy.optimize import linear_sum_assignment as _linear_sum_assignment
except Exception:  # pragma: no cover - optional
    _linear_sum_assignment = None


BACKEND_IOU = "iou"
BACKEND_BYTETRACK = "bytetrack"
SUPPORTED_BACKENDS = (BACKEND_IOU, BACKEND_BYTETRACK)

DEFAULTS = {
    BACKEND_IOU: {
        "match_iou": 0.3,
        "max_misses": 3,
        "min_hits": 1,
        "max_tracks": 256,
        "history_size": 32,
    },
    BACKEND_BYTETRACK: {
        "max_misses": 30,
        "min_hits": 1,
        "max_tracks": 256,
        "history_size": 32,
        "track_high_thresh": 0.5,
        "track_low_thresh": 0.1,
        "new_track_thresh": 0.6,
        "match_thresh": 0.8,
    },
}


@dataclass
class Track:
    track_id: int
    box: List[float]
    label: str
    confidence: float
    hits: int = 1
    misses: int = 0
    age: int = 1
    last_seen_ts: float = 0.0
    history: Deque[Tuple[float, float, float]] = field(default_factory=deque)
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_detection(self, backend: str) -> Dict[str, Any]:
        detection = dict(self.payload)
        attributes = dict(detection.get("attributes") or {})
        attributes.update({
            "hits": self.hits,
            "misses": self.misses,
            "age": self.age,
            "backend": backend,
        })
        attributes["history"] = [
            {"ts": float(ts), "cx": float(cx), "cy": float(cy)}
            for ts, cx, cy in self.history
        ]
        detection.update({
            "box": [float(v) for v in self.box[:4]],
            "label": self.label,
            "confidence": float(self.confidence),
            "track_id": int(self.track_id),
            "attributes": attributes,
        })
        if not detection.get("label_name"):
            detection["label_name"] = self.label
        return detection


@dataclass
class ParsedDetection:
    box: List[float]
    score: float
    label: str
    track_id: Optional[int]
    payload: Dict[str, Any]


class BaseTracker(ABC):
    backend: str = ""

    @abstractmethod
    def update(
        self,
        detections: Optional[Iterable[Dict[str, Any]]] = None,
        timestamp: Optional[float] = None,
    ) -> List[Track]:
        raise NotImplementedError

    @abstractmethod
    def reset(self) -> None:
        raise NotImplementedError


class Tracker(Protocol):
    backend: str

    def update(
        self,
        detections: Optional[Iterable[Dict[str, Any]]] = None,
        timestamp: Optional[float] = None,
    ) -> List[Track]:
        ...

    def reset(self) -> None:
        ...


def box_iou(box_a: Sequence[float], box_b: Sequence[float]) -> float:
    ax1, ay1, ax2, ay2 = [float(v) for v in box_a[:4]]
    bx1, by1, bx2, by2 = [float(v) for v in box_b[:4]]
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    inter_w = max(0.0, ix2 - ix1)
    inter_h = max(0.0, iy2 - iy1)
    inter = inter_w * inter_h
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def pairwise_iou(track_boxes: Sequence[Sequence[float]], det_boxes: Sequence[Sequence[float]]) -> np.ndarray:
    iou = np.zeros((len(track_boxes), len(det_boxes)), dtype=np.float64)
    for i, track_box in enumerate(track_boxes):
        for j, det_box in enumerate(det_boxes):
            iou[i, j] = box_iou(track_box, det_box)
    return iou


def associate_by_iou(
    track_boxes: Sequence[Sequence[float]],
    det_boxes: Sequence[Sequence[float]],
    iou_threshold: float,
):
    n_tracks = len(track_boxes)
    n_dets = len(det_boxes)
    if n_tracks == 0 or n_dets == 0:
        return [], list(range(n_tracks)), list(range(n_dets))

    iou = pairwise_iou(track_boxes, det_boxes)
    matches: List[Tuple[int, int]] = []
    unmatched_tracks = set(range(n_tracks))
    unmatched_dets = set(range(n_dets))

    if _linear_sum_assignment is not None:
        cost = 1.0 - iou
        cost[iou < float(iou_threshold)] = 1e5
        rows, cols = _linear_sum_assignment(cost)
        for row, col in zip(rows.tolist(), cols.tolist()):
            if iou[row, col] >= float(iou_threshold):
                matches.append((int(row), int(col)))
                unmatched_tracks.discard(int(row))
                unmatched_dets.discard(int(col))
    else:
        pairs = []
        for i in range(n_tracks):
            for j in range(n_dets):
                if iou[i, j] >= float(iou_threshold):
                    pairs.append((float(iou[i, j]), i, j))
        pairs.sort(reverse=True)
        for _, i, j in pairs:
            if i in unmatched_tracks and j in unmatched_dets:
                matches.append((i, j))
                unmatched_tracks.discard(i)
                unmatched_dets.discard(j)

    return matches, sorted(unmatched_tracks), sorted(unmatched_dets)


def detection_label(det: Dict[str, Any]) -> str:
    label = det.get("label") or det.get("label_name") or det.get("class_name")
    if label is None and det.get("class") is not None:
        return str(det.get("class"))
    return str(label or "object")


def detection_score(det: Dict[str, Any]) -> float:
    if det.get("confidence") is not None:
        return float(det.get("confidence") or 0.0)
    if det.get("score") is not None:
        return float(det.get("score") or 0.0)
    return 0.0


def detection_box(det: Dict[str, Any]) -> Optional[List[float]]:
    box = det.get("box") or det.get("bbox")
    if not isinstance(box, (list, tuple)) or len(box) < 4:
        return None
    x1, y1, x2, y2 = [float(v) for v in box[:4]]
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def parse_detection(det: Dict[str, Any]) -> Optional[ParsedDetection]:
    if not isinstance(det, dict):
        return None
    box = detection_box(det)
    if box is None:
        return None
    raw_track_id = det.get("track_id")
    track_id = None
    if raw_track_id is not None:
        try:
            track_id = int(raw_track_id)
        except (TypeError, ValueError):
            track_id = None
    return ParsedDetection(
        box=box,
        score=detection_score(det),
        label=detection_label(det),
        track_id=track_id,
        payload=dict(det),
    )


def filter_parsed(
    detections: Iterable[ParsedDetection],
    label_filter: Sequence[Any],
) -> List[ParsedDetection]:
    if not label_filter:
        return list(detections)
    allowed = {str(item) for item in label_filter}
    kept = []
    for det in detections:
        class_id = det.payload.get("class")
        if det.label in allowed or str(class_id) in allowed:
            kept.append(det)
            continue
        if class_id is not None and class_id in label_filter:
            kept.append(det)
    return kept


def normalize_label_filter(label_filter: Optional[Sequence[Any]] = None) -> List[str]:
    if not label_filter:
        return []
    if isinstance(label_filter, str):
        return [part.strip() for part in label_filter.split(",") if part.strip()]
    return [str(item).strip() for item in label_filter if str(item).strip()]


def parse_detections(
    detections: Optional[Iterable[Dict[str, Any]]],
    label_filter: Sequence[Any] = (),
) -> List[ParsedDetection]:
    parsed = []
    for det in detections or []:
        item = parse_detection(det)
        if item is not None:
            parsed.append(item)
    return filter_parsed(parsed, normalize_label_filter(label_filter))


def center_of(box: Sequence[float]) -> Tuple[float, float]:
    return (float(box[0]) + float(box[2])) / 2.0, (float(box[1]) + float(box[3])) / 2.0


def append_history(track: Track, timestamp: float, history_size: int) -> None:
    cx, cy = center_of(track.box)
    if not isinstance(track.history, deque):
        track.history = deque(track.history, maxlen=int(history_size))
    if track.history.maxlen != int(history_size):
        track.history = deque(track.history, maxlen=int(history_size))
    track.history.append((float(timestamp), cx, cy))


def new_track(
    track_id: int,
    det: ParsedDetection,
    timestamp: float,
    history_size: int,
) -> Track:
    track = Track(
        track_id=track_id,
        box=list(det.box),
        label=det.label,
        confidence=det.score,
        hits=1,
        misses=0,
        age=1,
        last_seen_ts=float(timestamp),
        history=deque(maxlen=int(history_size)),
        payload=dict(det.payload),
    )
    append_history(track, timestamp, history_size)
    return track


def mark_matched(track: Track, det: ParsedDetection, timestamp: float, history_size: int) -> None:
    track.box = list(det.box)
    track.label = det.label
    track.confidence = det.score
    track.payload = dict(det.payload)
    track.hits += 1
    track.misses = 0
    track.age += 1
    track.last_seen_ts = float(timestamp)
    append_history(track, timestamp, history_size)


def mark_missed(track: Track) -> None:
    track.misses += 1
    track.age += 1


def prune_tracks(tracks: List[Track], max_misses: int, max_tracks: int) -> List[Track]:
    alive = [track for track in tracks if track.misses < int(max_misses)]
    overflow = len(alive) - int(max_tracks)
    if overflow <= 0:
        return alive
    ranked = sorted(
        alive,
        key=lambda track: (-track.misses, track.last_seen_ts, track.age),
    )
    drop_ids = {id(track) for track in ranked[:overflow]}
    return [track for track in alive if id(track) not in drop_ids]


def confirmed_tracks(tracks: Sequence[Track], min_hits: int) -> List[Track]:
    return [track for track in tracks if track.hits >= int(min_hits) and track.misses == 0]


def associate_class_aware(
    tracks: Sequence[Track],
    detections: Sequence[ParsedDetection],
    iou_threshold: float,
    box_fn=None,
):
    if box_fn is None:
        box_fn = lambda track: track.box

    matches: List[Tuple[int, int]] = []
    unmatched_tracks = set(range(len(tracks)))
    unmatched_dets = set(range(len(detections)))
    labels = {track.label for track in tracks} | {det.label for det in detections}

    for label in labels:
        track_indices = [idx for idx, track in enumerate(tracks) if track.label == label]
        det_indices = [idx for idx, det in enumerate(detections) if det.label == label]
        if not track_indices or not det_indices:
            continue
        local_matches, _, _ = associate_by_iou(
            [box_fn(tracks[idx]) for idx in track_indices],
            [detections[idx].box for idx in det_indices],
            iou_threshold,
        )
        for local_t, local_d in local_matches:
            track_idx = track_indices[local_t]
            det_idx = det_indices[local_d]
            matches.append((track_idx, det_idx))
            unmatched_tracks.discard(track_idx)
            unmatched_dets.discard(det_idx)

    return matches, sorted(unmatched_tracks), sorted(unmatched_dets)


class IdAllocator:
    def __init__(self) -> None:
        self.next_id = 1

    def allocate(
        self,
        preferred: Optional[int] = None,
        live_ids: Optional[Iterable[int]] = None,
    ) -> int:
        live = {int(item) for item in (live_ids or [])}
        if preferred is not None:
            preferred_id = int(preferred)
            if preferred_id not in live:
                self.next_id = max(self.next_id, preferred_id + 1)
                return preferred_id
        while self.next_id in live:
            self.next_id += 1
        track_id = self.next_id
        self.next_id += 1
        return track_id

    def reset(self) -> None:
        self.next_id = 1


def split_passthrough(detections: Sequence[ParsedDetection]) -> Tuple[List[ParsedDetection], List[ParsedDetection]]:
    owned: List[ParsedDetection] = []
    fresh: List[ParsedDetection] = []
    for det in detections:
        if det.track_id is None:
            fresh.append(det)
        else:
            owned.append(det)
    return owned, fresh


def find_track(tracks: Sequence[Track], track_id: Optional[int]) -> Optional[Track]:
    if track_id is None:
        return None
    for track in tracks:
        if track.track_id == track_id:
            return track
    return None


def resolve_timestamp(timestamp: Optional[float], fallback_age: int) -> float:
    if timestamp is None:
        return float(fallback_age)
    return float(timestamp)


def create_tracker(backend: str = BACKEND_IOU, **kwargs) -> BaseTracker:
    normalized = str(backend or BACKEND_IOU).strip().lower()
    if normalized in {"byte", "byte_track"}:
        normalized = BACKEND_BYTETRACK
    if normalized == BACKEND_IOU:
        from app.user_scripts.common.iou_tracker import IoUTracker

        defaults = DEFAULTS[BACKEND_IOU]
        return IoUTracker(
            match_iou=kwargs.get("match_iou", defaults["match_iou"]),
            max_misses=kwargs.get("max_misses", defaults["max_misses"]),
            min_hits=kwargs.get("min_hits", defaults["min_hits"]),
            max_tracks=kwargs.get("max_tracks", defaults["max_tracks"]),
            history_size=kwargs.get("history_size", defaults["history_size"]),
            label_filter=normalize_label_filter(kwargs.get("label_filter")),
        )
    if normalized == BACKEND_BYTETRACK:
        from app.user_scripts.common.byte_tracker import ByteTracker

        defaults = DEFAULTS[BACKEND_BYTETRACK]
        return ByteTracker(
            max_misses=kwargs.get("max_misses", defaults["max_misses"]),
            min_hits=kwargs.get("min_hits", defaults["min_hits"]),
            max_tracks=kwargs.get("max_tracks", defaults["max_tracks"]),
            history_size=kwargs.get("history_size", defaults["history_size"]),
            label_filter=normalize_label_filter(kwargs.get("label_filter")),
            track_high_thresh=kwargs.get("track_high_thresh", defaults["track_high_thresh"]),
            track_low_thresh=kwargs.get("track_low_thresh", defaults["track_low_thresh"]),
            new_track_thresh=kwargs.get("new_track_thresh", defaults["new_track_thresh"]),
            match_thresh=kwargs.get("match_thresh", defaults["match_thresh"]),
        )
    raise ValueError(f"未知跟踪后端: {backend}，可选 {SUPPORTED_BACKENDS}")
