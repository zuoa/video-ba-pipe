import zipfile

import pytest

from app.web.api.models import _extract_ocr_archive


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
