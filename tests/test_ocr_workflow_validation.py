from types import SimpleNamespace

from app.web.api.workflows import _validate_ocr_text_conditions


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
