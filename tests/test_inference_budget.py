from app.core.inference_budget import (
    InferenceAdmissionController,
    MemorySnapshot,
    OomCircuitBreaker,
    read_cgroup_oom_kill_count,
    read_memory_snapshot,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_read_memory_snapshot_excludes_swap_from_available(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text(
        "MemTotal: 16384 kB\n"
        "MemAvailable: 4096 kB\n"
        "SwapTotal: 8192 kB\n"
        "SwapFree: 1024 kB\n"
    )
    snapshot = read_memory_snapshot(str(path))
    assert snapshot.total_mb == 16
    assert snapshot.available_mb == 4
    assert snapshot.swap_used_mb == 7


def test_read_memory_snapshot_falls_back_to_psutil_off_linux(tmp_path):
    snapshot = read_memory_snapshot(str(tmp_path / "missing-meminfo"))
    assert snapshot is not None
    assert snapshot.total_mb > 0
    assert snapshot.available_mb > 0


def test_read_cgroup_oom_kill_count(tmp_path):
    path = tmp_path / "memory.events"
    path.write_text("low 0\nhigh 0\noom 3\noom_kill 14\n")
    assert read_cgroup_oom_kill_count(str(path)) == 14


def test_admission_counts_only_new_shared_models():
    controller = InferenceAdmissionController(
        enabled=True,
        reserve_mb=2048,
        reserve_percent=15,
        default_new_model_mb=1024,
        margin_percent=25,
        memory_reader=lambda: MemorySnapshot(16384, 4000, 0),
    )
    controller.commit(1, {7})
    controller.mark_source_ready(1)

    decision = controller.evaluate(2, {7, 8}, service_model_ids={7})

    assert decision.allowed is True
    assert decision.new_model_ids == (8,)
    assert decision.estimated_increment_mb == 1280


def test_admission_does_not_charge_confirmed_ocr_recognition_id():
    controller = InferenceAdmissionController(
        enabled=True,
        reserve_mb=1000,
        reserve_percent=0,
        default_new_model_mb=1000,
        margin_percent=0,
        memory_reader=lambda: MemorySnapshot(16000, 2500, 0),
    )
    controller.commit(1, {11, 12})
    controller.mark_source_ready(1)

    decision = controller.evaluate(2, {11}, service_model_ids={11, 12})

    assert decision.allowed is True
    assert decision.new_model_ids == ()
    assert decision.estimated_increment_mb == 0


def test_admission_retains_pending_shared_reservation():
    controller = InferenceAdmissionController(
        enabled=True,
        reserve_mb=1000,
        reserve_percent=0,
        default_new_model_mb=1000,
        margin_percent=0,
        memory_reader=lambda: MemorySnapshot(16000, 2500, 0),
    )
    controller.commit(1, {7})

    decision = controller.evaluate(2, {7, 8})

    assert decision.allowed is False
    # One pending reservation for model 7 plus one new model 8.
    assert decision.estimated_increment_mb == 2000


def test_admission_never_deduplicates_local_model_copies():
    controller = InferenceAdmissionController(
        enabled=True,
        reserve_mb=1000,
        reserve_percent=0,
        default_new_model_mb=1000,
        margin_percent=0,
        memory_reader=lambda: MemorySnapshot(16000, 3500, 0),
    )
    controller.commit(1, set(), local_model_ids=(7,))
    controller.mark_source_ready(1)

    decision = controller.evaluate(2, set(), local_model_ids=(7, 7))

    assert decision.allowed is True
    assert decision.local_model_ids == (7, 7)
    assert decision.estimated_increment_mb == 2000


def test_admission_rejects_when_reserve_would_be_crossed():
    controller = InferenceAdmissionController(
        enabled=True,
        reserve_mb=2048,
        reserve_percent=15,
        default_new_model_mb=1024,
        margin_percent=25,
        memory_reader=lambda: MemorySnapshot(16384, 3000, 7800),
    )

    decision = controller.evaluate(1, {7})

    assert decision.allowed is False
    assert decision.reason == "insufficient_mem_available"
    assert decision.estimated_increment_mb == 1280


def test_admission_uses_observed_high_water_pss():
    controller = InferenceAdmissionController(
        enabled=True,
        reserve_mb=1000,
        reserve_percent=0,
        default_new_model_mb=500,
        margin_percent=20,
        memory_reader=lambda: MemorySnapshot(16000, 3000, 0),
    )
    controller.update_observed_model_pss(7, 1800)
    controller.update_observed_model_pss(7, 1200)

    decision = controller.evaluate(1, {7})

    assert decision.estimated_increment_mb == 2160
    assert decision.allowed is False


def test_oom_circuit_uses_backoff_then_opens():
    clock = FakeClock()
    breaker = OomCircuitBreaker(
        enabled=True,
        failure_threshold=3,
        open_seconds=600,
        stable_reset_seconds=600,
        backoff_cap_seconds=300,
        time_func=clock,
    )

    first = breaker.record_oom(1)
    assert first.failures == 1
    allowed, reason, _ = breaker.can_start(1)
    assert allowed is False
    assert reason == "global_oom_backoff"

    clock.advance(16)
    assert breaker.can_start(1)[0] is True
    breaker.record_oom(1)
    clock.advance(31)
    breaker.record_oom(1)

    state = breaker.states[1]
    assert state.failures == 3
    assert state.circuit_open_until == clock.now + 600
    assert breaker.can_start(1)[1] == "global_oom_backoff"

    clock.advance(61)
    assert breaker.can_start(1)[1] == "circuit_open"


def test_oom_circuit_resets_after_stable_window():
    clock = FakeClock()
    breaker = OomCircuitBreaker(
        enabled=True,
        failure_threshold=3,
        open_seconds=60,
        stable_reset_seconds=120,
        backoff_cap_seconds=300,
        time_func=clock,
    )
    breaker.record_oom(1)
    clock.advance(121)

    assert breaker.can_start(1) == (True, "stable_reset", 0.0)
    assert 1 not in breaker.states
