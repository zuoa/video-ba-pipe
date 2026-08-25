"""Worker-local algorithm test server, client, and lifecycle controller."""

from __future__ import annotations

import hmac
import json
import multiprocessing
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Tuple

import requests

from app import logger
from app.config import (
    ALGORITHM_TEST_MAX_IMAGE_BYTES,
    ALGORITHM_TEST_QUEUE_SIZE,
    ALGORITHM_TEST_TIMEOUT_SECONDS,
    ALGORITHM_TEST_WORKER_HOST,
    ALGORITHM_TEST_WORKER_PORT,
    ALGORITHM_TEST_WORKER_TOKEN,
    ALGORITHM_TEST_WORKER_URL,
    APP_DIR,
)


def _job_process_main(job: Dict[str, Any], result_queue) -> None:
    from app.core.algorithm_test_execution import (
        AlgorithmTestInputError,
        execute_algorithm_test_job,
    )
    from app.core.database_models import db

    try:
        db.connect(reuse_if_open=True)
        result_queue.put({"status": 200, "body": execute_algorithm_test_job(job)})
    except AlgorithmTestInputError as exc:
        result_queue.put(
            {"status": exc.status_code, "body": {"success": False, "error": str(exc)}}
        )
    except Exception as exc:
        logger.exception("Worker 算法测试执行失败")
        result_queue.put(
            {
                "status": 500,
                "body": {"success": False, "error": f"测试失败: {exc}"},
            }
        )
    finally:
        if not db.is_closed():
            db.close()


def _runtime_capabilities_process_main(result_queue) -> None:
    """Probe heavyweight runtimes without retaining them in the server."""
    try:
        from app.core.face_inference import runtime_capabilities
        from app.core.ocr_runtime import ocr_runtime_payload
        from app.version import get_app_version

        result_queue.put(
            {
                "status": 200,
                "body": {
                    "success": True,
                    "app_version": get_app_version(),
                    "face": runtime_capabilities(),
                    "ocr": ocr_runtime_payload(),
                },
            },
        )
    except Exception as exc:
        logger.exception("Worker 运行时能力探测失败")
        result_queue.put(
            {
                "status": 500,
                "body": {"success": False, "error": f"能力探测失败: {exc}"},
            }
        )


class AlgorithmTestJobRunner:
    """Admit a bounded number of requests and execute exactly one at a time."""

    def __init__(self, *, queue_size: int, timeout_seconds: float):
        self.timeout_seconds = float(timeout_seconds)
        self._capacity = threading.BoundedSemaphore(max(1, int(queue_size) + 1))
        self._execution_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._active_process = None
        self._capability_process = None
        self._runtime_capabilities_cache = None
        self._capability_lock = threading.Lock()
        self._closed = False
        self._mp_context = multiprocessing.get_context("spawn")

    def _stop_process(self, process) -> None:
        if process is None or not process.is_alive():
            return
        process.terminate()
        process.join(timeout=3)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)

    def run(self, job: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        if self._closed:
            return {"success": False, "error": "推理 Worker 正在关闭"}, 503
        if not self._capacity.acquire(blocking=False):
            return {
                "success": False,
                "error": "算法测试队列已满，请稍后重试",
            }, 429

        deadline = time.monotonic() + self.timeout_seconds
        acquired_execution = False
        result_queue = None
        process = None
        try:
            remaining = deadline - time.monotonic()
            acquired_execution = self._execution_lock.acquire(timeout=max(0.0, remaining))
            if not acquired_execution:
                return {"success": False, "error": "算法测试等待超时"}, 504
            if self._closed:
                return {"success": False, "error": "推理 Worker 正在关闭"}, 503

            result_queue = self._mp_context.Queue(maxsize=1)
            process = self._mp_context.Process(
                target=_job_process_main,
                args=(job, result_queue),
                name="algorithm-test-job",
            )
            with self._process_lock:
                self._active_process = process
            process.start()

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._stop_process(process)
                    return {"success": False, "error": "算法测试执行超时"}, 504
                try:
                    response = result_queue.get(timeout=min(0.2, remaining))
                    process.join(timeout=2)
                    if process.is_alive():
                        self._stop_process(process)
                    return response["body"], int(response["status"])
                except queue.Empty:
                    if not process.is_alive():
                        process.join(timeout=1)
                        return {
                            "success": False,
                            "error": f"算法测试进程异常退出，退出码: {process.exitcode}",
                        }, 500
        except Exception as exc:
            self._stop_process(process)
            logger.exception("Worker 算法测试调度失败")
            return {"success": False, "error": f"测试调度失败: {exc}"}, 500
        finally:
            with self._process_lock:
                if self._active_process is process:
                    self._active_process = None
            if result_queue is not None:
                result_queue.close()
                result_queue.join_thread()
            if acquired_execution:
                self._execution_lock.release()
            self._capacity.release()

    def close(self) -> None:
        self._closed = True
        with self._process_lock:
            process = self._active_process
            capability_process = self._capability_process
        self._stop_process(process)
        self._stop_process(capability_process)

    def runtime_capabilities(self) -> Tuple[Dict[str, Any], int]:
        """Return a cached probe produced by a disposable child process."""
        with self._capability_lock:
            if self._runtime_capabilities_cache is not None:
                return dict(self._runtime_capabilities_cache), 200
            if self._closed:
                return {"success": False, "error": "推理 Worker 正在关闭"}, 503

            result_queue = self._mp_context.Queue(maxsize=1)
            process = self._mp_context.Process(
                target=_runtime_capabilities_process_main,
                args=(result_queue,),
                name="runtime-capability-probe",
            )
            with self._process_lock:
                self._capability_process = process
            try:
                process.start()
                deadline = time.monotonic() + 60
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._stop_process(process)
                        return {"success": False, "error": "运行时能力探测超时"}, 504
                    try:
                        response = result_queue.get(timeout=min(0.2, remaining))
                        break
                    except queue.Empty:
                        if not process.is_alive():
                            process.join(timeout=1)
                            return {
                                "success": False,
                                "error": (
                                    "运行时能力探测进程异常退出，退出码: "
                                    f"{process.exitcode}"
                                ),
                            }, 500
                process.join(timeout=2)
                if process.is_alive():
                    self._stop_process(process)
                body = response.get("body") or {}
                status = int(response.get("status") or 500)
                if (
                    status == 200
                    and isinstance(body.get("face"), dict)
                    and isinstance(body.get("ocr"), dict)
                ):
                    self._runtime_capabilities_cache = dict(body)
                return body, status
            except Exception as exc:
                self._stop_process(process)
                logger.exception("Worker 人脸运行时能力调度失败")
                return {"success": False, "error": f"能力探测失败: {exc}"}, 500
            finally:
                with self._process_lock:
                    if self._capability_process is process:
                        self._capability_process = None
                result_queue.close()
                result_queue.join_thread()


class _AlgorithmTestHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, runner: AlgorithmTestJobRunner):
        super().__init__(address, _AlgorithmTestRequestHandler)
        self.runner = runner


class _AlgorithmTestRequestHandler(BaseHTTPRequestHandler):
    server: _AlgorithmTestHttpServer

    def log_message(self, message: str, *args) -> None:
        logger.debug("Algorithm test service: " + message, *args)

    def _write_json(self, status: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Algorithm-Test-Token", "")
        return hmac.compare_digest(supplied, ALGORITHM_TEST_WORKER_TOKEN)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._authorized():
            self._write_json(401, {"success": False, "error": "内部鉴权失败"})
            return
        if self.path == "/v1/capabilities/runtime":
            body, status = self.server.runner.runtime_capabilities()
            self._write_json(status, body)
            return
        if self.path == "/v1/metrics/accelerators":
            from app.core.system_metrics import collect_accelerator_metrics

            self._write_json(
                200,
                {
                    "success": True,
                    "data": collect_accelerator_metrics(),
                },
            )
            return
        if self.path != "/health":
            self._write_json(404, {"success": False, "error": "接口不存在"})
            return
        self._write_json(200, {"success": True, "status": "ready"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._authorized():
            self._write_json(401, {"success": False, "error": "内部鉴权失败"})
            return
        if self.path != "/v1/tests":
            self._write_json(404, {"success": False, "error": "接口不存在"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        max_payload_bytes = int(ALGORITHM_TEST_MAX_IMAGE_BYTES * 1.5) + 1024 * 1024
        if content_length <= 0:
            self._write_json(400, {"success": False, "error": "请求内容为空"})
            return
        if content_length > max_payload_bytes:
            self._write_json(413, {"success": False, "error": "测试图片过大"})
            return
        try:
            job = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._write_json(400, {"success": False, "error": "请求格式不正确"})
            return
        if not isinstance(job, dict):
            self._write_json(400, {"success": False, "error": "请求格式不正确"})
            return
        body, status = self.server.runner.run(job)
        self._write_json(status, body)


def run_algorithm_test_server() -> None:
    runner = AlgorithmTestJobRunner(
        queue_size=ALGORITHM_TEST_QUEUE_SIZE,
        timeout_seconds=ALGORITHM_TEST_TIMEOUT_SECONDS,
    )
    server = _AlgorithmTestHttpServer(
        (ALGORITHM_TEST_WORKER_HOST, ALGORITHM_TEST_WORKER_PORT), runner
    )

    def stop_server(*_args) -> None:
        runner.close()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop_server)
    signal.signal(signal.SIGTERM, stop_server)
    logger.info(
        "Worker 算法测试服务已启动: %s:%s queue=%s timeout=%ss",
        ALGORITHM_TEST_WORKER_HOST,
        ALGORITHM_TEST_WORKER_PORT,
        ALGORITHM_TEST_QUEUE_SIZE,
        ALGORITHM_TEST_TIMEOUT_SECONDS,
    )
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        runner.close()
        server.server_close()


def submit_algorithm_test(job: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    try:
        response = requests.post(
            f"{ALGORITHM_TEST_WORKER_URL}/v1/tests",
            json=job,
            headers={"X-Algorithm-Test-Token": ALGORITHM_TEST_WORKER_TOKEN},
            timeout=ALGORITHM_TEST_TIMEOUT_SECONDS + 5,
        )
    except requests.Timeout:
        return {"success": False, "error": "推理 Worker 响应超时"}, 504
    except requests.RequestException as exc:
        logger.warning("无法连接 Worker 算法测试服务: %s", exc)
        return {"success": False, "error": "推理 Worker 不可用"}, 503
    try:
        body = response.json()
    except ValueError:
        return {"success": False, "error": "推理 Worker 返回了无效响应"}, 502
    if not isinstance(body, dict):
        return {"success": False, "error": "推理 Worker 返回了无效响应"}, 502
    return body, response.status_code


_runtime_capability_cache_lock = threading.Lock()
_runtime_capability_cache: Tuple[float, Dict[str, Any], int] | None = None


def _fetch_worker_json(path: str, timeout: float) -> Tuple[Dict[str, Any], int]:
    try:
        response = requests.get(
            f"{ALGORITHM_TEST_WORKER_URL}{path}",
            headers={"X-Algorithm-Test-Token": ALGORITHM_TEST_WORKER_TOKEN},
            timeout=timeout,
        )
    except requests.Timeout:
        return {"success": False, "error": "推理 Worker 响应超时"}, 504
    except requests.RequestException as exc:
        logger.warning("无法连接 Worker 能力探测服务: %s", exc)
        return {"success": False, "error": "推理 Worker 不可用"}, 503
    try:
        body = response.json()
    except ValueError:
        return {"success": False, "error": "推理 Worker 返回了无效响应"}, 502
    if not isinstance(body, dict):
        return {"success": False, "error": "推理 Worker 返回了无效响应"}, 502
    return body, response.status_code


def fetch_runtime_capabilities() -> Tuple[Dict[str, Any], int]:
    """Query and briefly cache the worker's inference runtime capabilities."""
    global _runtime_capability_cache
    now = time.monotonic()
    with _runtime_capability_cache_lock:
        cached = _runtime_capability_cache
        if cached is not None and now - cached[0] <= (60 if cached[2] == 200 else 2):
            return dict(cached[1]), cached[2]

    body, status = _fetch_worker_json("/v1/capabilities/runtime", 65)
    with _runtime_capability_cache_lock:
        _runtime_capability_cache = (time.monotonic(), dict(body), status)
    return body, status


def fetch_face_runtime_capabilities() -> Tuple[Dict[str, Any], int]:
    body, status = fetch_runtime_capabilities()
    capabilities = body.get("face")
    if status == 200 and isinstance(capabilities, dict):
        return {
            "success": True,
            "app_version": body.get("app_version"),
            "capabilities": capabilities,
        }, 200
    return body, status


def fetch_ocr_runtime_capabilities() -> Tuple[Dict[str, Any], int]:
    body, status = fetch_runtime_capabilities()
    capabilities = body.get("ocr")
    if status == 200 and isinstance(capabilities, dict):
        return {
            "success": True,
            "app_version": body.get("app_version"),
            **capabilities,
        }, 200
    return body, status


def fetch_accelerator_metrics() -> Tuple[Dict[str, Any], int]:
    return _fetch_worker_json("/v1/metrics/accelerators", 5)


class AlgorithmTestServiceController:
    """Own the worker-container subprocess that serves interactive tests."""

    def __init__(self, environment: Dict[str, str] | None = None):
        self.process = None
        self.environment = dict(environment or os.environ)
        self._next_restart_at = 0.0

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, timeout: float = 15.0) -> bool:
        if self.is_running:
            return True
        if time.monotonic() < self._next_restart_at:
            return False
        entry = os.path.join(APP_DIR, "algorithm_test_worker.py")
        self.process = subprocess.Popen(
            [sys.executable, "-u", entry],
            cwd=APP_DIR,
            env=self.environment,
        )
        deadline = time.monotonic() + timeout
        health_url = f"http://127.0.0.1:{ALGORITHM_TEST_WORKER_PORT}/health"
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.stop()
                self._next_restart_at = time.monotonic() + 30.0
                return False
            try:
                response = requests.get(
                    health_url,
                    headers={"X-Algorithm-Test-Token": ALGORITHM_TEST_WORKER_TOKEN},
                    timeout=1,
                )
                if response.status_code == 200:
                    self._next_restart_at = 0.0
                    return True
            except requests.RequestException:
                time.sleep(0.1)
        self.stop()
        self._next_restart_at = time.monotonic() + 30.0
        return False

    def ensure_running(self) -> bool:
        return self.is_running or self.start(timeout=3.0)

    def replace_environment(self, environment: Dict[str, str]) -> bool:
        self.stop()
        self.environment = dict(environment)
        self._next_restart_at = 0.0
        return self.start()

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.process = None
