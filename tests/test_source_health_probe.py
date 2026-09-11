import threading
from types import SimpleNamespace

import requests

from app.core import algorithm_test_service as service
from app.core import database_models, ringbuffer


def _source(*, status="RUNNING"):
    return SimpleNamespace(
        id=7,
        name="大门",
        status=status,
        enabled=True,
        source_fps=5,
        source_decode_width=640,
        source_decode_height=360,
        analysis_buffer_name="video_buffer.analysis.gate",
    )


def test_worker_collects_live_source_health(monkeypatch):
    closed = []

    class FakeBuffer:
        def __init__(self, **kwargs):
            assert kwargs["name"] == "video_buffer.analysis.gate"
            self.shm = SimpleNamespace(_name="/video_buffer.analysis.gate")

        def get_health_status(self):
            return {
                "last_write_time": 100.0,
                "time_since_last_frame": 0.2,
                "consecutive_errors": 0,
                "frame_count": 12,
                "is_healthy": True,
            }

        def close(self):
            closed.append(True)

    monkeypatch.setattr(database_models.VideoSource, "get_by_id", lambda _id: _source())
    monkeypatch.setattr(ringbuffer, "VideoRingBuffer", FakeBuffer)
    monkeypatch.setattr(service, "_untrack_attached_shared_memory", lambda _buffer: None)

    body, status = service.collect_source_health(7)

    assert status == 200
    assert body["health_state"] == "healthy"
    assert body["time_since_last_frame"] == 0.2
    assert body["probed_at"] > 0
    assert closed == [True]


def test_worker_does_not_mark_stopped_source_unhealthy_when_buffer_is_absent(
    monkeypatch,
):
    class MissingBuffer:
        def __init__(self, **_kwargs):
            raise FileNotFoundError

    monkeypatch.setattr(
        database_models.VideoSource,
        "get_by_id",
        lambda _id: _source(status="STOPPED"),
    )
    monkeypatch.setattr(ringbuffer, "VideoRingBuffer", MissingBuffer)

    body, status = service.collect_source_health(7)

    assert status == 200
    assert body["is_healthy"] is None
    assert body["health_state"] == "inactive"
    assert body["error"] is None


def test_internal_http_service_exposes_source_health(monkeypatch):
    monkeypatch.setattr(
        service,
        "collect_source_health",
        lambda source_id: ({"success": True, "source_id": source_id}, 200),
    )
    server = service._AlgorithmTestHttpServer(("127.0.0.1", 0), object())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = requests.get(
            f"http://127.0.0.1:{server.server_port}/v1/video-sources/7/health",
            headers={"X-Algorithm-Test-Token": service.ALGORITHM_TEST_WORKER_TOKEN},
            timeout=2,
        )
        assert response.status_code == 200
        assert response.json() == {"success": True, "source_id": 7}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_source_health_client_requests_a_fresh_worker_sample(monkeypatch):
    calls = []

    def fetch(path, timeout):
        calls.append((path, timeout))
        return {"success": True, "source_id": 7}, 200

    monkeypatch.setattr(service, "_fetch_worker_json", fetch)

    body, status = service.fetch_source_health(7)

    assert status == 200
    assert body["source_id"] == 7
    assert calls == [("/v1/video-sources/7/health", 3)]
