from types import SimpleNamespace

import app.core.mediamtx_client as mediamtx_module
from app.core.mediamtx_client import MediaMTXClient


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def _configure(monkeypatch):
    monkeypatch.setattr(mediamtx_module, "cfg", SimpleNamespace(
        MEDIAMTX_ENABLED=True,
        MEDIAMTX_API_HOST="mediamtx",
        MEDIAMTX_API_PORT=9997,
        MEDIAMTX_API_USER="preview-api",
        MEDIAMTX_API_PASSWORD="secret",
    ))


def test_add_path_uses_v3_contract_and_basic_auth(monkeypatch):
    _configure(monkeypatch)
    responses = iter([
        _Response(200, {"items": []}),
        _Response(200, {"items": []}),
        _Response(200),
    ])
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return next(responses)

    monkeypatch.setattr(mediamtx_module.requests, "request", fake_request)
    client = MediaMTXClient()

    assert client.register_path("camera-1", "rtsp://camera/live") is True
    assert [call[:2] for call in calls] == [
        ("GET", "http://mediamtx:9997/v3/paths/list"),
        ("GET", "http://mediamtx:9997/v3/config/paths/list"),
        ("POST", "http://mediamtx:9997/v3/config/paths/add/camera-1"),
    ]
    assert all(call[2]["auth"] == ("preview-api", "secret") for call in calls)
    assert calls[-1][2]["json"] == {
        "source": "rtsp://camera/live",
        "rtspTransport": "tcp",
        "sourceOnDemand": True,
    }


def test_existing_path_is_patched_and_list_items_are_arrays(monkeypatch):
    _configure(monkeypatch)
    responses = iter([
        _Response(200, {"items": []}),
        _Response(200, {"items": [{"name": "camera-1"}]}),
        _Response(200),
    ])
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return next(responses)

    monkeypatch.setattr(mediamtx_module.requests, "request", fake_request)
    client = MediaMTXClient()

    assert client.register_path("camera-1", "rtsp://camera/new") is True
    assert calls[-1][0] == "PATCH"
    assert calls[-1][1].endswith("/v3/config/paths/patch/camera-1")


def test_unregister_uses_v3_delete_route(monkeypatch):
    _configure(monkeypatch)
    responses = iter([_Response(200, {"items": []}), _Response(404)])
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return next(responses)

    monkeypatch.setattr(mediamtx_module.requests, "request", fake_request)
    client = MediaMTXClient()

    assert client.unregister_path("camera-1") is True
    assert calls[-1][0] == "DELETE"
    assert calls[-1][1].endswith("/v3/config/paths/delete/camera-1")


def test_rtsp_read_url_uses_internal_relay_and_escapes_path(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(mediamtx_module.cfg, "MEDIAMTX_RTSP_PORT", 8554, raising=False)
    client = MediaMTXClient()
    monkeypatch.setattr(client, "is_available", lambda: True)
    monkeypatch.setattr(client, "list_path_names", lambda: ["camera floor/1"])

    assert client.rtsp_read_url("camera floor/1") == (
        "rtsp://mediamtx:8554/camera%20floor%2F1"
    )


def test_rtsp_read_url_rejects_stale_cached_path_after_restart(monkeypatch):
    _configure(monkeypatch)
    client = MediaMTXClient()
    client._registered_paths.add("camera-1")
    monkeypatch.setattr(client, "is_available", lambda: True)
    monkeypatch.setattr(
        client,
        "_request",
        lambda *_args, **_kwargs: _Response(200, {"items": []}),
    )

    assert client.rtsp_read_url("camera-1") is None
    assert client._registered_paths == set()
