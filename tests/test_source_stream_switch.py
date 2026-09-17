from types import SimpleNamespace

from peewee import SqliteDatabase

import app.core.orchestrator as orchestrator_module
from app.core.database_models import SystemSetting
from app.core.orchestrator import Orchestrator
from app.core.source_stream_switch import (
    clear_deferred_stream_switch,
    defer_stream_switch,
    get_deferred_stream_switch,
    get_matching_deferred_stream_switch,
)


def test_deferred_stream_switch_is_durable_and_preserves_active_url():
    database = SqliteDatabase(':memory:')
    with database.bind_ctx([SystemSetting]):
        database.create_tables([SystemSetting])
        defer_stream_switch(
            7,
            active_url='rtsp://camera/original',
            active_codec='h264',
            pending_url='rtsp://camera/pending-1',
            requested_by='operator',
        )
        marker = defer_stream_switch(
            7,
            active_url='rtsp://camera/pending-1',
            active_codec='unknown',
            pending_url='rtsp://camera/pending-2',
            requested_by='operator',
        )

        assert marker['active_url'] == 'rtsp://camera/original'
        assert marker['active_codec'] == 'h264'
        assert marker['pending_url'] == 'rtsp://camera/pending-2'
        assert get_matching_deferred_stream_switch(
            SimpleNamespace(id=7, source_url='rtsp://camera/pending-2')
        ) == marker
        assert get_matching_deferred_stream_switch(
            SimpleNamespace(id=7, source_url='rtsp://camera/other')
        ) is None

        assert clear_deferred_stream_switch(7) is True
        assert get_deferred_stream_switch(7) is None


def test_deferred_url_change_does_not_reload_until_other_config_changes(
    monkeypatch,
):
    source = SimpleNamespace(
        id=7,
        source_code='camera-007',
        source_url='rtsp://camera/original',
        source_decode_width=960,
        source_decode_height=540,
        source_fps=10,
        source_codec='h264',
        decode_keyframes_only=None,
    )
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.decode_keyframes_only = False
    running_signature = orchestrator._runtime_source_config_signature(source)
    orchestrator.running_processes = {
        source.id: {'source_config_signature': running_signature}
    }

    source.source_url = 'rtsp://camera/pending'
    source.source_codec = 'unknown'
    monkeypatch.setattr(
        orchestrator_module,
        'get_matching_deferred_stream_switch',
        lambda _source: {
            'active_url': 'rtsp://camera/original',
            'active_codec': 'h264',
            'pending_url': 'rtsp://camera/pending',
        },
    )

    assert orchestrator._source_config_requires_reload(source) is False
    source.source_fps = 15
    assert orchestrator._source_config_requires_reload(source) is True


def test_stream_failure_activates_pending_url(monkeypatch):
    source = SimpleNamespace(
        id=7,
        source_code='camera-007',
        source_url='rtsp://camera/pending',
    )
    cleared = []
    synced = []
    events = []
    monkeypatch.setattr(
        orchestrator_module,
        'get_matching_deferred_stream_switch',
        lambda _source: {
            'active_url': 'rtsp://camera/original',
            'pending_url': 'rtsp://camera/pending',
        },
    )
    monkeypatch.setattr(
        orchestrator_module,
        'clear_deferred_stream_switch',
        lambda source_id: cleared.append(source_id),
    )
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator._sync_source_relay_path = (
        lambda item, url: synced.append((item.id, url))
    )
    orchestrator._log_health_event = (
        lambda item, event_type, details, **kwargs:
        events.append((item.id, event_type, details, kwargs))
    )

    assert orchestrator._activate_deferred_stream_switch(
        source,
        '视频流健康检查失败',
    ) is True
    assert cleared == [7]
    assert synced == [(7, 'rtsp://camera/pending')]
    assert events[0][1] == 'deferred_stream_switch_activated'
