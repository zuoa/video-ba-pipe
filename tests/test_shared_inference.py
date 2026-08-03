import os

import app.core.shared_inference as shared_inference_module
from app.core.shared_inference import _ModelRegistry, build_model_spec, model_key


def _fake_worker(spec, base_config, request_queue, result_queue):
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


def test_model_key_changes_when_model_file_changes(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"first")
    spec_a = build_model_spec(str(model), {"framework": "ultralytics"}, {"model_id": 7})
    model.write_bytes(b"second-version")
    spec_b = build_model_spec(str(model), {"framework": "ultralytics"}, {"model_id": 7})

    assert model_key(spec_a) != model_key(spec_b)


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
