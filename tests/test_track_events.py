from app.user_scripts.common.track_events import (
    EVENT_LOITER,
    EVENT_REGION_CROSS,
    EVENT_STAY,
    apply_event,
    init_event_state,
    point_in_polygon,
)


def _det(box, track_id=1, label="person"):
    return {"box": box, "confidence": 0.9, "label": label, "track_id": track_id}


def test_point_in_polygon_even_odd():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert point_in_polygon(5, 5, square)
    assert not point_in_polygon(15, 5, square)


def test_loiter_waits_for_dwell():
    config = {"event": EVENT_LOITER, "min_dwell_seconds": 8}
    state = init_event_state(config)
    early, _ = apply_event([_det([0, 0, 10, 10])], config=config, state=state, timestamp=10)
    ready, meta = apply_event([_det([0, 0, 10, 10])], config=config, state=state, timestamp=18)
    assert early == []
    assert len(ready) == 1
    assert ready[0]["attributes"]["dwell_seconds"] == 8
    assert ready[0]["attributes"]["event"] == EVENT_LOITER
    assert meta["event_count"] == 1


def test_stay_rejects_large_displacement():
    config = {"event": EVENT_STAY, "min_dwell_seconds": 2, "max_displace_px": 20}
    state = init_event_state(config)
    apply_event([_det([0, 0, 10, 10])], config=config, state=state, timestamp=1)
    moving, _ = apply_event([_det([80, 0, 90, 10])], config=config, state=state, timestamp=4)
    assert moving == []


def test_region_cross_enter_left_to_right():
    config = {
        "event": EVENT_REGION_CROSS,
        "cross_mode": "enter",
        "cross_direction": "left_to_right",
    }
    state = init_event_state(config)
    roi = [{"polygon": [[10, 0], [20, 0], [20, 16], [10, 16]], "name": "lane"}]
    kwargs = {"config": config, "state": state, "roi_regions": roi, "frame_width": 20, "frame_height": 16}
    first, _ = apply_event([_det([0, 0, 8, 8])], timestamp=1, **kwargs)
    crossed, _ = apply_event([_det([12, 0, 20, 8])], timestamp=2, **kwargs)
    assert first == []
    assert len(crossed) == 1
    assert crossed[0]["attributes"]["cross_mode"] == "enter"
    assert crossed[0]["attributes"]["roi_name"] == "lane"


def test_region_cross_ignores_opposite_direction():
    config = {
        "event": EVENT_REGION_CROSS,
        "cross_mode": "enter",
        "cross_direction": "right_to_left",
    }
    state = init_event_state(config)
    roi = [{"polygon": [[10, 0], [20, 0], [20, 16], [10, 16]]}]
    kwargs = {"config": config, "state": state, "roi_regions": roi, "frame_width": 20, "frame_height": 16}
    apply_event([_det([0, 0, 8, 8])], timestamp=1, **kwargs)
    crossed, _ = apply_event([_det([12, 0, 20, 8])], timestamp=2, **kwargs)
    assert crossed == []
