import pytest

from app.core.gpu_placement import (
    GpuDeviceSnapshot,
    GpuPlacementBroker,
    GpuPlacementError,
)


class FakeGpuProvider:
    def __init__(self, devices, process_usage=None):
        self.snapshots = list(devices)
        self.process_usage = dict(process_usage or {})
        self.error = None
        self.closed = False

    def devices(self):
        if self.error is not None:
            raise self.error
        return list(self.snapshots)

    def process_used_mb(self, gpu_uuid, pid):
        return self.process_usage.get((gpu_uuid, pid))

    def close(self):
        self.closed = True


def _gpu(index, *, total=24000, used=0, utilization=0):
    return GpuDeviceSnapshot(
        index=index,
        uuid=f"GPU-{index}",
        name=f"Fake GPU {index}",
        total_mb=total,
        used_mb=used,
        free_mb=total - used,
        utilization_percent=utilization,
    )


def _spec(*, backend="ultralytics", file_size=0, device="auto"):
    return {
        "backend": backend,
        "file_size": file_size,
        "backend_config": {"device": device},
    }


def _broker(provider, **overrides):
    options = {
        "enabled": True,
        "reserve_mb": 1000,
        "default_model_mb": 2000,
        "margin_percent": 0,
        "provider": provider,
    }
    options.update(overrides)
    return GpuPlacementBroker(**options)


def test_balanced_policy_counts_pending_cold_start_reservations():
    broker = _broker(FakeGpuProvider([_gpu(0), _gpu(1)]))

    first = broker.reserve("model-a", _spec())
    second = broker.reserve("model-b", _spec())
    third = broker.reserve("model-c", _spec())

    assert [first.gpu_index, second.gpu_index, third.gpu_index] == [0, 1, 0]
    status = broker.status()
    assert status["gpus"][0]["pending_reserved_mb"] == 4000
    assert status["gpus"][1]["pending_reserved_mb"] == 2000


def test_capacity_admission_rejects_when_all_cards_are_below_reserve():
    broker = _broker(FakeGpuProvider([
        _gpu(0, total=8000, used=5500),
        _gpu(1, total=8000, used=6000),
    ]))

    with pytest.raises(GpuPlacementError) as error:
        broker.reserve("large-model", _spec())

    assert error.value.code == "gpu_capacity_exhausted"
    assert error.value.details["estimated_mb"] == 2000
    assert len(error.value.details["gpus"]) == 2


def test_ready_worker_records_pid_memory_and_learns_high_water():
    provider = FakeGpuProvider(
        [_gpu(0), _gpu(1)],
        process_usage={("GPU-0", 42): 768.5},
    )
    broker = _broker(provider)
    assignment = broker.reserve("model-a", _spec())

    ready = broker.mark_ready("model-a", 42)

    assert assignment is ready
    assert ready.actual_mb == 768.5
    assert ready.state == "ready"
    broker.release("model-a")
    assert broker.estimate_model_mb("model-a", _spec()) == 768.5


def test_paddle_reservation_includes_detection_and_recognition_weights():
    broker = _broker(
        FakeGpuProvider([_gpu(0)]),
        default_model_mb=100,
    )
    spec = _spec(backend="paddleocr", file_size=10 * 1024 * 1024)
    spec["recognition_file_size"] = 100 * 1024 * 1024

    assert broker.estimate_model_mb("ocr-pair", spec) == 440.0


def test_cpu_and_non_cuda_workers_are_not_assigned():
    broker = _broker(FakeGpuProvider([_gpu(0), _gpu(1)]))

    assert broker.reserve("cpu-ocr", _spec(backend="paddleocr", device="cpu")) is None
    assert broker.reserve("rknn", _spec(backend="rknn")) is None


def test_metrics_failure_rejects_or_explicitly_degrades_to_legacy():
    reject_provider = FakeGpuProvider([])
    reject_provider.error = RuntimeError("NVML unavailable")
    reject_broker = _broker(reject_provider, failure_mode="reject")

    with pytest.raises(GpuPlacementError) as error:
        reject_broker.reserve("model-a", _spec())
    assert error.value.code == "gpu_metrics_unavailable"

    legacy_provider = FakeGpuProvider([])
    legacy_provider.error = RuntimeError("NVML unavailable")
    legacy_broker = _broker(legacy_provider, failure_mode="legacy")
    assert legacy_broker.reserve("model-a", _spec()) is None
    assert legacy_broker.status()["degraded_to_legacy"] is True


def test_stale_snapshot_conservatively_retains_ready_worker_reservation():
    provider = FakeGpuProvider(
        [_gpu(0, total=5000)],
        process_usage={("GPU-0", 42): 500},
    )
    broker = _broker(
        provider,
        reserve_mb=0,
        default_model_mb=3000,
        stale_seconds=60,
        time_func=lambda: 10,
    )
    broker.reserve("model-a", _spec())
    broker.mark_ready("model-a", 42)
    provider.error = RuntimeError("temporary NVML failure")

    with pytest.raises(GpuPlacementError) as error:
        broker.reserve("model-b", _spec())

    assert error.value.code == "gpu_capacity_exhausted"


def test_cuda_oom_cooldown_excludes_failed_gpu():
    broker = _broker(FakeGpuProvider([_gpu(0), _gpu(1)]))
    first = broker.reserve("model-a", _spec())
    broker.fail("model-a", cooldown=True)

    replacement = broker.reserve(
        "model-a", _spec(), exclude_gpu_uuids={first.gpu_uuid}
    )

    assert replacement.gpu_index == 1
