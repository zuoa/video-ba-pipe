import json
from types import SimpleNamespace

import numpy as np
import pytest

from app.plugins.vl_algorithm import (
    VLAlgorithm,
    VLResponseError,
    build_chat_completions_endpoint,
    normalize_vl_result,
)
from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_types import AlgorithmNodeData
from app.core.vl_algorithm_config import normalize_vl_algorithm_config


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def _config():
    return {
        "id": 12,
        "name": "危险区域识别",
        "pixel_format": "rgb24",
        "runtime_timeout": 15,
        "workflow_name": "测试工作流",
        "source_name": "东门",
        "vl_config": {
            "base_url": "https://vl.example/v1",
            "api_key": "secret",
            "model_name": "vision-model",
            "prompt_template": "识别画面，工作流={workflow_name}，上游={upstream_results_json}",
            "temperature": 0,
            "max_tokens": 512,
            "timeout_seconds": 30,
            "image_detail": "low",
            "extra_headers": {"X-Tenant": "demo"},
            "extra_body": {"seed": 7},
        },
    }


def test_build_chat_completions_endpoint_accepts_base_or_full_path():
    assert build_chat_completions_endpoint("https://vl.example/v1") == "https://vl.example/v1/chat/completions"
    assert build_chat_completions_endpoint("https://vl.example/v1/chat/completions") == "https://vl.example/v1/chat/completions"


def test_vl_config_update_preserves_write_only_api_key():
    updated = normalize_vl_algorithm_config(
        {
            "base_url": "https://new.example/v1",
            "api_key": "",
            "model_name": "new-model",
            "prompt_template": "检查画面",
        },
        current=_config()["vl_config"],
    )

    assert updated["api_key"] == "secret"
    assert updated["base_url"] == "https://new.example/v1"
    assert updated["temperature"] == 0


def test_vl_config_rejects_reserved_extra_body_fields():
    config = _config()["vl_config"] | {"extra_body": {"messages": []}}
    try:
        normalize_vl_algorithm_config(config)
    except ValueError as exc:
        assert "messages" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_normalize_vl_result_supports_semantic_detection_without_bbox():
    result = normalize_vl_result(
        {
            "has_detection": True,
            "detections": [{"label_name": "人员聚集", "confidence": 0.91, "bbox": None}],
            "reason": "画面中存在多人聚集",
        }
    )

    assert result["has_detection"] is True
    assert result["detections"][0]["semantic"] is True
    assert "bbox" not in result["detections"][0]


def test_normalize_vl_result_rejects_inconsistent_has_detection():
    try:
        normalize_vl_result({"has_detection": True, "detections": [], "reason": "命中"})
    except VLResponseError as exc:
        assert "保持一致" in str(exc)
    else:
        raise AssertionError("expected VLResponseError")


def test_vl_algorithm_builds_request_and_returns_standard_detections(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured.update(url=url, headers=headers, payload=json, timeout=timeout)
        content = {
            "has_detection": True,
            "detections": [
                {"label_name": "person", "confidence": 0.88, "bbox": [0.1, 0.2, 0.8, 0.9]}
            ],
            "reason": "检测到人员",
        }
        return _FakeResponse(
            {
                "choices": [{"message": {"content": json_module.dumps(content, ensure_ascii=False)}}],
                "usage": {"total_tokens": 42},
            }
        )

    json_module = json
    monkeypatch.setattr("app.plugins.vl_algorithm.requests.post", fake_post)
    monkeypatch.setattr(VLAlgorithm, "_frame_to_data_url", lambda self, frame: "data:image/jpeg;base64,AA==")
    algorithm = VLAlgorithm(_config())
    result = algorithm.process(
        np.zeros((32, 48, 3), dtype=np.uint8),
        upstream_results={"algo-1": {"detections": []}},
    )

    assert len(result["detections"]) == 1
    assert result["detections"][0]["box"] == pytest.approx([4.8, 6.4, 38.4, 28.8])
    assert result["metadata"]["vl_reason"] == "检测到人员"
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["headers"]["X-Tenant"] == "demo"
    assert captured["payload"]["seed"] == 7
    assert captured["payload"]["response_format"]["type"] == "json_schema"
    assert captured["timeout"] == 30


def test_vl_algorithm_node_timeout_override_is_explicit(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["timeout"] = timeout
        return _FakeResponse(
            {
                "choices": [{"message": {"content": '{"has_detection":false,"detections":[],"reason":"无"}'}}]
            }
        )

    config = _config() | {"vl_timeout_override_seconds": 5}
    monkeypatch.setattr("app.plugins.vl_algorithm.requests.post", fake_post)
    monkeypatch.setattr(VLAlgorithm, "_frame_to_data_url", lambda self, frame: "data:image/jpeg;base64,AA==")

    VLAlgorithm(config).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert captured["timeout"] == 5


def test_vl_algorithm_masks_roi_and_filters_outside_boxes(monkeypatch):
    captured = {}

    def fake_data_url(self, frame):
        captured["request_frame"] = frame.copy()
        return "data:image/jpeg;base64,AA=="

    def fake_post(url, headers, json, timeout):
        captured["prompt"] = json["messages"][0]["content"][0]["text"]
        return _FakeResponse(
            {
                "choices": [{
                    "message": {
                        "content": (
                            '{"has_detection":true,"detections":['
                            '{"label_name":"outside","confidence":0.9,"bbox":[0.7,0.2,0.9,0.8]}],'
                            '"reason":"测试"}'
                        )
                    }
                }]
            }
        )

    roi_regions = [{
        "name": "left",
        "polygon": [
            {"x": 0.0, "y": 0.0},
            {"x": 0.5, "y": 0.0},
            {"x": 0.5, "y": 1.0},
            {"x": 0.0, "y": 1.0},
        ],
    }]
    monkeypatch.setattr("app.plugins.vl_algorithm.requests.post", fake_post)
    monkeypatch.setattr(VLAlgorithm, "_frame_to_data_url", fake_data_url)

    result = VLAlgorithm(_config()).process(
        np.full((20, 40, 3), 255, dtype=np.uint8),
        roi_regions=roi_regions,
    )

    assert result["detections"] == []
    assert result["metadata"]["roi_applied"] is True
    assert result["metadata"]["roi_filtered_count"] == 1
    assert captured["request_frame"][:, 30:].max() == 0
    assert captured["request_frame"][:, :10].min() == 255
    assert "只允许判断以下 ROI 内" in captured["prompt"]


def test_vl_algorithm_failure_returns_empty_detections(monkeypatch):
    def fake_post(*args, **kwargs):
        raise TimeoutError("request timed out")

    monkeypatch.setattr("app.plugins.vl_algorithm.requests.post", fake_post)
    monkeypatch.setattr(VLAlgorithm, "_frame_to_data_url", lambda self, frame: "data:image/jpeg;base64,AA==")
    result = VLAlgorithm(_config()).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert result["detections"] == []
    assert "timed out" in result["metadata"]["error"]
    assert result["metadata"]["vl_checked"] is False


def test_workflow_loads_vl_record_as_standard_algorithm_node(monkeypatch):
    fake_algorithm = SimpleNamespace(
        id=31,
        name="VL 人员聚集",
        script_path="",
        config_dict={},
        ext_config={
            "algorithm_type": "vl",
            "interval_seconds": 2,
            "runtime_timeout": 25,
            "label_name": "人员聚集",
            "label_color": "#13c2c2",
            "vl_config": _config()["vl_config"],
        },
    )
    monkeypatch.setattr(
        "app.core.workflow_executor.Algorithm.get_by_id",
        lambda algorithm_id: fake_algorithm,
    )

    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = 8
    executor.workflow = SimpleNamespace(name="园区巡检")
    executor.video_source = SimpleNamespace(id=4, name="东门", source_code="gate-east")
    executor.nodes = {
        "algo-vl": AlgorithmNodeData(
            node_id="algo-vl",
            data_id=31,
            interval_seconds=3,
            config={"interval_seconds": 3},
        )
    }
    executor.workflow_data = {
        "nodes": [
            {
                "id": "algo-vl",
                "type": "algorithm",
                "dataId": 31,
                "config": {
                    "interval_seconds": 3,
                    "runtime_timeout": 20,
                    "runtime_timeout_override_enabled": True,
                    "label_name": "聚集事件",
                    "label_color": "#13c2c2",
                },
            }
        ]
    }
    executor.algorithms = {}
    executor.algorithm_configs = {}
    executor.algorithm_datamap = {}
    executor.algorithm_roi_configs = {}

    executor._load_algorithms()

    assert isinstance(executor.algorithms["algo-vl"], VLAlgorithm)
    assert executor.algorithm_datamap["algo-vl"]["algorithm_type"] == "vl"
    assert executor.algorithm_datamap["algo-vl"]["interval_seconds"] == 3
    assert executor.algorithms["algo-vl"].config["workflow_name"] == "园区巡检"
    assert executor.algorithms["algo-vl"].config["vl_timeout_override_seconds"] == 20
