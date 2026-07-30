import pytest
from flask import Flask

import app.web.api.models as models_api
from app.web.api.models import (
    _build_huggingface_download_url,
    _infer_model_meta,
    _normalize_hf_endpoint,
    _parse_boolean,
)


def test_build_huggingface_url_uses_official_endpoint_by_default():
    url = _build_huggingface_download_url(
        "owner/model",
        "weights/model.onnx",
        revision="release/v1",
    )

    assert url == (
        "https://huggingface.co/owner/model/resolve/"
        "release%2Fv1/weights/model.onnx?download=true"
    )


def test_build_huggingface_url_uses_configured_mirror():
    url = _build_huggingface_download_url(
        "owner/model",
        "模型 weights/model v1.onnx",
        use_mirror=True,
        mirror_endpoint="https://hf-mirror.example/base/",
    )

    assert url == (
        "https://hf-mirror.example/base/owner/model/resolve/main/"
        "%E6%A8%A1%E5%9E%8B%20weights/model%20v1.onnx?download=true"
    )


def test_safetensors_metadata_is_inferred_as_pytorch():
    assert _infer_model_meta("model.safetensors") == ("PyTorch", "pytorch")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("yes", True),
        ("OFF", False),
        (1, True),
        (0, False),
        (None, True),
    ],
)
def test_parse_boolean(value, expected):
    assert _parse_boolean(value, default=True) is expected


def test_parse_boolean_rejects_ambiguous_values():
    with pytest.raises(ValueError, match="布尔值"):
        _parse_boolean("enabled")


@pytest.mark.parametrize(
    ("repo_id", "repo_filename"),
    [
        ("owner/repo/extra", "model.onnx"),
        ("owner\\repo", "model.onnx"),
        ("owner/repo", "../model.onnx"),
        ("owner/repo", "/model.onnx"),
        ("owner/repo", r"weights\model.onnx"),
    ],
)
def test_build_huggingface_url_rejects_invalid_paths(repo_id, repo_filename):
    with pytest.raises(ValueError):
        _build_huggingface_download_url(repo_id, repo_filename)


def test_normalize_hf_endpoint_rejects_credentials_or_query():
    with pytest.raises(ValueError, match="下载端点"):
        _normalize_hf_endpoint("https://user:password@example.com")

    with pytest.raises(ValueError, match="下载端点"):
        _normalize_hf_endpoint("https://example.com?token=secret")


class _FakeDownloadResponse:
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        assert chunk_size == 1024 * 1024
        yield b"model-bytes"


@pytest.mark.parametrize(
    ("use_mirror", "expected_endpoint"),
    [
        (False, "https://huggingface.co/"),
        (True, "https://hf-mirror.com/"),
    ],
)
def test_import_endpoint_downloads_from_selected_huggingface_source(
    tmp_path,
    monkeypatch,
    use_mirror,
    expected_endpoint,
):
    request_details = {}

    def fake_get(url, **kwargs):
        request_details["url"] = url
        request_details.update(kwargs)
        return _FakeDownloadResponse()

    monkeypatch.setattr(models_api, "MODELS_ROOT", str(tmp_path))
    monkeypatch.setattr(models_api.requests, "get", fake_get)
    monkeypatch.setattr(
        models_api,
        "_upsert_model_record",
        lambda **kwargs: type("Model", (), kwargs)(),
    )
    monkeypatch.setattr(
        models_api,
        "serialize_model",
        lambda model: {"name": model.name, "filename": model.filename},
    )
    monkeypatch.setattr(models_api, "current_username", lambda default: "tester")

    flask_app = Flask(__name__)
    with flask_app.test_request_context(
        "/api/models/import",
        method="POST",
        json={
            "source_type": "huggingface",
            "repo_id": "owner/model",
            "filename": "weights/model.onnx",
            "revision": "main",
            "hf_token": "hf_test",
            "use_hf_mirror": use_mirror,
            "model_type": "ONNX",
            "framework": "onnx",
        },
    ):
        response = models_api.import_model_from_url()

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert request_details["url"].startswith(expected_endpoint)
    assert request_details["headers"]["Authorization"] == "Bearer hf_test"
    assert request_details["stream"] is True
    assert request_details["allow_redirects"] is True
    assert (tmp_path / "onnx" / "model.onnx").read_bytes() == b"model-bytes"
