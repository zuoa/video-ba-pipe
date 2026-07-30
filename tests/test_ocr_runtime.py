from app.core.ocr_runtime import get_ocr_runtime_status


def test_ocr_runtime_reports_missing_dependencies(monkeypatch):
    monkeypatch.setattr("app.core.ocr_runtime.importlib.util.find_spec", lambda _name: None)

    available, error = get_ocr_runtime_status()

    assert available is False
    assert "paddlepaddle" in error
    assert "paddleocr" in error


def test_ocr_runtime_reports_available_when_both_modules_exist(monkeypatch):
    monkeypatch.setattr("app.core.ocr_runtime.importlib.util.find_spec", lambda _name: object())

    available, error = get_ocr_runtime_status()

    assert available is True
    assert error is None
