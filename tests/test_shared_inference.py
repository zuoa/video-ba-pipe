import os
import time
from types import SimpleNamespace

import app.core.shared_inference as shared_inference_module
from app.core.gpu_placement import (
    GpuDeviceSnapshot,
    GpuPlacementBroker,
    spec_requires_cuda,
)
from app.core.ocr_backend import build_ocr_model_spec
from app.core.shared_inference import (
    _ModelRegistry,
    build_face_model_spec,
    build_model_spec,
    model_key,
)


def _fake_worker(spec, base_config, request_queue, result_queue, gpu_assignment=None):
    result_queue.put({
        "kind": "worker_ready",
        "key": model_key(spec),
        "pid": os.getpid(),
    })
    while True:
        request = request_queue.get()
        if request is None:
            return
        result_queue.put({
            "kind": "result",
            "request_id": request["request_id"],
            "ok": True,
            "detections": [{"label": "fake"}],
            "details": [],
            "metadata": {"fake": True},
        })


def _slow_start_worker(spec, base_config, request_queue, result_queue, gpu_assignment=None):
    time.sleep(0.35)
    _fake_worker(spec, base_config, request_queue, result_queue, gpu_assignment)


def _failed_start_worker(spec, _base_config, _request_queue, result_queue, gpu_assignment=None):
    result_queue.put({
        "kind": "worker_start_failed",
        "key": model_key(spec),
        "error": "ImportError: missing CUDA runtime",
    })


def _gpu0_oom_worker(spec, base_config, request_queue, result_queue, gpu_assignment=None):
    if gpu_assignment and gpu_assignment.get("gpu_index") == 0:
        result_queue.put({
            "kind": "worker_start_failed",
            "key": model_key(spec),
            "pid": os.getpid(),
            "error": "OutOfMemoryError: CUDA out of memory",
            "failure_kind": "cuda_oom",
            "gpu_index": 0,
            "gpu_uuid": gpu_assignment.get("gpu_uuid"),
        })
        return
    _fake_worker(spec, base_config, request_queue, result_queue, gpu_assignment)


def _gpu0_runtime_oom_worker(spec, base_config, request_queue, result_queue, gpu_assignment=None):
    if not gpu_assignment or gpu_assignment.get("gpu_index") != 0:
        _fake_worker(spec, base_config, request_queue, result_queue, gpu_assignment)
        return
    result_queue.put({
        "kind": "worker_ready",
        "key": model_key(spec),
        "pid": os.getpid(),
        "gpu_index": 0,
        "gpu_uuid": gpu_assignment.get("gpu_uuid"),
    })
    request = request_queue.get()
    if request is None:
        return
    error = "OutOfMemoryError: CUDA out of memory"
    result_queue.put({
        "kind": "result",
        "request_id": request["request_id"],
        "ok": False,
        "error": error,
        "failure_kind": "cuda_oom",
    })
    result_queue.put({
        "kind": "worker_runtime_failed",
        "key": model_key(spec),
        "pid": os.getpid(),
        "error": error,
        "failure_kind": "cuda_oom",
        "gpu_index": 0,
        "gpu_uuid": gpu_assignment.get("gpu_uuid"),
    })


class _FakeGpuProvider:
    def devices(self):
        return [
            GpuDeviceSnapshot(0, "GPU-0", "GPU 0", 24000, 0, 24000, 0),
            GpuDeviceSnapshot(1, "GPU-1", "GPU 1", 24000, 0, 24000, 0),
        ]

    def process_used_mb(self, _gpu_uuid, _pid):
        return None

    def close(self):
        pass


class _SingleGpuProvider(_FakeGpuProvider):
    def devices(self):
        return [GpuDeviceSnapshot(0, "GPU-0", "GPU 0", 24000, 0, 24000, 0)]


def test_model_key_changes_when_model_file_changes(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"first")
    spec_a = build_model_spec(str(model), {"framework": "ultralytics"}, {"model_id": 7})
    model.write_bytes(b"second-version")
    spec_b = build_model_spec(str(model), {"framework": "ultralytics"}, {"model_id": 7})

    assert model_key(spec_a) != model_key(spec_b)


def test_paddle_resource_exhausted_error_is_cuda_oom():
    class ResourceExhaustedError(RuntimeError):
        pass

    error = ResourceExhaustedError(
        "Out of memory error on GPU 0. Cannot allocate 512MB memory."
    )

    assert shared_inference_module._is_cuda_oom(error) is True


def test_model_spec_selects_rknn_and_keys_runtime_configuration(tmp_path):
    model = tmp_path / "model.rknn"
    model.write_bytes(b"weights")
    model_info = {
        "framework": "rknn",
        "classes": {0: "person"},
        "model_postprocess": {"profile": "head_dfl", "reg_max": 16},
    }
    spec_auto = build_model_spec(
        str(model), model_info, {"model_id": 7, "rknn_core_mask": "auto"}
    )
    spec_core_0 = build_model_spec(
        str(model), model_info, {"model_id": 7, "rknn_core_mask": "core_0"}
    )

    assert spec_auto["backend"] == "rknn"
    assert spec_auto["classes"] == {"0": "person"}
    assert spec_auto["model_postprocess"]["reg_max"] == 16
    assert model_key(spec_auto) != model_key(spec_core_0)


def test_face_model_spec_exposes_selected_cuda_runtime_to_placement(monkeypatch):
    bundle = SimpleNamespace(
        id=9, version='v1', contract_id='face-v1',
    )

    def build(runtime, device=None):
        metadata = {} if device is None else {'device': device}
        artifacts = {
            'detection': SimpleNamespace(
                file_path='/models/detector.bin', file_size=10,
                artifact_sha256='a' * 64, metadata=metadata,
            ),
            'embedding': SimpleNamespace(
                file_path='/models/embedder.bin', file_size=20,
                artifact_sha256='b' * 64, metadata=metadata,
            ),
        }
        monkeypatch.setattr(
            'app.core.face_inference.select_bundle_artifacts',
            lambda *_args, **_kwargs: (runtime, artifacts, {}),
        )
        return build_face_model_spec(bundle)

    for runtime in ('onnxruntime-cuda', 'tensorrt', 'torchscript'):
        spec = build(runtime)
        assert spec['backend'] == 'face_pipeline'
        assert spec['requires_cuda'] is True
        assert spec_requires_cuda(spec) is True

    cpu_torch = build('torchscript', device='cpu')
    assert cpu_torch['requires_cuda'] is False
    assert spec_requires_cuda(cpu_torch) is False
    cpu_onnx = build('onnxruntime')
    assert cpu_onnx['requires_cuda'] is False
    assert spec_requires_cuda(cpu_onnx) is False


def test_model_key_normalizes_auto_and_explicit_rknn_backend(tmp_path):
    model = tmp_path / "model.rknn"
    model.write_bytes(b"weights")
    model_info = {"framework": "rknn"}

    automatic = build_model_spec(
        str(model), model_info, {"model_id": 7, "backend": "auto"}
    )
    explicit = build_model_spec(
        str(model), model_info, {"model_id": 7, "backend": "rknn"}
    )

    assert automatic["backend"] == explicit["backend"] == "rknn"
    assert "backend" not in automatic["backend_config"]
    assert "backend" not in explicit["backend_config"]
    assert model_key(automatic) == model_key(explicit)


def _ocr_spec(tmp_path, *, device="auto", recognition_name="rec.bin", extra_config=None):
    detection = tmp_path / "det.bin"
    recognition = tmp_path / recognition_name
    if not detection.exists():
        detection.write_bytes(b"det-weights")
    if not recognition.exists():
        recognition.write_bytes(b"rec-weights")
    config = {"device": device, "recognition_score_threshold": 0.9}
    if extra_config:
        config.update(extra_config)
    return build_ocr_model_spec(
        detection_model_id=11,
        detection_path=str(detection),
        recognition_model_id=12,
        recognition_path=str(recognition),
        ocr_config=config,
    )


def test_ocr_spec_reuses_one_worker_for_same_det_rec_pair(tmp_path):
    spec = _ocr_spec(tmp_path)
    other_threshold = _ocr_spec(
        tmp_path, extra_config={"recognition_score_threshold": 0.1}
    )
    registry = _ModelRegistry(queue_size=2, idle_seconds=60, worker_target=_fake_worker)
    try:
        first = registry.acquire(spec, {})
        second = registry.acquire(other_threshold, {})

        assert first["model_key"] == second["model_key"]
        assert model_key(spec) == model_key(other_threshold)
        stats = registry.stats()
        assert stats["model_count"] == 1
        assert stats["models"][0]["references"] == 2
        assert stats["models"][0]["recognition_model_id"] == 12
    finally:
        registry.close()


def test_create_model_worker_backend_selects_paddleocr(monkeypatch, tmp_path):
    spec = _ocr_spec(tmp_path)
    created = {}

    class FakeBackend:
        name = "paddleocr"

        @classmethod
        def from_worker_spec(cls, worker_spec, base_config):
            created["spec"] = worker_spec
            created["config"] = base_config
            return cls()

    monkeypatch.setattr(
        "app.core.ocr_backend.PaddleOCRBackend",
        FakeBackend,
    )

    backend = shared_inference_module._create_model_worker_backend(
        spec, {"model_type": "OCR"}, {}
    )

    assert isinstance(backend, FakeBackend)
    assert created["spec"]["recognition_model_id"] == 12


def test_create_model_worker_backend_selects_rknn_ocr(monkeypatch, tmp_path):
    detection = tmp_path / "det.rknn"
    recognition = tmp_path / "rec.rknn"
    detection.write_bytes(b"det")
    recognition.write_bytes(b"rec")
    spec = build_ocr_model_spec(
        detection_model_id=11,
        detection_path=str(detection),
        recognition_model_id=12,
        recognition_path=str(recognition),
        ocr_config={"device": "auto"},
        detection_info={"framework": "rknn"},
        recognition_info={"framework": "rknn"},
    )
    created = {}

    class FakeBackend:
        name = "rknn_ocr"

        @classmethod
        def from_worker_spec(cls, worker_spec, base_config):
            created["spec"] = worker_spec
            created["config"] = base_config
            return cls()

    monkeypatch.setattr("app.core.ocr_backend.RKNNOcrBackend", FakeBackend)

    backend = shared_inference_module._create_model_worker_backend(
        spec, {"model_type": "OCR", "framework": "rknn"}, {}
    )

    assert isinstance(backend, FakeBackend)
    assert spec["backend"] == "rknn_ocr"
    assert created["spec"]["recognition_model_id"] == 12


def test_model_worker_binds_gpu_before_backend_creation(monkeypatch, tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"weights")
    spec = build_model_spec(str(model), {}, {"model_id": 31})
    observed = {}
    responses = []

    class FakeBackend:
        name = "ultralytics"
        model = None

        def infer(self, _frame):
            observed["visible_devices"] = os.environ.get("CUDA_VISIBLE_DEVICES")
            return [], [], {}

        def cleanup(self):
            observed["cleaned"] = True

    class RequestQueue:
        @staticmethod
        def get():
            return None

    class ResultQueue:
        @staticmethod
        def put(value):
            responses.append(value)

    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr(
        shared_inference_module,
        "_create_model_worker_backend",
        lambda *_args, **_kwargs: FakeBackend(),
    )

    shared_inference_module._model_worker_main(
        spec,
        {},
        RequestQueue(),
        ResultQueue(),
        {
            "gpu_index": 1,
            "gpu_uuid": "GPU-test-uuid",
            "gpu_name": "Fake GPU",
            "reserved_mb": 2048,
        },
    )

    assert observed == {"visible_devices": "GPU-test-uuid", "cleaned": True}
    assert responses[0]["kind"] == "worker_ready"
    assert responses[0]["gpu_index"] == 1
    assert responses[0]["gpu_uuid"] == "GPU-test-uuid"


def test_ocr_spec_changes_when_device_or_recognition_file_changes(tmp_path):
    auto = _ocr_spec(tmp_path, device="auto")
    cpu = _ocr_spec(tmp_path, device="cpu")
    other_rec = _ocr_spec(tmp_path, recognition_name="rec-v2.bin")

    assert auto["backend"] == "paddleocr"
    assert "recognition_score_threshold" not in auto["backend_config"]
    assert model_key(auto) != model_key(cpu)
    assert model_key(auto) != model_key(other_rec)


def test_registry_reuses_one_worker_for_same_model(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"weights")
    spec = build_model_spec(str(model), {"framework": "ultralytics"}, {"model_id": 7})
    registry = _ModelRegistry(queue_size=2, idle_seconds=60, worker_target=_fake_worker)
    try:
        first = registry.acquire(spec, {"confidence": 0.5})
        second = registry.acquire(spec, {"confidence": 0.8})

        assert first["model_key"] == second["model_key"]
        stats = registry.stats()
        assert stats["model_count"] == 1
        assert stats["models"][0]["references"] == 2

        response = registry.submit(
            first["model_key"],
            {"request_id": "request-1"},
            timeout=2,
        )
        assert response["ok"] is True
        assert response["detections"] == [{"label": "fake"}]
    finally:
        registry.close()


def test_model_startup_wait_is_not_charged_to_inference_timeout(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"weights")
    spec = build_model_spec(str(model), {}, {"model_id": 12})
    registry = _ModelRegistry(
        queue_size=1,
        idle_seconds=60,
        worker_target=_slow_start_worker,
    )
    try:
        acquired = registry.acquire(spec, {})
        started_at = time.monotonic()
        response = registry.submit(
            acquired["model_key"],
            {"request_id": "cold-start-request"},
            timeout=0.2,
            startup_timeout=2,
        )

        assert response["ok"] is True
        assert time.monotonic() - started_at >= 0.3
    finally:
        registry.close()


def test_model_start_failure_interrupts_startup_wait(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"weights")
    spec = build_model_spec(str(model), {}, {"model_id": 13})
    registry = _ModelRegistry(
        queue_size=1,
        idle_seconds=60,
        worker_target=_failed_start_worker,
    )
    try:
        acquired = registry.acquire(spec, {})
        started_at = time.monotonic()
        response = registry.submit(
            acquired["model_key"],
            {"request_id": "failed-start-request"},
            timeout=0.2,
            startup_timeout=2,
        )

        assert response == {
            "ok": False,
            "error": "ImportError: missing CUDA runtime",
        }
        assert time.monotonic() - started_at < 1

        failed_pid = registry.slots[acquired["model_key"]].process.pid
        reacquired = registry.acquire(spec, {})
        assert reacquired == response
        assert registry.slots[acquired["model_key"]].process.pid == failed_pid
    finally:
        registry.close()


def test_registry_queue_is_bounded(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"weights")
    spec = build_model_spec(str(model), {}, {"model_id": 8})
    registry = _ModelRegistry(queue_size=1, idle_seconds=60, worker_target=_fake_worker)
    try:
        result = registry.acquire(spec, {})
        slot = registry.slots[result["model_key"]]
        # Validate the actual multiprocessing queue was created with the requested
        # bound without relying on timing-sensitive producer/consumer races.
        assert slot.request_queue._maxsize == 1
    finally:
        registry.close()


def test_acquire_honors_dead_worker_oom_backoff(tmp_path, monkeypatch):
    model = tmp_path / "model.pt"
    model.write_bytes(b"weights")
    spec = build_model_spec(str(model), {}, {"model_id": 9})
    oom_count = {"value": 0}
    monkeypatch.setattr(
        shared_inference_module,
        "read_cgroup_oom_kill_count",
        lambda: oom_count["value"],
    )
    registry = _ModelRegistry(queue_size=1, idle_seconds=60, worker_target=_fake_worker)
    try:
        first = registry.acquire(spec, {})
        slot = registry.slots[first["model_key"]]
        original_pid = slot.process.pid
        slot.process.kill()
        slot.process.join(timeout=2)
        oom_count["value"] = 1

        blocked = registry.acquire(spec, {})

        assert blocked["ok"] is False
        assert blocked["error"] == "model_worker_oom_backoff"
        assert blocked["oom_failures"] == 1
        assert registry.slots[first["model_key"]].process.pid == original_pid

        # Once the deadline expires, restart keeps the failure history rather
        # than replacing the slot with a clean circuit state.
        slot.oom_retry_at = 0
        restarted = registry.acquire(spec, {})
        replacement = registry.slots[first["model_key"]]
        assert restarted["ok"] is True
        assert replacement.process.pid != original_pid
        assert replacement.oom_failures == 1
    finally:
        registry.close()


def test_model_worker_uses_configured_oom_open_period(tmp_path, monkeypatch):
    model = tmp_path / "model.pt"
    model.write_bytes(b"weights")
    spec = build_model_spec(str(model), {}, {"model_id": 10})
    oom_count = {"value": 0}
    monkeypatch.setattr(
        shared_inference_module,
        "read_cgroup_oom_kill_count",
        lambda: oom_count["value"],
    )
    registry = _ModelRegistry(
        queue_size=1,
        idle_seconds=60,
        oom_circuit_enabled=True,
        oom_failure_threshold=1,
        oom_open_seconds=45,
        oom_backoff_cap_seconds=20,
        worker_target=_fake_worker,
    )
    try:
        first = registry.acquire(spec, {})
        slot = registry.slots[first["model_key"]]
        slot.process.kill()
        slot.process.join(timeout=2)
        oom_count["value"] = 1

        blocked = registry.acquire(spec, {})

        assert blocked["ok"] is False
        assert blocked["error"] == "model_worker_oom_backoff"
        assert 44 <= blocked["retry_in_seconds"] <= 45
    finally:
        registry.close()


def test_disabling_model_oom_policy_clears_active_backoff(tmp_path, monkeypatch):
    model = tmp_path / "model.pt"
    model.write_bytes(b"weights")
    spec = build_model_spec(str(model), {}, {"model_id": 11})
    oom_count = {"value": 0}
    monkeypatch.setattr(
        shared_inference_module,
        "read_cgroup_oom_kill_count",
        lambda: oom_count["value"],
    )
    registry = _ModelRegistry(
        queue_size=1,
        idle_seconds=60,
        oom_circuit_enabled=True,
        worker_target=_fake_worker,
    )
    try:
        first = registry.acquire(spec, {})
        slot = registry.slots[first["model_key"]]
        original_pid = slot.process.pid
        slot.process.kill()
        slot.process.join(timeout=2)
        oom_count["value"] = 1
        assert registry.acquire(spec, {})["ok"] is False

        response = registry.configure_oom_policy({"enabled": False})
        restarted = registry.acquire(spec, {})

        assert response["ok"] is True
        assert response["oom_policy"]["enabled"] is False
        assert restarted["ok"] is True
        assert registry.slots[first["model_key"]].process.pid != original_pid
    finally:
        registry.close()


def test_cuda_oom_retries_model_once_on_another_gpu(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"weights")
    spec = build_model_spec(str(model), {}, {"model_id": 21})
    broker = GpuPlacementBroker(
        enabled=True,
        reserve_mb=1024,
        default_model_mb=2048,
        margin_percent=0,
        provider=_FakeGpuProvider(),
    )
    registry = _ModelRegistry(
        queue_size=1,
        idle_seconds=60,
        gpu_broker=broker,
        worker_target=_gpu0_oom_worker,
    )
    try:
        acquired = registry.acquire(spec, {})
        response = registry.submit(
            acquired["model_key"],
            {"request_id": "gpu-retry-request"},
            timeout=2,
            startup_timeout=5,
        )

        assert response["ok"] is True
        slot = registry.slots[acquired["model_key"]]
        assert slot.gpu_assignment.gpu_index == 1
        assert slot.oom_failures == 1
    finally:
        registry.close()


def test_cuda_oom_slot_recovers_after_alternate_gpu_was_unavailable(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"weights")
    spec = build_model_spec(str(model), {}, {"model_id": 23})
    clock = {"now": 0.0}
    broker = GpuPlacementBroker(
        enabled=True,
        reserve_mb=1024,
        default_model_mb=2048,
        margin_percent=0,
        oom_cooldown_seconds=10,
        provider=_SingleGpuProvider(),
        time_func=lambda: clock["now"],
    )
    registry = _ModelRegistry(
        queue_size=1,
        idle_seconds=60,
        oom_circuit_enabled=False,
        gpu_broker=broker,
        worker_target=_gpu0_oom_worker,
    )
    try:
        acquired = registry.acquire(spec, {})
        failed = registry.submit(
            acquired["model_key"],
            {"request_id": "gpu-no-alternate"},
            timeout=2,
            startup_timeout=5,
        )

        assert failed["ok"] is False
        assert "out of memory" in failed["error"].lower()
        slot = registry.slots[acquired["model_key"]]
        assert slot.start_failure_kind == "cuda_oom"

        clock["now"] = 11.0
        registry.worker_target = _fake_worker
        retried = registry.acquire(spec, {})
        recovered = registry.submit(
            acquired["model_key"],
            {"request_id": "gpu-after-cooldown"},
            timeout=2,
            startup_timeout=5,
        )

        assert retried["ok"] is True
        assert recovered["ok"] is True
        assert registry.slots[acquired["model_key"]].start_error is None
    finally:
        registry.close()


def test_runtime_cuda_oom_moves_future_requests_to_another_gpu(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"weights")
    spec = build_model_spec(str(model), {}, {"model_id": 22})
    broker = GpuPlacementBroker(
        enabled=True,
        reserve_mb=1024,
        default_model_mb=2048,
        margin_percent=0,
        provider=_FakeGpuProvider(),
    )
    registry = _ModelRegistry(
        queue_size=1,
        idle_seconds=60,
        gpu_broker=broker,
        worker_target=_gpu0_runtime_oom_worker,
    )
    try:
        acquired = registry.acquire(spec, {})
        first = registry.submit(
            acquired["model_key"],
            {"request_id": "runtime-oom-request"},
            timeout=2,
            startup_timeout=5,
        )
        assert first["ok"] is False
        assert first["failure_kind"] == "cuda_oom"

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            slot = registry.slots[acquired["model_key"]]
            if slot.ready and slot.gpu_assignment.gpu_index == 1:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("model did not become ready on the second GPU")

        second = registry.submit(
            acquired["model_key"],
            {"request_id": "after-runtime-oom"},
            timeout=2,
            startup_timeout=5,
        )
        assert second["ok"] is True
        assert registry.slots[acquired["model_key"]].gpu_assignment.gpu_index == 1
    finally:
        registry.close()
