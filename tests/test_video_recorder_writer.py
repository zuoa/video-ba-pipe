import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

import app.core.video_recorder as video_recorder_module
from app.core.video_recorder import (
    _FFmpegVideoWriter,
    VideoRecorder,
    build_ffmpeg_raw_encode_command,
    ensure_browser_compatible_mp4,
    even_frame_size,
    probe_mp4_video_codec,
)


def test_video_recorder_stops_at_disk_waterline(tmp_path, monkeypatch):
    recorder = VideoRecorder(
        buffer=object(),
        save_dir=str(tmp_path),
        max_disk_used_percent=80,
    )
    monkeypatch.setattr(
        video_recorder_module.shutil,
        'disk_usage',
        lambda _path: type('DiskUsage', (), {'total': 100, 'used': 80, 'free': 20})(),
    )

    assert recorder._disk_allows_recording() is False


def test_video_recorder_allows_recording_below_waterline(tmp_path, monkeypatch):
    recorder = VideoRecorder(
        buffer=object(),
        save_dir=str(tmp_path),
        max_disk_used_percent=80,
    )
    monkeypatch.setattr(
        video_recorder_module.shutil,
        'disk_usage',
        lambda _path: type('DiskUsage', (), {'total': 100, 'used': 79, 'free': 21})(),
    )

    assert recorder._disk_allows_recording() is True


class _ClosedOpenCVWriter:
    def isOpened(self):
        return False

    def release(self):
        return None


def test_video_recorder_prefers_ffmpeg_over_opencv(monkeypatch, tmp_path):
    opencv_called = []
    fake_cv2 = SimpleNamespace(
        __version__='test',
        VideoWriter_fourcc=lambda *_args: opencv_called.append('fourcc') or 0,
        VideoWriter=lambda *_args: opencv_called.append('writer') or _ClosedOpenCVWriter(),
        getBuildInformation=lambda: 'Video I/O:\n  FFMPEG: YES\n',
    )
    ffmpeg_writer = object()
    captured = {}
    recorder = VideoRecorder(
        buffer=SimpleNamespace(pixel_format='rgb24'),
        save_dir=str(tmp_path),
        fps=3,
    )

    def fake_open_ffmpeg(output_path, width, height):
        captured.update(
            output_path=output_path,
            width=width,
            height=height,
        )
        return ffmpeg_writer

    monkeypatch.setattr(video_recorder_module, 'cv2', fake_cv2)
    monkeypatch.setattr(recorder, '_open_ffmpeg_video_writer', fake_open_ffmpeg)

    output_path = str(tmp_path / 'alert.mp4')
    writer = recorder._open_video_writer(
        np.zeros((12, 16, 3), dtype=np.uint8),
        output_path,
    )

    assert writer is ffmpeg_writer
    assert opencv_called == []
    assert captured == {
        'output_path': output_path,
        'width': 16,
        'height': 12,
    }

    odd_captured = {}

    def fake_open_ffmpeg_odd(output_path, width, height):
        odd_captured.update(width=width, height=height)
        return ffmpeg_writer

    monkeypatch.setattr(recorder, '_open_ffmpeg_video_writer', fake_open_ffmpeg_odd)
    recorder._open_video_writer(
        np.zeros((13, 17, 3), dtype=np.uint8),
        output_path,
    )
    assert odd_captured == {'width': 16, 'height': 12}


def test_video_recorder_uses_opencv_h264_when_ffmpeg_unavailable(monkeypatch, tmp_path):
    class OpenedWriter:
        def isOpened(self):
            return True

        def release(self):
            return None

    opened = OpenedWriter()
    fourccs = []
    fake_cv2 = SimpleNamespace(
        __version__='test',
        VideoWriter_fourcc=lambda *args: fourccs.append(''.join(args)) or 1,
        VideoWriter=lambda *_args: opened,
        getBuildInformation=lambda: 'Video I/O:\n  FFMPEG: YES\n',
    )
    recorder = VideoRecorder(
        buffer=SimpleNamespace(pixel_format='rgb24'),
        save_dir=str(tmp_path),
        fps=3,
    )
    monkeypatch.setattr(video_recorder_module, 'cv2', fake_cv2)
    monkeypatch.setattr(recorder, '_open_ffmpeg_video_writer', lambda *_args, **_kwargs: None)

    writer = recorder._open_video_writer(
        np.zeros((12, 16, 3), dtype=np.uint8),
        str(tmp_path / 'alert.mp4'),
    )

    assert writer is opened
    assert fourccs[0] == 'avc1'
    assert 'mp4v' not in fourccs


def test_select_ffmpeg_encoder_prefers_h264(monkeypatch):
    result = SimpleNamespace(
        returncode=0,
        stdout=(
            ' V.S... mpeg4               MPEG-4 part 2\n'
            ' V....D libx264              H.264 / AVC\n'
        ),
    )
    monkeypatch.setattr(
        video_recorder_module.subprocess,
        'run',
        lambda *_args, **_kwargs: result,
    )

    assert VideoRecorder._select_ffmpeg_encoder('/usr/bin/ffmpeg') == 'libx264'


def test_select_ffmpeg_encoder_ignores_mpeg4(monkeypatch):
    result = SimpleNamespace(
        returncode=0,
        stdout=' V.S... mpeg4               MPEG-4 part 2\n',
    )
    monkeypatch.setattr(
        video_recorder_module.subprocess,
        'run',
        lambda *_args, **_kwargs: result,
    )

    assert VideoRecorder._select_ffmpeg_encoder('/usr/bin/ffmpeg') is None


def test_ffmpeg_raw_encode_command_is_browser_compatible():
    command = build_ffmpeg_raw_encode_command(
        ffmpeg_path='/usr/bin/ffmpeg',
        output_path='/tmp/alert.mp4',
        fps=10,
        frame_size=(960, 540),
        encoder='libx264',
    )

    assert command[:3] == ['/usr/bin/ffmpeg', '-hide_banner', '-loglevel']
    assert '-c:v' in command
    assert command[command.index('-c:v') + 1] == 'libx264'
    assert 'mpeg4' not in command
    assert 'bgr24' in command
    assert 'yuv420p' in command
    assert '+faststart' in command
    assert 'avc1' in command
    assert 'baseline' in command


def test_even_frame_size_rounds_odd_dimensions():
    assert even_frame_size(961, 541) == (960, 540)


def test_independent_ffmpeg_writer_creates_h264_mp4(tmp_path):
    ffmpeg_path = shutil.which('ffmpeg')
    if not ffmpeg_path:
        pytest.skip('ffmpeg is unavailable')

    encoder = VideoRecorder._select_ffmpeg_encoder(ffmpeg_path)
    if encoder is None:
        pytest.skip('ffmpeg has no H.264 encoder')

    output_path = tmp_path / 'fallback.mp4'
    writer = _FFmpegVideoWriter(
        ffmpeg_path=ffmpeg_path,
        output_path=str(output_path),
        fps=3,
        frame_size=(16, 12),
        encoder=encoder,
    )
    for value in (0, 64, 128):
        writer.write(np.full((12, 16, 3), value, dtype=np.uint8))

    assert writer.release() is True
    assert output_path.stat().st_size > 0
    assert output_path.read_bytes()[4:8] == b'ftyp'
    assert probe_mp4_video_codec(str(output_path)) == 'h264'


def test_ensure_browser_compatible_transcodes_mpeg4(tmp_path):
    ffmpeg_path = shutil.which('ffmpeg')
    if not ffmpeg_path:
        pytest.skip('ffmpeg is unavailable')
    if VideoRecorder._select_ffmpeg_encoder(ffmpeg_path) is None:
        pytest.skip('ffmpeg has no H.264 encoder')

    source = tmp_path / 'mpeg4.mp4'
    result = subprocess.run(
        [
            ffmpeg_path,
            '-hide_banner',
            '-loglevel', 'error',
            '-y',
            '-f', 'lavfi',
            '-i', 'color=c=red:s=16x12:d=0.4',
            '-c:v', 'mpeg4',
            '-pix_fmt', 'yuv420p',
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0 or not source.exists():
        pytest.skip(f'unable to create mpeg4 fixture: {result.stderr}')

    assert probe_mp4_video_codec(str(source)) == 'mpeg4'
    assert ensure_browser_compatible_mp4(str(source), ffmpeg_path=ffmpeg_path) is True
    assert probe_mp4_video_codec(str(source)) == 'h264'
