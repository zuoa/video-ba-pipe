import logging
from types import SimpleNamespace
from unittest.mock import Mock

import app.decoder_worker as decoder_worker_module
from app.core.decoder.async_dec import AsyncSoftwareDecoder
from app.core.decoder.base import BaseDecoder
from app.decoder_worker import DecoderWorker
from app.core.orchestrator import Orchestrator


class _DummyDecoder(BaseDecoder):
    def _initialize(self) -> bool:
        return True

    def send_packet(self, data: bytes):
        return None

    def get_frame(self, timeout=1.0):
        return None

    def _cleanup(self):
        return None


def test_base_decoder_output_queue_size_is_configurable():
    decoder = _DummyDecoder(
        decoder_id=1,
        width=320,
        height=240,
        output_queue_size=7,
    )

    assert decoder.output_queue.maxsize == 7


def test_async_software_decoder_builds_ffmpeg_command_with_thread_limit():
    decoder = AsyncSoftwareDecoder(
        decoder_id=1,
        width=960,
        height=540,
        input_format='h264',
        output_format='nv12',
        threads=2,
    )
    decoder.logger = logging.getLogger("test.decoder")

    command = decoder._build_ffmpeg_command()

    assert command[:5] == ['ffmpeg', '-threads', '2', '-skip_frame', 'nokey']
    assert '-f' in command
    assert 'rawvideo' in command


def test_async_software_decoder_uses_hevc_demuxer_for_h265():
    decoder = AsyncSoftwareDecoder(
        decoder_id=2,
        width=960,
        height=540,
        input_format='h265',
        output_format='nv12',
    )

    command = decoder._build_ffmpeg_command()

    input_flag_index = command.index('-f')
    assert command[input_flag_index + 1] == 'hevc'


def test_async_software_decoder_can_disable_keyframe_only_mode():
    decoder = AsyncSoftwareDecoder(
        decoder_id=3,
        width=960,
        height=540,
        input_format='h264',
        output_format='nv12',
        keyframes_only=False,
    )

    command = decoder._build_ffmpeg_command()

    assert decoder.keyframes_only is False
    assert '-skip_frame' not in command


def test_worker_requests_single_source_full_frame_fallback(monkeypatch):
    monkeypatch.setattr(
        decoder_worker_module,
        'FFMPEG_SW_KEYFRAME_FALLBACK_SECONDS',
        10.0,
    )
    monkeypatch.setattr(
        decoder_worker_module,
        'FFMPEG_SW_KEYFRAME_FALLBACK_MIN_BYTES',
        1024,
    )
    worker = DecoderWorker(
        stream_url='rtsp://camera/stream',
        analysis_buffer_name='analysis',
        recording_buffer_name=None,
        source_info={},
    )
    worker.decoder = SimpleNamespace(
        keyframes_only=True,
        frames_decoded=0,
        frames_dropped=0,
        bytes_processed=4096,
    )

    assert worker._should_request_software_full_frame_fallback(100.0) is False
    assert worker._should_request_software_full_frame_fallback(109.9) is False
    assert worker._should_request_software_full_frame_fallback(110.0) is True

    worker.decoder.frames_decoded = 1
    assert worker._should_request_software_full_frame_fallback(120.0) is False


def test_orchestrator_switches_only_failed_source_and_clears_backoff():
    source = SimpleNamespace(id=2, source_url='rtsp://camera/dy01')
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.software_full_frame_sources = {1: 'rtsp://camera/other'}
    orchestrator.source_backoff = {2: {'failures': 3}}
    orchestrator._log_health_event = Mock()
    orchestrator._stop_source = Mock()

    orchestrator._switch_source_to_full_frame_software_decode(source, 10.04)

    assert orchestrator.software_full_frame_sources == {
        1: 'rtsp://camera/other',
        2: source.source_url,
    }
    assert 2 not in orchestrator.source_backoff
    orchestrator._stop_source.assert_called_once_with(source)
    assert orchestrator._log_health_event.call_args.kwargs == {
        'source': source,
        'event_type': 'software_decode_fallback',
        'details': {
            'from': 'keyframes_only',
            'to': 'all_frames',
            'uptime_seconds': 10.0,
        },
        'severity': 'warning',
    }
