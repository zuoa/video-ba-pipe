import json
from types import SimpleNamespace

import numpy as np
import pytest
import requests

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
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.text = json.dumps(body, ensure_ascii=False)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

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
    assert updated["response_format_mode"] == "auto"


@pytest.mark.parametrize("mode", ["auto", "json_schema", "json_object", "text"])
def test_vl_config_preserves_response_format_mode_when_editing_other_fields(mode):
    current = _config()["vl_config"] | {"response_format_mode": mode}
    updated = normalize_vl_algorithm_config({"prompt_template": "检查新的业务条件"}, current=current)
    assert updated["response_format_mode"] == mode


def test_vl_config_rejects_unknown_response_format_mode():
    with pytest.raises(ValueError, match="response_format_mode"):
        normalize_vl_algorithm_config(_config()["vl_config"] | {"response_format_mode": "xml"})


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
    assert captured["timeout"] == pytest.approx(30, abs=0.1)


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

    assert captured["timeout"] == pytest.approx(5, abs=0.1)


def _no_match_response():
    return _FakeResponse({
        "choices": [{"message": {"content": json.dumps({
            "has_detection": False, "detections": [], "reason": "未发现符合业务条件的目标",
        }, ensure_ascii=False)}}],
    })


def _mock_vl_responses(monkeypatch, responses):
    pending = iter(responses)
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append({"payload": json, "timeout": timeout})
        response = next(pending)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("app.plugins.vl_algorithm.requests.post", fake_post)
    monkeypatch.setattr(VLAlgorithm, "_frame_to_data_url", lambda self, frame: "data:image/jpeg;base64,AA==")
    return calls


@pytest.mark.parametrize("business_prompt", [
    "识别地面散落垃圾，忽略垃圾桶内的物品。摄像头：{source_name}",
    "识别未佩戴安全帽的人员，忽略墙上的宣传海报。摄像头：{source_name}",
])
def test_vl_system_adds_format_examples_without_fixing_business_targets(monkeypatch, business_prompt):
    calls = _mock_vl_responses(monkeypatch, [_no_match_response()])
    config = _config()
    config["vl_config"]["prompt_template"] = business_prompt

    result = VLAlgorithm(config).process(np.zeros((32, 48, 3), dtype=np.uint8))

    prompt = calls[0]["payload"]["messages"][0]["content"][0]["text"]
    assert business_prompt.replace("{source_name}", "东门") in prompt
    contract = prompt.split("【系统统一输出协议】", 1)[1]
    assert "垃圾" not in contract and "安全帽" not in contract
    assert "画面宽 48、高 32" in contract
    examples = [json.loads(line) for line in contract.splitlines() if line.startswith("{")]
    assert [normalize_vl_result(example)["has_detection"] for example in examples] == [True, False]
    assert config["vl_config"]["prompt_template"] == business_prompt
    assert result["metadata"]["vl_checked"] is True


def test_vl_auto_falls_back_to_json_object_and_reuses_successful_mode(monkeypatch):
    calls = _mock_vl_responses(monkeypatch, [
        _FakeResponse({"error": {"message": "response_format json_schema is not supported"}}, 400),
        _no_match_response(), _no_match_response(),
    ])
    algorithm = VLAlgorithm(_config())
    frame = np.zeros((16, 16, 3), dtype=np.uint8)

    first = algorithm.process(frame)
    second = algorithm.process(frame)

    assert [call["payload"]["response_format"]["type"] for call in calls] == [
        "json_schema", "json_object", "json_object",
    ]
    assert first["metadata"]["vl_checked"] is True
    assert first["metadata"]["response_format"] == "json_object"
    assert first["metadata"]["request_attempts"] == 2
    assert second["metadata"]["request_attempts"] == 1
    assert "http_error" not in first["metadata"]
    assert calls[0]["payload"]["messages"] == calls[1]["payload"]["messages"]
    assert calls[1]["payload"]["seed"] == 7


def test_vl_auto_falls_back_to_prompt_when_endpoint_rejects_response_format(monkeypatch):
    calls = _mock_vl_responses(monkeypatch, [
        _FakeResponse({"detail": "Unknown parameter response_format"}, 422),
        _FakeResponse({"detail": "Unknown parameter response_format"}, 422),
        _no_match_response(),
    ])

    result = VLAlgorithm(_config()).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert len(calls) == 3
    assert "response_format" not in calls[-1]["payload"]
    assert "系统统一输出协议" in calls[-1]["payload"]["messages"][0]["content"][0]["text"]
    assert result["metadata"]["response_format"] == "text"
    assert result["metadata"]["vl_checked"] is True


def test_vl_fallback_supports_pydantic_extra_field_rejections(monkeypatch):
    calls = _mock_vl_responses(monkeypatch, [
        _FakeResponse({"detail": [{
            "type": "extra_forbidden", "loc": ["body", "response_format"],
            "msg": "Extra inputs are not permitted",
        }]}, 422),
        _no_match_response(),
    ])

    result = VLAlgorithm(_config()).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert len(calls) == 2
    assert result["metadata"]["response_format"] == "json_object"
    assert result["metadata"]["vl_checked"] is True


@pytest.mark.parametrize("mode", ["json_schema", "json_object", "text"])
def test_vl_explicit_output_mode_selects_request_format(monkeypatch, mode):
    calls = _mock_vl_responses(monkeypatch, [_no_match_response()])
    config = _config()
    config["vl_config"]["response_format_mode"] = mode

    result = VLAlgorithm(config).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert len(calls) == 1
    assert calls[0]["payload"].get("response_format", {}).get("type", "text") == mode
    assert result["metadata"]["response_format_requested"] == mode
    assert result["metadata"]["vl_checked"] is True


def test_vl_explicit_mode_reports_unsupported_format_without_fallback(monkeypatch):
    calls = _mock_vl_responses(monkeypatch, [
        _FakeResponse({"error": {"message": "json_schema is not supported"}}, 400),
    ])
    config = _config()
    config["vl_config"]["response_format_mode"] = "json_schema"

    result = VLAlgorithm(config).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert len(calls) == 1
    assert result["metadata"]["vl_checked"] is False
    assert "not supported" in result["metadata"]["error"]


@pytest.mark.parametrize("status,message", [
    (401, "Invalid API key"), (403, "Forbidden"), (429, "Rate limited"),
    (500, "Internal error: response_format unsupported"),
    (400, "Invalid image"), (422, "Invalid model"),
])
def test_vl_does_not_retry_unrelated_http_errors(monkeypatch, status, message):
    calls = _mock_vl_responses(monkeypatch, [_FakeResponse({"error": {"message": message}}, status)])

    result = VLAlgorithm(_config()).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert len(calls) == 1
    assert result["metadata"]["vl_checked"] is False
    assert str(status) in result["metadata"]["error"]
    assert message in result["metadata"]["http_error"]


@pytest.mark.parametrize("invalid", ["false", "[]", "null", '"否"',
    '{"has_detection":false,"detections":[],"reason":123}',
])
def test_vl_invalid_output_is_rechecked_once_without_guessing_result(monkeypatch, invalid):
    invalid_response = _FakeResponse({"choices": [{"message": {"content": invalid}}]})
    calls = _mock_vl_responses(monkeypatch, [invalid_response, invalid_response])

    result = VLAlgorithm(_config()).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert len(calls) == 2
    assert calls[1]["payload"]["response_format"]["type"] == "json_object"
    assert "上一次返回结果不符合系统输出协议" in calls[1]["payload"]["messages"][0]["content"][-1]["text"]
    assert result["detections"] == []
    assert result["metadata"]["vl_checked"] is False
    assert result["metadata"]["raw_response"] == invalid
    assert result["metadata"]["retry_reason"]


def test_vl_invalid_output_can_recover_after_correction(monkeypatch):
    calls = _mock_vl_responses(monkeypatch, [
        _FakeResponse({"choices": [{"message": {"content": "false"}}]}),
        _no_match_response(),
    ])

    result = VLAlgorithm(_config()).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert len(calls) == 2
    assert result["metadata"]["vl_checked"] is True
    assert result["metadata"]["response_format"] == "json_object"
    assert "error" not in result["metadata"]


@pytest.mark.parametrize("mode", ["json_schema", "json_object", "text"])
def test_vl_correction_preserves_explicit_format_choice(monkeypatch, mode):
    calls = _mock_vl_responses(monkeypatch, [
        _FakeResponse({"choices": [{"message": {"content": "[]"}}]}),
        _no_match_response(),
    ])
    config = _config()
    config["vl_config"]["response_format_mode"] = mode

    result = VLAlgorithm(config).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert [call["payload"].get("response_format", {}).get("type", "text") for call in calls] == [mode, mode]
    assert result["metadata"]["vl_checked"] is True


@pytest.mark.parametrize("invalid_envelope", [[], {"choices": {}}, {"choices": [{"message": "invalid"}]}])
def test_vl_invalid_response_envelope_keeps_failure_diagnostics(monkeypatch, invalid_envelope):
    response = _FakeResponse(invalid_envelope)
    calls = _mock_vl_responses(monkeypatch, [response, response])

    result = VLAlgorithm(_config()).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert len(calls) == 2
    assert result["metadata"]["vl_checked"] is False
    assert result["metadata"]["raw_response"] == json.dumps(invalid_envelope)


def test_vl_fallback_and_correction_can_be_combined_with_bounded_attempts(monkeypatch):
    calls = _mock_vl_responses(monkeypatch, [
        _FakeResponse({"choices": [{"message": {"content": "[]"}}]}),
        _FakeResponse({"error": "response_format is not supported"}, 400),
        _no_match_response(),
    ])

    result = VLAlgorithm(_config()).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert len(calls) == 3
    assert result["metadata"]["response_format"] == "text"
    assert result["metadata"]["vl_checked"] is True


def test_vl_fallback_uses_remaining_timeout_budget(monkeypatch):
    calls = _mock_vl_responses(monkeypatch, [
        _FakeResponse({"error": "json_schema is not supported"}, 400),
        _no_match_response(),
    ])
    times = iter([0, 0, 0, 12, 13])
    monkeypatch.setattr("app.plugins.vl_algorithm.time.perf_counter", lambda: next(times))

    result = VLAlgorithm(_config()).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert result["metadata"]["vl_checked"] is True
    assert [call["timeout"] for call in calls] == [30, 18]


def test_vl_stops_fallback_after_timeout_budget_exhausted(monkeypatch):
    calls = _mock_vl_responses(monkeypatch, [
        _FakeResponse({"error": "json_schema is not supported"}, 400),
    ])
    times = iter([0, 0, 0, 31, 31])
    monkeypatch.setattr("app.plugins.vl_algorithm.time.perf_counter", lambda: next(times))

    result = VLAlgorithm(_config()).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert len(calls) == 1
    assert result["metadata"]["vl_checked"] is False
    assert "超时限制" in result["metadata"]["error"]


def test_vl_network_timeout_does_not_trigger_format_fallback(monkeypatch):
    calls = _mock_vl_responses(monkeypatch, [requests.ReadTimeout("read timed out")])

    result = VLAlgorithm(_config()).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert len(calls) == 1
    assert "timed out" in result["metadata"]["error"]


def test_vl_failure_response_preview_is_bounded_and_redacts_api_key(monkeypatch):
    content = json.dumps("secret " + "x" * 5000)
    invalid_response = _FakeResponse({"choices": [{"message": {"content": content}}]})
    _mock_vl_responses(monkeypatch, [invalid_response, invalid_response])

    result = VLAlgorithm(_config()).process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert len(result["metadata"]["raw_response"]) == 4000
    assert "secret" not in result["metadata"]["raw_response"]
    assert "[REDACTED]" in result["metadata"]["raw_response"]


@pytest.mark.parametrize("valid", [True, False])
def test_saved_vl_test_forwards_format_configuration_and_failure_diagnostics(monkeypatch, valid):
    from app.core import algorithm_test_execution as execution

    response = _no_match_response() if valid else _FakeResponse({"choices": [{"message": {"content": "[]"}}]})
    calls = _mock_vl_responses(monkeypatch, [response, response])
    config = _config()
    config["vl_config"]["response_format_mode"] = "json_object"
    saved_algorithm = SimpleNamespace(
        id=12, name="业务检测", script_path="", config_dict={},
        ext_config={"algorithm_type": "vl", "vl_config": config["vl_config"]},
    )
    monkeypatch.setattr(execution.Algorithm, "get_by_id", lambda _algorithm_id: saved_algorithm)
    ok, image_bytes = execution.cv2.imencode(".jpg", np.zeros((640, 640, 3), dtype=np.uint8))
    assert ok

    result = execution.execute_saved_algorithm_test(12, image_bytes.tobytes())

    assert all(call["payload"]["response_format"] == {"type": "json_object"} for call in calls)
    assert result["success"] is valid
    assert result["metadata"]["vl_checked"] is valid
    if not valid:
        assert result["error"] == result["metadata"]["error"]
        assert result["metadata"]["raw_response"] == "[]"


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


def test_vl_algorithm_honors_region_anchor_and_preserves_semantic_results(monkeypatch):
    def fake_post(url, headers, json, timeout):
        return _FakeResponse(
            {
                "choices": [{
                    "message": {
                        "content": (
                            '{"has_detection":true,"detections":['
                            '{"label_name":"crossing","confidence":0.9,"bbox":[0.4,0.2,0.8,0.8]},'
                            '{"label_name":"semantic","confidence":0.8,"bbox":null}],'
                            '"reason":"测试"}'
                        )
                    }
                }]
            }
        )

    roi_regions = [{
        "name": "left",
        "mode": "post_filter",
        "anchor": "top_left",
        "polygon": [
            {"x": 0.0, "y": 0.0},
            {"x": 0.5, "y": 0.0},
            {"x": 0.5, "y": 1.0},
            {"x": 0.0, "y": 1.0},
        ],
    }]
    monkeypatch.setattr("app.plugins.vl_algorithm.requests.post", fake_post)
    monkeypatch.setattr(VLAlgorithm, "_frame_to_data_url", lambda self, frame: "data:image/jpeg;base64,AA==")

    result = VLAlgorithm(_config()).process(
        np.full((20, 40, 3), 255, dtype=np.uint8),
        roi_regions=roi_regions,
    )

    assert [item["label_name"] for item in result["detections"]] == ["crossing", "semantic"]
    assert result["metadata"]["roi_filtered_count"] == 0


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
        config_dict={
            "created_by": "victim",
            "_execution_owner": "victim",
            "_execution_role": "admin",
            "_preview_mode": False,
        },
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
    monkeypatch.setattr(
        "app.core.workflow_executor.User.get_or_none",
        lambda *_args, **_kwargs: SimpleNamespace(role="user"),
    )

    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = 8
    executor.workflow = SimpleNamespace(name="园区巡检", created_by="workflow-owner")
    executor.video_source = SimpleNamespace(
        id=4, name="东门", source_code="gate-east", created_by="source-owner"
    )
    executor.test_mode = True
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
    assert executor.algorithms["algo-vl"].config["created_by"] == "source-owner"
    assert executor.algorithms["algo-vl"].config["_execution_owner"] == "source-owner"
    assert executor.algorithms["algo-vl"].config["_execution_role"] == "user"
    assert executor.algorithms["algo-vl"].config["_preview_mode"] is True
