import json
from types import SimpleNamespace

import pytest

import app.core.orchestrator as orchestrator_module
import app.core.video_probe as video_probe_module
from app.core.orchestrator import Orchestrator
from app.core.streamer import (
    FileStreamer,
    HLSStreamer,
    HTTPFLVStreamer,
    RTSPStreamer,
)
from app.core.video_probe import (
    VideoCodecProbeError,
    normalize_video_codec,
    probe_video_codec,
)
from app.decoder_worker import DecoderWorker
from app.core.decoder.nv import FFmpegNVDECDecoder


@pytest.mark.parametrize(
    ("codec", "expected"),
    [
        ("h264", "h264"),
        ("avc1", "h264"),
        ("hevc", "h265"),
        ("h265", "h265"),
    ],
)
def test_normalize_video_codec(codec, expected):
    assert normalize_video_codec(codec) == expected


def test_probe_video_codec_normalizes_hevc_and_uses_rtsp_transport(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"streams": [{"codec_name": "hevc"}]}),
            stderr="",
        )

    monkeypatch.setattr(video_probe_module.subprocess, "run", fake_run)

    codec = probe_video_codec("rtsp://user:password@camera/stream")

    assert codec == "h265"
    assert captured["command"][-1] == "rtsp://user:password@camera/stream"
    assert captured["command"][:5] == [
        "ffprobe",
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
    ]
    assert captured["kwargs"]["timeout"] == 15.0


@pytest.mark.parametrize(
    "result,error",
    [
        (SimpleNamespace(returncode=1, stdout="", stderr="failed"), "ffprobe failed"),
        (SimpleNamespace(returncode=0, stdout="{}", stderr=""), "did not find"),
    ],
)
def test_probe_video_codec_fails_without_a_supported_video_stream(
    monkeypatch, result, error
):
    monkeypatch.setattr(
        video_probe_module.subprocess,
        "run",
        lambda *args, **kwargs: result,
    )

    with pytest.raises(VideoCodecProbeError, match=error):
        probe_video_codec("sample.mp4")


def _command_muxer(command):
    muxer_index = len(command) - 3
    assert command[muxer_index] == "-f"
    return command[muxer_index + 1]


def test_all_streamers_emit_hevc_when_decoder_uses_h265(tmp_path):
    video_file = tmp_path / "sample.mp4"
    video_file.touch()
    streamers = [
        RTSPStreamer("rtsp://camera/stream", input_format="h265"),
        HTTPFLVStreamer("https://camera/live.flv", input_format="h265"),
        HLSStreamer("https://camera/live.m3u8", input_format="h265"),
        FileStreamer(str(video_file), input_format="h265"),
    ]

    assert [_command_muxer(streamer._build_command()) for streamer in streamers] == [
        "hevc",
        "hevc",
        "hevc",
        "hevc",
    ]


def test_decoder_worker_passes_decoder_codec_to_streamer():
    worker = DecoderWorker(
        stream_url="rtsp://camera/stream",
        analysis_buffer_name="analysis",
        recording_buffer_name=None,
        source_info={},
        stream_config={"type": "rtsp", "transport": "tcp"},
        decoder_config={"input_format": "h265"},
    )

    assert worker._build_stream_kwargs() == {
        "input_format": "h265",
        "transport": "tcp",
    }


def test_orchestrator_probes_and_stores_source_codec(monkeypatch):
    source = SimpleNamespace(
        id=7,
        source_url="rtsp://camera/stream",
        source_codec="unknown",
    )
    saved = []
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator._save_source = lambda item, operation: saved.append((item, operation))
    monkeypatch.setattr(orchestrator_module, "probe_video_codec", lambda _url: "h265")

    assert orchestrator._resolve_source_codec(source) == "h265"
    assert source.source_codec == "h265"
    assert saved[0][0] is source


def test_orchestrator_uses_cached_codec_when_reprobe_temarily_fails(monkeypatch):
    source = SimpleNamespace(
        id=8,
        source_url="rtsp://camera/stream",
        source_codec="h265",
    )
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator._save_source = lambda *args: None

    def fail_probe(_url):
        raise VideoCodecProbeError("offline")

    monkeypatch.setattr(orchestrator_module, "probe_video_codec", fail_probe)

    assert orchestrator._resolve_source_codec(source) == "h265"


def test_orchestrator_decoder_args_propagate_detected_codec(monkeypatch):
    source = SimpleNamespace(
        id=9,
        source_url="/data/sample.mp4",
        source_decode_width=854,
        source_decode_height=480,
    )
    monkeypatch.setattr(orchestrator_module, "VIDEO_DECODER_TYPE", "jetson_gst")

    arguments = Orchestrator._build_decoder_args(
        source,
        analysis_fps=3,
        input_format="h265",
    )

    input_index = arguments.index("--input-format")
    assert arguments[input_index + 1] == "h265"
    assert arguments[arguments.index("--decoder-type") + 1] == "jetson_gst"


def test_orchestrator_decoder_args_can_select_full_frame_software_decode():
    source = SimpleNamespace(
        id=9,
        source_url="rtsp://camera/stream",
        source_decode_width=960,
        source_decode_height=540,
    )

    arguments = Orchestrator._build_decoder_args(
        source,
        analysis_fps=2,
        input_format="h264",
        decoder_type="ffmpeg_sw",
        software_decode_keyframes_only=False,
    )

    option_index = arguments.index("--software-decode-keyframes-only")
    assert arguments[option_index + 1] == "false"


def test_nvdec_uses_ffmpeg_hevc_decoder_name_for_h265():
    class ConcreteNVDECDecoder(FFmpegNVDECDecoder):
        def send_packet(self, data):
            return None

        def get_frame(self, timeout=1):
            return None

    decoder = ConcreteNVDECDecoder(
        decoder_id=1,
        device_id=0,
        width=854,
        height=480,
        input_format="h265",
        output_format="nv12",
    )

    command = decoder._build_ffmpeg_command()

    assert command[command.index("-c:v") + 1] == "hevc_cuvid"
