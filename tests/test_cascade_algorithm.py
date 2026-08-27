from types import SimpleNamespace

import numpy as np
import pytest

from app.core.cascade_algorithm_config import (
    cascade_model_ids,
    cascade_v1_to_v2,
    normalize_cascade_algorithm_config,
)
from app.core.orchestrator import Orchestrator
from app.plugins.cascade_algorithm import CascadeAlgorithm, _crop_box
from app.user_scripts.common.roi import expand_and_clip_box


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


def _combination_config():
    return {
        "version": 2,
        "evaluation": {"scope": "per_anchor", "anchor_node_id": "head"},
        "nodes": [
            {"id": "frame", "type": "frame", "name": "画面输入"},
            {
                "id": "head", "type": "detector", "name": "检测头部", "model_id": 1,
                "class_ids": [0], "confidence": 0.6, "max_candidates": 20,
                "expand_ratio": 0.1, "inference": {"backend": "auto", "nms_iou": 0.45},
            },
            {
                "id": "helmet", "type": "detector", "name": "检测安全帽", "model_id": 2,
                "class_ids": [0], "confidence": 0.55, "max_candidates": 20,
                "expand_ratio": 0.1, "inference": {"backend": "auto", "nms_iou": 0.45},
            },
            {"id": "head_exists", "type": "predicate", "name": "检测到头部", "operator": "exists"},
            {"id": "helmet_missing", "type": "predicate", "name": "没有安全帽", "operator": "not_exists"},
            {"id": "all", "type": "logic", "name": "全部满足", "operator": "and"},
            {
                "id": "output", "type": "output", "name": "最终输出",
                "label": "未戴安全帽", "color": "#ff4d4f", "box_source_node_id": "head",
            },
        ],
        "edges": [
            {"source": "frame", "target": "head", "kind": "data"},
            {"source": "head", "target": "helmet", "kind": "data"},
            {"source": "head", "target": "head_exists", "kind": "rule"},
            {"source": "helmet", "target": "helmet_missing", "kind": "rule"},
            {"source": "head_exists", "target": "all", "kind": "rule"},
            {"source": "helmet_missing", "target": "all", "kind": "rule"},
            {"source": "all", "target": "output", "kind": "rule"},
        ],
        "layout": {"nodes": {}},
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


def test_crop_box_matches_expand_and_clip_box():
    detection = {"box": [10, 20, 30, 50]}
    frame_shape = (100, 80, 3)
    expand_ratio = 0.1
    assert _crop_box(detection, frame_shape, expand_ratio) == expand_and_clip_box(
        detection["box"], frame_shape, expand_ratio
    )
    assert _crop_box({"label": "no-box"}, frame_shape, expand_ratio) is None


def test_cascade_config_normalizes_linear_stages(cascade_models):
    config = normalize_cascade_algorithm_config(_raw_config())

    assert config["version"] == 1
    assert config["stages"][0]["input"] == {"type": "frame"}
    assert config["stages"][1]["input"]["parent_stage_id"] == "person"
    assert config["stages"][1]["inference"] == {
        "backend": "auto",
        "inference_mode": "letterbox",
        "nms_iou": 0.45,
    }
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


def test_combination_config_normalizes_graph_and_model_ids(cascade_models):
    config = normalize_cascade_algorithm_config(_combination_config())

    assert config["version"] == 2
    assert config["evaluation"] == {"scope": "per_anchor", "anchor_node_id": "head"}
    assert cascade_model_ids(config) == (1, 2)
    assert next(node for node in config["nodes"] if node["id"] == "helmet")["model_name"] == "smoke"


def test_combination_config_normalizes_inference_mode_and_input_size(cascade_models):
    config = _combination_config()
    head = next(node for node in config["nodes"] if node["id"] == "head")
    head["inference"] = {
        "backend": "auto",
        "inference_mode": "sliced",
        "input_width": "960",
        "input_height": 544,
        "nms_iou": 0.45,
    }

    normalized = normalize_cascade_algorithm_config(config)
    inference = next(
        node for node in normalized["nodes"] if node["id"] == "head"
    )["inference"]

    assert inference == {
        "backend": "auto",
        "inference_mode": "sahi",
        "input_width": 960,
        "input_height": 544,
        "nms_iou": 0.45,
    }


def test_combination_config_rejects_unknown_inference_mode(cascade_models):
    config = _combination_config()
    head = next(node for node in config["nodes"] if node["id"] == "head")
    head["inference"]["inference_mode"] = "stretch"

    with pytest.raises(ValueError, match="推理模式不受支持"):
        normalize_cascade_algorithm_config(config)


def test_combination_config_rejects_data_cycle(cascade_models):
    config = _combination_config()
    config["edges"][0] = {"source": "helmet", "target": "head", "kind": "data"}

    with pytest.raises(ValueError, match="循环"):
        normalize_cascade_algorithm_config(config)


def test_combination_config_rejects_disconnected_rule_and_accepts_zero_count(cascade_models):
    config = _combination_config()
    config["nodes"].append({
        "id": "unused", "type": "predicate", "name": "未使用条件", "operator": "eq", "value": 0,
    })
    config["edges"].append({"source": "head", "target": "unused", "kind": "rule"})

    with pytest.raises(ValueError, match="未连接到最终输出"):
        normalize_cascade_algorithm_config(config)

    config["nodes"] = [node for node in config["nodes"] if node["id"] != "unused"]
    config["edges"] = [edge for edge in config["edges"] if edge["target"] != "unused"]
    helmet_missing = next(node for node in config["nodes"] if node["id"] == "helmet_missing")
    helmet_missing.update({"operator": "eq", "value": 0})
    normalized = normalize_cascade_algorithm_config(config)
    assert next(node for node in normalized["nodes"] if node["id"] == "helmet_missing")["value"] == 0


def test_v1_conversion_builds_equivalent_and_rule(cascade_models):
    legacy = normalize_cascade_algorithm_config(_raw_config())
    converted = normalize_cascade_algorithm_config(cascade_v1_to_v2(legacy))

    assert converted["version"] == 2
    assert converted["evaluation"]["anchor_node_id"] == "person"
    assert [node["operator"] for node in converted["nodes"] if node["type"] == "predicate"] == [
        "exists", "exists"
    ]


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


def _runtime_combination(config, backends):
    algorithm = CascadeAlgorithm.__new__(CascadeAlgorithm)
    algorithm.config = {"cascade_config": config, "pixel_format": "rgb24"}
    algorithm.cascade_config = config
    detector_nodes = [node for node in config["nodes"] if node["type"] == "detector"]
    algorithm.stage_runtimes = [
        {"stage": node, "backend": backend, "model_info": {}}
        for node, backend in zip(detector_nodes, backends)
    ]
    algorithm.node_runtimes = {
        runtime["stage"]["id"]: runtime for runtime in algorithm.stage_runtimes
    }
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


def test_combination_marks_only_anchor_without_helmet(cascade_models):
    config = normalize_cascade_algorithm_config(_combination_config())
    heads = _Backend([
        {"box": [5, 5, 35, 45], "confidence": 0.95, "label": "head"},
        {"box": [55, 5, 85, 45], "confidence": 0.9, "label": "head"},
    ])
    helmets = _SequenceBackend([
        [{"box": [5, 2, 20, 12], "confidence": 0.8, "label": "helmet"}],
        [],
    ])
    algorithm = _runtime_combination(config, [heads, helmets])

    result = algorithm.process(np.zeros((80, 100, 3), dtype=np.uint8))

    assert [detection["box"] for detection in result["detections"]] == [[55, 5, 85, 45]]
    assert result["detections"][0]["label"] == "未戴安全帽"
    assert [item["state"] for item in result["metadata"]["context_evaluations"]] == ["false", "true"]


def test_combination_diagnostics_distinguish_executed_miss(cascade_models):
    raw = _combination_config()
    helmet_condition = next(node for node in raw["nodes"] if node["id"] == "helmet_missing")
    helmet_condition.update({"name": "检测到安全帽", "operator": "exists"})
    config = normalize_cascade_algorithm_config(raw)
    heads = _Backend([
        {"box": [5, 5, 35, 45], "confidence": 0.95, "label": "head"},
    ])
    helmets = _Backend([])
    algorithm = _runtime_combination(config, [heads, helmets])

    result = algorithm.process(np.zeros((80, 100, 3), dtype=np.uint8))

    head_debug, helmet_debug = result["metadata"]["node_debug"]
    assert head_debug["execution_state"] == "matched"
    assert helmet_debug["execution_state"] == "not_matched"
    assert helmet_debug["input_count"] == 1
    assert helmet_debug["successful_inferences"] == 1
    assert helmet_debug["detection_count"] == 0
    assert "已执行 1 次，但没有检测到目标" in helmet_debug["reason"]
    context = result["metadata"]["context_evaluations"][0]
    helmet_predicate = next(item for item in context["predicates"] if item["node_id"] == "helmet_missing")
    assert helmet_predicate["state"] == "false"
    assert "命中 0 个目标" in helmet_predicate["reason"]
    assert result["metadata"]["diagnosis"]["state"] == "not_matched"
    assert "检测到安全帽" in context["summary"]


def test_combination_diagnostics_distinguish_upstream_skip(cascade_models):
    config = normalize_cascade_algorithm_config(_combination_config())
    heads = _Backend([])
    helmets = _Backend([{"box": [1, 1, 5, 5], "confidence": 0.9, "label": "helmet"}])
    algorithm = _runtime_combination(config, [heads, helmets])

    result = algorithm.process(np.zeros((80, 100, 3), dtype=np.uint8))

    head_debug, helmet_debug = result["metadata"]["node_debug"]
    assert head_debug["execution_state"] == "not_matched"
    assert helmet_debug["execution_state"] == "skipped"
    assert helmet_debug["successful_inferences"] == 0
    assert "上游节点“检测头部”没有可继续检测的目标" in helmet_debug["reason"]
    assert helmets.frames == []
    assert result["metadata"]["context_evaluations"] == []
    assert result["metadata"]["diagnosis"] == {
        "state": "no_context",
        "summary": "未进入规则判断：锚点节点“检测头部”没有检测到目标",
        "first_break_node_id": "head",
        "first_break_node_name": "检测头部",
    }


def test_combination_does_not_turn_detector_failure_into_negative_match(cascade_models):
    config = normalize_cascade_algorithm_config(_combination_config())
    heads = _Backend([
        {"box": [5, 5, 35, 45], "confidence": 0.95, "label": "head"},
        {"box": [55, 5, 85, 45], "confidence": 0.9, "label": "head"},
    ])
    helmets = _SequenceBackend([
        [{"box": [5, 2, 20, 12], "confidence": 0.8, "label": "helmet"}],
        RuntimeError("worker unavailable"),
    ])
    algorithm = _runtime_combination(config, [heads, helmets])

    result = algorithm.process(np.zeros((80, 100, 3), dtype=np.uint8))

    assert result["detections"] == []
    assert [item["state"] for item in result["metadata"]["context_evaluations"]] == ["false", "unknown"]
    assert result["metadata"]["node_debug"][1]["status"] == "degraded"
    assert result["metadata"]["node_debug"][1]["execution_state"] == "degraded"
    assert result["metadata"]["diagnosis"]["state"] == "unknown"


def test_combination_frame_scope_supports_count_predicate(cascade_models):
    raw = _combination_config()
    raw["evaluation"] = {"scope": "frame", "anchor_node_id": None}
    helmet_condition = next(node for node in raw["nodes"] if node["id"] == "helmet_missing")
    helmet_condition.update({"name": "安全帽数量为一", "operator": "eq", "value": 1})
    config = normalize_cascade_algorithm_config(raw)
    heads = _Backend([
        {"box": [5, 5, 35, 45], "confidence": 0.95, "label": "head"},
        {"box": [55, 5, 85, 45], "confidence": 0.9, "label": "head"},
    ])
    helmets = _SequenceBackend([
        [{"box": [5, 2, 20, 12], "confidence": 0.8, "label": "helmet"}],
        [],
    ])
    algorithm = _runtime_combination(config, [heads, helmets])

    result = algorithm.process(np.zeros((80, 100, 3), dtype=np.uint8))

    assert [detection["box"] for detection in result["detections"]] == [
        [5, 5, 35, 45], [55, 5, 85, 45]
    ]
    assert result["metadata"]["context_evaluations"][0]["state"] == "true"


def test_combination_frame_scope_partial_failure_is_unknown(cascade_models):
    raw = _combination_config()
    raw["evaluation"] = {"scope": "frame", "anchor_node_id": None}
    config = normalize_cascade_algorithm_config(raw)
    heads = _Backend([
        {"box": [5, 5, 35, 45], "confidence": 0.95, "label": "head"},
        {"box": [55, 5, 85, 45], "confidence": 0.9, "label": "head"},
    ])
    helmets = _SequenceBackend([[], RuntimeError("one crop failed")])
    algorithm = _runtime_combination(config, [heads, helmets])

    result = algorithm.process(np.zeros((80, 100, 3), dtype=np.uint8))

    assert result["detections"] == []
    assert result["metadata"]["context_evaluations"][0]["state"] == "unknown"
    assert result["metadata"]["node_debug"][1]["status"] == "degraded"


def test_combination_propagates_failed_empty_parent_to_descendant(cascade_models):
    raw = _combination_config()
    raw["nodes"] = [node for node in raw["nodes"] if node["id"] != "helmet_missing"]
    raw["edges"] = [
        edge for edge in raw["edges"]
        if edge["source"] != "helmet_missing" and edge["target"] != "helmet_missing"
    ]
    raw["nodes"].extend([
        {
            "id": "badge", "type": "detector", "name": "检测徽章", "model_id": 2,
            "class_ids": [0], "confidence": 0.55, "max_candidates": 20,
            "expand_ratio": 0.1, "inference": {"backend": "auto", "nms_iou": 0.45},
        },
        {"id": "badge_missing", "type": "predicate", "name": "没有徽章", "operator": "not_exists"},
    ])
    raw["edges"].extend([
        {"source": "helmet", "target": "badge", "kind": "data"},
        {"source": "badge", "target": "badge_missing", "kind": "rule"},
        {"source": "badge_missing", "target": "all", "kind": "rule"},
    ])
    config = normalize_cascade_algorithm_config(raw)
    heads = _Backend([{"box": [5, 5, 35, 45], "confidence": 0.95, "label": "head"}])
    helmets = _Backend(error=RuntimeError("helmet worker unavailable"))
    badges = _Backend([])
    algorithm = _runtime_combination(config, [heads, helmets, badges])

    result = algorithm.process(np.zeros((80, 100, 3), dtype=np.uint8))

    assert badges.frames == []
    assert result["detections"] == []
    assert result["metadata"]["context_evaluations"][0]["state"] == "unknown"
    assert result["metadata"]["node_debug"][2]["status"] == "failed"
    assert result["metadata"]["node_debug"][2]["execution_state"] == "blocked"


def test_combination_caps_merged_candidates_before_next_detector(cascade_models):
    raw = _combination_config()
    helmet_node = next(node for node in raw["nodes"] if node["id"] == "helmet")
    helmet_node["max_candidates"] = 1
    raw["nodes"].append({
        "id": "badge", "type": "detector", "name": "检测徽章", "model_id": 2,
        "class_ids": [0], "confidence": 0.55, "max_candidates": 20,
        "expand_ratio": 0.1, "inference": {"backend": "auto", "nms_iou": 0.45},
    })
    raw["edges"].append({"source": "helmet", "target": "badge", "kind": "data"})
    config = normalize_cascade_algorithm_config(raw)
    heads = _Backend([
        {"box": [5, 5, 35, 45], "confidence": 0.95, "label": "head"},
        {"box": [55, 5, 85, 45], "confidence": 0.9, "label": "head"},
    ])
    helmets = _SequenceBackend([
        [{"box": [2, 2, 12, 12], "confidence": 0.9, "label": "helmet"}],
        [{"box": [2, 2, 12, 12], "confidence": 0.8, "label": "helmet"}],
    ])
    badges = _Backend([])
    algorithm = _runtime_combination(config, [heads, helmets, badges])

    result = algorithm.process(np.zeros((80, 100, 3), dtype=np.uint8))

    helmet_debug = result["metadata"]["node_debug"][1]
    assert helmet_debug["detection_count"] == 2
    assert helmet_debug["forwarded_count"] == 1
    assert len(badges.frames) == 1
    # Rules see both observations, so bounding downstream work cannot create a false absence alert.
    assert result["detections"] == []


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


def test_orchestrator_extracts_v2_detector_nodes():
    config = _combination_config()
    assert Orchestrator._model_ids_from_algorithm_config({"cascade_config": config}) == (1, 2)
    assert Orchestrator._model_occurrences_from_algorithm_config({"cascade_config": config}) == (
        (1, {"backend": "auto", "nms_iou": 0.45}),
        (2, {"backend": "auto", "nms_iou": 0.45}),
    )
