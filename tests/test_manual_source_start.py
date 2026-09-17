from types import SimpleNamespace
import time
import json

from peewee import SqliteDatabase

import app.core.orchestrator as orchestrator_module
from app.core.database_models import SystemSetting
from app.core.orchestrator import Orchestrator
from app.core.source_start_request import (
    finish_source_start,
    get_source_start_request,
    pending_source_starts,
    queue_source_start,
)


def test_manual_start_request_is_durable_and_can_finish():
    database = SqliteDatabase(':memory:')
    with database.bind_ctx([SystemSetting]):
        database.create_tables([SystemSetting])
        queued = queue_source_start(7, 'operator')

        pending = pending_source_starts()
        assert [item['source_id'] for item in pending] == [7]
        assert pending[0]['request_id'] == queued['request_id']

        assert finish_source_start(pending[0], 'started') is True
        stored = get_source_start_request(7)
        assert stored['status'] == 'started'
        assert stored['requested_by'] == 'operator'
        assert pending_source_starts() == []


def test_new_manual_click_is_not_overwritten_by_old_worker_result():
    database = SqliteDatabase(':memory:')
    with database.bind_ctx([SystemSetting]):
        database.create_tables([SystemSetting])
        queue_source_start(7, 'first')
        stale_request = pending_source_starts()[0]
        latest = queue_source_start(7, 'second')

        assert finish_source_start(stale_request, 'failed', 'old failure') is False
        stored = get_source_start_request(7)
        assert stored['request_id'] == latest['request_id']
        assert stored['status'] == 'pending'


def test_manual_start_runs_before_normal_queue(monkeypatch):
    source = SimpleNamespace(
        id=7,
        name='大门',
        source_code='gate',
        source_url='rtsp://camera/stream',
        enabled=True,
        status='STOPPED',
    )
    request = {
        'request_id': 'request-7',
        'source_id': 7,
        'status': 'pending',
        'requested_by': 'operator',
        '_stored_value': '{}',
    }
    results = []
    starts = []

    monkeypatch.setattr(
        orchestrator_module, 'pending_source_starts', lambda: [request]
    )
    monkeypatch.setattr(
        orchestrator_module,
        'finish_source_start',
        lambda item, status, error=None: results.append((status, error)),
    )
    monkeypatch.setattr(
        orchestrator_module.VideoSource,
        'get_by_id',
        lambda _source_id: source,
    )

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.rotation_config = SimpleNamespace(enabled=False)
    orchestrator.desired_source_ids = {7}
    orchestrator.source_backoff = {}
    orchestrator._prioritize_rotation_source = lambda _source: None
    orchestrator._rotation_has_decoder_capacity = lambda: True
    orchestrator._attempt_source_start = lambda item, **kwargs: (
        starts.append((item.id, kwargs['trigger'])) or True
    )
    orchestrator._log_health_event = lambda *args, **kwargs: None

    orchestrator._process_manual_source_starts({7})

    assert starts == [(7, 'manual')]
    assert results == [('started', None)]


def test_no_initial_frame_after_grace_is_a_decoder_failure():
    class EmptyBuffer:
        @staticmethod
        def get_health_status():
            return {
                'time_since_last_frame': float('inf'),
                'consecutive_errors': 0,
                'frame_count': 0,
                'last_write_time': 0,
            }

    source = SimpleNamespace(
        id=7,
        name='大门',
        source_code='gate',
        source_url='rtsp://camera/stream',
    )
    failures = []
    health_events = []
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.buffers = {7: EmptyBuffer()}
    orchestrator.source_start_times = {7: time.time() - 120}
    orchestrator.start_grace_period = 60
    orchestrator.last_health_log_times = {}
    orchestrator.health_log_interval = 30
    orchestrator.running_processes = {7: {'decoder_type': 'ffmpeg_sw'}}
    orchestrator._log_decoder_failure = (
        lambda _source, phase, **details: failures.append((phase, details))
    )
    orchestrator._log_health_event = (
        lambda _source, event_type, details, **_kwargs:
        health_events.append((event_type, details))
    )

    assert orchestrator._check_source_health(source) is False
    assert failures[0][0] == 'no_initial_frame'
    assert failures[0][1]['decoder_type'] == 'ffmpeg_sw'
    assert health_events[0][0] == 'no_initial_frame'


def test_decoder_failure_log_redacts_source_url(monkeypatch):
    messages = []
    fake_logger = SimpleNamespace(error=lambda message: messages.append(message))
    monkeypatch.setattr(orchestrator_module, 'decoder_failure_logger', fake_logger)
    source = SimpleNamespace(
        id=7,
        name='大门',
        source_code='gate',
        source_url='rtsp://user:secret@camera/stream',
    )

    Orchestrator.__new__(Orchestrator)._log_decoder_failure(
        source,
        'codec_probe',
        error=f'cannot open {source.source_url}',
    )

    record = json.loads(messages[0])
    assert record['phase'] == 'codec_probe'
    assert record['error'] == 'cannot open <redacted-source-url>'
    assert 'secret' not in messages[0]
