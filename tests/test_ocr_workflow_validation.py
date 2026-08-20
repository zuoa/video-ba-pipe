from types import SimpleNamespace
from unittest.mock import Mock

import app.web.api.workflows as workflows_api
from app.web.api.workflows import (
    _normalize_algorithm_edge_condition,
    _sanitize_workflow_edge_conditions,
    _validate_ocr_crop_nodes,
    _validate_ocr_text_conditions,
)


def _workflow(condition_data):
    return {
        "nodes": [
            {"id": "ocr-1", "type": "algorithm", "dataId": 9},
            {"id": "condition-1", "type": "condition", "data": condition_data},
        ],
        "connections": [{"from": "ocr-1", "to": "condition-1"}],
    }


def test_validate_ocr_text_condition_requires_connected_ocr_algorithm(monkeypatch):
    monkeypatch.setattr(
        "app.web.api.workflows.Algorithm.get_by_id",
        lambda _algorithm_id: SimpleNamespace(ext_config={"algorithm_type": "ocr"}),
    )
    valid, error = _validate_ocr_text_conditions(
        _workflow(
            {
                "conditionKind": "ocr_text",
                "sourceNodeId": "ocr-1",
                "textOperator": "contains",
                "patternType": "keywords",
                "keywords": ["安全出口"],
                "keywordLogic": "any",
            }
        )
    )

    assert valid is True
    assert error is None


def test_validate_ocr_text_condition_rejects_invalid_regex(monkeypatch):
    monkeypatch.setattr(
        "app.web.api.workflows.Algorithm.get_by_id",
        lambda _algorithm_id: SimpleNamespace(ext_config={"algorithm_type": "ocr"}),
    )
    valid, error = _validate_ocr_text_conditions(
        _workflow(
            {
                "conditionKind": "ocr_text",
                "sourceNodeId": "ocr-1",
                "textOperator": "contains",
                "patternType": "regex",
                "regexPattern": "[",
            }
        )
    )

    assert valid is False
    assert "正则无效" in error


def _ocr_algorithm(_algorithm_id):
    algorithm_id = int(_algorithm_id)
    if algorithm_id == 9:
        return SimpleNamespace(ext_config={"algorithm_type": "ocr"})
    return SimpleNamespace(ext_config={"algorithm_type": "yolo"})


def _crop_workflow(ocr_config, connections, extra_nodes=None):
    nodes = [
        {"id": "yolo-1", "type": "algorithm", "dataId": 5},
        {"id": "ocr-1", "type": "algorithm", "dataId": 9, "config": ocr_config},
    ]
    if extra_nodes:
        nodes.extend(extra_nodes)
    return {"nodes": nodes, "connections": connections}


def test_upstream_crops_rejects_no_incoming(monkeypatch):
    monkeypatch.setattr("app.web.api.workflows.Algorithm.get_by_id", _ocr_algorithm)
    valid, error, _warnings = _validate_ocr_crop_nodes(
        _crop_workflow({"input_mode": "upstream_crops"}, [])
    )

    assert valid is False
    assert "恰好连接一条入边" in error


def test_upstream_crops_rejects_two_incoming(monkeypatch):
    monkeypatch.setattr("app.web.api.workflows.Algorithm.get_by_id", _ocr_algorithm)
    valid, error, _warnings = _validate_ocr_crop_nodes(_crop_workflow(
        {"input_mode": "upstream_crops"},
        [
            {"from": "yolo-1", "to": "ocr-1", "condition": "detected"},
            {"from": "yolo-2", "to": "ocr-1", "condition": "detected"},
        ],
        extra_nodes=[{"id": "yolo-2", "type": "algorithm", "dataId": 6}],
    ))

    assert valid is False
    assert "恰好连接一条入边" in error


def test_upstream_crops_rejects_extra_source_incoming(monkeypatch):
    monkeypatch.setattr("app.web.api.workflows.Algorithm.get_by_id", _ocr_algorithm)
    valid, error, _warnings = _validate_ocr_crop_nodes(_crop_workflow(
        {"input_mode": "upstream_crops"},
        [
            {"from": "source-1", "to": "ocr-1", "condition": None},
            {"from": "yolo-1", "to": "ocr-1", "condition": "detected"},
        ],
        extra_nodes=[{"id": "source-1", "type": "source", "dataId": 1}],
    ))

    assert valid is False
    assert "恰好连接一条入边" in error


def test_upstream_crops_rejects_condition_not_detected(monkeypatch):
    monkeypatch.setattr("app.web.api.workflows.Algorithm.get_by_id", _ocr_algorithm)
    valid, error, _warnings = _validate_ocr_crop_nodes(_crop_workflow(
        {"input_mode": "upstream_crops"},
        [{"from": "yolo-1", "to": "ocr-1", "condition": None}],
    ))

    assert valid is False
    assert "必须为 detected" in error


def test_upstream_crops_rejects_condition_node_incoming(monkeypatch):
    monkeypatch.setattr("app.web.api.workflows.Algorithm.get_by_id", _ocr_algorithm)
    valid, error, _warnings = _validate_ocr_crop_nodes(_crop_workflow(
        {"input_mode": "upstream_crops"},
        [
            {"from": "yolo-1", "to": "cond-1"},
            {"from": "cond-1", "to": "ocr-1", "from_port": "true", "condition": "true"},
        ],
        extra_nodes=[{"id": "cond-1", "type": "condition", "data": {"conditionKind": "count"}}],
    ))

    assert valid is False
    assert "必须来自算法、函数或外部 API" in error


def test_upstream_crops_whitespace_input_mode_still_requires_detected(monkeypatch):
    monkeypatch.setattr("app.web.api.workflows.Algorithm.get_by_id", _ocr_algorithm)
    valid, error, _warnings = _validate_ocr_crop_nodes(_crop_workflow(
        {"input_mode": "upstream_crops "},
        [{"from": "yolo-1", "to": "ocr-1", "condition": None}],
    ))

    assert valid is False
    assert "必须为 detected" in error


def test_ocr_frame_mixed_incoming_emits_or_warning(monkeypatch):
    monkeypatch.setattr("app.web.api.workflows.Algorithm.get_by_id", _ocr_algorithm)
    valid, error, warnings = _validate_ocr_crop_nodes(_crop_workflow(
        {"input_mode": "frame"},
        [
            {"from": "yolo-1", "to": "ocr-1", "condition": None},
            {"from": "yolo-2", "to": "ocr-1", "condition": "detected"},
        ],
        extra_nodes=[{"id": "yolo-2", "type": "algorithm", "dataId": 6}],
    ))

    assert valid is True
    assert error is None
    assert any("执行语义是 OR" in warning for warning in warnings)


def test_upstream_crops_accepts_exactly_one_detected_from_algorithm(monkeypatch):
    monkeypatch.setattr("app.web.api.workflows.Algorithm.get_by_id", _ocr_algorithm)
    valid, error, warnings = _validate_ocr_crop_nodes(_crop_workflow(
        {"input_mode": "upstream_crops", "expand_ratio": 0.1, "max_candidates": 8},
        [{"from": "yolo-1", "to": "ocr-1", "condition": "detected"}],
    ))

    assert valid is True
    assert error is None
    assert warnings == []


def test_ocr_crop_bad_expand_ratio_is_rejected(monkeypatch):
    monkeypatch.setattr("app.web.api.workflows.Algorithm.get_by_id", _ocr_algorithm)
    valid, error, _warnings = _validate_ocr_crop_nodes(_crop_workflow(
        {"input_mode": "upstream_crops", "expand_ratio": 1.5},
        [{"from": "yolo-1", "to": "ocr-1", "condition": "detected"}],
    ))

    assert valid is False
    assert "expand_ratio" in error


def test_ocr_crop_validation_does_not_call_normalize_ocr_algorithm_config(monkeypatch):
    assert "normalize_ocr_algorithm_config" not in workflows_api.__dict__
    normalize = Mock(side_effect=AssertionError("normalize_ocr_algorithm_config must not run on node overlay"))
    monkeypatch.setattr("app.core.ocr_algorithm_config.normalize_ocr_algorithm_config", normalize)
    if "normalize_ocr_algorithm_config" in workflows_api.__dict__:
        monkeypatch.setattr(workflows_api, "normalize_ocr_algorithm_config", normalize)
    monkeypatch.setattr("app.web.api.workflows.Algorithm.get_by_id", _ocr_algorithm)

    valid, error, _warnings = _validate_ocr_crop_nodes(_crop_workflow(
        {"input_mode": "upstream_crops"},
        [{"from": "yolo-1", "to": "ocr-1", "condition": "detected"}],
    ))

    assert valid is True
    assert error is None
    normalize.assert_not_called()


def test_always_is_not_persisted_as_edge_condition():
    assert _normalize_algorithm_edge_condition("always") is None
    assert _normalize_algorithm_edge_condition("detected") == "detected"
    assert _normalize_algorithm_edge_condition("not_detected") == "not_detected"
    assert _normalize_algorithm_edge_condition(None) is None

    workflow = {
        "nodes": [
            {"id": "yolo-1", "type": "algorithm", "dataId": 5},
            {"id": "ocr-1", "type": "algorithm", "dataId": 9, "config": {"input_mode": "frame"}},
            {"id": "cond-1", "type": "condition"},
        ],
        "connections": [
            {"from": "yolo-1", "to": "ocr-1", "condition": "always"},
            {"from": "cond-1", "to": "ocr-1", "from_port": "true", "condition": "true"},
        ],
    }
    _sanitize_workflow_edge_conditions(workflow)

    assert workflow["connections"][0]["condition"] is None
    assert "always" not in {conn.get("condition") for conn in workflow["connections"]}
    assert workflow["connections"][1]["condition"] == "true"
