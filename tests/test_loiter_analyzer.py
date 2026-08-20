import importlib.util
from pathlib import Path

import numpy as np


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "app" / "user_scripts" / "templates" / "loiter_analyzer.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("loiter_analyzer_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _frame():
    return np.zeros((16, 20, 3), dtype=np.uint8)


def _upstream(box, track_id=1, label="person", confidence=0.9):
    return {
        "algo-track": {
            "detections": [{
                "box": box,
                "confidence": confidence,
                "label": label,
                "track_id": track_id,
            }]
        }
    }


def test_loiter_waits_until_min_dwell():
    script = _load_script()
    config = {"min_dwell_seconds": 8, "min_hits": 1}
    state = script.init(config)
    upstream = _upstream([0, 0, 10, 10])

    early = script.process(_frame(), config, state=state, upstream_results=upstream, frame_timestamp=10)
    assert early["detections"] == []

    ready = script.process(_frame(), config, state=state, upstream_results=upstream, frame_timestamp=18)
    assert len(ready["detections"]) == 1
    det = ready["detections"][0]
    assert det["track_id"] == 1
    assert det["attributes"]["dwell_seconds"] == 8
    assert det["attributes"]["history"]


def test_loiter_ignores_detections_without_track_id():
    script = _load_script()
    config = {"min_dwell_seconds": 1}
    state = script.init(config)
    upstream = {"algo-1": {"detections": [{"box": [0, 0, 10, 10], "confidence": 0.9, "label": "person"}]}}
    result = script.process(_frame(), config, state=state, upstream_results=upstream, frame_timestamp=10)
    assert result["detections"] == []
    assert result["metadata"]["skipped_no_track_id"] == 1


def test_loiter_filters_passing_through_when_radius_set():
    script = _load_script()
    config = {"min_dwell_seconds": 2, "max_displace_px": 20}
    state = script.init(config)

    first = script.process(
        _frame(), config, state=state, upstream_results=_upstream([0, 0, 10, 10]), frame_timestamp=1,
    )
    assert first["detections"] == []

    moving = script.process(
        _frame(), config, state=state, upstream_results=_upstream([80, 0, 90, 10]), frame_timestamp=4,
    )
    assert moving["detections"] == []


def test_loiter_resets_after_disappear():
    script = _load_script()
    config = {"min_dwell_seconds": 3, "disappear_seconds": 2}
    state = script.init(config)
    upstream = _upstream([0, 0, 10, 10])

    script.process(_frame(), config, state=state, upstream_results=upstream, frame_timestamp=1)
    script.process(_frame(), config, state=state, upstream_results={"algo-track": {"detections": []}}, frame_timestamp=4)
    later = script.process(_frame(), config, state=state, upstream_results=upstream, frame_timestamp=5)
    assert later["detections"] == []
    assert later["metadata"]["live_tracks"] == 1


def test_loiter_without_state_returns_empty():
    script = _load_script()
    result = script.process(
        _frame(),
        {"min_dwell_seconds": 1},
        state=None,
        upstream_results=_upstream([0, 0, 10, 10]),
        frame_timestamp=1,
    )
    assert result["detections"] == []
    assert result["metadata"]["error"] == "loiter_state_missing"
