"""ByteTrack: Kalman prediction plus two-stage high/low-score association."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from app.user_scripts.common.tracker import (
    BACKEND_BYTETRACK,
    BaseTracker,
    IdAllocator,
    ParsedDetection,
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


class KalmanFilterXYAH:
    """Constant-velocity Kalman filter on (center x, center y, aspect, height)."""

    def __init__(self) -> None:
        self._std_weight_position = 1.0 / 20.0
        self._std_weight_velocity = 1.0 / 160.0

    @staticmethod
    def _motion_mat(dt: float) -> np.ndarray:
        mat = np.eye(8, dtype=np.float64)
        for idx in range(4):
            mat[idx, idx + 4] = dt
        return mat

    def initiate(self, measurement: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        mean = np.r_[measurement, np.zeros_like(measurement)]
        std = [
            2 * self._std_weight_position * measurement[3],
            2 * self._std_weight_position * measurement[3],
            1e-2,
            2 * self._std_weight_position * measurement[3],
            10 * self._std_weight_velocity * measurement[3],
            10 * self._std_weight_velocity * measurement[3],
            1e-5,
            10 * self._std_weight_velocity * measurement[3],
        ]
        covariance = np.diag(np.square(std))
        return mean, covariance

    def predict(
        self,
        mean: np.ndarray,
        covariance: np.ndarray,
        dt: float = 1.0,
    ) -> Tuple[np.ndarray, np.ndarray]:
        _ = dt
        motion_mat = self._motion_mat(1.0)
        std_pos = [
            self._std_weight_position * mean[3],
            self._std_weight_position * mean[3],
            1e-2,
            self._std_weight_position * mean[3],
        ]
        std_vel = [
            self._std_weight_velocity * mean[3],
            self._std_weight_velocity * mean[3],
            1e-5,
            self._std_weight_velocity * mean[3],
        ]
        motion_cov = np.diag(np.square(np.r_[std_pos, std_vel]))
        mean = motion_mat @ mean
        covariance = motion_mat @ covariance @ motion_mat.T + motion_cov
        return mean, covariance

    def update(
        self,
        mean: np.ndarray,
        covariance: np.ndarray,
        measurement: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        projected_mean, projected_cov = self.project(mean, covariance)
        kalman_gain = np.linalg.solve(projected_cov.T, (covariance @ self._update_mat.T).T).T
        innovation = measurement - projected_mean
        mean = mean + kalman_gain @ innovation
        covariance = covariance - kalman_gain @ projected_cov @ kalman_gain.T
        return mean, covariance

    @property
    def _update_mat(self) -> np.ndarray:
        return np.eye(4, 8, dtype=np.float64)

    def project(self, mean: np.ndarray, covariance: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        std = [
            self._std_weight_position * mean[3],
            self._std_weight_position * mean[3],
            1e-1,
            self._std_weight_position * mean[3],
        ]
        innovation_cov = np.diag(np.square(std))
        projected_mean = self._update_mat @ mean
        projected_cov = self._update_mat @ covariance @ self._update_mat.T + innovation_cov
        return projected_mean, projected_cov


def xyxy_to_xyah(box: Sequence[float]) -> np.ndarray:
    x1, y1, x2, y2 = [float(v) for v in box[:4]]
    width = max(1e-6, x2 - x1)
    height = max(1e-6, y2 - y1)
    return np.array([x1 + width / 2.0, y1 + height / 2.0, width / height, height], dtype=np.float64)


def xyah_to_xyxy(xyah: Sequence[float]) -> List[float]:
    cx, cy, aspect, height = [float(v) for v in xyah[:4]]
    height = max(1e-6, height)
    width = max(1e-6, aspect * height)
    return [cx - width / 2.0, cy - height / 2.0, cx + width / 2.0, cy + height / 2.0]


class ByteTracker(BaseTracker):
    backend = BACKEND_BYTETRACK

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
    ) -> None:
        self.max_misses = int(max_misses)
        self.min_hits = int(min_hits)
        self.max_tracks = int(max_tracks)
        self.history_size = int(history_size)
        self.label_filter = normalize_label_filter(label_filter)
        self.track_high_thresh = float(track_high_thresh)
        self.track_low_thresh = float(track_low_thresh)
        self.new_track_thresh = float(new_track_thresh)
        # ByteTrack original thresholds are costs on (1 - IoU).
        self.match_thresh = float(match_thresh)
        self.high_iou_thresh = max(0.0, 1.0 - self.match_thresh)
        self.low_iou_thresh = 0.5
        self.unconfirmed_iou_thresh = 0.3
        self.tracks: List[Track] = []
        self._ids = IdAllocator()
        self._kf = KalmanFilterXYAH()
        self._kf_state: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
        self._frame_index = 0

    def reset(self) -> None:
        self.tracks = []
        self._ids.reset()
        self._kf_state = {}
        self._frame_index = 0

    def update(
        self,
        detections: Optional[Iterable[Dict[str, Any]]] = None,
        timestamp: Optional[float] = None,
    ) -> List[Track]:
        self._frame_index += 1
        ts = resolve_timestamp(timestamp, self._frame_index)
        # BYTE Kalman Q/R is calibrated per frame, not per wall-clock second.
        self._predict_all(1.0)
        parsed = parse_detections(detections, self.label_filter)
        owned, fresh = split_passthrough(parsed)
        matched_ids = set()

        for det in owned:
            existing = find_track(self.tracks, det.track_id)
            if existing is not None and id(existing) not in matched_ids:
                self._commit_match(existing, det, ts)
                matched_ids.add(id(existing))
                continue
            started = self._start_track(det, ts, preferred_id=det.track_id)
            matched_ids.add(id(started))

        remaining_tracks = [track for track in self.tracks if id(track) not in matched_ids]
        high_dets = [det for det in fresh if det.score >= self.track_high_thresh]
        low_dets = [
            det for det in fresh
            if self.track_low_thresh <= det.score < self.track_high_thresh
        ]

        confirmed = [track for track in remaining_tracks if track.hits >= self.min_hits]
        unconfirmed = [track for track in remaining_tracks if track.hits < self.min_hits]

        leftover_high, leftover_confirmed = self._associate(
            confirmed, high_dets, self.high_iou_thresh, ts, matched_ids
        )
        _leftover_low, leftover_confirmed = self._associate(
            leftover_confirmed, low_dets, self.low_iou_thresh, ts, matched_ids
        )
        leftover_high, leftover_unconfirmed = self._associate(
            unconfirmed, leftover_high, self.unconfirmed_iou_thresh, ts, matched_ids
        )

        for track in leftover_confirmed + leftover_unconfirmed:
            if id(track) not in matched_ids:
                mark_missed(track)

        for det in leftover_high:
            if det.score >= self.new_track_thresh:
                self._start_track(det, ts)

        self.tracks = prune_tracks(self.tracks, self.max_misses, self.max_tracks)
        alive_ids = {track.track_id for track in self.tracks}
        self._kf_state = {
            track_id: state for track_id, state in self._kf_state.items() if track_id in alive_ids
        }
        return confirmed_tracks(self.tracks, self.min_hits)

    def _predict_all(self, dt: float) -> None:
        for track in self.tracks:
            state = self._kf_state.get(track.track_id)
            if state is None:
                mean, cov = self._kf.initiate(xyxy_to_xyah(track.box))
            else:
                mean, cov = state
            mean, cov = self._kf.predict(mean, cov, dt)
            self._kf_state[track.track_id] = (mean, cov)

    def _predicted_box(self, track: Track) -> List[float]:
        state = self._kf_state.get(track.track_id)
        if state is None:
            return list(track.box)
        return xyah_to_xyxy(state[0])

    def _associate(
        self,
        tracks: List[Track],
        detections: List[ParsedDetection],
        iou_threshold: float,
        timestamp: float,
        matched_ids: set,
    ) -> Tuple[List[ParsedDetection], List[Track]]:
        if not tracks or not detections:
            return detections, tracks
        matches, unmatched_track_idx, unmatched_det_idx = associate_class_aware(
            tracks,
            detections,
            iou_threshold,
            box_fn=self._predicted_box,
        )
        for track_idx, det_idx in matches:
            track = tracks[track_idx]
            self._commit_match(track, detections[det_idx], timestamp)
            matched_ids.add(id(track))
        leftover_dets = [detections[idx] for idx in unmatched_det_idx]
        leftover_tracks = [tracks[idx] for idx in unmatched_track_idx]
        return leftover_dets, leftover_tracks

    def _commit_match(self, track: Track, det: ParsedDetection, timestamp: float) -> None:
        mark_matched(track, det, timestamp, self.history_size)
        measurement = xyxy_to_xyah(det.box)
        state = self._kf_state.get(track.track_id)
        if state is None:
            mean, cov = self._kf.initiate(measurement)
        else:
            mean, cov = self._kf.update(state[0], state[1], measurement)
        self._kf_state[track.track_id] = (mean, cov)

    def _start_track(
        self,
        det: ParsedDetection,
        timestamp: float,
        preferred_id: Optional[int] = None,
    ) -> Track:
        live_ids = {item.track_id for item in self.tracks}
        track = new_track(self._ids.allocate(preferred_id, live_ids), det, timestamp, self.history_size)
        self.tracks.append(track)
        self._kf_state[track.track_id] = self._kf.initiate(xyxy_to_xyah(det.box))
        return track
