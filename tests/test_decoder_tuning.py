import logging
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

import app.decoder_worker as decoder_worker_module
import app.core.orchestrator as orchestrator_module
from app.core.decoder.async_dec import AsyncSoftwareDecoder
from app.core.decoder.base import BaseDecoder, DecodedFrame, DecoderStatus
from app.core.decoder.rk import FFmpegRKMPPDecoder
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


def test_decoder_queue_evicts_oldest_and_preserves_latest_n_frames():
    decoder = _DummyDecoder(
        decoder_id=2,
        width=2,
        height=2,
        output_queue_size=2,
    )
    for value in (1, 2, 3):
        decoder._enqueue_decoded_frame(
            np.full((2, 2), value, dtype=np.uint8),
            decoded_at=float(value),
        )

    pending = decoder.get_pending_frames()

    assert [int(item.image[0, 0]) for item in pending] == [2, 3]
    assert [item.decoded_at for item in pending] == [2.0, 3.0]
    assert decoder.frames_decoded == 3
    assert decoder.frames_dropped == 1


class _BatchBuffer:
    def __init__(self):
        self.writes = []

    def write(self, frame, timestamp):
        self.writes.append((frame.copy(), timestamp))

    def update_last_write_time(self, _timestamp):
        return None

    def increment_error_count(self):
        return None

    def close(self):
        return None


class _BatchDecoder:
    keyframes_only = False
    bytes_processed = 0
    frames_decoded = 3
    frames_dropped = 0
    fatal_error = None

    def __init__(self, frames):
        self._batches = [frames, []]

    def get_pending_frames(self, timeout=0.5):
        return self._batches.pop(0)

    def close(self):
        return None


class _StoppedStreamer:
    def start(self):
        return None

    def is_running(self):
        return False

    def stop(self):
        return None


def test_worker_sends_only_batch_latest_to_analysis_and_all_to_recording():
    decoded = [
        DecodedFrame(
            image=np.full((2, 2), value, dtype=np.uint8),
            decoded_at=100.0 + value,
            sequence=value,
        )
        for value in (1, 2, 3)
    ]
    worker = DecoderWorker(
        stream_url='rtsp://camera/stream',
        analysis_buffer_name='analysis',
        recording_buffer_name='recording',
        source_info={},
        analysis_config={'mode': 'all', 'fps': 2},
        recording_config={'fps': 10},
    )
    worker.decoder = _BatchDecoder(decoded)
    worker.streamer = _StoppedStreamer()
    worker.analysis_buffer = _BatchBuffer()
    worker.recording_buffer = _BatchBuffer()

    worker.start()

    assert len(worker.analysis_buffer.writes) == 1
    analysis_frame, analysis_timestamp = worker.analysis_buffer.writes[0]
    assert int(analysis_frame[0, 0]) == 3
    assert analysis_timestamp == 103.0
    assert [int(frame[0, 0]) for frame, _ in worker.recording_buffer.writes] == [1, 2, 3]
    assert [timestamp for _, timestamp in worker.recording_buffer.writes] == [101.0, 102.0, 103.0]


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

    assert command[:3] == ['ffmpeg', '-threads', '2']
    assert '-skip_frame' not in command
    assert '-f' in command
    assert 'rawvideo' in command


def test_async_software_decoder_uses_configured_decode_output_fps():
    decoder = AsyncSoftwareDecoder(
        decoder_id=11,
        width=960,
        height=540,
        input_format='h264',
        output_format='nv12',
        output_fps=10,
    )

    command = decoder._build_ffmpeg_command()

    assert command[command.index('-vf') + 1] == 'fps=10'
    assert command.index('-vf') < command.index('-s')


def test_async_software_decoder_can_decode_rtsp_directly():
    decoder = AsyncSoftwareDecoder(
        decoder_id=13,
        width=640,
        height=480,
        input_format='h264',
        output_format='nv12',
        output_fps=5,
        input_url='rtsp://camera/stream',
        rtsp_transport='tcp',
    )

    command = decoder._build_ffmpeg_command()

    assert command[command.index('-rtsp_transport') + 1] == 'tcp'
    assert command[command.index('-fflags') + 1] == 'nobuffer+discardcorrupt'
    assert command[command.index('-probesize') + 1] == '32768'
    assert command[command.index('-max_delay') + 1] == '0'
    assert command[command.index('-i') + 1] == 'rtsp://camera/stream'
    assert command[command.index('-map') + 1] == '0:v:0'
    assert command[command.index('-vf') + 1] == 'fps=5'
    assert 'pipe:0' not in command


def test_direct_rtsp_decoder_rejects_external_packets():
    decoder = AsyncSoftwareDecoder(
        decoder_id=14,
        width=640,
        height=480,
        input_url='rtsp://camera/stream',
    )

    try:
        decoder.send_packet(b'encoded')
    except RuntimeError as exc:
        assert '不接受外部编码数据包' in str(exc)
    else:
        raise AssertionError('direct RTSP decoder must reject external packets')


def test_async_software_decoder_can_leave_output_rate_unlimited():
    decoder = AsyncSoftwareDecoder(
        decoder_id=12,
        width=960,
        height=540,
        input_format='h264',
        output_format='nv12',
        output_fps=0,
    )

    assert '-vf' not in decoder._build_ffmpeg_command()


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


def test_rkmpp_decoder_decodes_full_stream_then_samples_output_fps():
    decoder = FFmpegRKMPPDecoder(
        decoder_id=4,
        width=960,
        height=540,
        input_format='h265',
        output_format='nv12',
        output_fps=2,
    )

    command = decoder._build_ffmpeg_command()

    assert '-skip_frame' not in command
    assert command[command.index('-c:v') + 1] == 'hevc_rkmpp'
    assert command[command.index('-vf') + 1] == 'fps=2'


def test_rkmpp_decoder_can_decode_rtsp_directly():
    decoder = FFmpegRKMPPDecoder(
        decoder_id=15,
        width=640,
        height=480,
        input_format='h264',
        output_format='nv12',
        output_fps=5,
        input_url='rtsp://camera/stream',
    )

    command = decoder._build_ffmpeg_command()

    assert command[command.index('-c:v') + 1] == 'h264_rkmpp'
    assert command[command.index('-i') + 1] == 'rtsp://camera/stream'
    assert command[command.index('-map') + 1] == '0:v:0'
    assert 'pipe:0' not in command


def test_rkmpp_decoder_can_leave_output_rate_unlimited():
    decoder = FFmpegRKMPPDecoder(
        decoder_id=5,
        width=960,
        height=540,
        input_format='h264',
        output_format='nv12',
        output_fps=0,
    )

    command = decoder._build_ffmpeg_command()

    assert '-skip_frame' not in command
    assert '-vf' not in command


def test_rkmpp_decoder_can_enable_keyframe_only_mode_explicitly():
    decoder = FFmpegRKMPPDecoder(
        decoder_id=6,
        width=960,
        height=540,
        input_format='h265',
        output_format='nv12',
        keyframes_only=True,
    )

    command = decoder._build_ffmpeg_command()

    assert command[command.index('-skip_frame') + 1] == 'nokey'


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


def test_worker_jetson_decode_rate_covers_analysis_and_recording_consumers():
    analysis_only = DecoderWorker(
        stream_url='rtsp://camera/stream',
        analysis_buffer_name='analysis',
        recording_buffer_name=None,
        source_info={},
        analysis_config={'mode': 'fps', 'fps': 2},
        recording_config={'fps': 3},
    )
    with_recording = DecoderWorker(
        stream_url='rtsp://camera/stream',
        analysis_buffer_name='analysis',
        recording_buffer_name='recording',
        source_info={},
        analysis_config={'mode': 'fps', 'fps': 2},
        recording_config={'fps': 3},
    )

    assert analysis_only._required_decode_output_fps(25) == 2
    assert with_recording._required_decode_output_fps(25) == 3


def test_worker_uses_video_source_decode_fps_as_ffmpeg_output_rate():
    source = SimpleNamespace(source_fps=10)

    assert DecoderWorker._configured_decode_output_fps(source) == 10


def test_worker_enables_direct_rtsp_only_for_supported_full_frame_decoders():
    direct = DecoderWorker(
        stream_url='rtsp://camera/stream',
        analysis_buffer_name='analysis',
        recording_buffer_name=None,
        source_info={},
        decoder_config={
            'type': 'ffmpeg_sw',
            'direct_rtsp_enabled': True,
            'keyframes_only': False,
        },
    )
    keyframes = DecoderWorker(
        stream_url='rtsp://camera/stream',
        analysis_buffer_name='analysis',
        recording_buffer_name=None,
        source_info={},
        decoder_config={
            'type': 'rk_mpp',
            'direct_rtsp_enabled': True,
            'keyframes_only': True,
        },
    )
    jetson = DecoderWorker(
        stream_url='rtsp://camera/stream',
        analysis_buffer_name='analysis',
        recording_buffer_name=None,
        source_info={},
        decoder_config={
            'type': 'jetson_gst',
            'direct_rtsp_enabled': True,
        },
    )
    file_source = DecoderWorker(
        stream_url='/data/video.mp4',
        analysis_buffer_name='analysis',
        recording_buffer_name=None,
        source_info={},
        decoder_config={
            'type': 'ffmpeg_sw',
            'direct_rtsp_enabled': True,
        },
    )

    assert direct._direct_rtsp_eligible() is True
    assert keyframes._direct_rtsp_eligible() is False
    assert jetson._direct_rtsp_eligible() is False
    assert file_source._direct_rtsp_eligible() is False


def test_worker_direct_rtsp_falls_back_to_legacy_only_once(monkeypatch):
    class DirectDecoder:
        returncode = 1

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class LegacyDecoder:
        status = DecoderStatus.READY

        def close(self):
            return None

    class LegacyStreamer:
        def __init__(self):
            self.started = False

        def start(self):
            self.started = True

        def is_running(self):
            return self.started

        def stop(self):
            self.started = False

    worker = DecoderWorker(
        stream_url='rtsp://camera/stream',
        analysis_buffer_name='analysis',
        recording_buffer_name=None,
        source_info={},
        decoder_config={'type': 'ffmpeg_sw', 'direct_rtsp_enabled': True},
    )
    direct_decoder = DirectDecoder()
    legacy_streamer = LegacyStreamer()
    worker.decoder = direct_decoder
    worker.direct_rtsp_active = True

    def create_legacy(_source, *, direct_rtsp):
        assert direct_rtsp is False
        worker.streamer = legacy_streamer
        worker.decoder = LegacyDecoder()
        worker.direct_rtsp_active = False

    monkeypatch.setattr(worker, '_create_decode_path', create_legacy)

    assert worker._activate_legacy_fallback('direct process exited') is True
    assert direct_decoder.closed is True
    assert legacy_streamer.started is True
    assert worker.direct_rtsp_fallback_used is True
    assert worker._activate_legacy_fallback('again') is False


def test_worker_disables_jetson_frame_drop_for_all_frame_analysis():
    worker = DecoderWorker(
        stream_url='rtsp://camera/stream',
        analysis_buffer_name='analysis',
        recording_buffer_name=None,
        source_info={},
        analysis_config={'mode': 'all', 'fps': 2},
    )

    assert worker._required_decode_output_fps(25) == 25


def test_orchestrator_switches_only_failed_source_and_clears_backoff():
    source = SimpleNamespace(
        id=2,
        source_url='rtsp://camera/dy01',
        decode_keyframes_only=True,
    )
    orchestrator = Orchestrator.__new__(Orchestrator)
    existing_marker = {'source_url': 'rtsp://camera/other'}
    orchestrator.software_full_frame_sources = {1: existing_marker}
    orchestrator.running_processes = {
        2: {'decoder_type': 'ffmpeg_sw'},
    }
    orchestrator.source_backoff = {2: {'failures': 3}}
    orchestrator._log_health_event = Mock()
    orchestrator._stop_source = Mock()

    orchestrator._switch_source_to_full_frame_software_decode(source, 10.04)

    assert orchestrator.software_full_frame_sources == {
        1: existing_marker,
        2: orchestrator._software_full_frame_fallback_marker(
            source,
            'ffmpeg_sw',
        ),
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


def test_orchestrator_invalidates_fallback_when_keyframe_policy_changes(monkeypatch):
    monkeypatch.setattr(orchestrator_module, 'DECODE_KEYFRAMES_ONLY', True)
    source = SimpleNamespace(
        id=2,
        source_url='rtsp://camera/dy01',
        decode_keyframes_only=None,
    )
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.software_full_frame_sources = {
        source.id: orchestrator._software_full_frame_fallback_marker(
            source,
            'ffmpeg_sw',
        ),
    }

    source.decode_keyframes_only = True

    assert orchestrator._use_software_full_frame_fallback(
        source,
        'ffmpeg_sw',
    ) is False
    assert source.id not in orchestrator.software_full_frame_sources


def test_orchestrator_invalidates_software_fallback_for_hardware_decoder():
    source = SimpleNamespace(
        id=2,
        source_url='rtsp://camera/dy01',
        decode_keyframes_only=True,
    )
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.software_full_frame_sources = {
        source.id: orchestrator._software_full_frame_fallback_marker(
            source,
            'ffmpeg_sw',
        ),
    }

    assert orchestrator._use_software_full_frame_fallback(
        source,
        'ffmpeg_nvdec',
    ) is False
    assert source.id not in orchestrator.software_full_frame_sources


def test_orchestrator_keeps_matching_software_fallback():
    source = SimpleNamespace(
        id=2,
        source_url='rtsp://camera/dy01',
        decode_keyframes_only=True,
    )
    orchestrator = Orchestrator.__new__(Orchestrator)
    marker = orchestrator._software_full_frame_fallback_marker(
        source,
        'ffmpeg_sw',
    )
    orchestrator.software_full_frame_sources = {source.id: marker}

    assert orchestrator._use_software_full_frame_fallback(
        source,
        'ffmpeg_sw',
    ) is True
    assert orchestrator.software_full_frame_sources[source.id] == marker


def test_orchestrator_system_keyframe_policy_applies_only_to_inheriting_sources():
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.decode_keyframes_only = True
    inherited = SimpleNamespace(decode_keyframes_only=None)
    explicitly_disabled = SimpleNamespace(decode_keyframes_only=False)

    assert orchestrator._configured_decode_keyframes_only(inherited) is True
    assert orchestrator._configured_decode_keyframes_only(explicitly_disabled) is False
