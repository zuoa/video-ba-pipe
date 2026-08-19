import app.user_scripts.common.tracker as tracker_mod
from app.user_scripts.common.byte_tracker import ByteTracker, KalmanFilterXYAH, xyxy_to_xyah, xyah_to_xyxy
from app.user_scripts.common.tracker import create_tracker


def _det(box, score=0.9, label="person", track_id=None):
    item = {"box": box, "confidence": score, "label": label}
    if track_id is not None:
        item["track_id"] = track_id
    return item


def test_create_tracker_bytetrack_alias():
    tracker = create_tracker("byte_track")
    assert tracker.backend == "bytetrack"
    assert tracker.max_misses == 30


def test_same_target_keeps_id():
    tracker = ByteTracker(min_hits=1, max_misses=5)
    first = tracker.update([_det([10, 10, 50, 80], 0.9)], timestamp=1)
    second = tracker.update([_det([12, 12, 52, 82], 0.88)], timestamp=2)
    assert first[0].track_id == second[0].track_id


def test_class_isolation():
    tracker = ByteTracker(min_hits=1)
    result = tracker.update([
        _det([0, 0, 20, 40], 0.9, "person"),
        _det([0, 0, 20, 40], 0.9, "car"),
    ], timestamp=1)
    assert len(result) == 2
    assert result[0].track_id != result[1].track_id


def test_low_score_rescues_track():
    tracker = ByteTracker(
        track_high_thresh=0.5,
        track_low_thresh=0.1,
        new_track_thresh=0.6,
        match_thresh=0.8,
        min_hits=1,
        max_misses=5,
    )
    first = tracker.update([_det([10, 10, 50, 80], 0.9, "person")], timestamp=1)
    track_id = first[0].track_id
    rescued = tracker.update([_det([12, 10, 52, 80], 0.2, "person")], timestamp=2)
    assert len(rescued) == 1
    assert rescued[0].track_id == track_id


def test_low_score_does_not_start_new_track():
    tracker = ByteTracker(
        track_high_thresh=0.5,
        new_track_thresh=0.6,
        min_hits=1,
    )
    result = tracker.update([_det([10, 10, 50, 80], 0.2, "person")], timestamp=1)
    assert result == []
    assert tracker.tracks == []


def test_kalman_keeps_id_on_moving_target():
    tracker = ByteTracker(match_thresh=0.8, min_hits=1, max_misses=5)
    boxes = [
        [0, 0, 40, 80],
        [12, 0, 52, 80],
        [24, 0, 64, 80],
        [36, 0, 76, 80],
        [48, 0, 88, 80],
    ]
    track_id = None
    for idx, box in enumerate(boxes, start=1):
        result = tracker.update([_det(box, 0.9, "person")], timestamp=idx)
        assert len(result) == 1
        if track_id is None:
            track_id = result[0].track_id
        else:
            assert result[0].track_id == track_id


def test_empty_detections_advance_misses():
    tracker = ByteTracker(max_misses=2, min_hits=1)
    tracker.update([_det([0, 0, 20, 40])], timestamp=1)
    tracker.update([], timestamp=2)
    assert tracker.tracks[0].misses == 1


def test_passthrough_track_id():
    tracker = ByteTracker(min_hits=1)
    first = tracker.update([_det([0, 0, 20, 40], track_id=9)], timestamp=1)
    second = tracker.update([_det([1, 0, 21, 40], track_id=9)], timestamp=2)
    assert first[0].track_id == 9
    assert second[0].track_id == 9


def test_duplicate_passthrough_ids_are_remapped():
    tracker = ByteTracker(min_hits=1)
    result = tracker.update([
        _det([0, 0, 20, 40], 0.9, "person", track_id=1),
        _det([80, 0, 100, 40], 0.9, "person", track_id=1),
    ], timestamp=1)
    assert len(result) == 2
    ids = {item.track_id for item in result}
    assert len(ids) == 2
    assert 1 in ids
    assert len(tracker._kf_state) == 2


def test_unix_timestamps_keep_id():
    tracker = ByteTracker(min_hits=1, max_misses=5)
    start = 1_700_000_000.0
    first = tracker.update([_det([10, 10, 50, 80], 0.9)], timestamp=start)
    second = tracker.update([_det([12, 12, 52, 82], 0.88)], timestamp=start + 0.1)
    assert first[0].track_id == second[0].track_id


def test_reset_clears_kalman_state():
    tracker = ByteTracker(min_hits=1)
    tracker.update([_det([0, 0, 20, 40])], timestamp=1)
    tracker.reset()
    assert tracker.tracks == []
    assert tracker._kf_state == {}
    result = tracker.update([_det([80, 0, 100, 40])], timestamp=2)
    assert result[0].track_id == 1


def test_assignment_falls_back_without_scipy(monkeypatch):
    monkeypatch.setattr(tracker_mod, "_linear_sum_assignment", None)
    tracker = ByteTracker(min_hits=1, max_misses=5)
    first = tracker.update([_det([10, 10, 50, 80], 0.9)], timestamp=1)
    second = tracker.update([_det([12, 12, 52, 82], 0.2)], timestamp=2)
    assert first[0].track_id == second[0].track_id


def test_xyah_roundtrip():
    box = [10.0, 20.0, 50.0, 100.0]
    restored = xyah_to_xyxy(xyxy_to_xyah(box))
    assert restored == box


def test_kalman_predict_moves_with_velocity():
    kf = KalmanFilterXYAH()
    mean, cov = kf.initiate(xyxy_to_xyah([0, 0, 20, 40]))
    updated, cov = kf.update(mean, cov, xyxy_to_xyah([10, 0, 30, 40]))
    predicted, _ = kf.predict(updated, cov, dt=1.0)
    predicted_box = xyah_to_xyxy(predicted)
    assert predicted.shape == (8,)
    assert predicted_box[2] > predicted_box[0]
    assert predicted[0] >= updated[0]
