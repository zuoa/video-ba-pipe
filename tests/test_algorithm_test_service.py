import ast
import os
import queue
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


def test_saved_algorithm_preview_overrides_untrusted_execution_fields():
    class FakeAlgorithm:
        id = 8
        name = "人脸预览"
        script_path = "templates/face_recognizer.py"
        created_by = "algorithm-owner"
        config_dict = {
            "created_by": "victim",
            "_execution_owner": "victim",
            "_execution_role": "admin",
            "_preview_mode": False,
            "save_events": True,
            "source_id": 99,
            "workflow_id": 88,
        }
        ext_config = {"algorithm_type": "script"}

    _algorithm_type, config = execution._saved_algorithm_config(
        FakeAlgorithm(),
        execution_owner="requester",
        execution_role="user",
    )

    assert config["created_by"] == "requester"
    assert config["_execution_owner"] == "requester"
    assert config["_execution_role"] == "user"
    assert config["_preview_mode"] is True
    assert config["save_events"] is False
    assert config["source_id"] == 0
    assert config["workflow_id"] is None


def test_cascade_preview_exposes_step_diagnostics(monkeypatch):
    config = {
        "version": 2,
        "nodes": [
            {
                "id": "output", "type": "output", "label": "未戴安全帽",
                "color": "#ff4d4f",
            },
        ],
    }

    class FakeInstance:
        def process(self, _image):
            return {
                "detections": [],
                "metadata": {
                    "stage_debug": [{
                        "node_id": "helmet",
                        "node_name": "检测安全帽",
                        "status": "ok",
                        "execution_state": "not_matched",
                        "reason_code": "not_matched",
                        "reason": "已执行 1 次，但没有检测到目标",
                        "upstream_node_id": "head",
                        "upstream_node_name": "检测头部",
                        "input_kind": "crops",
                        "input_count": 1,
                        "successful_inferences": 1,
                        "failed_inferences": 0,
                        "detection_count": 0,
                        "forwarded_count": 0,
                        "pruned_count": 0,
                        "error_count": 0,
                        "errors": [],
                        "detections": [],
                        "crop_boxes": [],
                        "inference_time_ms": 3.2,
                    }],
                    "context_evaluations": [],
                    "diagnosis": {"state": "not_matched", "summary": "安全帽条件不成立"},
                },
            }

        def visualize(self, image, _detections, label_color):
            assert label_color in {"#1677ff", "#ff4d4f"}
            return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        def cleanup(self):
            pass

    monkeypatch.setattr(execution, "normalize_cascade_algorithm_config", lambda _config: config)
    monkeypatch.setattr(execution, "_create_algorithm_instance", lambda *_args: FakeInstance())

    result = execution.execute_cascade_preview(config, _jpeg_bytes())

    node = result["node_previews"][0]
    assert node["execution_state"] == "not_matched"
    assert node["successful_inferences"] == 1
    assert node["reason"] == "已执行 1 次，但没有检测到目标"
    assert result["diagnosis"] == {"state": "not_matched", "summary": "安全帽条件不成立"}


def test_algorithm_test_job_rejects_invalid_image_payload():
    try:
        execution.execute_algorithm_test_job(
            {"kind": "saved_algorithm", "algorithm_id": 1, "image_base64": "not-base64"}
        )
    except execution.AlgorithmTestInputError as exc:
        assert "图片数据格式" in str(exc)
    else:
        raise AssertionError("invalid base64 should be rejected")


def test_face_enrollment_batch_reuses_one_loaded_runtime(monkeypatch):
    from app.core import face_inference

    bundle = type('Bundle', (), {
        'id': 7,
        'enabled': True,
        'embedding_dimension': 3,
        'contract_id': 'batch-v1',
    })()
    monkeypatch.setattr(execution.FaceModelBundle, 'get_by_id', lambda _id: bundle)
    calls = {'created': 0, 'inferred': 0, 'cleaned': 0}

    class FakeRuntime:
        pipeline = type('Pipeline', (), {'backend': 'fake'})()

        def __init__(self, actual_bundle, backend, config):
            assert actual_bundle is bundle
            assert backend == 'auto'
            assert config['min_face_size'] == 80
            calls['created'] += 1

        def infer(self, image):
            assert image.shape[2] == 3
            calls['inferred'] += 1
            return (
                [{'box': [0, 0, 10, 10]}],
                [{
                    'embedding': np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
                    'quality': {'accepted': True, 'score': 0.9},
                    'box': [0, 0, 10, 10],
                }],
                {'backend': 'fake'},
            )

        def cleanup(self):
            calls['cleaned'] += 1

    monkeypatch.setattr(face_inference, 'FaceWorkerBackend', FakeRuntime)

    result = execution.execute_face_enrollment_batch(
        bundle.id, [_jpeg_bytes(), _jpeg_bytes()]
    )

    assert result['success'] is True
    assert len(result['results']) == 2
    assert all(item['success'] for item in result['results'])
    assert calls == {'created': 1, 'inferred': 2, 'cleaned': 1}


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


def test_internal_http_service_reports_runtime_capabilities():
    class FakeRunner:
        def runtime_capabilities(self):
            return {
                "success": True,
                "app_version": "test-version",
                "face": {
                    "machine": "x86_64",
                    "available_runtimes": ["onnxruntime-cuda", "torchscript"],
                },
                "ocr": {
                    "available": True,
                    "error": None,
                    "backends": ["paddleocr"],
                },
            }, 200

    server = service._AlgorithmTestHttpServer(("127.0.0.1", 0), FakeRunner())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/v1/capabilities/runtime"
    try:
        unauthorized = requests.get(url, timeout=2)
        assert unauthorized.status_code == 401

        authorized = requests.get(
            url,
            headers={"X-Algorithm-Test-Token": service.ALGORITHM_TEST_WORKER_TOKEN},
            timeout=2,
        )
        assert authorized.status_code == 200
        assert authorized.json()["face"]["available_runtimes"] == [
            "onnxruntime-cuda",
            "torchscript",
        ]
        assert authorized.json()["ocr"]["backends"] == ["paddleocr"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_face_capability_client_maps_unavailable_worker_to_503(monkeypatch):
    def fail(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(service.requests, "get", fail)
    monkeypatch.setattr(service, "_runtime_capability_cache", None)

    body, status = service.fetch_face_runtime_capabilities()

    assert status == 503
    assert body == {"success": False, "error": "推理 Worker 不可用"}


def test_accelerator_metrics_endpoint_uses_worker_hardware(monkeypatch):
    class FakeRunner:
        pass

    monkeypatch.setattr(
        "app.core.system_metrics.collect_accelerator_metrics",
        lambda: {
            "timestamp": 123,
            "gpus": [{"index": 0, "usage_percent": 42.0}],
            "npus": [],
        },
    )
    server = service._AlgorithmTestHttpServer(("127.0.0.1", 0), FakeRunner())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/v1/metrics/accelerators"
    try:
        response = requests.get(
            url,
            headers={"X-Algorithm-Test-Token": service.ALGORITHM_TEST_WORKER_TOKEN},
            timeout=2,
        )
        assert response.status_code == 200
        assert response.json()["data"]["gpus"][0]["usage_percent"] == 42.0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


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


def test_runtime_capability_probe_returns_when_child_exits_without_result():
    class EmptyResultQueue:
        def __init__(self):
            self.timeouts = []

        def get(self, timeout):
            self.timeouts.append(timeout)
            raise queue.Empty

        def close(self):
            pass

        def join_thread(self):
            pass

    class ExitedProcess:
        exitcode = 137

        def start(self):
            pass

        def is_alive(self):
            return False

        def join(self, timeout=None):
            pass

    result_queue = EmptyResultQueue()
    process = ExitedProcess()

    class FakeContext:
        def Queue(self, maxsize):
            assert maxsize == 1
            return result_queue

        def Process(self, **kwargs):
            assert kwargs["name"] == "runtime-capability-probe"
            return process

    runner = service.AlgorithmTestJobRunner(queue_size=0, timeout_seconds=1)
    runner._mp_context = FakeContext()

    body, status = runner.runtime_capabilities()

    assert status == 500
    assert body["error"] == "运行时能力探测进程异常退出，退出码: 137"
    assert result_queue.timeouts == [0.2]


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
        "deploy/compose/templates/cpu.yml": "api",
        "deploy/compose/templates/jetson.yml": "api",
        "deploy/compose/templates/rknn.yml": "api",
        "deploy/compose/templates/cuda.yml": "app",
    }
    for filename, api_service in variants.items():
        compose = yaml.safe_load((root / filename).read_text(encoding="utf-8"))
        api_environment = compose["services"][api_service]["environment"]
        worker_environment = compose["services"]["worker"]["environment"]
        expected = "${ALGORITHM_TEST_WORKER_URL:-http://worker:5010}"
        assert api_environment["ALGORITHM_TEST_WORKER_URL"] == expected
        assert worker_environment["ALGORITHM_TEST_WORKER_URL"] == expected
        assert "5010:5010" not in compose["services"]["worker"].get("ports", [])
