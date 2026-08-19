"""Class-aware greedy IoU tracker."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.user_scripts.common.tracker import (
    BACKEND_IOU,
    BaseTracker,
    IdAllocator,
    Track,
    associate_class_aware,
    confirmed_tracks,
    find_track,
    mark_matched,
    mark_missed,
    new_track,
    normalize_label_filter,
    parse_detections,
    prune_tracks,
    resolve_timestamp,
    split_passthrough,
)


class IoUTracker(BaseTracker):
    backend = BACKEND_IOU

    def __init__(
        self,
        match_iou: float = 0.3,
        max_misses: int = 3,
        min_hits: int = 1,
        max_tracks: int = 256,
        history_size: int = 32,
        label_filter: Optional[Sequence[Any]] = None,
    ) -> None:
        self.match_iou = float(match_iou)
        self.max_misses = int(max_misses)
        self.min_hits = int(min_hits)
        self.max_tracks = int(max_tracks)
        self.history_size = int(history_size)
        self.label_filter = normalize_label_filter(label_filter)
        self.tracks: List[Track] = []
        self._ids = IdAllocator()
        self._frame_index = 0

    def reset(self) -> None:
        self.tracks = []
        self._ids.reset()
        self._frame_index = 0

    def update(
        self,
        detections: Optional[Iterable[Dict[str, Any]]] = None,
        timestamp: Optional[float] = None,
    ) -> List[Track]:
        self._frame_index += 1
        ts = resolve_timestamp(timestamp, self._frame_index)
        parsed = parse_detections(detections, self.label_filter)

        owned, fresh = split_passthrough(parsed)
        matched_tracks = set()

        for det in owned:
            existing = find_track(self.tracks, det.track_id)
            if existing is not None and id(existing) not in matched_tracks:
                mark_matched(existing, det, ts, self.history_size)
                matched_tracks.add(id(existing))
                continue
            live_ids = {track.track_id for track in self.tracks}
            started = new_track(
                self._ids.allocate(det.track_id, live_ids),
                det,
                ts,
                self.history_size,
            )
            self.tracks.append(started)
            matched_tracks.add(id(started))

        unmatched_tracks = [track for track in self.tracks if id(track) not in matched_tracks]
        matches, unmatched_track_idx, unmatched_det_idx = associate_class_aware(
            unmatched_tracks,
            fresh,
            self.match_iou,
        )
        for track_idx, det_idx in matches:
            mark_matched(unmatched_tracks[track_idx], fresh[det_idx], ts, self.history_size)
            matched_tracks.add(id(unmatched_tracks[track_idx]))

        for track_idx in unmatched_track_idx:
            mark_missed(unmatched_tracks[track_idx])

        for det_idx in unmatched_det_idx:
            live_ids = {track.track_id for track in self.tracks}
            self.tracks.append(new_track(self._ids.allocate(live_ids=live_ids), fresh[det_idx], ts, self.history_size))

        self.tracks = prune_tracks(self.tracks, self.max_misses, self.max_tracks)
        return confirmed_tracks(self.tracks, self.min_hits)
