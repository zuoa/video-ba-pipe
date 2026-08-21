import argparse
import logging
import math
import os
import signal
import sys
import time
from multiprocessing import resource_tracker

from app import logger
from app.config import (
    SNAPSHOT_ENABLED,
    SNAPSHOT_SAVE_PATH,
    SNAPSHOT_INTERVAL,
    ANALYSIS_BUFFER_SECONDS,
    ANALYSIS_TARGET_FPS,
    DECODE_KEYFRAMES_ONLY,
    DEFAULT_DECODE_HEIGHT,
    DEFAULT_DECODE_WIDTH,
    VIDEO_DECODER_TYPE,
    VIDEO_FRAME_PIXEL_FORMAT,
    FFMPEG_DIRECT_RTSP_ENABLED,
    FFMPEG_SW_DECODER_THREADS,
    FFMPEG_SW_KEYFRAME_FALLBACK_SECONDS,
    FFMPEG_SW_KEYFRAME_FALLBACK_MIN_BYTES,
    DECODER_OUTPUT_QUEUE_SIZE,
    RECORDING_BUFFER_DURATION,
    RECORDING_COMPRESSED_MAX_BYTES,
    RECORDING_FPS,
    RECORDING_JPEG_QUALITY,
    NO_FRAME_CRITICAL_THRESHOLD,
    LOW_FPS_RATIO,
    FPS_CHECK_INTERVAL,
    MAX_CONSECUTIVE_ERRORS,
    MONITOR_UPDATE_INTERVAL,
    HEALTH_MONITOR_ENABLED,
    RESOURCE_PROFILING_ENABLED,
    RESOURCE_PROFILE_LOG_INTERVAL_SECONDS,
    HW_DECODE_NV_GPU_INDEX,
)
from app.core.compressed_ringbuffer import CompressedVideoRingBuffer
from app.core.database_models import VideoSource
from app.core.decoder import DecoderFactory
from app.core.decoder.async_dec import SOFTWARE_DECODE_FALLBACK_EXIT_CODE
from app.core.decoder.base import DecoderStatus
from app.core.hw_decode_budget import NVDEC_DECODER_TYPES, RKMPP_DECODER_TYPES
from app.core.ringbuffer import VideoRingBuffer
from app.core.streamer import StreamerFactory  # 使用工厂模式
from app.core.utils import save_frame


class DecoderWorker:
    """通用视频流解码工作进程，支持 RTSP、文件、HTTP-FLV、HLS 等多种流类型"""

    def __init__(
        self,
        stream_url,
        analysis_buffer_name,
        recording_buffer_name,
        source_info,
        stream_config=None,
        decoder_config=None,
        analysis_config=None,
        recording_config=None,
    ):
        """
        初始化解码工作进程。

        Args:
            stream_url: 流源地址（RTSP URL、文件路径、HTTP-FLV URL等）
            analysis_buffer_name: 分析缓冲区名称
            recording_buffer_name: 录制缓冲区名称
            source_info: 源信息字典，包含 code 和 name
            stream_config: 流配置字典，包含 type, transport, loop 等参数
            decoder_config: 解码器配置字典
            analysis_config: 分析采样配置字典
            recording_config: 录制采样配置字典
        """
        self.stream_url = stream_url
        self.analysis_buffer_name = analysis_buffer_name
        self.recording_buffer_name = recording_buffer_name
        self.source_info = source_info or {}
        self.stream_config = stream_config or {}
        self.decoder_config = decoder_config or {}
        self.analysis_config = analysis_config or {}
        self.recording_config = recording_config or {}

        self.analysis_buffer = None
        self.recording_buffer = None
        self.streamer = None
        self.decoder = None
        self.running = False
        self.direct_rtsp_enabled = bool(
            self.decoder_config.get(
                'direct_rtsp_enabled',
                FFMPEG_DIRECT_RTSP_ENABLED,
            )
        )
        self.direct_rtsp_active = False
        self.direct_rtsp_fallback_used = False
        self._source = None
        self.software_full_frame_fallback_requested = False
        self._software_no_output_started_at = None

        self.analysis_sample_mode = self.analysis_config.get('mode', 'fps')
        self.analysis_sample_interval = self.analysis_config.get('interval', 1.0)
        self.analysis_target_fps = max(1, int(self.analysis_config.get('fps', ANALYSIS_TARGET_FPS)))
        self.analysis_last_write_time = 0.0
        self.analysis_frame_interval = 1.0 / self.analysis_target_fps if self.analysis_target_fps > 0 else 0.0

        self.recording_target_fps = max(1, int(self.recording_config.get('fps', RECORDING_FPS)))
        self.recording_last_write_time = 0.0
        self.recording_frame_interval = 1.0 / self.recording_target_fps if self.recording_target_fps > 0 else 0.0

        # Snapshot
        self.last_snapshot_time = 0
        self.snapshot_interval = SNAPSHOT_INTERVAL

        # 健康监控
        self.last_frame_time = None  # 最后一次成功获取帧的时间
        self.last_monitor_update = 0  # 上次更新监控时间戳的时间
        self.last_warning_time = 0  # 上次输出警告的时间
        self.warning_interval = 10  # 警告间隔（秒）
        self.expected_fps = self.analysis_target_fps if self.analysis_sample_mode == 'fps' else 1
        self.fps_check_grace_period = 30  # 帧率检查宽限期（秒），启动后30秒内不检查帧率

    def _required_decode_output_fps(self, source_fps: int) -> int:
        """Return the minimum decoder output rate needed by active consumers."""
        source_fps = max(1, int(source_fps))
        if self.analysis_sample_mode == 'all':
            analysis_fps = source_fps
        elif self.analysis_sample_mode == 'interval':
            interval = max(float(self.analysis_sample_interval), 0.001)
            analysis_fps = max(1, math.ceil(1.0 / interval))
        else:
            analysis_fps = self.analysis_target_fps

        required_fps = analysis_fps
        if self.recording_buffer_name:
            required_fps = max(required_fps, self.recording_target_fps)
        return min(source_fps, required_fps)

    @staticmethod
    def _configured_decode_output_fps(source) -> int:
        """Return the decoder FPS configured on the video source.

        ``source_fps`` is exposed as “解码帧率” in video source management.  It
        is therefore an explicit decoder output cap, not merely an estimate of
        the camera's native FPS and not the analysis-buffer sampling rate.
        """
        if source is None:
            return 0
        return max(1, int(source.source_fps))

    def setup(self, source=None):
        """初始化所有组件"""
        try:
            self._source = source
            # 如果提供了source参数，使用source的参数，否则使用默认配置
            if source:
                width = int(source.source_decode_width)
                height = int(source.source_decode_height)
                logger.info(
                    f"使用视频源参数: width={width}, height={height}, pixel_format={VIDEO_FRAME_PIXEL_FORMAT}"
                )
            else:
                width = DEFAULT_DECODE_WIDTH
                height = DEFAULT_DECODE_HEIGHT
                logger.info(
                    f"使用默认配置: width={width}, height={height}, pixel_format={VIDEO_FRAME_PIXEL_FORMAT}"
                )

            self.analysis_buffer = VideoRingBuffer(
                name=self.analysis_buffer_name,
                create=False,
                width=width,
                height=height,
                pixel_format=VIDEO_FRAME_PIXEL_FORMAT,
                fps=self.analysis_target_fps,
                duration_seconds=ANALYSIS_BUFFER_SECONDS
            )
            logger.info(
                f"已连接分析缓冲区: {self.analysis_buffer_name} "
                f"(fps={self.analysis_target_fps}, duration={ANALYSIS_BUFFER_SECONDS}s, "
                f"capacity={self.analysis_buffer.capacity}, frame_shape={self.analysis_buffer.frame_shape}, "
                f"pixel_format={self.analysis_buffer.pixel_format})"
            )

            # 注销资源跟踪器(避免进程退出时的警告)
            shm_name = self.analysis_buffer_name if os.name == 'nt' else f"/{self.analysis_buffer_name}"
            resource_tracker.unregister(shm_name, 'shared_memory')

            if self.recording_buffer_name:
                self.recording_buffer = CompressedVideoRingBuffer(
                    name=self.recording_buffer_name,
                    create=False,
                    width=width,
                    height=height,
                    pixel_format=VIDEO_FRAME_PIXEL_FORMAT,
                    fps=self.recording_target_fps,
                    duration_seconds=RECORDING_BUFFER_DURATION,
                    max_frame_bytes=RECORDING_COMPRESSED_MAX_BYTES,
                    jpeg_quality=RECORDING_JPEG_QUALITY,
                )
                logger.info(
                    f"已连接录制缓冲区: {self.recording_buffer_name} "
                    f"(compressed jpeg, fps={self.recording_target_fps}, duration={RECORDING_BUFFER_DURATION}s, "
                    f"capacity={self.recording_buffer.capacity}, frame_shape={self.recording_buffer.frame_shape}, "
                    f"pixel_format={self.recording_buffer.pixel_format})"
                )
                shm_name = self.recording_buffer_name if os.name == 'nt' else f"/{self.recording_buffer_name}"
                resource_tracker.unregister(shm_name, 'shared_memory')

            prefer_direct = self._direct_rtsp_eligible()
            try:
                self._create_decode_path(source, direct_rtsp=prefer_direct)
            except Exception as direct_error:
                if not prefer_direct:
                    raise
                logger.warning(
                    f"单阶段 RTSP 解码初始化失败，回退两阶段链路: {direct_error}",
                    exc_info=True,
                )
                self._cleanup_decode_path()
                self.direct_rtsp_fallback_used = True
                self._create_decode_path(source, direct_rtsp=False)

            if self.analysis_sample_mode == 'all':
                logger.info("分析采样模式: 写入所有帧")
            elif self.analysis_sample_mode == 'interval':
                logger.info(f"分析采样模式: 按时间间隔 ({self.analysis_sample_interval}秒)")
            elif self.analysis_sample_mode == 'fps':
                logger.info(f"分析采样模式: 按目标帧率 ({self.analysis_target_fps} fps)")

            if self.recording_buffer:
                logger.info(f"录制采样模式: 按目标帧率 ({self.recording_target_fps} fps)")

        except Exception as e:
            logger.error(f"初始化失败: {e}", exc_info=True)
            self.cleanup()
            raise

    def _resolved_stream_type(self) -> str:
        stream_type = str(self.stream_config.get('type') or '').strip().lower()
        if stream_type:
            return stream_type
        url_lower = self.stream_url.lower()
        if url_lower.startswith(('rtsp://', 'rtsps://')):
            return 'rtsp'
        if url_lower.endswith('.flv') or 'flv' in url_lower:
            return 'http-flv'
        if url_lower.endswith(('.m3u8', '.m3u')):
            return 'hls'
        if url_lower.startswith(('http://', 'https://')):
            return 'http-flv'
        return 'file'

    def _direct_rtsp_eligible(self) -> bool:
        decoder_type = str(
            self.decoder_config.get('type', VIDEO_DECODER_TYPE)
        ).lower()
        ffmpeg_direct_types = {
            'ffmpeg_sw',
            'ffmpeg',
            *NVDEC_DECODER_TYPES,
            *RKMPP_DECODER_TYPES,
        }
        return (
            self.direct_rtsp_enabled
            and self._resolved_stream_type() == 'rtsp'
            and decoder_type in ffmpeg_direct_types
            and not bool(
                self.decoder_config.get('keyframes_only', DECODE_KEYFRAMES_ONLY)
            )
        )

    def _decoder_kwargs(self, source, *, direct_rtsp: bool) -> dict:
        decoder_type = str(
            self.decoder_config.get('type', VIDEO_DECODER_TYPE)
        ).lower()
        if source is not None:
            width = int(source.source_decode_width)
            height = int(source.source_decode_height)
        else:
            width = int(self.decoder_config.get('width', DEFAULT_DECODE_WIDTH))
            height = int(self.decoder_config.get('height', DEFAULT_DECODE_HEIGHT))

        kwargs = {
            'decoder_id': self.decoder_config.get('id', 401),
            'width': width,
            'height': height,
            'input_format': self.decoder_config.get('input_format', 'h264'),
            'output_format': self.decoder_config.get(
                'output_format', VIDEO_FRAME_PIXEL_FORMAT
            ),
            'threads': int(
                self.decoder_config.get('threads', FFMPEG_SW_DECODER_THREADS)
            ),
            'keyframes_only': bool(
                self.decoder_config.get('keyframes_only', DECODE_KEYFRAMES_ONLY)
            ),
            'output_queue_size': int(
                self.decoder_config.get(
                    'output_queue_size', DECODER_OUTPUT_QUEUE_SIZE
                )
            ),
        }
        ffmpeg_types = {
            'ffmpeg_sw',
            'ffmpeg',
            *NVDEC_DECODER_TYPES,
            *RKMPP_DECODER_TYPES,
        }
        if decoder_type in ffmpeg_types:
            output_fps = self._configured_decode_output_fps(source)
            kwargs['output_fps'] = output_fps
            logger.info(
                "FFmpeg 解码输出帧率使用视频源配置: "
                f"{output_fps or 'unlimited'} fps"
            )
        if decoder_type in NVDEC_DECODER_TYPES:
            kwargs['device_id'] = int(
                self.decoder_config.get('device_id', HW_DECODE_NV_GPU_INDEX)
            )
        if decoder_type in {'jetson_gst', 'jetson', 'nvv4l2'}:
            if kwargs['keyframes_only']:
                logger.warning(
                    "Jetson GStreamer 解码器不支持仅关键帧模式，将继续完整解码"
                )
            source_fps = int(source.source_fps) if source is not None else 0
            kwargs.update(
                input_fps=source_fps,
                output_fps=(
                    self._required_decode_output_fps(source_fps)
                    if source_fps > 0
                    else 0
                ),
            )
        if direct_rtsp:
            kwargs.update(
                input_url=self.stream_url,
                rtsp_transport=self.stream_config.get('transport', 'tcp'),
            )
        return kwargs

    def _create_decode_path(self, source, *, direct_rtsp: bool):
        decoder_type = str(
            self.decoder_config.get('type', VIDEO_DECODER_TYPE)
        ).lower()
        kwargs = self._decoder_kwargs(source, direct_rtsp=direct_rtsp)
        width = kwargs['width']
        height = kwargs['height']

        if not direct_rtsp:
            self.streamer = StreamerFactory.create_streamer(
                source=self.stream_url,
                stream_type=self.stream_config.get('type'),
                **self._build_stream_kwargs(),
            )
            logger.info(
                f"已初始化流处理器: {self.streamer.__class__.__name__} "
                f"({self.stream_url})"
            )

        self.decoder = DecoderFactory.create_decoder(decoder_type, **kwargs)
        if self.decoder.status != DecoderStatus.READY:
            raise RuntimeError(f"解码器初始化失败: {decoder_type}")

        self.direct_rtsp_active = direct_rtsp
        if self.streamer is not None:
            self.streamer.add_packet_handler(self.decoder.send_packet)
        mode = '单阶段 RTSP' if direct_rtsp else '两阶段'
        logger.info(
            f"已创建解码路径: {decoder_type} ({width}x{height}, {mode})"
        )

    def _cleanup_decode_path(self):
        streamer, decoder = self.streamer, self.decoder
        self.streamer = None
        self.decoder = None
        self.direct_rtsp_active = False
        if streamer is not None:
            try:
                streamer.stop()
            except Exception as exc:
                logger.error(f"停止流处理器失败: {exc}")
        if decoder is not None:
            try:
                decoder.close()
            except Exception as exc:
                logger.error(f"关闭解码器失败: {exc}")

    def _activate_legacy_fallback(self, reason: str) -> bool:
        if not self.direct_rtsp_active or self.direct_rtsp_fallback_used:
            return False
        returncode = getattr(self.decoder, 'returncode', None)
        logger.warning(
            f"单阶段 RTSP 解码异常，切换两阶段链路: "
            f"reason={reason}, returncode={returncode}"
        )
        self.direct_rtsp_fallback_used = True
        self._cleanup_decode_path()
        try:
            self._create_decode_path(self._source, direct_rtsp=False)
            self.streamer.start()
            if not self.streamer.is_running():
                raise RuntimeError("回退流处理器未能启动")
            self.last_frame_time = None
            return True
        except Exception as exc:
            logger.error(f"两阶段回退启动失败: {exc}", exc_info=True)
            self._cleanup_decode_path()
            return False

    def _decode_path_is_running(self) -> bool:
        if self.streamer is not None:
            return self.streamer.is_running()
        is_running = getattr(self.decoder, 'is_running', None)
        return bool(is_running and is_running())

    def _build_stream_kwargs(self):
        """根据流类型构建相应的参数"""
        kwargs = {}

        # 获取流类型（可能是显式指定的或需要自动检测）
        stream_type = self.stream_config.get('type')

        # 如果没有显式指定类型，尝试自动检测
        if not stream_type:
            url_lower = self.stream_url.lower()
            if url_lower.startswith('rtsp://') or url_lower.startswith('rtsps://'):
                stream_type = 'rtsp'
            elif url_lower.endswith('.flv') or 'flv' in url_lower:
                stream_type = 'http-flv'
            elif url_lower.endswith('.m3u8') or url_lower.endswith('.m3u'):
                stream_type = 'hls'
            elif url_lower.startswith('http://') or url_lower.startswith('https://'):
                stream_type = 'http-flv'
            else:
                stream_type = 'file'

        # 编码格式必须与 decoder 使用同一个探测结果，避免 streamer 重新猜测。
        kwargs['input_format'] = self.decoder_config.get('input_format', 'h264')

        # 根据流类型添加相应参数
        if stream_type == 'rtsp':
            # RTSP 特定参数
            if 'transport' in self.stream_config:
                kwargs['transport'] = self.stream_config['transport']

        elif stream_type == 'file':
            # 文件流特定参数，默认循环播放
            kwargs['loop'] = self.stream_config.get('loop', True)

        # HTTP-FLV 和 HLS 目前没有特定参数，但预留扩展空间

        return kwargs

    def _should_write_analysis_frame(self, current_time: float) -> bool:
        """判断是否应该写入分析缓冲区。"""
        if self.analysis_sample_mode == 'all':
            return True

        if self.analysis_sample_mode == 'interval':
            if current_time - self.analysis_last_write_time >= self.analysis_sample_interval:
                self.analysis_last_write_time = current_time
                return True
            return False

        if self.analysis_sample_mode == 'fps':
            if self.analysis_frame_interval == 0:
                return True
            if current_time - self.analysis_last_write_time >= self.analysis_frame_interval:
                self.analysis_last_write_time = current_time
                return True
            return False

        return True

    def _should_write_recording_frame(self, current_time: float) -> bool:
        """判断是否应该写入录制缓冲区。"""
        if not self.recording_buffer:
            return False

        if self.recording_frame_interval == 0:
            return True

        if current_time - self.recording_last_write_time >= self.recording_frame_interval:
            self.recording_last_write_time = current_time
            return True
        return False

    def _should_request_software_full_frame_fallback(self, now: float) -> bool:
        """关键帧软解已收到足量数据却持续无输出时，请求单源全帧降级。"""
        decoder = self.decoder
        if decoder is None or not getattr(decoder, 'keyframes_only', False):
            self._software_no_output_started_at = None
            return False

        output_activity = (
            int(getattr(decoder, 'frames_decoded', 0))
            + int(getattr(decoder, 'frames_dropped', 0))
        )
        bytes_processed = int(getattr(decoder, 'bytes_processed', 0))
        if (
            output_activity > 0
            or bytes_processed < FFMPEG_SW_KEYFRAME_FALLBACK_MIN_BYTES
        ):
            self._software_no_output_started_at = None
            return False

        if self._software_no_output_started_at is None:
            self._software_no_output_started_at = now
            return False

        return (
            now - self._software_no_output_started_at
            >= FFMPEG_SW_KEYFRAME_FALLBACK_SECONDS
        )

    def snapshot(self, frame):
        """保存快照"""
        if SNAPSHOT_ENABLED:
            current_time = time.time()
            if current_time - self.last_snapshot_time < self.snapshot_interval:
                return
            self.last_snapshot_time = current_time

            # 如果启用快照功能，保存当前帧为图片
            filepath = os.path.join(SNAPSHOT_SAVE_PATH, f"{self.source_info.get('code')}.jpg")
            save_frame(frame, filepath)

    def start(self):
        """启动解码工作流程"""
        try:
            if self.streamer is not None:
                self.streamer.start()
            self.running = True

            stream_type = (
                '单阶段FFmpegRTSP'
                if self.direct_rtsp_active
                else self.streamer.__class__.__name__
            )
            logger.info(
                f"[PID:{os.getpid()}] 开始解码 {stream_type}: {self.stream_url} "
                f"-> analysis={self.analysis_buffer_name}, recording={self.recording_buffer_name or 'disabled'}"
            )

            frame_count = 0
            analysis_written_count = 0
            recording_written_count = 0
            analysis_skipped_count = 0
            error_count = 0
            max_consecutive_errors = MAX_CONSECUTIVE_ERRORS
            last_error_message = None
            repeated_error_count = 0

            # 帧率监控变量
            start_time = time.time()
            path_started_at = start_time
            last_fps_check_time = start_time
            last_fps_check_frame_count = 0
            latest_frame_age_ms = 0.0
            profile_next_log_at = time.monotonic() + RESOURCE_PROFILE_LOG_INTERVAL_SECONDS
            profile_counts = {
                'get_frame': 0,
                'analysis_write': 0,
                'recording_write': 0,
            }
            profile_totals_ms = {
                'get_frame': 0.0,
                'analysis_write': 0.0,
                'recording_write': 0.0,
            }

            while self.running:
                try:
                    # 一次取出当前批次：分析只使用最后一帧，录像可使用完整批次。
                    get_frame_started_at = time.perf_counter()
                    pending_frames = self.decoder.get_pending_frames(timeout=0.5)
                    if RESOURCE_PROFILING_ENABLED:
                        profile_counts['get_frame'] += 1
                        profile_totals_ms['get_frame'] += (time.perf_counter() - get_frame_started_at) * 1000

                    if pending_frames:
                        latest_decoded_frame = pending_frames[-1]
                        frame = latest_decoded_frame.image
                        frame_count += len(pending_frames)
                        current_time = time.time()
                        frame_timestamp = latest_decoded_frame.decoded_at
                        latest_frame_age_ms = max(0.0, (current_time - frame_timestamp) * 1000)

                        # 更新最后出帧时间
                        self.last_frame_time = current_time
                        error_count = 0

                        wrote_analysis = False
                        if self._should_write_analysis_frame(current_time):
                            write_started_at = time.perf_counter()
                            self.analysis_buffer.write(frame, timestamp=frame_timestamp)
                            if RESOURCE_PROFILING_ENABLED:
                                profile_counts['analysis_write'] += 1
                                profile_totals_ms['analysis_write'] += (
                                    time.perf_counter() - write_started_at
                                ) * 1000
                            analysis_written_count += 1
                            wrote_analysis = True

                            # 定期更新监控时间戳（避免每次写帧都更新）
                            if HEALTH_MONITOR_ENABLED and \
                               current_time - self.last_monitor_update >= MONITOR_UPDATE_INTERVAL:
                                self.analysis_buffer.update_last_write_time(current_time)
                                self.last_monitor_update = current_time

                        else:
                            analysis_skipped_count += 1

                        # 同一批次除最新帧外都不得进入算法链路。
                        analysis_skipped_count += max(0, len(pending_frames) - 1)

                        # 录像按解码顺序消费批次；未启用录像时旧帧在此直接释放。
                        for decoded_frame in pending_frames:
                            if not self._should_write_recording_frame(decoded_frame.decoded_at):
                                continue
                            try:
                                recording_started_at = time.perf_counter()
                                self.recording_buffer.write(
                                    decoded_frame.image,
                                    timestamp=decoded_frame.decoded_at,
                                )
                                if RESOURCE_PROFILING_ENABLED:
                                    profile_counts['recording_write'] += 1
                                    profile_totals_ms['recording_write'] += (
                                        time.perf_counter() - recording_started_at
                                    ) * 1000
                                recording_written_count += 1
                            except Exception as recording_error:
                                logger.warning(
                                    f"录制缓冲区写入失败，已跳过当前帧: {recording_error}"
                                )

                        if frame_count % 100 == 0 and wrote_analysis:
                            logger.info(
                                f"已解码 {frame_count} 帧, "
                                f"分析写入 {analysis_written_count} 帧, "
                                f"分析跳过 {analysis_skipped_count} 帧, "
                                f"录制写入 {recording_written_count} 帧, "
                                f"最新帧龄 {latest_frame_age_ms:.1f} ms"
                            )
                            self.snapshot(frame)

                        # 定期检查帧率
                        if current_time - last_fps_check_time >= FPS_CHECK_INTERVAL:
                            # 计算最近10秒的帧率（用于实时监控）
                            recent_frame_count = frame_count - last_fps_check_frame_count
                            recent_fps = recent_frame_count / FPS_CHECK_INTERVAL

                            # 计算整体平均帧率（用于参考）
                            time_since_start = current_time - start_time
                            overall_fps = frame_count / time_since_start if time_since_start > 0 else 0

                            # 检查低帧率（仅在宽限期后，使用最近10秒帧率检测实时下降）
                            if time_since_start > self.fps_check_grace_period:
                                if self.expected_fps > 0 and recent_fps < self.expected_fps * LOW_FPS_RATIO:
                                    logger.warning(
                                        f"帧率异常: 期望 {self.expected_fps:.2f} fps, "
                                        f"最近10秒 {recent_fps:.2f} fps "
                                        f"({recent_fps/self.expected_fps*100:.1f}%), "
                                        f"整体平均 {overall_fps:.2f} fps"
                                    )

                            last_fps_check_time = current_time
                            last_fps_check_frame_count = frame_count

                            # 输出当前状态（同时显示两种帧率）
                            logger.info(
                                f"解码状态: 已解码 {frame_count} 帧, "
                                f"分析写入 {analysis_written_count} 帧, "
                                f"录制写入 {recording_written_count} 帧, "
                                f"队列淘汰 {self.decoder.frames_dropped} 帧, "
                                f"最新帧龄 {latest_frame_age_ms:.1f} ms, "
                                f"最近10秒 {recent_fps:.2f} fps, "
                                f"整体平均 {overall_fps:.2f} fps"
                            )

                        if RESOURCE_PROFILING_ENABLED and time.monotonic() >= profile_next_log_at:
                            def _avg_ms(name):
                                count = profile_counts[name]
                                return profile_totals_ms[name] / count if count else 0.0

                            logger.info(
                                "Decoder profile: "
                                f"get_frame_count={profile_counts['get_frame']}, "
                                f"avg_get_frame_ms={_avg_ms('get_frame'):.2f}, "
                                f"analysis_writes={profile_counts['analysis_write']}, "
                                f"avg_analysis_write_ms={_avg_ms('analysis_write'):.2f}, "
                                f"recording_writes={profile_counts['recording_write']}, "
                                f"avg_recording_write_ms={_avg_ms('recording_write'):.2f}"
                            )
                            profile_next_log_at = time.monotonic() + RESOURCE_PROFILE_LOG_INTERVAL_SECONDS
                            for key in profile_counts:
                                profile_counts[key] = 0
                                profile_totals_ms[key] = 0.0

                    else:
                        # 检查流是否仍在运行
                        if not self._decode_path_is_running():
                            if self._activate_legacy_fallback('解码进程或视频流已停止'):
                                path_started_at = time.time()
                                error_count = 0
                                continue
                            logger.warning("视频解码路径已停止")
                            break

                        if self._should_request_software_full_frame_fallback(
                            time.monotonic()
                        ):
                            self.software_full_frame_fallback_requested = True
                            logger.warning(
                                "关键帧软解已持续收到 "
                                f"{self.decoder.bytes_processed / 1024 / 1024:.2f} MB "
                                "码流但仍无帧输出，请求切换当前视频源为全帧软解"
                            )
                            break

                        # 检查是否长时间无帧
                        if HEALTH_MONITOR_ENABLED:
                            last_frame_reference = self.last_frame_time or path_started_at
                            time_no_frame = time.time() - last_frame_reference
                            if time_no_frame >= NO_FRAME_CRITICAL_THRESHOLD:
                                if self._activate_legacy_fallback(
                                    f'持续 {time_no_frame:.1f} 秒无帧输出'
                                ):
                                    path_started_at = time.time()
                                    error_count = 0
                                    continue
                                logger.critical(
                                    f"已 {time_no_frame:.1f} 秒无有效帧输出，"
                                    f"可能解码器卡死或流断开，主动退出"
                                )
                                # 记录错误到buffer
                                if self.analysis_buffer:
                                    self.analysis_buffer.increment_error_count()
                                break
                            elif time_no_frame >= NO_FRAME_CRITICAL_THRESHOLD * 0.7:
                                # 70% 阈值时警告（限制频率）
                                if time.time() - self.last_warning_time >= self.warning_interval:
                                    logger.warning(
                                        f"已 {time_no_frame:.1f} 秒无有效帧输出"
                                    )
                                    self.last_warning_time = time.time()

                        error_count += 1
                        if error_count >= max_consecutive_errors:
                            if self._activate_legacy_fallback(
                                f'连续 {error_count} 次未获取到帧'
                            ):
                                path_started_at = time.time()
                                error_count = 0
                                continue
                            logger.error(f"连续 {error_count} 次获取帧失败，停止工作")
                            break

                except KeyboardInterrupt:
                    logger.info("收到中断信号")
                    break
                except Exception as e:
                    if self._activate_legacy_fallback(f'处理帧异常: {e}'):
                        path_started_at = time.time()
                        error_count = 0
                        continue
                    # 解码器不可恢复错误（如 Jetson pipeline 重建连续失败），
                    # 立即退出交给 orchestrator 重启进程，不要空转重试
                    fatal_error = getattr(self.decoder, 'fatal_error', None)
                    if fatal_error is not None:
                        logger.error(f"解码器出现不可恢复错误，停止工作: {fatal_error}")
                        if self.analysis_buffer:
                            self.analysis_buffer.increment_error_count()
                        break

                    # 相同错误（如 pipeline 恢复退避期间）只首条打完整 traceback，
                    # 避免毫秒内刷满日志
                    error_message = str(e)
                    if error_message != last_error_message:
                        logger.error(f"处理帧时出错: {e}", exc_info=True)
                        last_error_message = error_message
                        repeated_error_count = 0
                    else:
                        repeated_error_count += 1
                        if repeated_error_count % 20 == 0:
                            logger.error(
                                f"处理帧时出错（相同错误已重复 {repeated_error_count} 次）: {e}"
                            )

                    error_count += 1
                    if self.analysis_buffer:
                        self.analysis_buffer.increment_error_count()
                    if error_count >= max_consecutive_errors:
                        logger.error("错误过多，停止工作")
                        break

            # 结束统计
            total_duration = time.time() - start_time
            avg_fps = frame_count / total_duration if total_duration > 0 else 0
            logger.info(
                f"解码完成: 总时长 {total_duration:.1f}s, "
                f"总帧数 {frame_count}, "
                f"平均帧率 {avg_fps:.2f} fps"
            )

        except Exception as e:
            logger.error(f"解码过程出错: {e}", exc_info=True)
        finally:
            self.cleanup()

        return self.software_full_frame_fallback_requested

    def cleanup(self):
        """清理资源"""
        logger.info("开始清理资源...")
        self.running = False
        self._cleanup_decode_path()

        if self.analysis_buffer:
            try:
                self.analysis_buffer.close()
                logger.info("已断开分析缓冲区连接")
            except Exception as e:
                logger.error(f"关闭分析缓冲区失败: {e}")

        if self.recording_buffer:
            try:
                self.recording_buffer.close()
                logger.info("已断开录制缓冲区连接")
            except Exception as e:
                logger.error(f"关闭录制缓冲区失败: {e}")

        logger.info("资源清理完成")

    def signal_handler(self, signum, frame):
        """处理系统信号"""
        logger.info(f"收到信号 {signum}，准备退出...")
        self.running = False


def _redirect_logs_to_decoder_files():
    """解码进程独立运行：把 aj 日志从共享的 run.log/debug.log 拆到 decoder 专属文件，
    避免与 orchestrator/workflow 日志互相淹没。"""
    from app import create_rotating_file_handler
    from app.config import DECODER_DEBUG_LOG_PATH, DECODER_LOG_PATH

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.addHandler(create_rotating_file_handler(DECODER_LOG_PATH, logging.INFO))
    logger.addHandler(create_rotating_file_handler(DECODER_DEBUG_LOG_PATH, logging.DEBUG))

    # 本进程的 stdout/stderr 已被 orchestrator 接管并写入 decoder.log，
    # 屏蔽控制台输出，避免同一条日志经管道重复落盘
    for handler in logging.getLogger().handlers:
        handler.setLevel(logging.CRITICAL)


def main(args):
    """主函数"""

    _redirect_logs_to_decoder_files()

    logger.info("启动 DECODER 工作进程")

    source_id = args.source_id

    source = VideoSource.get_by_id(source_id)
    source_code = source.source_code
    source_name = source.name

    source_info = {
        'code': source_code,
        'name': source_name
    }

    # 流配置
    stream_config = {
        'type': args.stream_type,
        'transport': args.transport,  # RTSP transport
        'loop': args.loop,  # 文件流循环播放
    }

    if args.software_decode_keyframes_only is not None:
        logger.warning(
            "--software-decode-keyframes-only 已弃用，请改用 --decode-keyframes-only"
        )

    # 解码器配置
    decoder_config = {
        'type': args.decoder_type,
        'id': args.decoder_id,
        'width': args.width,
        'height': args.height,
        'input_format': args.input_format,
        'output_format': args.output_format,
        'threads': args.decoder_threads,
        'keyframes_only': (
            args.software_decode_keyframes_only
            if args.software_decode_keyframes_only is not None
            else args.decode_keyframes_only
        ),
        'output_queue_size': args.decoder_output_queue_size,
    }

    analysis_config = {
        'mode': args.sample_mode,
        'interval': args.sample_interval,
        'fps': args.analysis_fps or args.sample_fps or ANALYSIS_TARGET_FPS
    }

    recording_config = {
        'fps': args.recording_fps or RECORDING_FPS
    }

    # 创建工作进程
    worker = DecoderWorker(
        stream_url=args.url,
        analysis_buffer_name=source.analysis_buffer_name,
        recording_buffer_name=(
            source.recording_buffer_name if args.recording_enabled else None
        ),
        source_info=source_info,
        stream_config=stream_config,
        decoder_config=decoder_config,
        analysis_config=analysis_config,
        recording_config=recording_config
    )

    # 注册信号处理器
    signal.signal(signal.SIGINT, worker.signal_handler)
    signal.signal(signal.SIGTERM, worker.signal_handler)

    try:
        worker.setup(source=source)
        fallback_requested = worker.start()
    except Exception as e:
        logger.error(f"工作进程异常退出: {e}", exc_info=True)
        sys.exit(1)

    logger.warning("停止 DECODER 工作进程")
    if fallback_requested:
        sys.exit(SOFTWARE_DECODE_FALLBACK_EXIT_CODE)
    sys.exit(0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='通用视频流解码工作进程')

    # 必需参数
    parser.add_argument('--url', required=True,
                        help='流源地址 (RTSP URL、文件路径、HTTP-FLV URL、HLS URL等)')
    parser.add_argument('--source-id', required=True, help='视频源ID')

    # 流类型配置
    stream_group = parser.add_argument_group('流类型配置')
    stream_group.add_argument('--stream-type', default=None,
                              choices=['rtsp', 'file', 'http-flv', 'flv', 'hls', None],
                              help='显式指定流类型，留空则自动检测 (默认: 自动检测)')
    stream_group.add_argument('--transport', default='tcp',
                              choices=['tcp', 'udp'],
                              help='RTSP 传输协议 (默认: tcp)')
    stream_group.add_argument('--loop', action='store_true',
                              help='文件流是否循环播放')

    # 解码器配置参数
    decoder_group = parser.add_argument_group('解码器配置')
    decoder_group.add_argument('--decoder-type', default=VIDEO_DECODER_TYPE,
                               choices=[
                                   'ffmpeg_sw', 'ffmpeg',
                                   'ffmpeg_nvdec', 'nvdec',
                                   'opencv', 'pynvcodec', 'gstreamer',
                                   'jetson_gst', 'jetson', 'nvv4l2',
                                   'ffmpeg_videotoolbox', 'vtdec',
                                   'ffmpeg_rkmpp', 'rk_mpp', 'rkmpp'
                               ],
                               help='解码器类型 (RK3588 可设 rk_mpp；Jetson 可设 jetson_gst)')
    decoder_group.add_argument('--decoder-id', type=int, default=401,
                               help='解码器 ID (默认: 401)')
    decoder_group.add_argument('--width', type=int, default=DEFAULT_DECODE_WIDTH,
                               help=f'视频宽度 (默认: {DEFAULT_DECODE_WIDTH})')
    decoder_group.add_argument('--height', type=int, default=DEFAULT_DECODE_HEIGHT,
                               help=f'视频高度 (默认: {DEFAULT_DECODE_HEIGHT})')
    decoder_group.add_argument('--input-format', default='h264',
                               choices=['h264', 'h265', 'mjpeg'],
                               help='输入格式 (默认: h264)')
    decoder_group.add_argument('--output-format', default=VIDEO_FRAME_PIXEL_FORMAT,
                               choices=['nv12', 'rgb24', 'bgr24', 'yuv420p'],
                               help='输出格式 (默认读取 VIDEO_FRAME_PIXEL_FORMAT)')
    decoder_group.add_argument('--decoder-threads', type=int, default=FFMPEG_SW_DECODER_THREADS,
                               help='软解 ffmpeg 线程数 (默认读取 FFMPEG_SW_DECODER_THREADS)')
    decoder_group.add_argument('--decoder-output-queue-size', type=int, default=DECODER_OUTPUT_QUEUE_SIZE,
                               help='解码输出队列大小，越大越占内存 (默认读取 DECODER_OUTPUT_QUEUE_SIZE)')
    decoder_group.add_argument(
        '--decode-keyframes-only',
        type=lambda value: str(value).lower() in {'true', '1', 'yes', 'on'},
        default=DECODE_KEYFRAMES_ONLY,
        help='是否只解码关键帧（默认读取 DECODE_KEYFRAMES_ONLY，默认关闭）',
    )
    decoder_group.add_argument(
        '--software-decode-keyframes-only',
        type=lambda value: str(value).lower() in {'true', '1', 'yes', 'on'},
        default=None,
        help=argparse.SUPPRESS,
    )

    # 帧采样参数
    sample_group = parser.add_argument_group('帧采样配置')
    sample_group.add_argument('--sample-mode', default='interval',
                              choices=['all', 'interval', 'fps'],
                              help='采样模式: all=所有帧, interval=按时间间隔, fps=按目标帧率 (默认: interval)')
    sample_group.add_argument('--sample-interval', type=float, default=1.0,
                              help='采样时间间隔(秒), 仅在 interval 模式下生效 (默认: 1.0)')
    sample_group.add_argument('--sample-fps', type=float, default=None,
                              help='目标采样帧率, 仅在 fps 模式下生效 (例如: 5 表示每秒5帧)')
    sample_group.add_argument('--analysis-fps', type=float, default=None,
                              help='分析缓冲区目标帧率（默认读取 ANALYSIS_TARGET_FPS）')
    sample_group.add_argument('--recording-fps', type=float, default=None,
                              help='录制缓冲区目标帧率（默认读取 RECORDING_FPS）')
    sample_group.add_argument(
        '--recording-enabled',
        type=lambda value: str(value).lower() in {'true', '1', 'yes', 'on'},
        default=False,
        help='是否写入录制缓冲区（默认关闭）',
    )

    # 日志级别
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        help='日志级别 (默认: INFO)')

    args = parser.parse_args()

    main(args)
