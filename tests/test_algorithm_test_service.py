import ast
import os
import subprocess
import sys
import threading
from pathlib import Path

import cv2
import numpy as np
import requests

from app.core import algorithm_test_execution as execution
from app.core import algorithm_test_service as service


def _jpeg_bytes(width=32, height=24):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def test_worker_service_defaults_to_loopback():
    environment = os.environ.copy()
    environment.pop("ALGORITHM_TEST_WORKER_HOST", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.config import ALGORITHM_TEST_WORKER_HOST; print(ALGORITHM_TEST_WORKER_HOST)",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.stdout.strip() == "127.0.0.1"


def test_saved_algorithm_execution_returns_compatible_result_and_cleans_up(monkeypatch):
    class FakeAlgorithm:
        id = 7
        name = "测试算法"
        script_path = "templates/adaptive_yolo_detector.py"
        config_dict = {"label_name": "Person"}
        ext_config = {"algorithm_type": "script"}

    class FakeInstance:
        cleaned = False

        def process(self, image):
            assert image.shape[0] >= 640
            assert image.shape[1] >= 640
            return {
                "detections": [
                    {"box": [1, 2, 10, 12], "confidence": np.float32(0.9), "label": "Person"}
                ],
                "metadata": {"backend": "fake"},
            }

        def visualize(self, image, detections, label_color):
            assert detections
            return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        def cleanup(self):
            self.cleaned = True

    instance = FakeInstance()
    monkeypatch.setattr(execution.Algorithm, "get_by_id", lambda _algorithm_id: FakeAlgorithm())
    monkeypatch.setattr(execution, "_create_algorithm_instance", lambda *_args: instance)

    result = execution.execute_saved_algorithm_test(7, _jpeg_bytes())

    assert result["success"] is True
    assert result["detection_count"] == 1
    assert result["metadata"]["backend"] == "fake"
    assert result["result_image"].startswith("data:image/jpeg;base64,")
    assert instance.cleaned is True


def test_algorithm_test_job_rejects_invalid_image_payload():
    try:
        execution.execute_algorithm_test_job(
            {"kind": "saved_algorithm", "algorithm_id": 1, "image_base64": "not-base64"}
        )
    except execution.AlgorithmTestInputError as exc:
        assert "图片数据格式" in str(exc)
    else:
        raise AssertionError("invalid base64 should be rejected")


def test_internal_http_service_requires_token_and_forwards_job():
    received = []

    class FakeRunner:
        def run(self, job):
            received.append(job)
            return {"success": True, "detection_count": 0}, 200

    server = service._AlgorithmTestHttpServer(("127.0.0.1", 0), FakeRunner())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/v1/tests"
    try:
        unauthorized = requests.post(url, json={"kind": "saved_algorithm"}, timeout=2)
        assert unauthorized.status_code == 401

        authorized = requests.post(
            url,
            json={"kind": "saved_algorithm", "algorithm_id": 3, "image_base64": ""},
            headers={"X-Algorithm-Test-Token": service.ALGORITHM_TEST_WORKER_TOKEN},
            timeout=2,
        )
        assert authorized.status_code == 200
        assert authorized.json()["detection_count"] == 0
        assert received[0]["algorithm_id"] == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_worker_client_maps_unavailable_service_to_503(monkeypatch):
    def fail(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(service.requests, "post", fail)

    body, status = service.submit_algorithm_test({"kind": "saved_algorithm"})

    assert status == 503
    assert body == {"success": False, "error": "推理 Worker 不可用"}


def test_job_runner_rejects_full_queue():
    runner = service.AlgorithmTestJobRunner(queue_size=0, timeout_seconds=1)
    assert runner._capacity.acquire(blocking=False)
    try:
        body, status = runner.run({"kind": "saved_algorithm"})
    finally:
        runner._capacity.release()
        runner.close()

    assert status == 429
    assert "队列已满" in body["error"]


def test_job_runner_counts_queue_wait_toward_total_timeout():
    runner = service.AlgorithmTestJobRunner(queue_size=1, timeout_seconds=0.02)
    runner._execution_lock.acquire()
    try:
        body, status = runner.run({"kind": "saved_algorithm"})
    finally:
        runner._execution_lock.release()
        runner.close()

    assert status == 504
    assert "等待超时" in body["error"]


def test_public_test_routes_only_forward_and_do_not_create_models_in_api():
    webapp_path = Path(__file__).resolve().parents[1] / "app" / "web" / "webapp.py"
    tree = ast.parse(webapp_path.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for name in ("test_algorithm", "preview_cascade_algorithm"):
        calls = {
            node.func.id
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "_forward_algorithm_test" in calls
        assert "_create_algorithm_instance" not in calls


def test_compose_variants_share_worker_test_endpoint():
    import yaml

    root = Path(__file__).resolve().parents[1]
    variants = {
        "docker-compose.yml": "api",
        "docker-compose.yml.jetson": "api",
        "docker-compose.yml.rknn": "api",
        "docker-compose.yml.x86+cuda": "app",
    }
    for filename, api_service in variants.items():
        compose = yaml.safe_load((root / filename).read_text(encoding="utf-8"))
        api_environment = compose["services"][api_service]["environment"]
        worker_environment = compose["services"]["worker"]["environment"]
        expected = "${ALGORITHM_TEST_WORKER_URL:-http://worker:5010}"
        assert api_environment["ALGORITHM_TEST_WORKER_URL"] == expected
        assert worker_environment["ALGORITHM_TEST_WORKER_URL"] == expected
        assert "5010:5010" not in compose["services"]["worker"].get("ports", [])
