import shutil
from types import SimpleNamespace

import numpy as np
import pytest

import app.core.video_recorder as video_recorder_module
from app.core.video_recorder import _FFmpegVideoWriter, VideoRecorder


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


def test_video_recorder_falls_back_when_opencv_has_no_video_backend(
    monkeypatch,
    tmp_path,
):
    fake_cv2 = SimpleNamespace(
        __version__='test',
        VideoWriter_fourcc=lambda *_args: 0,
        VideoWriter=lambda *_args: _ClosedOpenCVWriter(),
        getBuildInformation=lambda: 'Video I/O:\n  FFMPEG: NO\n  GStreamer: NO\n',
    )
    fallback_writer = object()
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
        return fallback_writer

    monkeypatch.setattr(video_recorder_module, 'cv2', fake_cv2)
    monkeypatch.setattr(
        recorder,
        '_open_ffmpeg_video_writer',
        fake_open_ffmpeg,
    )

    output_path = str(tmp_path / 'alert.mp4')
    writer = recorder._open_video_writer(
        np.zeros((12, 16, 3), dtype=np.uint8),
        output_path,
    )

    assert writer is fallback_writer
    assert captured == {
        'output_path': output_path,
        'width': 16,
        'height': 12,
    }


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


def test_independent_ffmpeg_writer_creates_mp4(tmp_path):
    ffmpeg_path = shutil.which('ffmpeg')
    if not ffmpeg_path:
        pytest.skip('ffmpeg is unavailable')

    encoder = VideoRecorder._select_ffmpeg_encoder(ffmpeg_path)
    if encoder is None:
        pytest.skip('ffmpeg has neither libx264 nor mpeg4 encoder')

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
