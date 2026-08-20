from app.user_scripts.common.iou_tracker import IoUTracker
from app.user_scripts.common.tracker import create_tracker


def _det(box, score=0.9, label="car", track_id=None):
    item = {"box": box, "confidence": score, "label": label}
    if track_id is not None:
        item["track_id"] = track_id
    return item


def test_create_tracker_iou_backend():
    tracker = create_tracker("iou", match_iou=0.4)
    assert tracker.backend == "iou"
    assert tracker.match_iou == 0.4


def test_create_tracker_rejects_unknown_backend():
    try:
        create_tracker("unknown")
    except ValueError as exc:
        assert "未知跟踪后端" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_same_box_keeps_id():
    tracker = IoUTracker(match_iou=0.3, max_misses=3, min_hits=1)
    first = tracker.update([_det([10, 10, 40, 40])], timestamp=1)
    second = tracker.update([_det([12, 11, 41, 42])], timestamp=2)
    assert len(first) == 1
    assert len(second) == 1
    assert first[0].track_id == second[0].track_id


def test_class_isolation_does_not_share_ids():
    tracker = IoUTracker(match_iou=0.1, min_hits=1)
    result = tracker.update([
        _det([0, 0, 20, 20], label="car"),
        _det([0, 0, 20, 20], label="person"),
    ], timestamp=1)
    assert len(result) == 2
    assert {item.label for item in result} == {"car", "person"}
    assert result[0].track_id != result[1].track_id


def test_new_object_gets_new_id():
    tracker = IoUTracker(match_iou=0.5, min_hits=1)
    first = tracker.update([_det([0, 0, 10, 10])], timestamp=1)
    second = tracker.update([
        _det([0, 0, 10, 10]),
        _det([80, 80, 100, 100]),
    ], timestamp=2)
    assert first[0].track_id in {item.track_id for item in second}
    assert len(second) == 2


def test_misses_delete_track_and_reuse_is_new_id():
    tracker = IoUTracker(match_iou=0.3, max_misses=2, min_hits=1)
    first = tracker.update([_det([0, 0, 10, 10])], timestamp=1)
    assert tracker.update([], timestamp=2) == []
    assert tracker.update([], timestamp=3) == []
    again = tracker.update([_det([0, 0, 10, 10])], timestamp=4)
    assert len(again) == 1
    assert again[0].track_id != first[0].track_id


def test_min_hits_hides_tentative_tracks():
    tracker = IoUTracker(match_iou=0.3, min_hits=2)
    first = tracker.update([_det([0, 0, 20, 20])], timestamp=1)
    second = tracker.update([_det([1, 0, 21, 20])], timestamp=2)
    assert first == []
    assert len(second) == 1
    assert second[0].hits >= 2


def test_passthrough_track_id():
    tracker = IoUTracker(min_hits=1)
    first = tracker.update([_det([0, 0, 10, 10], track_id=7)], timestamp=1)
    second = tracker.update([_det([1, 0, 11, 10], track_id=7)], timestamp=2)
    assert first[0].track_id == 7
    assert second[0].track_id == 7


def test_duplicate_passthrough_ids_are_remapped():
    tracker = IoUTracker(min_hits=1, match_iou=0.9)
    result = tracker.update([
        _det([0, 0, 10, 10], track_id=1),
        _det([80, 80, 90, 90], track_id=1),
    ], timestamp=1)
    assert len(result) == 2
    assert len({item.track_id for item in result}) == 2
    assert 1 in {item.track_id for item in result}


def test_empty_detections_advance_misses():
    tracker = IoUTracker(max_misses=2, min_hits=1)
    tracker.update([_det([0, 0, 10, 10])], timestamp=1)
    tracker.update([], timestamp=2)
    assert len(tracker.tracks) == 1
    assert tracker.tracks[0].misses == 1


def test_capacity_limit():
    tracker = IoUTracker(max_tracks=2, min_hits=1, match_iou=0.9)
    tracker.update([
        _det([0, 0, 10, 10]),
        _det([20, 0, 30, 10]),
        _det([40, 0, 50, 10]),
    ], timestamp=1)
    assert len(tracker.tracks) <= 2


def test_reset_restarts_ids():
    tracker = IoUTracker(min_hits=1)
    tracker.update([_det([0, 0, 10, 10])], timestamp=1)
    tracker.reset()
    result = tracker.update([_det([80, 80, 90, 90])], timestamp=2)
    assert result[0].track_id == 1


def test_history_is_capped():
    tracker = IoUTracker(history_size=3, min_hits=1)
    for idx in range(5):
        tracker.update([_det([idx, 0, idx + 10, 10])], timestamp=idx)
    assert len(tracker.tracks[0].history) == 3


def test_label_filter():
    tracker = IoUTracker(label_filter=["car"], min_hits=1)
    result = tracker.update([
        _det([0, 0, 10, 10], label="car"),
        _det([20, 0, 30, 10], label="person"),
    ], timestamp=1)
    assert len(result) == 1
    assert result[0].label == "car"


def test_string_label_filter_does_not_split_characters():
    tracker = IoUTracker(label_filter="car", min_hits=1)
    result = tracker.update([
        _det([0, 0, 10, 10], label="car"),
        _det([20, 0, 30, 10], label="c"),
    ], timestamp=1)
    assert len(result) == 1
    assert result[0].label == "car"


def test_to_detection_preserves_label_name_and_emits_history():
    tracker = IoUTracker(min_hits=1)
    detection_in = _det([0, 0, 10, 10], label="car")
    detection_in["label_name"] = "人员"
    track = tracker.update([detection_in], timestamp=1)[0]
    detection = track.to_detection("iou")
    assert detection["track_id"] == track.track_id
    assert detection["label_name"] == "人员"
    assert detection["label"] == "car"
    assert detection["attributes"]["backend"] == "iou"
    assert detection["attributes"]["history"]
    assert detection["attributes"]["history"][0]["cx"] == 5.0
    assert detection["attributes"]["first_seen_ts"] == 1
    assert detection["attributes"]["dwell_seconds"] == 0.0


def test_to_detection_emits_dwell_seconds():
    tracker = IoUTracker(min_hits=1)
    first = tracker.update([_det([0, 0, 10, 10], label="car")], timestamp=10)[0]
    second = tracker.update([_det([1, 0, 11, 10], label="car")], timestamp=18)[0]
    assert first.track_id == second.track_id
    detection = second.to_detection("iou")
    assert detection["attributes"]["first_seen_ts"] == 10
    assert detection["attributes"]["last_seen_ts"] == 18
    assert detection["attributes"]["dwell_seconds"] == 8.0
