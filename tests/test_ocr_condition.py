from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_types import ConditionNodeData


def _node(**overrides):
    values = {
        "node_id": "condition-1",
        "condition_kind": "ocr_text",
        "source_node_id": "ocr-1",
        "text_operator": "contains",
        "pattern_type": "keywords",
        "keywords": ["安全", "出口"],
        "keyword_logic": "all",
        "case_sensitive": False,
    }
    values.update(overrides)
    return ConditionNodeData(**values)


def _result(text="", checked=True, error=None):
    metadata = {"ocr_checked": checked, "full_text": text}
    if error:
        metadata["error"] = error
    return {"ocr-1": {"detections": [], "metadata": metadata}}


def test_ocr_keyword_condition_supports_all_and_case_insensitive():
    passed, metadata, error = WorkflowExecutor._evaluate_ocr_text_condition(
        _node(keywords=["SAFE", "出口"]),
        _result("safe\n出口"),
    )

    assert passed is True
    assert error is None
    assert metadata["matched_terms"] == ["SAFE", "出口"]


def test_ocr_not_contains_passes_for_successful_empty_text():
    passed, _, error = WorkflowExecutor._evaluate_ocr_text_condition(
        _node(text_operator="not_contains", keywords=["禁止"], keyword_logic="any"),
        _result(""),
    )

    assert passed is True
    assert error is None


def test_ocr_failure_never_passes_not_contains():
    passed, _, error = WorkflowExecutor._evaluate_ocr_text_condition(
        _node(text_operator="not_contains", keywords=["禁止"], keyword_logic="any"),
        _result("", checked=False, error="model unavailable"),
    )

    assert passed is False
    assert error == "model unavailable"


def test_ocr_regex_condition_and_invalid_regex():
    passed, _, error = WorkflowExecutor._evaluate_ocr_text_condition(
        _node(pattern_type="regex", regex_pattern=r"车牌[A-Z0-9]{6}", keywords=[]),
        _result("车辆\n车牌ABC123"),
    )
    assert passed is True
    assert error is None

    passed, _, error = WorkflowExecutor._evaluate_ocr_text_condition(
        _node(pattern_type="regex", regex_pattern="[", keywords=[]),
        _result("anything"),
    )
    assert passed is False
    assert "正则表达式无效" in error
