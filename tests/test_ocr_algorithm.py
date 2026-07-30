from types import SimpleNamespace

import numpy as np
import pytest

from app.core.ocr_algorithm_config import normalize_ocr_algorithm_config
from app.plugins.ocr_algorithm import normalize_ocr_output


class _Result:
    def __init__(self, payload):
        self.json = {"res": payload}


def test_normalize_ocr_output_builds_standard_detections_and_filters_scores():
    result = normalize_ocr_output(
        [
            _Result(
                {
                    "rec_texts": ["安全出口", "低分文字"],
                    "rec_scores": np.array([0.96, 0.2]),
                    "rec_polys": np.array(
                        [
                            [[10, 20], [100, 20], [100, 60], [10, 60]],
                            [[1, 2], [3, 2], [3, 4], [1, 4]],
                        ]
                    ),
                }
            )
        ],
        score_threshold=0.5,
    )

    assert result["full_text"] == "安全出口"
    assert result["detections"] == [
        {
            "text": "安全出口",
            "label_name": "安全出口",
            "class_name": "text",
            "confidence": pytest.approx(0.96),
            "box": [10.0, 20.0, 100.0, 60.0],
            "bbox": [10.0, 20.0, 100.0, 60.0],
            "polygon": [[10.0, 20.0], [100.0, 20.0], [100.0, 60.0], [10.0, 60.0]],
        }
    ]


def test_ocr_config_requires_correct_model_roles(monkeypatch):
    models = {
        1: SimpleNamespace(id=1, name="det", enabled=True, model_type="OCR", model_role="detection"),
        2: SimpleNamespace(id=2, name="rec", enabled=True, model_type="OCR", model_role="recognition"),
    }
    monkeypatch.setattr(
        "app.core.ocr_algorithm_config.MLModel.get_by_id",
        lambda model_id: models[int(model_id)],
    )

    config = normalize_ocr_algorithm_config(
        {
            "detection_model_id": 1,
            "recognition_model_id": 2,
            "device": "cpu",
            "recognition_score_threshold": 0.6,
        }
    )

    assert config["detection_model_id"] == 1
    assert config["recognition_model_id"] == 2
    assert config["device"] == "cpu"
    assert config["recognition_score_threshold"] == pytest.approx(0.6)


def test_ocr_config_rejects_swapped_roles(monkeypatch):
    model = SimpleNamespace(id=1, name="rec", enabled=True, model_type="OCR", model_role="recognition")
    monkeypatch.setattr("app.core.ocr_algorithm_config.MLModel.get_by_id", lambda _model_id: model)

    with pytest.raises(ValueError, match="不是 OCR detection"):
        normalize_ocr_algorithm_config({"detection_model_id": 1, "recognition_model_id": 1})


def test_ocr_config_partial_update_preserves_existing_models(monkeypatch):
    models = {
        1: SimpleNamespace(id=1, name="det", enabled=True, model_type="OCR", model_role="detection"),
        2: SimpleNamespace(id=2, name="rec", enabled=True, model_type="OCR", model_role="recognition"),
    }
    monkeypatch.setattr(
        "app.core.ocr_algorithm_config.MLModel.get_by_id",
        lambda model_id: models[int(model_id)],
    )

    updated = normalize_ocr_algorithm_config(
        {"device": "cpu"},
        current={
            "detection_model_id": 1,
            "recognition_model_id": 2,
            "device": "auto",
            "recognition_score_threshold": 0.7,
        },
    )

    assert updated["detection_model_id"] == 1
    assert updated["recognition_model_id"] == 2
    assert updated["device"] == "cpu"
    assert updated["recognition_score_threshold"] == pytest.approx(0.7)
