import numpy as np

from app.user_scripts.common.bot_sort_reid_tracker import BoTSortReIdTracker
from app.user_scripts.common.tracker import create_tracker


def _det(box, feature=None, score=0.9, label='person', track_id=None):
    value = {'box': box, 'confidence': score, 'label': label}
    if feature is not None:
        value['_reid_embedding'] = np.asarray(feature, dtype=np.float32).tolist()
    if track_id is not None:
        value['track_id'] = track_id
    return value


def test_factory_creates_explicit_reid_backend():
    tracker = create_tracker('botsort_reid', min_hits=1)
    assert tracker.backend == 'botsort_reid'
    assert tracker.label_filter == ['person']


def test_reactivates_same_identity_after_track_becomes_lost():
    tracker = BoTSortReIdTracker(
        min_hits=1, max_misses=1, reid_memory_seconds=30,
        appearance_threshold=0.8,
    )
    first = tracker.update([_det([0, 0, 20, 80], [1, 0, 0])], timestamp=1)[0]
    tracker.update([], timestamp=2)
    assert first.track_id in tracker.lost

    result = tracker.update(
        [_det([100, 0, 120, 80], [0.99, 0.01, 0])], timestamp=10
    )
    assert result[0].track_id == first.track_id
    attributes = result[0].to_detection(tracker.backend)['attributes']
    assert attributes['reid_status'] == 'reactivated'
    assert attributes['association_method'] == 'appearance_reactivation'
    assert attributes['lost_seconds'] == 8


def test_different_appearance_gets_new_id():
    tracker = BoTSortReIdTracker(
        min_hits=1, max_misses=1, reid_memory_seconds=30,
        appearance_threshold=0.8,
    )
    first_id = tracker.update([_det([0, 0, 20, 80], [1, 0])], timestamp=1)[0].track_id
    tracker.update([], timestamp=2)
    second = tracker.update([_det([100, 0, 120, 80], [0, 1])], timestamp=3)[0]
    assert second.track_id != first_id


def test_expired_identity_is_not_reactivated():
    tracker = BoTSortReIdTracker(
        min_hits=1, max_misses=1, reid_memory_seconds=5,
        appearance_threshold=0.8,
    )
    first_id = tracker.update([_det([0, 0, 20, 80], [1, 0])], timestamp=1)[0].track_id
    tracker.update([], timestamp=2)
    second = tracker.update([_det([100, 0, 120, 80], [1, 0])], timestamp=8)[0]
    assert second.track_id != first_id


def test_upstream_track_id_does_not_own_reid_identity_domain():
    tracker = BoTSortReIdTracker(min_hits=1)
    result = tracker.update([
        _det([0, 0, 20, 80], [1, 0], track_id=99),
        _det([100, 0, 120, 80], [0, 1], track_id=99),
    ], timestamp=1)
    assert {item.track_id for item in result} == {1, 2}


def test_degraded_mode_keeps_motion_tracking_and_marks_output():
    tracker = BoTSortReIdTracker(min_hits=1, max_misses=3)
    first = tracker.update(
        [_det([0, 0, 20, 80])], timestamp=1,
        degraded_reason='queue_overloaded',
    )[0]
    second = tracker.update(
        [_det([1, 0, 21, 80])], timestamp=2,
        degraded_reason='queue_overloaded',
    )[0]
    assert second.track_id == first.track_id
    attributes = second.to_detection(tracker.backend)['attributes']
    assert attributes['reid_status'] == 'degraded'
    assert attributes['degradation_reason'] == 'queue_overloaded'


def test_non_person_detections_are_excluded_by_default():
    tracker = BoTSortReIdTracker(min_hits=1)
    assert tracker.update([_det([0, 0, 20, 80], [1, 0], label='car')], timestamp=1) == []


def test_camera_translation_compensates_active_motion_gate():
    tracker = BoTSortReIdTracker(min_hits=1, proximity_threshold=0.2)
    first = tracker.update([_det([0, 0, 20, 80], [1, 0])], timestamp=1)[0]
    second = tracker.update(
        [_det([50, 0, 70, 80], [1, 0])], timestamp=2,
        camera_motion=(50, 0),
    )[0]
    assert second.track_id == first.track_id


def test_scene_cut_preserves_identity_in_lost_gallery():
    tracker = BoTSortReIdTracker(min_hits=1, reid_memory_seconds=30)
    first = tracker.update([_det([0, 0, 20, 80], [1, 0])], timestamp=1)[0]
    tracker.handle_scene_cut(2)
    assert tracker.tracks == []
    assert first.track_id in tracker.lost


def test_rejected_crop_is_marked_without_leaking_internal_field():
    tracker = BoTSortReIdTracker(min_hits=1)
    detection = _det([0, 0, 20, 40])
    detection['_reid_rejected_reason'] = 'person_too_small'
    track = tracker.update([detection], timestamp=1)[0]
    output = track.to_detection(tracker.backend)
    assert output['attributes']['reid_status'] == 'not_eligible'
    assert output['attributes']['reid_ineligible_reason'] == 'person_too_small'
    assert '_reid_rejected_reason' not in output
