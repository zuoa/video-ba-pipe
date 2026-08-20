from types import SimpleNamespace

import numpy as np
import pytest

from app.core.ocr_algorithm_config import (
    normalize_ocr_algorithm_config,
    validate_ocr_crop_node_config,
)
from app.core.ocr_backend import build_ocr_model_spec, build_paddleocr_options
from app.plugins.ocr_algorithm import OCRAlgorithm, normalize_ocr_output
from app.user_scripts.common.roi import expand_and_clip_box, remap_detections_to_full_frame


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
    assert config["input_mode"] == "frame"
    assert config["expand_ratio"] == pytest.approx(0.1)
    assert config["max_candidates"] == 8
    assert config["min_crop_side"] == 8
    assert config["upstream_class_filter"] == []


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
    assert updated["rknn_input_format"] == "rgb"
    assert updated["rknn_core_mask"] == "auto"
    assert "character_dict_path" not in updated


def test_ocr_config_rejects_mixed_paddle_and_rknn(monkeypatch):
    models = {
        1: SimpleNamespace(
            id=1,
            name="det",
            enabled=True,
            model_type="OCR",
            model_role="detection",
            file_path="/models/det.rknn",
            framework="rknn",
        ),
        2: SimpleNamespace(
            id=2,
            name="rec",
            enabled=True,
            model_type="OCR",
            model_role="recognition",
            file_path="/models/rec",
            framework="paddleocr",
        ),
    }
    monkeypatch.setattr(
        "app.core.ocr_algorithm_config.MLModel.get_by_id",
        lambda model_id: models[int(model_id)],
    )

    with pytest.raises(ValueError, match="同属"):
        normalize_ocr_algorithm_config({"detection_model_id": 1, "recognition_model_id": 2})


def test_ocr_config_requires_auto_device_for_rknn(monkeypatch):
    models = {
        1: SimpleNamespace(
            id=1, name="det", enabled=True, model_type="OCR", model_role="detection",
            file_path="/models/det.rknn", framework="rknn",
        ),
        2: SimpleNamespace(
            id=2, name="rec", enabled=True, model_type="OCR", model_role="recognition",
            file_path="/models/rec.rknn", framework="rknn",
        ),
    }
    monkeypatch.setattr(
        "app.core.ocr_algorithm_config.MLModel.get_by_id",
        lambda model_id: models[int(model_id)],
    )

    with pytest.raises(ValueError, match="auto"):
        normalize_ocr_algorithm_config({
            "detection_model_id": 1,
            "recognition_model_id": 2,
            "device": "cpu",
        })


@pytest.mark.parametrize("batch_size", [0, 2])
def test_ocr_config_rejects_invalid_rknn_recognition_batch_size(monkeypatch, batch_size):
    models = {
        1: SimpleNamespace(
            id=1, name="det", enabled=True, model_type="OCR", model_role="detection",
            file_path="/models/det.rknn", framework="rknn",
        ),
        2: SimpleNamespace(
            id=2, name="rec", enabled=True, model_type="OCR", model_role="recognition",
            file_path="/models/rec.rknn", framework="rknn",
        ),
    }
    monkeypatch.setattr(
        "app.core.ocr_algorithm_config.MLModel.get_by_id",
        lambda model_id: models[int(model_id)],
    )

    with pytest.raises(ValueError, match="recognition_batch_size"):
        normalize_ocr_algorithm_config({
            "detection_model_id": 1,
            "recognition_model_id": 2,
            "recognition_batch_size": batch_size,
        })


def test_build_ocr_model_spec_selects_rknn_ocr(tmp_path):
    detection = tmp_path / "det.rknn"
    recognition = tmp_path / "rec.rknn"
    detection.write_bytes(b"det")
    recognition.write_bytes(b"rec")

    spec = build_ocr_model_spec(
        detection_model_id=11,
        detection_path=str(detection),
        recognition_model_id=12,
        recognition_path=str(recognition),
        ocr_config={"device": "auto", "rknn_core_mask": "core_0"},
        detection_info={"framework": "rknn", "input_shape": "480x480"},
        recognition_info={"framework": "rknn", "input_shape": "320x48"},
    )

    assert spec["backend"] == "rknn_ocr"
    assert spec["framework"] == "rknn"
    assert spec["input_width"] == 480
    assert spec["input_height"] == 480
    assert spec["recognition_input_shape"] == (320, 48)
    assert spec["backend_config"]["rknn_core_mask"] == "core_0"


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


class _RknnResolver:
    def _get_model_info(self, model_id):
        return {
            "path": f"/models/{model_id}.rknn",
            "framework": "rknn",
            "model_type": "OCR",
        }


def test_ocr_algorithm_loads_rknn_backend_when_shared_disabled(monkeypatch):
    captured = {}

    class FakeRknn:
        def __init__(self, detection_path, recognition_path, ocr_config, **kwargs):
            captured["detection_path"] = detection_path
            captured["recognition_path"] = recognition_path
            captured["kwargs"] = kwargs
            self.pipeline = None

        def infer(self, _frame):
            return [{"text": "RK文字", "confidence": 0.91}], [], {"device": "auto", "backend": "rknn_ocr"}

        def cleanup(self):
            captured["cleaned"] = True

    monkeypatch.setattr("app.plugins.ocr_algorithm.shared_ocr_client_enabled", lambda: False)
    monkeypatch.setattr("app.plugins.ocr_algorithm.get_model_resolver", lambda: _RknnResolver())
    monkeypatch.setattr("app.plugins.ocr_algorithm.RKNNOcrBackend", FakeRknn)

    def unexpected_paddle(*_args, **_kwargs):
        raise AssertionError("RKNN OCR must not load Paddle locally")

    monkeypatch.setattr("app.plugins.ocr_algorithm.PaddleOCRBackend", unexpected_paddle)

    algorithm = OCRAlgorithm({
        "ocr_config": {
            "detection_model_id": 1,
            "recognition_model_id": 2,
            "device": "auto",
        },
        "pixel_format": "rgb24",
    })
    result = algorithm.process(np.zeros((32, 32, 3), dtype=np.uint8))
    algorithm.cleanup()

    assert captured["detection_path"].endswith("1.rknn")
    assert captured["recognition_path"].endswith("2.rknn")
    assert result["detections"][0]["text"] == "RK文字"
    assert captured["cleaned"] is True


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


def _patch_ocr_backend(monkeypatch, infer=None):
    captured = {"frames": []}

    class FakeShared:
        def __init__(self, spec, ocr_config):
            captured["spec"] = spec
            captured["ocr_config"] = ocr_config
            captured["instance"] = self
            self.client = SimpleNamespace(model_key="ocr-key")
            self.frames = captured["frames"]

        def infer(self, frame):
            self.frames.append(np.ascontiguousarray(frame.copy()))
            if infer is not None:
                return infer(frame, len(self.frames) - 1)
            height, width = frame.shape[:2]
            return (
                [{
                    "text": "安全",
                    "confidence": 0.96,
                    "box": [0, 0, width, height],
                    "bbox": [0, 0, width, height],
                    "polygon": [[0, 0], [width, 0], [width, height], [0, height]],
                }],
                [],
                {"shared_inference": True, "model_key": "ocr-key"},
            )

        def cleanup(self):
            captured["cleaned"] = True

    monkeypatch.setattr("app.plugins.ocr_algorithm.shared_ocr_client_enabled", lambda: True)
    monkeypatch.setattr("app.plugins.ocr_algorithm.get_model_resolver", lambda: _FakeResolver())
    monkeypatch.setattr("app.plugins.ocr_algorithm.SharedOCRBackend", FakeShared)

    def unexpected_local(*_args, **_kwargs):
        raise AssertionError("shared OCR must not load Paddle locally")

    monkeypatch.setattr("app.plugins.ocr_algorithm.PaddleOCRBackend", unexpected_local)
    return captured


def _make_crop_ocr_algorithm(**extra):
    config = {
        "ocr_config": {
            "detection_model_id": 1,
            "recognition_model_id": 2,
            "recognition_score_threshold": 0.5,
        },
        "pixel_format": "rgb24",
        "input_mode": "upstream_crops",
        "expand_ratio": 0.0,
        "min_crop_side": 1,
        "max_candidates": 8,
    }
    config.update(extra)
    return OCRAlgorithm(config)


def test_expand_and_clip_box_expands_each_side_then_clips():
    assert expand_and_clip_box([10, 10, 30, 30], (100, 80, 3), 0.1) == [8, 8, 32, 32]
    assert expand_and_clip_box([0, 0, 10, 10], (100, 80, 3), 0.1) == [0, 0, 11, 11]
    assert expand_and_clip_box([70, 90, 80, 100], (100, 80, 3), 0.1) == [69, 89, 80, 100]
    assert expand_and_clip_box(None, (100, 80, 3), 0.1) is None


def test_remap_keeps_polygon_only_detection_and_synthesizes_box():
    remapped = remap_detections_to_full_frame(
        [{"text": "横幅", "polygon": [[1, 2], [5, 2], [5, 6], [1, 6]]}],
        [10, 20, 40, 50],
    )

    assert remapped == [{
        "text": "横幅",
        "polygon": [[11.0, 22.0], [15.0, 22.0], [15.0, 26.0], [11.0, 26.0]],
        "box": [11.0, 22.0, 15.0, 26.0],
        "bbox": [11.0, 22.0, 15.0, 26.0],
    }]


def test_validate_ocr_crop_node_config_accepts_overlay_without_model_ids():
    overlay = validate_ocr_crop_node_config({
        "input_mode": "upstream_crops",
        "expand_ratio": 0.2,
        "max_candidates": 4,
        "min_crop_side": 16,
        "upstream_class_filter": ["banner"],
        "unknown": 1,
        "detection_model_id": 99,
    })

    assert overlay == {
        "input_mode": "upstream_crops",
        "expand_ratio": pytest.approx(0.2),
        "max_candidates": 4,
        "min_crop_side": 16,
        "upstream_class_filter": ["banner"],
    }
    assert validate_ocr_crop_node_config({}) == {}
    assert validate_ocr_crop_node_config(None) == {}


def test_validate_ocr_crop_node_config_rejects_bad_expand_ratio():
    with pytest.raises(ValueError, match="expand_ratio"):
        validate_ocr_crop_node_config({"expand_ratio": 1.5})


def test_validate_ocr_crop_node_config_rejects_invalid_input_mode_and_bounds():
    with pytest.raises(ValueError, match="input_mode"):
        validate_ocr_crop_node_config({"input_mode": "always"})
    with pytest.raises(ValueError, match="max_candidates"):
        validate_ocr_crop_node_config({"max_candidates": 0})
    with pytest.raises(ValueError, match="max_candidates"):
        validate_ocr_crop_node_config({"max_candidates": 33})
    with pytest.raises(ValueError, match="min_crop_side"):
        validate_ocr_crop_node_config({"min_crop_side": 0})
    with pytest.raises(ValueError, match="upstream_class_filter"):
        validate_ocr_crop_node_config({"upstream_class_filter": "banner"})


def test_ocr_crop_infers_each_upstream_box(monkeypatch):
    captured = _patch_ocr_backend(monkeypatch)
    algorithm = _make_crop_ocr_algorithm()
    frame = np.zeros((80, 100, 3), dtype=np.uint8)
    frame[10:30, 10:30] = (10, 20, 30)
    frame[10:40, 40:70] = (40, 50, 60)

    result = algorithm.process(
        frame,
        upstream_results={
            "yolo_1": {
                "detections": [
                    {"box": [10, 10, 30, 30], "confidence": 0.9, "class_name": "banner"},
                    {"box": [40, 10, 70, 40], "confidence": 0.8, "class_name": "banner"},
                ]
            }
        },
    )

    assert len(captured["frames"]) == 2
    assert captured["frames"][0].shape == (20, 20, 3)
    assert captured["frames"][1].shape == (30, 30, 3)
    assert captured["frames"][0][0, 0].tolist() == [10, 20, 30]
    assert captured["frames"][1][0, 0].tolist() == [40, 50, 60]
    assert result["metadata"]["input_kind"] == "crops"
    assert result["metadata"]["input_count"] == 2
    assert result["metadata"]["ocr_checked"] is True
    assert [item["parent_node_id"] for item in result["detections"]] == ["yolo_1", "yolo_1"]
    assert result["detections"][0]["parent_box"] == [10, 10, 30, 30]
    assert result["detections"][1]["parent_box"] == [40, 10, 70, 40]


def test_ocr_crop_remaps_box_and_polygon_to_full_frame(monkeypatch):
    def infer(frame, _index):
        return (
            [{
                "text": "出口",
                "confidence": 0.91,
                "box": [1, 2, 10, 12],
                "bbox": [1, 2, 10, 12],
                "polygon": [[1, 2], [10, 2], [10, 12], [1, 12]],
            }],
            [],
            {"shared_inference": True},
        )

    _patch_ocr_backend(monkeypatch, infer=infer)
    algorithm = _make_crop_ocr_algorithm()
    result = algorithm.process(
        np.zeros((80, 100, 3), dtype=np.uint8),
        upstream_results={
            "yolo_1": {"detections": [{"box": [20, 10, 60, 50], "confidence": 0.95}]}
        },
    )

    detection = result["detections"][0]
    assert detection["box"] == [21.0, 12.0, 30.0, 22.0]
    assert detection["bbox"] == [21.0, 12.0, 30.0, 22.0]
    assert detection["polygon"] == [[21.0, 12.0], [30.0, 12.0], [30.0, 22.0], [21.0, 22.0]]
    assert detection["parent_box"] == [20, 10, 60, 50]
    assert detection["source_crop_index"] == 0


def test_ocr_crop_keeps_polygon_only_ocr_output(monkeypatch):
    def infer(_frame, _index):
        return (
            [{"text": "纯多边形", "confidence": 0.88, "polygon": [[1, 1], [5, 1], [5, 4], [1, 4]]}],
            [],
            {"shared_inference": True},
        )

    _patch_ocr_backend(monkeypatch, infer=infer)
    algorithm = _make_crop_ocr_algorithm()
    result = algorithm.process(
        np.zeros((40, 40, 3), dtype=np.uint8),
        upstream_results={"yolo_1": {"detections": [{"bbox": [10, 8, 30, 28], "confidence": 0.7}]}},
    )

    detection = result["detections"][0]
    assert detection["polygon"] == [[11.0, 9.0], [15.0, 9.0], [15.0, 12.0], [11.0, 12.0]]
    assert detection["box"] == [11.0, 9.0, 15.0, 12.0]
    assert detection["bbox"] == [11.0, 9.0, 15.0, 12.0]


def test_ocr_crop_uses_upstream_polygon_only_detection(monkeypatch):
    captured = _patch_ocr_backend(monkeypatch)
    algorithm = _make_crop_ocr_algorithm()
    result = algorithm.process(
        np.zeros((40, 40, 3), dtype=np.uint8),
        upstream_results={
            "yolo_1": {
                "detections": [{
                    "polygon": [[10, 10], [30, 10], [30, 26], [10, 26]],
                    "confidence": 0.8,
                    "class_name": "banner",
                }]
            }
        },
    )

    assert len(captured["frames"]) == 1
    assert captured["frames"][0].shape == (16, 20, 3)
    assert result["detections"][0]["parent_box"] == [10, 10, 30, 26]


def test_ocr_crop_none_upstream_falls_back_to_full_frame(monkeypatch):
    captured = _patch_ocr_backend(monkeypatch)
    algorithm = _make_crop_ocr_algorithm()
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    result = algorithm.process(frame)

    assert len(captured["frames"]) == 1
    assert captured["frames"][0].shape == (48, 64, 3)
    assert result["metadata"]["input_fallback"] == "frame"
    assert result["metadata"]["input_kind"] == "frame"
    assert result["metadata"]["ocr_checked"] is True


def test_ocr_crop_empty_dict_skips_without_full_frame(monkeypatch):
    captured = _patch_ocr_backend(monkeypatch)
    algorithm = _make_crop_ocr_algorithm()
    result = algorithm.process(np.zeros((32, 32, 3), dtype=np.uint8), upstream_results={})

    assert captured["frames"] == []
    assert result["detections"] == []
    assert result["metadata"]["ocr_checked"] is False
    assert result["metadata"]["execution_state"] == "skipped"
    assert result["metadata"]["reason_code"] == "upstream_empty"
    assert result["metadata"]["skipped"] is True
    assert result["metadata"]["input_kind"] == "crops"
    assert result["metadata"]["input_count"] == 0


def test_ocr_crop_truncates_to_max_candidates(monkeypatch):
    captured = _patch_ocr_backend(monkeypatch)
    algorithm = _make_crop_ocr_algorithm(max_candidates=2)
    result = algorithm.process(
        np.zeros((80, 80, 3), dtype=np.uint8),
        upstream_results={
            "yolo_a": {
                "detections": [
                    {"box": [0, 0, 10, 10], "confidence": 0.4, "class_name": "banner"},
                    {"box": [20, 0, 40, 10], "confidence": 0.9, "class_name": "banner"},
                ]
            },
            "yolo_b": {
                "detections": [
                    {"box": [0, 20, 12, 40], "confidence": 0.8, "class_name": "banner"},
                ]
            },
        },
    )

    assert len(captured["frames"]) == 2
    assert captured["frames"][0].shape == (10, 20, 3)
    assert captured["frames"][1].shape == (20, 12, 3)
    assert result["metadata"]["pruned_count"] == 1
    assert result["metadata"]["input_count"] == 2
    assert [item["parent_node_id"] for item in result["detections"]] == ["yolo_a", "yolo_b"]


def test_ocr_crop_applies_upstream_class_filter(monkeypatch):
    captured = _patch_ocr_backend(monkeypatch)
    algorithm = _make_crop_ocr_algorithm(upstream_class_filter=["banner"])
    result = algorithm.process(
        np.zeros((40, 40, 3), dtype=np.uint8),
        upstream_results={
            "yolo_1": {
                "detections": [
                    {"box": [0, 0, 10, 10], "confidence": 0.99, "class_name": "person"},
                    {"box": [20, 0, 30, 8], "confidence": 0.5, "label": "banner"},
                    {"box": [0, 20, 8, 30], "confidence": 0.7, "label_name": "other"},
                ]
            }
        },
    )

    assert len(captured["frames"]) == 1
    assert captured["frames"][0].shape == (8, 10, 3)
    assert result["detections"][0]["parent_box"] == [20, 0, 30, 8]


def test_ocr_crop_drops_tiny_boxes_after_expand(monkeypatch):
    captured = _patch_ocr_backend(monkeypatch)
    algorithm = _make_crop_ocr_algorithm(min_crop_side=8, expand_ratio=0.0)
    result = algorithm.process(
        np.zeros((40, 40, 3), dtype=np.uint8),
        upstream_results={
            "yolo_1": {"detections": [{"box": [10, 10, 12, 14], "confidence": 0.9}]}
        },
    )

    assert captured["frames"] == []
    assert result["metadata"]["execution_state"] == "skipped"
    assert result["metadata"]["reason_code"] == "upstream_empty"
    assert result["metadata"]["ocr_checked"] is False


def test_ocr_crop_partial_overload_keeps_successful_text(monkeypatch):
    def infer(_frame, index):
        if index == 0:
            return [], [], {"shared_inference": True, "overloaded": True}
        return (
            [{"text": "成功", "confidence": 0.93, "box": [0, 0, 4, 4]}],
            [],
            {"shared_inference": True},
        )

    captured = _patch_ocr_backend(monkeypatch, infer=infer)
    algorithm = _make_crop_ocr_algorithm()
    result = algorithm.process(
        np.zeros((40, 40, 3), dtype=np.uint8),
        upstream_results={
            "yolo_1": {
                "detections": [
                    {"box": [0, 0, 10, 10], "confidence": 0.95},
                    {"box": [20, 0, 30, 10], "confidence": 0.5},
                ]
            }
        },
    )

    assert len(captured["frames"]) == 2
    assert result["metadata"]["ocr_checked"] is True
    assert result["metadata"]["execution_state"] == "degraded"
    assert result["metadata"]["successful_inferences"] == 1
    assert result["metadata"]["failed_inferences"] == 1
    assert result["metadata"]["full_text"] == "成功"
    assert [item["text"] for item in result["detections"]] == ["成功"]
    assert result["detections"][0]["source_crop_index"] == 1


def test_ocr_input_mode_frame_ignores_upstream(monkeypatch):
    captured = _patch_ocr_backend(monkeypatch)
    algorithm = _make_crop_ocr_algorithm(input_mode="frame")
    frame = np.zeros((24, 32, 3), dtype=np.uint8)
    result = algorithm.process(
        frame,
        upstream_results={
            "yolo_1": {"detections": [{"box": [1, 1, 8, 8], "confidence": 0.99}]}
        },
    )

    assert len(captured["frames"]) == 1
    assert captured["frames"][0].shape == (24, 32, 3)
    assert result["metadata"]["input_kind"] == "frame"
    assert "input_fallback" not in result["metadata"]


def test_ocr_crop_does_not_apply_roi_pre_mask(monkeypatch):
    captured = _patch_ocr_backend(monkeypatch)
    algorithm = _make_crop_ocr_algorithm()
    frame = np.zeros((40, 40, 3), dtype=np.uint8)
    frame[10:30, 10:30] = (7, 8, 9)
    algorithm.process(
        frame,
        roi_regions=[{
            "polygon": [[0, 0], [2, 0], [2, 2], [0, 2]],
            "mode": "pre_mask",
        }],
        upstream_results={
            "yolo_1": {"detections": [{"box": [10, 10, 30, 30], "confidence": 0.8}]}
        },
    )

    assert captured["frames"][0][0, 0].tolist() == [7, 8, 9]
