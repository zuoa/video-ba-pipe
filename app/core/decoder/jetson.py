import math
import queue
import threading
import time
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

    # Pipeline 重建退避参数：可恢复错误（bus ERROR/EOS、push 被拒）后
    # 重建 pipeline 让 h264parse/h265parse 在下一个 IDR 处重新同步，
    # 避免一次码流损坏就拖垮整个 worker 进程。
    _RESTART_INITIAL_BACKOFF_SECONDS = 1.0
    _RESTART_MAX_BACKOFF_SECONDS = 30.0
    _RESTART_MAX_CONSECUTIVE_FAILURES = 5

    # 沉默卡死看门狗：NVMMLite 内部错误（Unsupported Codec 刷屏、解析器
    # 卡死等）只打 stderr、不上报 Gst bus，pipeline 状态看似正常，
    # bus 检测永远触发不了重建。此时唯一可观测特征是"码流持续输入但
    # 长时间无任何帧输出"。持续灌流最终会拖垮硬解堆（malloc_consolidate
    # SIGABRT），必须在崩溃前主动重建；连续重建无效则判死，让上层
    # worker 退出重启，而不是陪跑到进程崩溃连累其他通道。
    _STALL_WATCHDOG_SECONDS = 10.0
    _STALL_MAX_CONSECUTIVE_RESTARTS = 3

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

        self.input_fps = max(0.0, float(kwargs.get("input_fps") or 0.0))
        self.output_fps = max(0.0, float(kwargs.get("output_fps") or 0.0))
        self.drop_frame_interval = self._calculate_drop_frame_interval(
            self.input_fps,
            self.output_fps,
        )

        self.Gst = None
        self.GstVideo = None
        self.pipeline = None
        self.appsrc = None
        self.appsink = None
        self.bus = None
        self._running = False
        self._pipeline_error: Optional[RuntimeError] = None
        # 可恢复错误（码流损坏、EOS 等）会触发 pipeline 重建；
        # 不可恢复错误（自身帧处理异常、重建连续失败）通过 fatal_error 暴露，
        # 供上层 worker 立即退出而不是空转重试。
        self._pipeline_error_recoverable = False
        self._fatal_error: Optional[RuntimeError] = None
        self._restart_lock = threading.Lock()
        self._last_restart_attempt_at = 0.0
        self._restart_backoff_seconds = self._RESTART_INITIAL_BACKOFF_SECONDS
        self._consecutive_restart_failures = 0
        # nvv4l2decoder 附加属性解析失败时回退到裸插件名
        self._use_decoder_props = True
        self._invalid_video_meta_warned = False
        # 沉默卡死看门狗状态
        self._watchdog_bytes_baseline = 0
        self._watchdog_frames_baseline = 0
        self._stall_started_at: Optional[float] = None
        self._consecutive_stall_restarts = 0
        super().__init__(
            decoder_id=decoder_id,
            width=width,
            height=height,
            input_format=self.input_format,
            output_format=self.output_format,
            **kwargs,
        )

    @staticmethod
    def _calculate_drop_frame_interval(input_fps: float, output_fps: float) -> int:
        """Translate an input/output FPS ratio to nvv4l2decoder semantics."""
        if input_fps <= 0 or output_fps <= 0 or output_fps >= input_fps:
            return 0

        # nvv4l2decoder emits every Nth frame. Floor keeps the emitted FPS at
        # or slightly above the requested consumer rate.
        interval = math.floor(input_fps / output_fps)
        return min(30, interval) if interval > 1 else 0

    def build_pipeline_description(self) -> str:
        input_caps, parser = self._PARSER_BY_CODEC[self.input_format]
        output_format = self._GST_FORMAT_BY_OUTPUT[self.output_format]
        # nvv4l2decoder 降低显存占用（多路并发时），属性不存在会回退重建
        decoder_properties = ["num-extra-surfaces=0"]
        if self.drop_frame_interval > 1:
            decoder_properties.append(
                f"drop-frame-interval={self.drop_frame_interval}"
            )
        decoder_element = "nvv4l2decoder"
        if self._use_decoder_props:
            decoder_element += " " + " ".join(decoder_properties)
        pipeline = [
            (
                "appsrc name=source is-live=true format=time do-timestamp=true "
                f"block=false caps={input_caps}"
            ),
            # 编码侧队列绝不能 leaky：上游按任意字节块推送裸码流，
            # 丢弃任意一块都会截断 NAL/打断参考链，硬件解码器直接报
            # gst-resource-error-quark。队列满时阻塞形成背压即可。
            "queue max-size-buffers=500 max-size-time=2000000000",
            # disable-passthrough：强制解析器完整解析并按 AU 对齐输出，
            # 未拿到 SPS/PPS 之前的残帧直接丢弃不透传——否则 pipeline
            # 重建后码流从 GOP 中间灌入，nvv4l2decoder 收到无参数集上下文
            # 的 P 帧切片会报 gst-resource-error-quark: Failed to process
            # frame，陷入“重建→再报错→再重建”循环。
            # config-interval=1：每个 IDR 前补发缓存的 SPS/PPS，保证重建后
            # 第一个 IDR 即可恢复解码。
            f"{parser} config-interval=1 disable-passthrough=true",
            decoder_element,
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
        self._create_pipeline()

        self._running = True
        logger.info(
            "Jetson hardware decoder started: codec=%s, output=%s, size=%sx%s",
            self.input_format,
            self.output_format,
            self.width,
            self.height,
        )
        return True

    def _create_pipeline(self):
        """构建并启动 GStreamer pipeline（初始化与错误恢复共用）。"""
        try:
            pipeline = self.Gst.parse_launch(self.build_pipeline_description())
        except Exception:
            if not self._use_decoder_props:
                raise
            # 个别固件版本的 nvv4l2decoder 没有 num-extra-surfaces 属性，
            # 回退到裸插件名再试一次
            logger.warning(
                "Jetson pipeline 解析失败，回退到不带附加属性的 nvv4l2decoder 重试"
            )
            self._use_decoder_props = False
            pipeline = self.Gst.parse_launch(self.build_pipeline_description())

        appsrc = pipeline.get_by_name("source")
        appsink = pipeline.get_by_name("sink")
        if appsrc is None or appsink is None:
            pipeline.set_state(self.Gst.State.NULL)
            raise RuntimeError("Failed to create Jetson GStreamer appsrc/appsink pipeline")

        appsink.connect("new-sample", self._on_new_sample)
        state_result = pipeline.set_state(self.Gst.State.PLAYING)
        if state_result == self.Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(self.Gst.State.NULL)
            raise RuntimeError("Failed to start Jetson GStreamer decoding pipeline")

        self.pipeline = pipeline
        self.appsrc = appsrc
        self.appsink = appsink
        self.bus = pipeline.get_bus()

    def _teardown_pipeline(self):
        """停止并释放当前 pipeline（恢复重建与清理共用）。"""
        if self.appsrc is not None:
            try:
                self.appsrc.emit("end-of-stream")
            except Exception:
                pass
        if self.pipeline is not None and self.Gst is not None:
            try:
                self.pipeline.set_state(self.Gst.State.NULL)
            except Exception:
                pass
        self.pipeline = None
        self.appsrc = None
        self.appsink = None
        self.bus = None

    def _try_restart_pipeline(self) -> bool:
        """可恢复错误后重建 pipeline，带指数退避；返回是否已恢复。"""
        with self._restart_lock:
            if self._fatal_error is not None:
                return False
            now = time.monotonic()
            if now - self._last_restart_attempt_at < self._restart_backoff_seconds:
                return False
            self._last_restart_attempt_at = now

            logger.warning(
                "Jetson pipeline 出现可恢复错误，准备重建: %s",
                self._pipeline_error,
            )
            try:
                self._teardown_pipeline()
                self._create_pipeline()
            except Exception as exc:
                self._consecutive_restart_failures += 1
                self._restart_backoff_seconds = min(
                    self._restart_backoff_seconds * 2,
                    self._RESTART_MAX_BACKOFF_SECONDS,
                )
                logger.error(
                    "Jetson pipeline 重建失败（连续第 %d 次，下次退避 %.0fs）: %s",
                    self._consecutive_restart_failures,
                    self._restart_backoff_seconds,
                    exc,
                )
                if (
                    self._consecutive_restart_failures
                    >= self._RESTART_MAX_CONSECUTIVE_FAILURES
                ):
                    self._fatal_error = RuntimeError(
                        f"Jetson pipeline 连续重建 "
                        f"{self._consecutive_restart_failures} 次均失败，"
                        f"放弃恢复: {self._pipeline_error}"
                    )
                return False

            self._pipeline_error = None
            self._pipeline_error_recoverable = False
            self._consecutive_restart_failures = 0
            self._restart_backoff_seconds = self._RESTART_INITIAL_BACKOFF_SECONDS
            self.status = DecoderStatus.DECODING
            logger.info("Jetson pipeline 已重建并恢复运行")
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
            self._pipeline_error_recoverable = False
            self._fatal_error = self._pipeline_error
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
            # 自身帧处理代码出错，重建 pipeline 无法修复，直接判死
            self._pipeline_error_recoverable = False
            self._fatal_error = self._pipeline_error
            logger.exception(str(self._pipeline_error))
            return self.Gst.FlowReturn.ERROR
        finally:
            buffer.unmap(map_info)

        return self.Gst.FlowReturn.OK

    @property
    def fatal_error(self) -> Optional[RuntimeError]:
        """不可恢复的错误；非空时上层 worker 应立即退出而不是重试。"""
        return self._fatal_error

    def _raise_pipeline_error(self):
        if self._pipeline_error is not None:
            if (
                self._pipeline_error_recoverable
                and self._fatal_error is None
                and self._try_restart_pipeline()
            ):
                return
            # Raise a fresh exception so repeated polling does not keep appending
            # frames to the traceback stored on the original exception instance.
            raise RuntimeError(str(self._fatal_error or self._pipeline_error))
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
            # 码流损坏/资源抖动可通过重建 pipeline 恢复
            self._pipeline_error_recoverable = True
            if self._try_restart_pipeline():
                return
            raise RuntimeError(str(self._fatal_error or self._pipeline_error))
        self.status = DecoderStatus.ERROR
        self._pipeline_error = RuntimeError(
            "Jetson GStreamer decoding pipeline reached EOS"
        )
        self._pipeline_error_recoverable = True
        if self._try_restart_pipeline():
            return
        raise RuntimeError(str(self._fatal_error or self._pipeline_error))

    def _check_decode_watchdog(self):
        """检测硬解沉默卡死：码流持续输入但长时间无任何帧输出。

        只在 send_packet 路径调用（持有上游推流节奏，天然是周期触发点）。
        无输入时不归解码器背锅（信源问题由健康监控处理）；输出队列满导致
        的 frames_dropped 也算输出活动，解码器本身仍是活的。
        """
        if not self._running or self.appsrc is None or self._fatal_error is not None:
            return
        # 已有待处理的 pipeline 错误，交给常规恢复路径，不重复干预
        if self._pipeline_error is not None:
            return

        output_activity = self.frames_decoded + self.frames_dropped
        if output_activity != self._watchdog_frames_baseline:
            self._watchdog_frames_baseline = output_activity
            self._watchdog_bytes_baseline = self.bytes_processed
            self._stall_started_at = None
            self._consecutive_stall_restarts = 0
            return
        if self.bytes_processed == self._watchdog_bytes_baseline:
            self._stall_started_at = None
            return
        self._watchdog_bytes_baseline = self.bytes_processed

        now = time.monotonic()
        if self._stall_started_at is None:
            self._stall_started_at = now
            return
        stall_seconds = now - self._stall_started_at
        if stall_seconds < self._STALL_WATCHDOG_SECONDS:
            return

        # 确认卡死：走重建流程；连续重建都无帧输出说明码流/固件层面
        # 已不可恢复，判死让上层重启，避免持续灌流引发硬解堆崩溃
        self._consecutive_stall_restarts += 1
        stall_error = RuntimeError(
            f"Jetson decoder stalled: encoded data flowing but no decoded "
            f"frames for {stall_seconds:.0f}s "
            f"(bytes_processed={self.bytes_processed})"
        )
        logger.warning(
            "Jetson 解码器沉默卡死（%.0fs 有输入无输出），主动重建 pipeline（第 %d/%d 次）",
            stall_seconds,
            self._consecutive_stall_restarts,
            self._STALL_MAX_CONSECUTIVE_RESTARTS,
        )
        self.errors += 1
        self.status = DecoderStatus.ERROR
        self._pipeline_error = stall_error
        self._pipeline_error_recoverable = True
        # 重置计时，重建后给下一个完整观察窗口
        self._stall_started_at = None
        if self._consecutive_stall_restarts >= self._STALL_MAX_CONSECUTIVE_RESTARTS:
            self._fatal_error = RuntimeError(
                f"Jetson 解码器沉默卡死且连续重建 "
                f"{self._consecutive_stall_restarts} 次后仍无帧输出，"
                f"放弃恢复: {stall_error}"
            )
        if self._try_restart_pipeline():
            return
        raise RuntimeError(str(self._fatal_error or stall_error))

    def send_packet(self, data: bytes):
        if not data:
            return
        if not self._running or self.appsrc is None:
            raise RuntimeError("Jetson GStreamer decoder is not running")

        self._raise_pipeline_error()
        self._check_decode_watchdog()
        buffer = self.Gst.Buffer.new_allocate(None, len(data), None)
        buffer.fill(0, data)
        flow_result = self.appsrc.emit("push-buffer", buffer)
        if flow_result != self.Gst.FlowReturn.OK:
            self.errors += 1
            self.status = DecoderStatus.ERROR
            self._pipeline_error = RuntimeError(
                f"Jetson GStreamer rejected encoded packet: {flow_result}"
            )
            # FLUSHING/ERROR 等多为 pipeline 内部异常，重建可恢复
            self._pipeline_error_recoverable = True
            if self._try_restart_pipeline():
                return
            raise RuntimeError(str(self._fatal_error or self._pipeline_error))
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
        self._teardown_pipeline()
        self.GstVideo = None
