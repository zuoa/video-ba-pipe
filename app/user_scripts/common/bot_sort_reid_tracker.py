"""BoT-SORT style single-camera tracker with appearance re-identification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from app.user_scripts.common import tracker as tracker_common
from app.user_scripts.common.byte_tracker import KalmanFilterXYAH, xyah_to_xyxy, xyxy_to_xyah
from app.user_scripts.common.tracker import (
    BaseTracker,
    IdAllocator,
    ParsedDetection,
    Track,
    append_history,
    box_iou,
    confirmed_tracks,
    mark_missed,
    new_track,
    normalize_label_filter,
    parse_detections,
    resolve_timestamp,
)


BACKEND_BOTSORT_REID = 'botsort_reid'


@dataclass
class LostTrack:
    track: Track
    feature: np.ndarray
    lost_at: float


def _feature(det: ParsedDetection) -> Optional[np.ndarray]:
    value = det.payload.pop('_reid_embedding', None)
    if value is None:
        return None
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-12:
        return None
    vector = vector / norm
    return vector if np.all(np.isfinite(vector)) else None


def _assign_cost(cost: np.ndarray, invalid_cost: float = 1e5):
    rows, cols = cost.shape
    if rows == 0 or cols == 0:
        return [], list(range(rows)), list(range(cols))
    matches: List[Tuple[int, int]] = []
    unmatched_rows, unmatched_cols = set(range(rows)), set(range(cols))
    solver = tracker_common._linear_sum_assignment
    if solver is not None:
        assigned_rows, assigned_cols = solver(cost)
        for row, col in zip(assigned_rows.tolist(), assigned_cols.tolist()):
            if float(cost[row, col]) < invalid_cost:
                matches.append((int(row), int(col)))
                unmatched_rows.discard(int(row))
                unmatched_cols.discard(int(col))
    else:
        candidates = sorted(
            (float(cost[row, col]), row, col)
            for row in range(rows) for col in range(cols)
            if float(cost[row, col]) < invalid_cost
        )
        for _, row, col in candidates:
            if row in unmatched_rows and col in unmatched_cols:
                matches.append((row, col))
                unmatched_rows.remove(row)
                unmatched_cols.remove(col)
    return matches, sorted(unmatched_rows), sorted(unmatched_cols)


class BoTSortReIdTracker(BaseTracker):
    backend = BACKEND_BOTSORT_REID

    def __init__(
        self,
        max_misses: int = 30,
        min_hits: int = 1,
        max_tracks: int = 256,
        history_size: int = 32,
        label_filter: Optional[Sequence[Any]] = None,
        track_high_thresh: float = 0.5,
        track_low_thresh: float = 0.1,
        new_track_thresh: float = 0.6,
        match_thresh: float = 0.8,
        appearance_threshold: float = 0.75,
        proximity_threshold: float = 0.2,
        reid_memory_seconds: float = 300.0,
        feature_ema_alpha: float = 0.9,
    ) -> None:
        self.max_misses = max(1, int(max_misses))
        self.min_hits = max(1, int(min_hits))
        self.max_tracks = max(1, int(max_tracks))
        self.history_size = max(1, int(history_size))
        self.label_filter = normalize_label_filter(label_filter or ['person'])
        self.track_high_thresh = float(track_high_thresh)
        self.track_low_thresh = float(track_low_thresh)
        self.new_track_thresh = float(new_track_thresh)
        self.match_thresh = float(match_thresh)
        self.proximity_threshold = float(proximity_threshold)
        self.appearance_threshold = float(appearance_threshold)
        self.reid_memory_seconds = max(0.0, float(reid_memory_seconds))
        self.feature_ema_alpha = min(0.999, max(0.0, float(feature_ema_alpha)))
        self.tracks: List[Track] = []
        self.lost: Dict[int, LostTrack] = {}
        self.features: Dict[int, np.ndarray] = {}
        self._kf = KalmanFilterXYAH()
        self._kf_state: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
        self._ids = IdAllocator()
        self._frame_index = 0
        self._camera_shift = (0.0, 0.0)

    def reset(self) -> None:
        self.tracks = []
        self.lost = {}
        self.features = {}
        self._kf_state = {}
        self._ids.reset()
        self._frame_index = 0
        self._camera_shift = (0.0, 0.0)

    def _predict(self) -> None:
        for track in self.tracks:
            state = self._kf_state.get(track.track_id)
            if state is None:
                state = self._kf.initiate(xyxy_to_xyah(track.box))
            self._kf_state[track.track_id] = self._kf.predict(state[0], state[1], 1.0)

    def _predicted_box(self, track: Track) -> List[float]:
        state = self._kf_state.get(track.track_id)
        box = xyah_to_xyxy(state[0]) if state is not None else list(track.box)
        dx, dy = self._camera_shift
        return [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]

    def _update_feature(self, track_id: int, value: Optional[np.ndarray]) -> None:
        if value is None:
            return
        current = self.features.get(track_id)
        if current is None or current.shape != value.shape:
            updated = value
        else:
            updated = self.feature_ema_alpha * current + (1.0 - self.feature_ema_alpha) * value
            norm = float(np.linalg.norm(updated))
            if norm > 1e-12:
                updated = updated / norm
        self.features[track_id] = np.asarray(updated, dtype=np.float32)

    @staticmethod
    def _annotate(track: Track, status: str, method: str, score: Optional[float] = None, **extra):
        attributes = dict(track.payload.get('attributes') or {})
        attributes.update({
            'reid_status': status,
            'association_method': method,
            'reidentified': status == 'reactivated',
        })
        if score is not None:
            attributes['appearance_score'] = float(score)
        attributes.update(extra)
        track.payload['attributes'] = attributes

    def _commit(
        self,
        track: Track,
        det: ParsedDetection,
        timestamp: float,
        feature: Optional[np.ndarray],
        method: str,
        score: Optional[float] = None,
        status: str = 'matched',
    ) -> None:
        ineligible_reason = det.payload.pop('_reid_rejected_reason', None)
        track.box = list(det.box)
        track.label = det.label
        track.confidence = det.score
        track.payload = dict(det.payload)
        track.hits += 1
        track.misses = 0
        track.age += 1
        track.last_seen_ts = float(timestamp)
        append_history(track, timestamp, self.history_size)
        state = self._kf_state.get(track.track_id)
        measurement = xyxy_to_xyah(det.box)
        self._kf_state[track.track_id] = (
            self._kf.update(state[0], state[1], measurement)
            if state is not None else self._kf.initiate(measurement)
        )
        self._update_feature(track.track_id, feature)
        if ineligible_reason and status != 'reactivated':
            self._annotate(
                track, 'not_eligible', method, score,
                reid_ineligible_reason=str(ineligible_reason),
            )
        else:
            self._annotate(track, status, method, score)

    def _active_cost(
        self,
        tracks: Sequence[Track],
        detections: Sequence[ParsedDetection],
        det_features: Sequence[Optional[np.ndarray]],
    ) -> np.ndarray:
        invalid = 1e5
        cost = np.full((len(tracks), len(detections)), invalid, dtype=np.float64)
        min_iou = max(self.proximity_threshold, 1.0 - self.match_thresh)
        for row, track in enumerate(tracks):
            track_feature = self.features.get(track.track_id)
            for col, det in enumerate(detections):
                if track.label != det.label:
                    continue
                iou = box_iou(self._predicted_box(track), det.box)
                similarity = None
                if track_feature is not None and det_features[col] is not None:
                    similarity = float(np.dot(track_feature, det_features[col]))
                # Appearance refines a spatially plausible active-track match;
                # it must never teleport an active identity across the frame.
                # Long-distance matches are handled only by the lost gallery.
                if iou < min_iou:
                    continue
                if similarity is None:
                    cost[row, col] = 1.0 - iou
                else:
                    cost[row, col] = 0.55 * (1.0 - iou) + 0.45 * (1.0 - similarity)
        return cost

    def _match_active(
        self,
        tracks: Sequence[Track],
        detections: Sequence[ParsedDetection],
        det_features: Sequence[Optional[np.ndarray]],
        timestamp: float,
    ):
        matches, unmatched_tracks, unmatched_dets = _assign_cost(
            self._active_cost(tracks, detections, det_features)
        )
        for track_idx, det_idx in matches:
            track_feature = self.features.get(tracks[track_idx].track_id)
            feature = det_features[det_idx]
            similarity = (
                float(np.dot(track_feature, feature))
                if track_feature is not None and feature is not None else None
            )
            method = 'appearance_iou' if similarity is not None else 'motion_iou'
            self._commit(
                tracks[track_idx], detections[det_idx], timestamp,
                feature, method, similarity,
            )
        return unmatched_tracks, unmatched_dets

    def _match_low(self, tracks, detections, timestamp):
        invalid = 1e5
        cost = np.full((len(tracks), len(detections)), invalid, dtype=np.float64)
        for row, track in enumerate(tracks):
            for col, det in enumerate(detections):
                if track.label != det.label:
                    continue
                iou = box_iou(self._predicted_box(track), det.box)
                if iou >= 0.5:
                    cost[row, col] = 1.0 - iou
        matches, unmatched_tracks, unmatched_dets = _assign_cost(cost)
        for track_idx, det_idx in matches:
            self._commit(
                tracks[track_idx], detections[det_idx], timestamp,
                None, 'low_score_iou', None,
            )
        return unmatched_tracks, unmatched_dets

    def _expire_lost(self, timestamp: float) -> None:
        expired = [
            track_id for track_id, item in self.lost.items()
            if timestamp - item.lost_at > self.reid_memory_seconds
        ]
        for track_id in expired:
            self.lost.pop(track_id, None)
            self.features.pop(track_id, None)
        if len(self.lost) > self.max_tracks:
            oldest = sorted(self.lost.values(), key=lambda item: item.lost_at)
            for item in oldest[:len(self.lost) - self.max_tracks]:
                self.lost.pop(item.track.track_id, None)
                self.features.pop(item.track.track_id, None)

    def _reactivate(
        self,
        detections: Sequence[ParsedDetection],
        det_features: Sequence[Optional[np.ndarray]],
        timestamp: float,
    ):
        candidates = list(self.lost.values())
        invalid = 1e5
        cost = np.full((len(candidates), len(detections)), invalid, dtype=np.float64)
        for row, lost in enumerate(candidates):
            for col, det in enumerate(detections):
                feature = det_features[col]
                if lost.track.label != det.label or feature is None:
                    continue
                similarity = float(np.dot(lost.feature, feature))
                if similarity >= self.appearance_threshold:
                    cost[row, col] = 1.0 - similarity
        matches, _, unmatched_dets = _assign_cost(cost)
        for lost_idx, det_idx in matches:
            item = candidates[lost_idx]
            track = item.track
            self.lost.pop(track.track_id, None)
            self.tracks.append(track)
            self._kf_state[track.track_id] = self._kf.initiate(xyxy_to_xyah(detections[det_idx].box))
            score = float(np.dot(item.feature, det_features[det_idx]))
            self._commit(
                track, detections[det_idx], timestamp, det_features[det_idx],
                'appearance_reactivation', score, status='reactivated',
            )
            self._annotate(
                track, 'reactivated', 'appearance_reactivation', score,
                lost_seconds=max(0.0, timestamp - item.lost_at),
            )
        return unmatched_dets

    def _start(self, det, timestamp, feature, degraded_reason=None):
        ineligible_reason = det.payload.pop('_reid_rejected_reason', None)
        live_ids = {item.track_id for item in self.tracks} | set(self.lost)
        track = new_track(
            self._ids.allocate(live_ids=live_ids), det, timestamp, self.history_size
        )
        self.tracks.append(track)
        self._kf_state[track.track_id] = self._kf.initiate(xyxy_to_xyah(det.box))
        self._update_feature(track.track_id, feature)
        if degraded_reason:
            self._annotate(track, 'degraded', 'new', degradation_reason=degraded_reason)
        elif ineligible_reason:
            self._annotate(
                track, 'not_eligible', 'new',
                reid_ineligible_reason=str(ineligible_reason),
            )
        else:
            self._annotate(track, 'new', 'new')

    def update(
        self,
        detections: Optional[Iterable[Dict[str, Any]]] = None,
        timestamp: Optional[float] = None,
        degraded_reason: Optional[str] = None,
        camera_motion: Optional[Sequence[float]] = None,
    ) -> List[Track]:
        self._frame_index += 1
        ts = resolve_timestamp(timestamp, self._frame_index)
        if camera_motion is not None and len(camera_motion) >= 2:
            self._camera_shift = (float(camera_motion[0]), float(camera_motion[1]))
        else:
            self._camera_shift = (0.0, 0.0)
        self._expire_lost(ts)
        self._predict()
        raw = []
        for detection in detections or []:
            item = dict(detection)
            item.pop('track_id', None)  # this backend owns the identity domain
            raw.append(item)
        parsed = parse_detections(raw, self.label_filter)
        parsed_features = [_feature(det) for det in parsed]
        high_indexes = [idx for idx, det in enumerate(parsed) if det.score >= self.track_high_thresh]
        low_indexes = [
            idx for idx, det in enumerate(parsed)
            if self.track_low_thresh <= det.score < self.track_high_thresh
        ]
        high = [parsed[idx] for idx in high_indexes]
        high_features = [parsed_features[idx] for idx in high_indexes]
        low = [parsed[idx] for idx in low_indexes]

        active_before = list(self.tracks)
        unmatched_track_idx, unmatched_high_idx = self._match_active(
            active_before, high, high_features, ts
        )
        remaining_tracks = [active_before[idx] for idx in unmatched_track_idx]
        unmatched_low_track_idx, _ = self._match_low(remaining_tracks, low, ts)
        still_unmatched = [remaining_tracks[idx] for idx in unmatched_low_track_idx]
        for track in still_unmatched:
            mark_missed(track)
            if track.misses >= self.max_misses:
                self.tracks.remove(track)
                self._kf_state.pop(track.track_id, None)
                feature = self.features.get(track.track_id)
                if feature is not None and self.reid_memory_seconds > 0:
                    self.lost[track.track_id] = LostTrack(track, feature, ts)
                else:
                    self.features.pop(track.track_id, None)

        unmatched_high = [high[idx] for idx in unmatched_high_idx]
        unmatched_features = [high_features[idx] for idx in unmatched_high_idx]
        remaining_det_idx = self._reactivate(unmatched_high, unmatched_features, ts)
        for index in remaining_det_idx:
            if unmatched_high[index].score >= self.new_track_thresh:
                self._start(
                    unmatched_high[index], ts, unmatched_features[index], degraded_reason
                )

        if degraded_reason:
            for track in self.tracks:
                if track.misses == 0:
                    self._annotate(
                        track, 'degraded',
                        (track.payload.get('attributes') or {}).get('association_method', 'motion_iou'),
                        degradation_reason=degraded_reason,
                    )
        if len(self.tracks) > self.max_tracks:
            keep = sorted(self.tracks, key=lambda item: (item.misses, -item.last_seen_ts))[:self.max_tracks]
            keep_ids = {item.track_id for item in keep}
            for track in self.tracks:
                if track.track_id not in keep_ids:
                    self._kf_state.pop(track.track_id, None)
                    self.features.pop(track.track_id, None)
            self.tracks = keep
        return confirmed_tracks(self.tracks, self.min_hits)

    def handle_scene_cut(self, timestamp: float) -> None:
        """Discard motion state while preserving appearance identities."""
        ts = float(timestamp)
        for track in list(self.tracks):
            feature = self.features.get(track.track_id)
            if feature is not None and self.reid_memory_seconds > 0:
                self.lost[track.track_id] = LostTrack(track, feature, ts)
        self.tracks = []
        self._kf_state = {}
        self._camera_shift = (0.0, 0.0)
