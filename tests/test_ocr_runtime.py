from app.core.ocr_runtime import (
    OCR_BACKEND_PADDLE,
    OCR_BACKEND_RKNN,
    get_ocr_runtime_status,
    list_ocr_backends,
    ocr_backend_family,
)


def test_ocr_runtime_reports_missing_dependencies(monkeypatch):
    monkeypatch.setattr("app.core.ocr_runtime.importlib.util.find_spec", lambda _name: None)

    available, error = get_ocr_runtime_status()

    assert available is False
    assert "paddlepaddle" in error or "rknnlite" in error
    assert list_ocr_backends() == []


def test_ocr_runtime_reports_available_when_paddle_modules_exist(monkeypatch):
    monkeypatch.setattr(
        "app.core.ocr_runtime.importlib.util.find_spec",
        lambda name: object() if name in {"paddle", "paddleocr"} else None,
    )

    available, error = get_ocr_runtime_status()

    assert available is True
    assert error is None
    assert list_ocr_backends() == [OCR_BACKEND_PADDLE]


def test_ocr_runtime_reports_available_when_rknnlite_exists(monkeypatch):
    monkeypatch.setattr(
        "app.core.ocr_runtime.importlib.util.find_spec",
        lambda name: object() if name in {"rknnlite", "rknnlite.api"} else None,
    )
    monkeypatch.setattr("app.core.ocr_runtime.importlib.import_module", lambda _name: object())

    available, error = get_ocr_runtime_status()

    assert available is True
    assert error is None
    assert OCR_BACKEND_RKNN in list_ocr_backends()


def test_ocr_runtime_required_backend_rejects_missing_family(monkeypatch):
    monkeypatch.setattr(
        "app.core.ocr_runtime.importlib.util.find_spec",
        lambda name: object() if name in {"rknnlite", "rknnlite.api"} else None,
    )
    monkeypatch.setattr("app.core.ocr_runtime.importlib.import_module", lambda _name: object())

    available, error = get_ocr_runtime_status(required_backend=OCR_BACKEND_PADDLE)

    assert available is False
    assert "PaddleOCR" in error


def test_ocr_runtime_rejects_broken_rknnlite_import(monkeypatch):
    monkeypatch.setattr(
        "app.core.ocr_runtime.importlib.util.find_spec",
        lambda name: object() if name == "rknnlite.api" else None,
    )

    def fail_import(_name):
        raise OSError("missing librknnrt.so")

    monkeypatch.setattr("app.core.ocr_runtime.importlib.import_module", fail_import)

    assert list_ocr_backends() == []


def test_ocr_backend_family_detects_rknn_path_and_framework():
    assert ocr_backend_family("/models/det.rknn", "custom") == OCR_BACKEND_RKNN
    assert ocr_backend_family("/models/det", "rknn") == OCR_BACKEND_RKNN
    assert ocr_backend_family("/models/det", "paddleocr") == OCR_BACKEND_PADDLE
