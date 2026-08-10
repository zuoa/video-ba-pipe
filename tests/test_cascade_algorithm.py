from types import SimpleNamespace

import numpy as np
import pytest

from app.core.cascade_algorithm_config import (
    cascade_model_ids,
    normalize_cascade_algorithm_config,
)
from app.core.orchestrator import Orchestrator
from app.plugins.cascade_algorithm import CascadeAlgorithm


def _raw_config():
    return {
        "version": 1,
        "stages": [
            {
                "id": "person",
                "name": "找到人员",
                "model_id": 1,
                "class_ids": [0],
                "confidence": 0.6,
                "input": {"type": "frame"},
            },
            {
                "id": "smoke",
                "name": "确认烟",
                "model_id": 2,
                "class_ids": [0],
                "confidence": 0.55,
                "input": {
                    "type": "parent_boxes",
                    "parent_stage_id": "person",
                    "expand_ratio": 0.1,
                },
            },
        ],
        "output": {"label": "吸烟", "color": "#FF4D4F"},
    }


@pytest.fixture
def cascade_models(monkeypatch):
    models = {
        1: SimpleNamespace(id=1, name="person", enabled=True, model_type="YOLO"),
        2: SimpleNamespace(id=2, name="smoke", enabled=True, model_type="ONNX"),
    }
    monkeypatch.setattr(
        "app.core.cascade_algorithm_config.MLModel.get_by_id",
        lambda model_id: models[int(model_id)],
    )
    return models


def test_cascade_config_normalizes_linear_stages(cascade_models):
    config = normalize_cascade_algorithm_config(_raw_config())

    assert config["version"] == 1
    assert config["stages"][0]["input"] == {"type": "frame"}
    assert config["stages"][1]["input"]["parent_stage_id"] == "person"
    assert config["stages"][1]["inference"] == {"backend": "auto", "nms_iou": 0.45}
    assert config["output"] == {
        "label": "吸烟",
        "color": "#ff4d4f",
        "box_stage_id": "person",
        "confidence_strategy": "minimum",
    }
    assert cascade_model_ids(config) == (1, 2)


def test_cascade_config_rejects_non_linear_parent(cascade_models):
    config = _raw_config()
    config["stages"][1]["input"]["parent_stage_id"] = "missing"

    with pytest.raises(ValueError, match="必须使用上一阶段"):
        normalize_cascade_algorithm_config(config)


def test_cascade_config_rejects_incompatible_model(monkeypatch):
    model = SimpleNamespace(id=1, name="ocr", enabled=True, model_type="OCR")
    monkeypatch.setattr(
        "app.core.cascade_algorithm_config.MLModel.get_by_id",
        lambda _model_id: model,
    )

    with pytest.raises(ValueError, match="模型类型不受支持"):
        normalize_cascade_algorithm_config(_raw_config())


class _Backend:
    def __init__(self, detections=None, error=None, name="fake"):
        self.detections = detections or []
        self.error = error
        self.name = name
        self.frames = []
        self.cleaned = False

    def infer(self, frame):
        self.frames.append(frame.copy())
        if self.error:
            raise self.error
        return list(self.detections), [], {}

    def cleanup(self):
        self.cleaned = True


class _SequenceBackend(_Backend):
    def __init__(self, responses, name="fake"):
        super().__init__(name=name)
        self.responses = iter(responses)

    def infer(self, frame):
        self.frames.append(frame.copy())
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return list(response), [], {}


def _runtime_algorithm(config, backends):
    algorithm = CascadeAlgorithm.__new__(CascadeAlgorithm)
    algorithm.config = {"cascade_config": config, "pixel_format": "rgb24"}
    algorithm.cascade_config = config
    algorithm.stage_runtimes = [
        {"stage": stage, "backend": backend, "model_info": {}}
        for stage, backend in zip(config["stages"], backends)
    ]
    return algorithm


def test_cascade_process_crops_remaps_and_outputs_root_box(cascade_models):
    config = normalize_cascade_algorithm_config(_raw_config())
    first = _Backend([{
        "box": [20, 10, 60, 70],
        "confidence": 0.9,
        "class": 0,
        "class_name": "person",
        "label": "person",
    }])
    second = _Backend([{
        "box": [8, 5, 18, 15],
        "confidence": 0.7,
        "class": 0,
        "class_name": "smoke",
        "label": "smoke",
    }])
    algorithm = _runtime_algorithm(config, [first, second])

    result = algorithm.process(np.zeros((100, 100, 3), dtype=np.uint8))

    assert len(result["detections"]) == 1
    detection = result["detections"][0]
    assert detection["label"] == "吸烟"
    assert detection["box"] == [20, 10, 60, 70]
    assert detection["confidence"] == pytest.approx(0.7)
    assert detection["stages"][0]["box"] == [20, 10, 60, 70]
    # Expanded person crop is [16, 4, 64, 76], so the local smoke box is remapped.
    assert detection["stages"][1]["box"] == [24.0, 9.0, 34.0, 19.0]
    assert second.frames[0].shape == (72, 48, 3)
    assert result["metadata"]["completed_paths"] == 1


def test_cascade_process_fails_closed_when_required_stage_fails(cascade_models):
    config = normalize_cascade_algorithm_config(_raw_config())
    first = _Backend([{"box": [10, 10, 50, 50], "confidence": 0.9, "label": "person"}])
    second = _Backend(error=RuntimeError("worker unavailable"))
    algorithm = _runtime_algorithm(config, [first, second])

    result = algorithm.process(np.zeros((80, 80, 3), dtype=np.uint8))

    assert result["detections"] == []
    assert result["metadata"]["error_code"] == "cascade_stage_failed"
    assert "worker unavailable" in result["metadata"]["error"]


def test_cascade_stops_without_running_child_when_root_has_no_detection(cascade_models):
    config = normalize_cascade_algorithm_config(_raw_config())
    first = _Backend([])
    second = _Backend([{"box": [1, 1, 2, 2], "confidence": 0.9, "label": "smoke"}])
    algorithm = _runtime_algorithm(config, [first, second])

    result = algorithm.process(np.zeros((80, 80, 3), dtype=np.uint8))

    assert result["detections"] == []
    assert len(result["metadata"]["stage_debug"]) == 1
    assert second.frames == []


def test_child_limit_does_not_skip_parent_candidates(cascade_models):
    raw_config = _raw_config()
    raw_config["stages"][0]["max_candidates"] = 20
    raw_config["stages"][1]["max_candidates"] = 1
    config = normalize_cascade_algorithm_config(raw_config)
    first = _Backend([
        {"box": [5, 5, 25, 45], "confidence": 0.95, "label": "person"},
        {"box": [50, 5, 75, 45], "confidence": 0.8, "label": "person"},
    ])
    second = _SequenceBackend([
        [],
        [{"box": [2, 2, 8, 8], "confidence": 0.7, "label": "smoke"}],
    ])
    algorithm = _runtime_algorithm(config, [first, second])

    result = algorithm.process(np.zeros((80, 100, 3), dtype=np.uint8))

    assert len(second.frames) == 2
    assert len(result["detections"]) == 1
    assert result["detections"][0]["box"] == [50, 5, 75, 45]


def test_cascade_applies_pre_mask_and_filters_only_post_filter_regions(cascade_models):
    config = normalize_cascade_algorithm_config(_raw_config())
    first = _Backend([
        {"box": [10, 10, 30, 40], "confidence": 0.95, "label": "left"},
        {"box": [65, 10, 85, 40], "confidence": 0.9, "label": "right"},
    ])
    second = _Backend([{"box": [1, 1, 5, 5], "confidence": 0.8, "label": "smoke"}])
    algorithm = _runtime_algorithm(config, [first, second])
    frame = np.full((100, 100, 3), 255, dtype=np.uint8)
    roi_regions = [
        {
            "mode": "pre_mask",
            "points": [[0, 0], [49, 0], [49, 99], [0, 99]],
        },
        {
            "mode": "post_filter",
            "points": [[50, 0], [99, 0], [99, 99], [50, 99]],
        },
    ]

    result = algorithm.process(frame, roi_regions=roi_regions)

    assert np.all(first.frames[0][:, 75] == 0)
    assert np.any(first.frames[0][:, 25] > 0)
    assert len(second.frames) == 1
    assert len(result["detections"]) == 1
    assert result["detections"][0]["box"] == [65, 10, 85, 40]


def test_cascade_initialization_cleans_loaded_backends(monkeypatch, cascade_models):
    config = normalize_cascade_algorithm_config(_raw_config())
    first = _Backend()
    model_infos = {
        1: {"id": 1, "path": "/models/person.pt"},
        2: {"id": 2, "path": "/models/smoke.onnx"},
    }
    resolver = SimpleNamespace(_get_model_info=lambda model_id: model_infos[int(model_id)])
    monkeypatch.setattr(
        "app.plugins.cascade_algorithm.normalize_cascade_algorithm_config",
        lambda _config: config,
    )
    monkeypatch.setattr(
        "app.plugins.cascade_algorithm.get_model_resolver",
        lambda: resolver,
    )
    calls = iter([first, RuntimeError("second model failed")])

    def create(*_args, **_kwargs):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("app.plugins.cascade_algorithm.create_backend", create)

    with pytest.raises(RuntimeError, match="second model failed"):
        CascadeAlgorithm({"cascade_config": config})
    assert first.cleaned is True


def test_orchestrator_extracts_cascade_model_ids():
    assert Orchestrator._model_ids_from_algorithm_config({
        "cascade_config": _raw_config(),
    }) == (1, 2)


def test_orchestrator_preserves_per_stage_model_occurrences():
    config = _raw_config()
    config["stages"][1]["model_id"] = 1
    config["stages"][0]["inference"] = {"backend": "ultralytics"}
    config["stages"][1]["inference"] = {"backend": "auto"}

    assert Orchestrator._model_occurrences_from_algorithm_config({
        "cascade_config": config,
    }) == (
        (1, {"backend": "ultralytics"}),
        (1, {"backend": "auto"}),
    )
