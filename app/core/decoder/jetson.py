import queue
from typing import Optional

import numpy as np

from app import logger
from app.core.decoder.base import BaseDecoder, DecoderStatus
from app.core.frame_utils import (
    get_frame_size_bytes,
    normalize_pixel_format,
    reshape_frame,
)


class JetsonGStreamerDecoder(BaseDecoder):
    """Jetson nvv4l2decoder implementation for Annex-B H.264/H.265 streams."""

    _PARSER_BY_CODEC = {
        "h264": ("video/x-h264,stream-format=byte-stream", "h264parse"),
        "h265": ("video/x-h265,stream-format=byte-stream", "h265parse"),
        "hevc": ("video/x-h265,stream-format=byte-stream", "h265parse"),
    }
    _GST_FORMAT_BY_OUTPUT = {
        "nv12": "NV12",
        "yuv420p": "I420",
        "rgb24": "RGB",
        "bgr24": "BGR",
    }

    def __init__(
        self,
        decoder_id: int,
        width: int = 1920,
        height: int = 1080,
        input_format: str = "h264",
        output_format: str = "nv12",
        **kwargs,
    ):
        self.input_format = str(input_format or "h264").strip().lower()
        self.output_format = normalize_pixel_format(output_format)
        if self.input_format not in self._PARSER_BY_CODEC:
            raise ValueError(
                f"Jetson hardware decoder does not support codec: {self.input_format}; "
                "supported codecs: h264, h265"
            )
        if self.output_format not in self._GST_FORMAT_BY_OUTPUT:
            raise ValueError(
                f"Jetson hardware decoder does not support output format: {self.output_format}"
            )

        self.Gst = None
        self.GstVideo = None
        self.pipeline = None
        self.appsrc = None
        self.appsink = None
        self.bus = None
        self._running = False
        self._pipeline_error: Optional[RuntimeError] = None
        self._invalid_video_meta_warned = False
        super().__init__(
            decoder_id=decoder_id,
            width=width,
            height=height,
            input_format=self.input_format,
            output_format=self.output_format,
            **kwargs,
        )

    def build_pipeline_description(self) -> str:
        input_caps, parser = self._PARSER_BY_CODEC[self.input_format]
        output_format = self._GST_FORMAT_BY_OUTPUT[self.output_format]
        pipeline = [
            (
                "appsrc name=source is-live=true format=time do-timestamp=true "
                f"block=false caps={input_caps}"
            ),
            "queue max-size-buffers=4 leaky=downstream",
            parser,
            "nvv4l2decoder",
            "nvvidconv",
            f"video/x-raw,format=NV12,width={self.width},height={self.height}",
        ]
        if output_format != "NV12":
            pipeline.extend(["videoconvert", f"video/x-raw,format={output_format}"])
        pipeline.append(
            "appsink name=sink emit-signals=true sync=false max-buffers=1 drop=true"
        )
        return " ! ".join(pipeline)

    def _initialize(self) -> bool:
        try:
            import gi

            gi.require_version("Gst", "1.0")
            gi.require_version("GstVideo", "1.0")
            from gi.repository import Gst, GstVideo
        except (ImportError, ValueError) as exc:
            raise RuntimeError(
                "Jetson GStreamer Python bindings are unavailable; "
                "install python3-gi and gir1.2-gstreamer-1.0"
            ) from exc

        Gst.init(None)
        if Gst.ElementFactory.find("nvv4l2decoder") is None:
            raise RuntimeError(
                "GStreamer plugin nvv4l2decoder is unavailable; run this image on "
                "JetPack 6.2.1 with the NVIDIA container runtime"
            )
        if Gst.ElementFactory.find("nvvidconv") is None:
            raise RuntimeError(
                "GStreamer plugin nvvidconv is unavailable; run this image on "
                "JetPack 6.2.1 with the NVIDIA container runtime"
            )

        self.Gst = Gst
        self.GstVideo = GstVideo
        self.pipeline = Gst.parse_launch(self.build_pipeline_description())
        self.appsrc = self.pipeline.get_by_name("source")
        self.appsink = self.pipeline.get_by_name("sink")
        self.bus = self.pipeline.get_bus()
        if self.appsrc is None or self.appsink is None:
            raise RuntimeError("Failed to create Jetson GStreamer appsrc/appsink pipeline")

        self.appsink.connect("new-sample", self._on_new_sample)
        state_result = self.pipeline.set_state(Gst.State.PLAYING)
        if state_result == Gst.StateChangeReturn.FAILURE:
            self.pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("Failed to start Jetson GStreamer decoding pipeline")

        self._running = True
        logger.info(
            "Jetson hardware decoder started: codec=%s, output=%s, size=%sx%s",
            self.input_format,
            self.output_format,
            self.width,
            self.height,
        )
        return True

    def _frame_from_bytes(self, raw_frame: bytes, width: int, height: int) -> np.ndarray:
        expected_size = get_frame_size_bytes(width, height, self.output_format)
        if len(raw_frame) != expected_size:
            raise ValueError(
                f"Jetson decoded frame size {len(raw_frame)} does not match "
                f"{expected_size} for {self.output_format} {width}x{height}"
            )
        return reshape_frame(raw_frame, width, height, self.output_format).copy()

    @staticmethod
    def _copy_plane(
        raw_frame: bytes,
        *,
        offset: int,
        stride: int,
        row_bytes: int,
        rows: int,
    ) -> bytes:
        if stride == 0 or abs(stride) < row_bytes:
            raise ValueError(
                f"Invalid GStreamer plane stride {stride} for {row_bytes} active bytes"
            )

        active_rows = []
        for row_index in range(rows):
            row_start = offset + row_index * stride
            row_end = row_start + row_bytes
            if row_start < 0 or row_end > len(raw_frame):
                raise ValueError(
                    "GStreamer plane layout exceeds mapped buffer: "
                    f"offset={offset}, stride={stride}, row={row_index}, "
                    f"active={row_bytes}, mapped={len(raw_frame)}"
                )
            active_rows.append(raw_frame[row_start:row_end])
        return b"".join(active_rows)

    def _frame_from_strided_bytes(
        self,
        raw_frame: bytes,
        width: int,
        height: int,
        strides,
        offsets,
    ) -> np.ndarray:
        plane_specs = self._plane_specs(width, height)

        if len(strides) < len(plane_specs) or len(offsets) < len(plane_specs):
            raise ValueError(
                f"Incomplete GStreamer video layout for {self.output_format}: "
                f"strides={list(strides)}, offsets={list(offsets)}"
            )

        packed = b"".join(
            self._copy_plane(
                raw_frame,
                offset=int(offsets[plane_index]),
                stride=int(strides[plane_index]),
                row_bytes=row_bytes,
                rows=rows,
            )
            for plane_index, (row_bytes, rows) in enumerate(plane_specs)
        )
        return self._frame_from_bytes(packed, width, height)

    def _plane_specs(self, width: int, height: int):
        if self.output_format == "nv12":
            return (
                (width, height),
                (width, height // 2),
            )
        if self.output_format == "yuv420p":
            return (
                (width, height),
                (width // 2, height // 2),
                (width // 2, height // 2),
            )
        if self.output_format in {"rgb24", "bgr24"}:
            return ((width * 3, height),)
        raise ValueError(f"Unsupported Jetson output format: {self.output_format}")

    def _is_valid_video_layout(self, width, height, strides, offsets) -> bool:
        if width <= 0 or height <= 0:
            return False
        plane_specs = self._plane_specs(width, height)
        if len(strides) < len(plane_specs) or len(offsets) < len(plane_specs):
            return False
        return all(
            int(strides[index]) != 0
            and abs(int(strides[index])) >= row_bytes
            for index, (row_bytes, _rows) in enumerate(plane_specs)
        )

    def _video_info_from_caps(self, caps):
        video_info_type = self.GstVideo.VideoInfo
        new_from_caps = getattr(video_info_type, "new_from_caps", None)
        if new_from_caps is not None:
            video_info = new_from_caps(caps)
            if video_info is None:
                raise ValueError("Failed to parse GStreamer video caps")
            return video_info

        # Older PyGObject bindings expose from_caps either as a mutating
        # instance method or as a function returning (success, VideoInfo).
        video_info = video_info_type()
        result = video_info.from_caps(caps)
        if isinstance(result, tuple):
            success, parsed_info = result[:2]
            if not success or parsed_info is None:
                raise ValueError("Failed to parse GStreamer video caps")
            return parsed_info
        if not result:
            raise ValueError("Failed to parse GStreamer video caps")
        return video_info

    def _video_layout(self, buffer, caps):
        video_meta = self.GstVideo.buffer_get_video_meta(buffer)
        if video_meta is not None:
            meta_layout = (
                int(video_meta.width),
                int(video_meta.height),
                tuple(video_meta.stride),
                tuple(video_meta.offset),
            )
            if self._is_valid_video_layout(*meta_layout):
                return meta_layout
            if not self._invalid_video_meta_warned:
                logger.warning(
                    "Jetson returned invalid GstVideoMeta "
                    "(size=%sx%s, strides=%s, offsets=%s); "
                    "falling back to negotiated caps",
                    meta_layout[0],
                    meta_layout[1],
                    meta_layout[2],
                    meta_layout[3],
                )
                self._invalid_video_meta_warned = True

        video_info = self._video_info_from_caps(caps)
        caps_layout = (
            int(video_info.width),
            int(video_info.height),
            tuple(video_info.stride),
            tuple(video_info.offset),
        )
        if not self._is_valid_video_layout(*caps_layout):
            raise ValueError(
                "Invalid GStreamer video layout from negotiated caps: "
                f"size={caps_layout[0]}x{caps_layout[1]}, "
                f"strides={caps_layout[2]}, offsets={caps_layout[3]}"
            )
        return caps_layout

    def _enqueue_frame(self, frame: np.ndarray):
        try:
            self.output_queue.put_nowait(frame)
            self.frames_decoded += 1
        except queue.Full:
            self.frames_dropped += 1

    def _on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return self.Gst.FlowReturn.OK

        buffer = sample.get_buffer()
        caps = sample.get_caps()
        success, map_info = buffer.map(self.Gst.MapFlags.READ)
        if not success:
            self.errors += 1
            self.status = DecoderStatus.ERROR
            self._pipeline_error = RuntimeError("Failed to map a Jetson decoded frame")
            logger.error(str(self._pipeline_error))
            return self.Gst.FlowReturn.ERROR

        try:
            width, height, strides, offsets = self._video_layout(buffer, caps)
            frame = self._frame_from_strided_bytes(
                bytes(map_info.data),
                width,
                height,
                strides,
                offsets,
            )
            self._enqueue_frame(frame)
        except Exception as exc:
            self.errors += 1
            self.status = DecoderStatus.ERROR
            self._pipeline_error = RuntimeError(
                f"Failed to process a Jetson decoded frame: "
                f"{type(exc).__name__}: {exc}"
            )
            logger.exception(str(self._pipeline_error))
            return self.Gst.FlowReturn.ERROR
        finally:
            buffer.unmap(map_info)

        return self.Gst.FlowReturn.OK

    def _raise_pipeline_error(self):
        if self._pipeline_error is not None:
            # Raise a fresh exception so repeated polling does not keep appending
            # frames to the traceback stored on the original exception instance.
            raise RuntimeError(str(self._pipeline_error))
        if self.bus is None:
            return
        message = self.bus.pop_filtered(
            self.Gst.MessageType.ERROR | self.Gst.MessageType.EOS
        )
        if message is None:
            return
        if message.type == self.Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            self.errors += 1
            self.status = DecoderStatus.ERROR
            self._pipeline_error = RuntimeError(
                f"Jetson GStreamer decoding error: {error}; {debug or ''}"
            )
            raise self._pipeline_error
        self.status = DecoderStatus.ERROR
        self._pipeline_error = RuntimeError(
            "Jetson GStreamer decoding pipeline reached EOS"
        )
        raise self._pipeline_error

    def send_packet(self, data: bytes):
        if not data:
            return
        if not self._running or self.appsrc is None:
            raise RuntimeError("Jetson GStreamer decoder is not running")

        self._raise_pipeline_error()
        buffer = self.Gst.Buffer.new_allocate(None, len(data), None)
        buffer.fill(0, data)
        flow_result = self.appsrc.emit("push-buffer", buffer)
        if flow_result != self.Gst.FlowReturn.OK:
            self.errors += 1
            self.status = DecoderStatus.ERROR
            self._pipeline_error = RuntimeError(
                f"Jetson GStreamer rejected encoded packet: {flow_result}"
            )
            raise self._pipeline_error
        self.bytes_processed += len(data)

    def get_frame(self, timeout=1.0) -> Optional[np.ndarray]:
        self._raise_pipeline_error()
        try:
            return self.output_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_latest_frame(self, timeout=0.01) -> Optional[np.ndarray]:
        self._raise_pipeline_error()
        return super().get_latest_frame(timeout=timeout)

    def _cleanup(self):
        self._running = False
        if self.appsrc is not None:
            try:
                self.appsrc.emit("end-of-stream")
            except Exception:
                pass
        if self.pipeline is not None and self.Gst is not None:
            self.pipeline.set_state(self.Gst.State.NULL)
        self.pipeline = None
        self.appsrc = None
        self.appsink = None
        self.bus = None
        self.GstVideo = None
