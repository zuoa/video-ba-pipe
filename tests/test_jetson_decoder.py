import logging

import numpy as np
import pytest

from app.core.decoder import DecoderFactory
from app.core.decoder.jetson import JetsonGStreamerDecoder


def _decoder(codec="h264", output_format="nv12", width=16, height=8):
    decoder = JetsonGStreamerDecoder(
        decoder_id=7,
        width=width,
        height=height,
        input_format=codec,
        output_format=output_format,
    )
    decoder.logger = logging.getLogger("test.jetson_decoder")
    return decoder


@pytest.mark.parametrize(
    ("codec", "caps", "parser"),
    [
        ("h264", "video/x-h264", "h264parse"),
        ("h265", "video/x-h265", "h265parse"),
        ("hevc", "video/x-h265", "h265parse"),
    ],
)
def test_jetson_pipeline_uses_expected_hardware_decoder(codec, caps, parser):
    pipeline = _decoder(codec=codec).build_pipeline_description()

    assert caps in pipeline
    assert parser in pipeline
    assert "nvv4l2decoder" in pipeline
    assert "nvvidconv" in pipeline
    assert "video/x-raw,format=NV12,width=16,height=8" in pipeline
    assert "appsink name=sink" in pipeline


@pytest.mark.parametrize(
    ("output_format", "gst_format", "shape"),
    [
        ("nv12", "NV12", (12, 16)),
        ("yuv420p", "I420", (12, 16)),
        ("rgb24", "RGB", (8, 16, 3)),
        ("bgr24", "BGR", (8, 16, 3)),
    ],
)
def test_jetson_pipeline_and_frame_shape_follow_output_format(
    output_format, gst_format, shape
):
    decoder = _decoder(output_format=output_format)
    pipeline = decoder.build_pipeline_description()
    raw_frame = bytes(np.prod(shape))

    assert f"format={gst_format}" in pipeline
    assert decoder._frame_from_bytes(raw_frame, 16, 8).shape == shape


def test_jetson_decoder_rejects_unsupported_codec():
    with pytest.raises(ValueError, match="does not support codec"):
        _decoder(codec="mjpeg")


def test_jetson_decoder_rejects_wrong_frame_size():
    decoder = _decoder()

    with pytest.raises(ValueError, match="decoded frame size"):
        decoder._frame_from_bytes(b"too short", 16, 8)


def test_jetson_decoder_tracks_queue_backpressure():
    decoder = JetsonGStreamerDecoder(
        decoder_id=10,
        width=16,
        height=8,
        input_format="h264",
        output_format="nv12",
        output_queue_size=1,
    )
    frame = np.zeros((12, 16), dtype=np.uint8)

    decoder._enqueue_frame(frame)
    decoder._enqueue_frame(frame)

    assert decoder.frames_decoded == 1
    assert decoder.frames_dropped == 1
    assert decoder.output_queue.qsize() == 1


def test_jetson_latest_frame_path_polls_pipeline_errors(monkeypatch):
    decoder = _decoder()
    calls = []
    decoder._enqueue_frame(np.zeros((12, 16), dtype=np.uint8))
    monkeypatch.setattr(
        decoder,
        "_raise_pipeline_error",
        lambda: calls.append("polled"),
    )

    frame = decoder.get_latest_frame()

    assert calls == ["polled"]
    assert frame.shape == (12, 16)


def test_jetson_latest_frame_propagates_pipeline_error(monkeypatch):
    decoder = _decoder()

    def raise_error():
        raise RuntimeError("decoder failed")

    monkeypatch.setattr(decoder, "_raise_pipeline_error", raise_error)

    with pytest.raises(RuntimeError, match="decoder failed"):
        decoder.get_latest_frame()


def test_jetson_pipeline_error_remains_visible_after_writer_observes_it():
    decoder = _decoder()
    decoder._pipeline_error = RuntimeError("persistent decoder failure")

    with pytest.raises(RuntimeError, match="persistent decoder failure") as first:
        decoder._raise_pipeline_error()
    with pytest.raises(RuntimeError, match="persistent decoder failure") as second:
        decoder.get_latest_frame()

    assert first.value is not decoder._pipeline_error
    assert second.value is not decoder._pipeline_error


def test_jetson_encoded_queue_never_leaks_buffers():
    pipeline = _decoder().build_pipeline_description()

    assert "leaky" not in pipeline.split("h264parse")[0]


@pytest.mark.parametrize("parser", ["h264parse", "h265parse"])
def test_jetson_parser_realigns_stream_and_reinserts_parameter_sets(parser):
    codec = "h264" if parser == "h264parse" else "h265"
    pipeline = _decoder(codec=codec).build_pipeline_description()

    # 重建 pipeline 后码流从 GOP 中间灌入，解析器必须按 AU 对齐并补发
    # SPS/PPS，否则 nvv4l2decoder 会报 gst-resource-error-quark
    assert f"{parser} config-interval=1 disable-passthrough=true" in pipeline


def test_jetson_recoverable_error_restarts_pipeline_and_clears_error(monkeypatch):
    decoder = _decoder()
    restarts = []
    monkeypatch.setattr(decoder, "_teardown_pipeline", lambda: None)
    monkeypatch.setattr(
        decoder, "_create_pipeline", lambda: restarts.append("restarted")
    )
    decoder._pipeline_error = RuntimeError("corrupted bitstream")
    decoder._pipeline_error_recoverable = True

    decoder._raise_pipeline_error()

    assert restarts == ["restarted"]
    assert decoder._pipeline_error is None
    assert decoder.fatal_error is None


def test_jetson_restart_backoff_defers_second_attempt(monkeypatch):
    decoder = _decoder()
    restarts = []
    monkeypatch.setattr(decoder, "_teardown_pipeline", lambda: None)
    monkeypatch.setattr(
        decoder, "_create_pipeline", lambda: restarts.append("restarted")
    )

    decoder._pipeline_error = RuntimeError("first failure")
    decoder._pipeline_error_recoverable = True
    decoder._raise_pipeline_error()

    decoder._pipeline_error = RuntimeError("second failure")
    decoder._pipeline_error_recoverable = True
    with pytest.raises(RuntimeError, match="second failure"):
        decoder._raise_pipeline_error()

    assert restarts == ["restarted"]


def test_jetson_failed_restarts_eventually_become_fatal(monkeypatch):
    decoder = _decoder()
    monkeypatch.setattr(decoder, "_teardown_pipeline", lambda: None)

    def fail_create():
        raise RuntimeError("out of decoder memory")

    monkeypatch.setattr(decoder, "_create_pipeline", fail_create)
    decoder._last_restart_attempt_at = float("-inf")
    decoder._restart_backoff_seconds = 0.0

    for _ in range(decoder._RESTART_MAX_CONSECUTIVE_FAILURES):
        decoder._pipeline_error = RuntimeError("corrupted bitstream")
        decoder._pipeline_error_recoverable = True
        with pytest.raises(RuntimeError):
            decoder._raise_pipeline_error()

    assert decoder.fatal_error is not None
    assert "放弃恢复" in str(decoder.fatal_error)


def test_jetson_non_recoverable_error_never_restarts(monkeypatch):
    decoder = _decoder()
    monkeypatch.setattr(
        decoder,
        "_try_restart_pipeline",
        lambda: pytest.fail("must not attempt restart"),
    )
    decoder._pipeline_error = RuntimeError("frame processing bug")
    decoder._pipeline_error_recoverable = False

    with pytest.raises(RuntimeError, match="frame processing bug"):
        decoder._raise_pipeline_error()


class _FakeVideoInfo:
    def __init__(
        self,
        *,
        width=16,
        height=8,
        strides=(16, 16, 0, 0),
        offsets=(0, 128, 0, 0),
        parses_caps=True,
    ):
        self.width = width
        self.height = height
        self.stride = strides
        self.offset = offsets
        self._parses_caps = parses_caps

    def from_caps(self, _caps):
        return self._parses_caps


class _FakeVideoMeta:
    def __init__(self, width, height, strides, offsets):
        self.width = width
        self.height = height
        self.stride = strides
        self.offset = offsets


class _FakeGstVideo:
    def __init__(self, meta, video_info):
        self._meta = meta
        self._video_info = video_info

    def buffer_get_video_meta(self, _buffer):
        return self._meta

    def VideoInfo(self):
        return self._video_info


class _TupleReturningVideoInfo:
    parsed_info = None

    def from_caps(self, _caps):
        return True, self.parsed_info


class _TupleReturningGstVideo:
    VideoInfo = _TupleReturningVideoInfo

    @staticmethod
    def buffer_get_video_meta(_buffer):
        return None


def test_jetson_video_layout_falls_back_to_caps_for_zeroed_jetson_meta(caplog):
    decoder = _decoder()
    decoder.GstVideo = _FakeGstVideo(
        _FakeVideoMeta(
            width=0,
            height=0,
            strides=(0, 0, 0, 0),
            offsets=(0, 0, 0, 0),
        ),
        _FakeVideoInfo(),
    )

    with caplog.at_level(logging.WARNING):
        layout = decoder._video_layout(object(), object())

    assert layout == (16, 8, (16, 16, 0, 0), (0, 128, 0, 0))
    assert "falling back to negotiated caps" in caplog.text


def test_jetson_video_layout_uses_info_returned_by_pygobject_from_caps():
    decoder = _decoder()
    _TupleReturningVideoInfo.parsed_info = _FakeVideoInfo()
    decoder.GstVideo = _TupleReturningGstVideo()

    layout = decoder._video_layout(object(), object())

    assert layout == (16, 8, (16, 16, 0, 0), (0, 128, 0, 0))


def test_jetson_video_layout_prefers_valid_buffer_meta():
    decoder = _decoder()
    decoder.GstVideo = _FakeGstVideo(
        _FakeVideoMeta(
            width=16,
            height=8,
            strides=(32, 32, 0, 0),
            offsets=(0, 256, 0, 0),
        ),
        _FakeVideoInfo(width=99, height=99),
    )

    layout = decoder._video_layout(object(), object())

    assert layout == (16, 8, (32, 32, 0, 0), (0, 256, 0, 0))


def _padded_plane(rows, active_width, stride, start_value):
    plane = bytearray(rows * stride)
    active = bytearray()
    value = start_value
    for row in range(rows):
        for column in range(active_width):
            plane[row * stride + column] = value
            active.append(value)
            value = (value + 1) % 251
        for column in range(active_width, stride):
            plane[row * stride + column] = 255
    return bytes(plane), bytes(active)


def test_jetson_nv12_frame_extraction_removes_plane_padding():
    decoder = _decoder(output_format="nv12", width=6, height=4)
    y_plane, active_y = _padded_plane(4, 6, 8, 1)
    uv_plane, active_uv = _padded_plane(2, 6, 8, 40)
    raw_frame = y_plane + uv_plane

    frame = decoder._frame_from_strided_bytes(
        raw_frame,
        6,
        4,
        strides=(8, 8),
        offsets=(0, len(y_plane)),
    )

    assert frame.shape == (6, 6)
    assert frame.tobytes() == active_y + active_uv


def test_jetson_i420_frame_extraction_removes_each_plane_padding():
    decoder = _decoder(output_format="yuv420p", width=6, height=4)
    y_plane, active_y = _padded_plane(4, 6, 8, 1)
    u_plane, active_u = _padded_plane(2, 3, 4, 50)
    v_plane, active_v = _padded_plane(2, 3, 4, 80)
    raw_frame = y_plane + u_plane + v_plane

    frame = decoder._frame_from_strided_bytes(
        raw_frame,
        6,
        4,
        strides=(8, 4, 4),
        offsets=(0, len(y_plane), len(y_plane) + len(u_plane)),
    )

    assert frame.shape == (6, 6)
    assert frame.tobytes() == active_y + active_u + active_v


@pytest.mark.parametrize("output_format", ["rgb24", "bgr24"])
def test_jetson_rgb_frame_extraction_removes_row_padding(output_format):
    decoder = _decoder(output_format=output_format, width=3, height=2)
    raw_frame, active = _padded_plane(2, 9, 12, 1)

    frame = decoder._frame_from_strided_bytes(
        raw_frame,
        3,
        2,
        strides=(12,),
        offsets=(0,),
    )

    assert frame.shape == (2, 3, 3)
    assert frame.tobytes() == active


@pytest.mark.parametrize("alias", ["jetson_gst", "jetson", "nvv4l2"])
def test_decoder_factory_registers_jetson_aliases(monkeypatch, alias):
    monkeypatch.setattr(JetsonGStreamerDecoder, "initialize", lambda self: True)

    decoder = DecoderFactory.create_decoder(
        alias,
        decoder_id=8,
        width=16,
        height=8,
        input_format="h264",
        output_format="nv12",
    )

    assert isinstance(decoder, JetsonGStreamerDecoder)


def test_decoder_factory_fails_fast_when_jetson_initialization_fails(monkeypatch):
    monkeypatch.setattr(JetsonGStreamerDecoder, "initialize", lambda self: False)

    with pytest.raises(RuntimeError, match="initialization failed"):
        DecoderFactory.create_decoder(
            "jetson",
            decoder_id=9,
            width=16,
            height=8,
            input_format="h264",
            output_format="nv12",
        )
