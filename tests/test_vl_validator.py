from app.core.vl_validator import _build_endpoint, _parse_validation_response, render_prompt_template
from app.core.workflow_types import create_node_data, AlertNodeData


def test_create_alert_node_data_with_vl_validation():
    node = create_node_data(
        {
            "id": "alert-1",
            "type": "alert",
            "data": {
                "alertLevel": "warning",
                "alertType": "person",
                "alertMessage": "检测到人员",
                "vlValidation": {"enable": True},
            },
        }
    )

    assert isinstance(node, AlertNodeData)
    assert node.vl_validation == {"enable": True}


def test_parse_validation_response_from_json_code_block():
    parsed = _parse_validation_response(
        """```json
{"is_alert": false, "confidence": 0.12, "reason": "画面中不存在真实告警目标"}
```"""
    )

    assert parsed is not None
    assert parsed["is_alert"] is False
    assert parsed["confidence"] == 0.12
    assert parsed["reason"] == "画面中不存在真实告警目标"


def test_build_endpoint_accepts_base_or_full_path():
    assert _build_endpoint("https://example.com/v1") == "https://example.com/v1/chat/completions"
    assert _build_endpoint("https://example.com/v1/chat/completions") == "https://example.com/v1/chat/completions"


def test_render_prompt_template_replaces_placeholders():
    rendered = render_prompt_template(
        prompt_template="类型:{alert_type}; 数量:{detection_count}; 工作流:{workflow_name}",
        alert_type="person",
        alert_message="检测到人员",
        result={"detections": [{"class_name": "person", "confidence": 0.88, "bbox": [1, 2, 3, 4]}]},
        extra_context={"workflow_name": "测试工作流"},
    )

    assert "类型:person" in rendered
    assert "数量:1" in rendered
    assert "工作流:测试工作流" in rendered
