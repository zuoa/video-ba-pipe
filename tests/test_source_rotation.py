import pytest

from app.core.source_rotation import (
    MIN_DWELL_SECONDS,
    RoundRobinBatchSelector,
    normalize_source_rotation_config,
    save_source_rotation_config,
    SourceRotationConfig,
)
from app.core.orchestrator import Orchestrator
from app.source_workflow_host import SourceWorkflowHost


def test_normalize_source_rotation_config_uses_safe_defaults_and_minimums():
    config = normalize_source_rotation_config({
        "enabled": True,
        "batch_size": 0,
        "dwell_seconds": 1,
    })

    assert config.enabled is True
    assert config.batch_size == 1
    assert config.dwell_seconds == MIN_DWELL_SECONDS


def test_round_robin_selector_keeps_tail_batch_and_wraps_next_cycle():
    selector = RoundRobinBatchSelector()

    assert selector.select([5, 3, 2, 1, 4], 2) == [1, 2]
    assert selector.select([1, 2, 3, 4, 5], 2) == [3, 4]
    assert selector.select([1, 2, 3, 4, 5], 2) == [5]
    assert selector.select([1, 2, 3, 4, 5], 2) == [1, 2]


def test_round_robin_selector_continues_after_candidate_changes():
    selector = RoundRobinBatchSelector()

    assert selector.select([10, 20, 30, 40], 2) == [10, 20]
    assert selector.select([10, 30, 40, 50], 2) == [30, 40]
    assert selector.select([10, 30, 50], 2) == [50]


@pytest.mark.parametrize(
    "data,error_text",
    [
        ({"batch_size": 0, "dwell_seconds": 30}, "必须大于 0"),
        ({"batch_size": 20, "dwell_seconds": 9}, "不能少于"),
        ({"batch_size": "many", "dwell_seconds": 30}, "必须是整数"),
    ],
)
def test_save_source_rotation_config_rejects_invalid_values(data, error_text):
    with pytest.raises(ValueError, match=error_text):
        save_source_rotation_config(data)


class _FakeSource:
    def __init__(self, source_id):
        self.id = source_id

    @classmethod
    def get_by_id(cls, source_id):
        return cls(source_id)


def _make_rotation_orchestrator(candidate_ids, ready_ids, drained_ids):
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.rotation_config = SourceRotationConfig(
        enabled=True,
        batch_size=2,
        dwell_seconds=30,
    )
    orchestrator.rotation_selector = RoundRobinBatchSelector()
    orchestrator.rotation_batch_ids = []
    orchestrator.rotation_phase = 'IDLE'
    orchestrator.rotation_batch_launch_at = None
    orchestrator.rotation_dwell_started_at = None
    orchestrator.draining_sources = {}
    orchestrator._rotation_was_enabled = True
    orchestrator._refresh_rotation_config = lambda _now: None
    orchestrator._rotation_candidate_ids = lambda: list(candidate_ids)
    orchestrator._mark_ready_sources_running = lambda: None
    orchestrator._source_host_ready = lambda source_id: source_id in ready_ids

    def begin_drain(source, _reason):
        drained_ids.append(source.id)
        orchestrator.draining_sources[source.id] = {}

    orchestrator._begin_rotation_drain = begin_drain
    return orchestrator


def test_orchestrator_starts_dwell_after_ready_and_rotates(monkeypatch):
    monkeypatch.setattr('app.core.orchestrator.VideoSource', _FakeSource)
    ready_ids = set()
    drained_ids = []
    orchestrator = _make_rotation_orchestrator(
        [1, 2, 3, 4, 5],
        ready_ids,
        drained_ids,
    )

    assert orchestrator._update_rotation_schedule(0.0) == {1, 2}
    assert orchestrator.rotation_phase == 'STARTING'

    ready_ids.update([1, 2])
    orchestrator._update_rotation_schedule(5.0)
    assert orchestrator.rotation_phase == 'RUNNING'
    assert orchestrator.rotation_dwell_started_at == 5.0

    assert orchestrator._update_rotation_schedule(34.9) == {1, 2}
    assert orchestrator._update_rotation_schedule(35.0) == {3, 4}
    assert drained_ids == [1, 2]


def test_orchestrator_replaces_source_that_misses_startup_deadline(monkeypatch):
    monkeypatch.setattr('app.core.orchestrator.VideoSource', _FakeSource)
    ready_ids = {1}
    drained_ids = []
    orchestrator = _make_rotation_orchestrator(
        [1, 2, 3, 4],
        ready_ids,
        drained_ids,
    )

    orchestrator._update_rotation_schedule(0.0)
    orchestrator.rotation_batch_launch_at = 0.0
    orchestrator._update_rotation_schedule(60.0)

    assert drained_ids == [2]
    assert orchestrator.rotation_batch_ids == [1, 3]
    assert orchestrator.rotation_phase == 'STARTING'


def test_ready_rotated_source_leaves_decoder_startup_grace(monkeypatch):
    saved_sources = []

    class ReadySource(_FakeSource):
        status = 'STARTING'

    monkeypatch.setattr('app.core.orchestrator.VideoSource', ReadySource)
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.rotation_batch_ids = [7]
    orchestrator.source_start_times = {7: 123.0}
    orchestrator.workflow_hosts = {
        7: {
            'process': type('Process', (), {'poll': lambda self: None})(),
            'ready_event': type('Event', (), {'is_set': lambda self: True})(),
        }
    }
    orchestrator._save_source = lambda source, _operation: saved_sources.append(source)

    orchestrator._mark_ready_sources_running()

    assert 7 not in orchestrator.source_start_times
    assert saved_sources[0].status == 'RUNNING'


def test_missing_host_restarts_dwell_without_extending_crash_deadline(monkeypatch):
    monkeypatch.setattr('app.core.orchestrator.VideoSource', _FakeSource)
    ready_ids = {1, 2}
    drained_ids = []
    orchestrator = _make_rotation_orchestrator(
        [1, 2, 3, 4],
        ready_ids,
        drained_ids,
    )

    orchestrator._update_rotation_schedule(0.0)
    orchestrator._update_rotation_schedule(5.0)
    assert orchestrator.rotation_phase == 'RUNNING'
    assert orchestrator.rotation_dwell_started_at == 5.0

    ready_ids.remove(2)
    orchestrator._update_rotation_schedule(10.0)
    assert orchestrator.rotation_phase == 'STARTING'
    assert orchestrator.rotation_dwell_started_at is None
    assert orchestrator.rotation_batch_launch_at == 10.0

    # 同一宿主反复崩溃时，原始恢复截止时间不能被不断后移。
    orchestrator._mark_rotation_batch_starting(2, now=40.0)
    assert orchestrator.rotation_batch_launch_at == 10.0

    orchestrator._update_rotation_schedule(70.0)
    assert drained_ids == [2]
    assert orchestrator.rotation_batch_ids == [1, 3]


def test_source_host_announces_ready_only_with_a_runnable_workflow(monkeypatch):
    printed = []
    host = SourceWorkflowHost.__new__(SourceWorkflowHost)
    host.source_id = 9
    host.running = True
    host.ready_announced = False
    host.runners = {}
    monkeypatch.setattr('builtins.print', lambda *args, **kwargs: printed.append(args))

    assert host._announce_ready_if_runnable() is False
    assert printed == []

    host.runners[101] = object()
    assert host._announce_ready_if_runnable() is True
    assert printed == [('SOURCE_HOST_READY:9',)]
    assert host._announce_ready_if_runnable() is False
