from types import SimpleNamespace

import numpy as np
import pytest

from app.core.ocr_algorithm_config import normalize_ocr_algorithm_config
from app.core.ocr_backend import build_ocr_model_spec, build_paddleocr_options
from app.plugins.ocr_algorithm import OCRAlgorithm, normalize_ocr_output


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


class _FakeResolver:
    def _get_model_info(self, model_id):
        return {"path": f"/models/{model_id}"}


def test_ocr_algorithm_uses_shared_backend_when_enabled(monkeypatch):
    captured = {}

    class FakeShared:
        def __init__(self, spec, ocr_config):
            captured["spec"] = spec
            captured["ocr_config"] = ocr_config
            self.client = SimpleNamespace(model_key="ocr-key")

        def infer(self, frame):
            assert frame.shape == (48, 64, 3)
            return [
                {"text": "低分", "confidence": 0.2},
                {"text": "安全出口", "confidence": 0.96},
            ], [], {"shared_inference": True, "model_key": "ocr-key", "device": "auto"}

        def cleanup(self):
            captured["cleaned"] = True

    monkeypatch.setattr("app.plugins.ocr_algorithm.shared_ocr_client_enabled", lambda: True)
    monkeypatch.setattr("app.plugins.ocr_algorithm.get_model_resolver", lambda: _FakeResolver())
    monkeypatch.setattr("app.plugins.ocr_algorithm.SharedOCRBackend", FakeShared)

    def unexpected_local(*_args, **_kwargs):
        raise AssertionError("shared OCR must not load Paddle locally")

    monkeypatch.setattr("app.plugins.ocr_algorithm.PaddleOCRBackend", unexpected_local)

    algorithm = OCRAlgorithm({
        "ocr_config": {
            "detection_model_id": 1,
            "recognition_model_id": 2,
            "device": "auto",
            "recognition_score_threshold": 0.5,
        },
        "pixel_format": "rgb24",
    })
    result = algorithm.process(np.zeros((48, 64, 3), dtype=np.uint8))
    algorithm.cleanup()

    assert captured["spec"]["backend"] == "paddleocr"
    assert captured["spec"]["recognition_model_id"] == 2
    assert result["metadata"]["shared_inference"] is True
    assert result["metadata"]["model_key"] == "ocr-key"
    assert result["metadata"]["full_text"] == "安全出口"
    assert [item["text"] for item in result["detections"]] == ["安全出口"]
    assert captured["cleaned"] is True


def test_ocr_algorithm_loads_local_backend_when_shared_disabled(monkeypatch):
    class FakeLocal:
        def __init__(self, detection_path, recognition_path, ocr_config):
            self.pipeline = object()
            self.detection_path = detection_path
            self.recognition_path = recognition_path
            self.ocr_config = ocr_config

        def infer(self, _frame):
            return [{"text": "本地", "confidence": 0.88}], [], {"device": "cpu"}

        def cleanup(self):
            return None

    monkeypatch.setattr("app.plugins.ocr_algorithm.shared_ocr_client_enabled", lambda: False)
    monkeypatch.setattr("app.plugins.ocr_algorithm.get_model_resolver", lambda: _FakeResolver())
    monkeypatch.setattr("app.plugins.ocr_algorithm.PaddleOCRBackend", FakeLocal)

    algorithm = OCRAlgorithm({
        "ocr_config": {
            "detection_model_id": 1,
            "recognition_model_id": 2,
            "device": "cpu",
        },
        "pixel_format": "rgb24",
    })
    result = algorithm.process(np.zeros((32, 32, 3), dtype=np.uint8))

    assert result["metadata"]["shared_inference"] is False
    assert result["detections"][0]["text"] == "本地"
    assert algorithm.pipeline is not None


def test_ocr_algorithm_reports_overload_as_unchecked(monkeypatch):
    class FakeShared:
        def __init__(self, spec, ocr_config):
            self.client = SimpleNamespace(model_key="ocr-key")

        def infer(self, _frame):
            return [], [], {"shared_inference": True, "overloaded": True}

        def cleanup(self):
            return None

    monkeypatch.setattr("app.plugins.ocr_algorithm.shared_ocr_client_enabled", lambda: True)
    monkeypatch.setattr("app.plugins.ocr_algorithm.get_model_resolver", lambda: _FakeResolver())
    monkeypatch.setattr("app.plugins.ocr_algorithm.SharedOCRBackend", FakeShared)

    algorithm = OCRAlgorithm({
        "ocr_config": {"detection_model_id": 1, "recognition_model_id": 2},
        "pixel_format": "rgb24",
    })
    result = algorithm.process(np.zeros((16, 16, 3), dtype=np.uint8))

    assert result["detections"] == []
    assert result["metadata"]["ocr_checked"] is False
    assert result["metadata"]["error"] == "shared_inference_overloaded"


def test_paddleocr_options_omit_auto_device_and_score_threshold(tmp_path):
    detection = tmp_path / "det"
    recognition = tmp_path / "rec"
    detection.mkdir()
    recognition.mkdir()
    options = build_paddleocr_options(
        str(detection),
        str(recognition),
        {
            "device": "auto",
            "recognition_score_threshold": 0.8,
            "recognition_batch_size": 2,
        },
    )
    spec = build_ocr_model_spec(
        detection_model_id=1,
        detection_path=str(detection),
        recognition_model_id=2,
        recognition_path=str(recognition),
        ocr_config={"device": "gpu", "recognition_score_threshold": 0.8},
    )

    assert "device" not in options
    assert options["text_recognition_batch_size"] == 2
    assert spec["backend_config"]["device"] == "gpu"
    assert "recognition_score_threshold" not in spec["backend_config"]
