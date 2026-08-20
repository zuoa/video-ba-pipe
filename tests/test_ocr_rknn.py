from types import MethodType, SimpleNamespace

import numpy as np
import pytest

from app.core.ocr_backend import RKNNOcrBackend
from app.core.ocr_rknn import _config_value


def test_rknn_config_value_preserves_explicit_zero():
    config = {"detection_threshold": 0, "box_threshold": 0.0}

    assert _config_value(config, "detection_threshold", 0.3) == 0
    assert _config_value(config, "box_threshold", 0.6) == 0.0
    assert _config_value({}, "detection_threshold", 0.3) == 0.3


def test_rknn_backend_rejects_recognition_batching_before_loading_models():
    with pytest.raises(ValueError, match="recognition_batch_size 必须为 1"):
        RKNNOcrBackend(
            "/missing/det.rknn",
            "/missing/rec.rknn",
            {"recognition_batch_size": 2},
        )


def test_rknn_backend_passes_explicit_zero_thresholds_to_db(monkeypatch):
    backend = RKNNOcrBackend.__new__(RKNNOcrBackend)
    backend.ocr_config = {
        "detection_threshold": 0,
        "box_threshold": 0,
    }
    backend.device = "auto"
    backend.rknn_input_format = "rgb"
    backend.det_width = 32
    backend.det_height = 32
    backend.character_dict_path = "/built-in/ppocr_keys_v1.txt"
    backend._det_entry = SimpleNamespace(name="det")
    backend._rec_entry = SimpleNamespace(name="rec")
    captured = {}

    def fake_infer_runtime(_self, _entry, _image):
        return [np.zeros((1, 1, 32, 32), dtype=np.float32)]

    def fake_db_detect_polygons(_output, _width, _height, **kwargs):
        captured.update(kwargs)
        return []

    backend._infer_runtime = MethodType(fake_infer_runtime, backend)
    monkeypatch.setattr("app.core.ocr_rknn.db_detect_polygons", fake_db_detect_polygons)

    detections, _details, _metadata = backend.infer(
        np.zeros((64, 64, 3), dtype=np.uint8)
    )

    assert detections == []
    assert captured["thresh"] == 0.0
    assert captured["box_thresh"] == 0.0


def test_rknn_ocr_backend_runs_synthetic_det_and_rec_without_npu():
    backend = RKNNOcrBackend.__new__(RKNNOcrBackend)
    backend.ocr_config = {
        "detection_threshold": 0.3,
        "box_threshold": 0.5,
        "unclip_ratio": 1.5,
    }
    backend.device = "auto"
    backend.rknn_input_format = "rgb"
    backend.det_width = 32
    backend.det_height = 32
    backend.rec_width = 32
    backend.rec_height = 8
    backend.characters = ["A", "B"]
    backend.character_dict_path = "/built-in/ppocr_keys_v1.txt"
    backend._det_entry = SimpleNamespace(name="det")
    backend._rec_entry = SimpleNamespace(name="rec")

    heatmap = np.zeros((1, 1, 32, 32), dtype=np.float32)
    heatmap[:, :, 8:24, 6:26] = 0.95
    logits = np.array(
        [[[0.1, 0.8, 0.1], [0.9, 0.05, 0.05], [0.1, 0.1, 0.8]]],
        dtype=np.float32,
    )
    calls = []

    def fake_infer_runtime(self, entry, image):
        calls.append((entry.name, image.shape, image.dtype))
        return [heatmap] if entry is self._det_entry else [logits]

    backend._infer_runtime = MethodType(fake_infer_runtime, backend)

    detections, details, metadata = backend.infer(
        np.zeros((64, 64, 3), dtype=np.uint8)
    )

    assert [call[0] for call in calls] == ["det", "rec"]
    assert calls[0][1] == (32, 32, 3)
    assert calls[1][1] == (8, 32, 3)
    assert calls[1][2] == np.float32
    assert detections == details
    assert detections[0]["text"] == "AB"
    assert detections[0]["confidence"] > 0.7
    assert metadata["backend"] == "rknn_ocr"
    assert metadata["full_text"] == "AB"
