import importlib.util
from pathlib import Path

import numpy as np

from app.plugins.script_algorithm import ScriptAlgorithm
from tests.test_pixel_format_runtime_regressions import _FakeExecutor, _FakeHookManager


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "app" / "user_scripts" / "templates" / "object_tracker.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("object_tracker_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _frame():
    return np.zeros((16, 20, 3), dtype=np.uint8)


def test_script_assigns_track_id_from_upstream():
    script = _load_script()
    state = script.init({"backend": "iou", "min_hits": 1})
    upstream = {
        "algo-1": {
            "detections": [{"box": [1, 2, 8, 9], "confidence": 0.9, "label": "car"}]
        }
    }
    first = script.process(_frame(), {"backend": "iou"}, state=state, upstream_results=upstream, frame_timestamp=1)
    second = script.process(_frame(), {"backend": "iou"}, state=state, upstream_results=upstream, frame_timestamp=2)
    assert first["detections"][0]["track_id"] == second["detections"][0]["track_id"]
    assert first["metadata"]["backend"] == "iou"


def test_script_without_state_returns_empty():
    script = _load_script()
    upstream = {"algo-1": {"detections": [{"box": [0, 0, 10, 10], "confidence": 0.9, "label": "car"}]}}
    result = script.process(_frame(), {"backend": "iou"}, state=None, upstream_results=upstream, frame_timestamp=1)
    assert result["detections"] == []
    assert result["metadata"]["error"] == "tracker_state_missing"


def test_script_mutates_caller_state_dict():
    script = _load_script()
    state = {}
    config = {"backend": "iou", "min_hits": 1}
    upstream = {"algo-1": {"detections": [{"box": [0, 0, 10, 10], "confidence": 0.9, "label": "car"}]}}
    first = script.process(_frame(), config, state=state, upstream_results=upstream, frame_timestamp=1)
    second = script.process(_frame(), config, state=state, upstream_results=upstream, frame_timestamp=2)
    assert "tracker" in state
    assert first["detections"][0]["track_id"] == second["detections"][0]["track_id"]


def test_script_without_upstream_returns_empty():
    script = _load_script()
    state = script.init({"backend": "iou"})
    result = script.process(_frame(), {"backend": "iou"}, state=state, upstream_results=None, frame_timestamp=1)
    assert result["detections"] == []


def test_script_without_timestamp():
    script = _load_script()
    state = script.init({"backend": "bytetrack", "min_hits": 1})
    upstream = {"algo-1": {"detections": [{"box": [0, 0, 10, 20], "confidence": 0.9, "label": "person"}]}}
    result = script.process(_frame(), {"backend": "bytetrack"}, state=state, upstream_results=upstream)
    assert result["detections"][0]["track_id"] >= 1
    assert result["metadata"]["backend"] == "bytetrack"


def test_script_label_filter():
    script = _load_script()
    config = {"backend": "iou", "label_filter": "car", "min_hits": 1}
    state = script.init(config)
    upstream = {
        "algo-1": {
            "detections": [
                {"box": [0, 0, 10, 10], "confidence": 0.9, "label": "car"},
                {"box": [20, 0, 30, 10], "confidence": 0.9, "label": "person"},
            ]
        }
    }
    result = script.process(_frame(), config, state=state, upstream_results=upstream, frame_timestamp=1)
    assert len(result["detections"]) == 1
    assert result["detections"][0]["label"] == "car"


def test_script_resets_when_source_changes():
    script = _load_script()
    state = script.init({"backend": "iou", "min_hits": 1, "source_id": 1})
    upstream = {"algo-1": {"detections": [{"box": [0, 0, 10, 10], "confidence": 0.9, "label": "car"}]}}
    first = script.process(
        _frame(),
        {"backend": "iou", "source_id": 1},
        state=state,
        upstream_results=upstream,
        frame_width=20,
        frame_height=16,
        frame_timestamp=1,
    )
    second = script.process(
        _frame(),
        {"backend": "iou", "source_id": 2},
        state=state,
        upstream_results=upstream,
        frame_width=20,
        frame_height=16,
        frame_timestamp=2,
    )
    assert first["detections"][0]["track_id"] == 1
    assert second["detections"][0]["track_id"] == 1


def test_script_algorithm_passes_frame_timestamp():
    captured = {}

    def process_func(frame, frame_timestamp=None, **kwargs):
        captured["frame_timestamp"] = frame_timestamp
        return {"detections": []}

    algo = ScriptAlgorithm.__new__(ScriptAlgorithm)
    algo.config = {"source_id": 0}
    algo.process_func = process_func
    algo.executor = _FakeExecutor()
    algo.hook_manager = _FakeHookManager()
    algo.algorithm_id = None
    algo.script_state = None
    algo.script_path = "inline.py"
    algo._empty_detection_count = 0
    algo._last_empty_detection_log_at = 0.0
    algo.resolved_config = algo.config

    frame = np.zeros((8, 10, 3), dtype=np.uint8)
    algo.process(frame, frame_timestamp=123.5)
    assert captured["frame_timestamp"] == 123.5


def test_old_script_signature_ignores_frame_timestamp():
    captured = {}

    def process_func(frame, pixel_format=None, **kwargs):
        captured["kwargs"] = kwargs
        return {"detections": []}

    algo = ScriptAlgorithm.__new__(ScriptAlgorithm)
    algo.config = {"source_id": 0}
    algo.process_func = process_func
    algo.executor = _FakeExecutor()
    algo.hook_manager = _FakeHookManager()
    algo.algorithm_id = None
    algo.script_state = None
    algo.script_path = "inline.py"
    algo._empty_detection_count = 0
    algo._last_empty_detection_log_at = 0.0
    algo.resolved_config = algo.config

    frame = np.zeros((8, 10, 3), dtype=np.uint8)
    result = algo.process(frame, frame_timestamp=99)
    assert result["detections"] == []
    assert "frame_timestamp" not in captured["kwargs"]
