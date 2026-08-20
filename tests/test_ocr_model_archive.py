import io
import zipfile

import pytest
from flask import Flask

import app.web.api.models as models_api
from app.web.api.models import (
    _default_ocr_input_shape,
    _extract_ocr_archive,
    _materialize_ocr_upload,
)


def test_extract_ocr_archive_normalizes_single_wrapper_directory(tmp_path):
    archive_path = tmp_path / "det.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("PP-OCR-det/inference.json", "{}")
        archive.writestr("PP-OCR-det/inference.pdiparams", b"weights")

    model_dir, extracted_size = _extract_ocr_archive(str(archive_path))

    assert not archive_path.exists()
    assert (tmp_path / "det" / "inference.json").exists()
    assert model_dir == str(tmp_path / "det")
    assert extracted_size > 0


def test_extract_ocr_archive_uses_model_directory_when_archive_has_siblings(tmp_path):
    archive_path = tmp_path / "rec.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("README.md", "documentation")
        archive.writestr("LICENSE", "license")
        archive.writestr("PP-OCR-rec/inference.json", "{}")
        archive.writestr("PP-OCR-rec/inference.pdiparams", b"weights")

    model_dir, _ = _extract_ocr_archive(str(archive_path))

    assert model_dir == str(tmp_path / "rec")
    assert (tmp_path / "rec" / "inference.json").exists()
    assert not (tmp_path / "rec" / "README.md").exists()


def test_extract_ocr_archive_rejects_directory_traversal(tmp_path):
    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../inference.json", "{}")
        archive.writestr("inference.pdiparams", b"weights")

    with pytest.raises(ValueError, match="目录穿越"):
        _extract_ocr_archive(str(archive_path))

    assert not (tmp_path.parent / "inference.json").exists()


def test_extract_ocr_archive_requires_paddle_inference_markers(tmp_path):
    archive_path = tmp_path / "invalid.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("README.txt", "not a model")

    with pytest.raises(ValueError, match="PaddleOCR"):
        _extract_ocr_archive(str(archive_path))


def test_extract_ocr_archive_accepts_single_rknn_and_keys(tmp_path):
    archive_path = tmp_path / "det.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("README.md", "docs")
        archive.writestr("models/ppocrv4_det.rknn", b"rknn-weights")
        archive.writestr("models/ppocr_keys_v1.txt", "A\nB\n")

    model_dir, _ = _extract_ocr_archive(str(archive_path))

    assert (tmp_path / "det" / "ppocrv4_det.rknn").exists()
    assert (tmp_path / "det" / "ppocr_keys_v1.txt").exists()
    assert model_dir == str(tmp_path / "det")


def test_materialize_ocr_upload_keeps_raw_rknn_file(tmp_path):
    model_path = tmp_path / "det.rknn"
    model_path.write_bytes(b"weights")

    stored, size, framework = _materialize_ocr_upload(str(model_path))

    assert stored == str(model_path)
    assert size == 7
    assert framework == "rknn"


def test_rknn_ocr_upload_defaults_role_specific_input_shape():
    assert _default_ocr_input_shape("detection", "rknn") == "480x480"
    assert _default_ocr_input_shape("recognition", "rknn") == "48x320"
    assert _default_ocr_input_shape("detection", "paddleocr") == ""


def test_extract_ocr_archive_rejects_mixed_model_families(tmp_path):
    archive_path = tmp_path / "mixed.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("paddle/inference.json", "{}")
        archive.writestr("paddle/inference.pdiparams", b"weights")
        archive.writestr("rknn/model.rknn", b"weights")

    with pytest.raises(ValueError, match="不能同时包含"):
        _extract_ocr_archive(str(archive_path))


def test_upload_endpoint_keeps_ocr_type_role_and_rknn_framework(tmp_path, monkeypatch):
    captured = {}

    def fake_upsert(**kwargs):
        captured.update(kwargs)
        return type("Model", (), kwargs)()

    monkeypatch.setattr(models_api, "MODELS_ROOT", str(tmp_path))
    monkeypatch.setattr(models_api, "_upsert_model_record", fake_upsert)
    monkeypatch.setattr(
        models_api,
        "serialize_model",
        lambda model: {
            "model_type": model.model_type,
            "model_role": model.model_role,
            "framework": model.framework,
            "input_shape": model.input_shape,
        },
    )
    monkeypatch.setattr(models_api, "current_username", lambda _default: "tester")

    flask_app = Flask(__name__)
    with flask_app.test_request_context(
        "/api/models/",
        method="POST",
        data={
            "name": "ppocr-det-rknn",
            "model_type": "OCR",
            "model_role": "detection",
            "framework": "paddleocr",
            "file": (io.BytesIO(b"rknn-model"), "ppocrv4_det.rknn"),
        },
        content_type="multipart/form-data",
    ):
        response = models_api.upload_model()

    assert response.status_code == 200
    assert captured["model_type"] == "OCR"
    assert captured["model_role"] == "detection"
    assert captured["framework"] == "rknn"
    assert captured["input_shape"] == "480x480"
