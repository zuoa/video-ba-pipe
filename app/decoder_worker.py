import argparse
import logging
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
    IS_EXTREME_DECODE_MODE,
    RECORDING_BUFFER_DURATION,
    RECORDING_COMPRESSED_MAX_BYTES,
    RECORDING_ENABLED,
    RECORDING_FPS,
    RECORDING_JPEG_QUALITY,
    NO_FRAME_CRITICAL_THRESHOLD,
    LOW_FPS_RATIO,
    FPS_CHECK_INTERVAL,
    MAX_CONSECUTIVE_ERRORS,
    MONITOR_UPDATE_INTERVAL,
    HEALTH_MONITOR_ENABLED
)
from app.core.compressed_ringbuffer import CompressedVideoRingBuffer
from app.core.database_models import VideoSource
from app.core.decoder import DecoderFactory
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

    def setup(self, source=None):
        """初始化所有组件"""
        try:
            # 如果提供了source参数，使用source的参数，否则使用默认配置
            if source:
                frame_shape = (source.source_decode_height, source.source_decode_width, 3)
                logger.info(f"使用视频源参数: frame_shape={frame_shape}")
            else:
                frame_shape = (1080, 1920, 3)  # 默认形状
                logger.info(f"使用默认配置: frame_shape={frame_shape}")

            self.analysis_buffer = VideoRingBuffer(
                name=self.analysis_buffer_name,
                create=False,
                frame_shape=frame_shape,
                fps=self.analysis_target_fps,
                duration_seconds=ANALYSIS_BUFFER_SECONDS
            )
            logger.info(
                f"已连接分析缓冲区: {self.analysis_buffer_name} "
                f"(fps={self.analysis_target_fps}, duration={ANALYSIS_BUFFER_SECONDS}s, "
                f"capacity={self.analysis_buffer.capacity}, frame_shape={self.analysis_buffer.frame_shape})"
            )

            # 注销资源跟踪器(避免进程退出时的警告)
            shm_name = self.analysis_buffer_name if os.name == 'nt' else f"/{self.analysis_buffer_name}"
            resource_tracker.unregister(shm_name, 'shared_memory')

            if self.recording_buffer_name:
                self.recording_buffer = CompressedVideoRingBuffer(
                    name=self.recording_buffer_name,
                    create=False,
                    frame_shape=frame_shape,
                    fps=self.recording_target_fps,
                    duration_seconds=RECORDING_BUFFER_DURATION,
                    max_frame_bytes=RECORDING_COMPRESSED_MAX_BYTES,
                    jpeg_quality=RECORDING_JPEG_QUALITY,
                )
                logger.info(
                    f"已连接录制缓冲区: {self.recording_buffer_name} "
                    f"(compressed jpeg, fps={self.recording_target_fps}, duration={RECORDING_BUFFER_DURATION}s, "
                    f"capacity={self.recording_buffer.capacity}, frame_shape={self.recording_buffer.frame_shape})"
                )
                shm_name = self.recording_buffer_name if os.name == 'nt' else f"/{self.recording_buffer_name}"
                resource_tracker.unregister(shm_name, 'shared_memory')

            # 使用工厂创建合适的 Streamer
            stream_type = self.stream_config.get('type')
            stream_kwargs = self._build_stream_kwargs()

            self.streamer = StreamerFactory.create_streamer(
                source=self.stream_url,
                stream_type=stream_type,
                **stream_kwargs
            )

            logger.info(f"已初始化流处理器: {self.streamer.__class__.__name__} ({self.stream_url})")

            # 初始化解码器
            decoder_type = self.decoder_config.get('type', 'ffmpeg_sw')
            decoder_id = self.decoder_config.get('id', 401)
            
            # 如果提供了source参数，使用source的宽高参数，否则使用配置参数
            if source:
                width = source.source_decode_width
                height = source.source_decode_height
                logger.info(f"使用视频源解码参数: width={width}, height={height}")
            else:
                width = self.decoder_config.get('width', 1920)
                height = self.decoder_config.get('height', 1080)
                logger.info(f"使用配置解码参数: width={width}, height={height}")
            
            input_format = self.decoder_config.get('input_format', 'h264')
            output_format = self.decoder_config.get('output_format', 'rgb24')

            self.decoder = DecoderFactory.create_decoder(
                decoder_type,
                decoder_id=decoder_id,
                width=width,
                height=height,
                input_format=input_format,
                output_format=output_format
            )
            logger.info(f"已创建解码器: {decoder_type} ({width}x{height})")

            # 连接流处理管道
            self.streamer.add_packet_handler(self.decoder.send_packet)

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
            self.streamer.start()
            self.running = True

            stream_type = self.streamer.__class__.__name__
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

            # 帧率监控变量
            start_time = time.time()
            last_fps_check_time = start_time
            last_fps_check_frame_count = 0

            while self.running:
                try:
                    # 获取解码后的帧
                    if IS_EXTREME_DECODE_MODE:
                        frame = self.decoder.get_latest_frame()
                    else:
                        frame = self.decoder.get_frame(timeout=0.5)

                    if frame is not None:
                        frame_count += 1
                        current_time = time.time()
                        frame_timestamp = current_time

                        # 更新最后出帧时间
                        self.last_frame_time = current_time

                        wrote_analysis = False
                        if self._should_write_analysis_frame(current_time):
                            self.analysis_buffer.write(frame, timestamp=frame_timestamp)
                            analysis_written_count += 1
                            wrote_analysis = True
                            error_count = 0  # 重置错误计数

                            # 定期更新监控时间戳（避免每次写帧都更新）
                            if HEALTH_MONITOR_ENABLED and \
                               current_time - self.last_monitor_update >= MONITOR_UPDATE_INTERVAL:
                                self.analysis_buffer.update_last_write_time(current_time)
                                self.last_monitor_update = current_time

                        else:
                            analysis_skipped_count += 1

                        if self._should_write_recording_frame(current_time):
                            try:
                                self.recording_buffer.write(frame, timestamp=frame_timestamp)
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
                                f"录制写入 {recording_written_count} 帧"
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
                                f"最近10秒 {recent_fps:.2f} fps, "
                                f"整体平均 {overall_fps:.2f} fps"
                            )

                    else:
                        # 检查流是否仍在运行
                        if not self.streamer.is_running():
                            logger.warning("视频流已停止")
                            break

                        # 检查是否长时间无帧
                        if HEALTH_MONITOR_ENABLED and self.last_frame_time is not None:
                            time_no_frame = time.time() - self.last_frame_time
                            if time_no_frame >= NO_FRAME_CRITICAL_THRESHOLD:
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

                        if not IS_EXTREME_DECODE_MODE:
                            error_count += 1
                            if error_count >= max_consecutive_errors:
                                logger.error(f"连续 {error_count} 次获取帧失败，停止工作")
                                break

                except KeyboardInterrupt:
                    logger.info("收到中断信号")
                    break
                except Exception as e:
                    logger.error(f"处理帧时出错: {e}", exc_info=True)
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

    def cleanup(self):
        """清理资源"""
        logger.info("开始清理资源...")
        self.running = False

        if self.streamer:
            try:
                self.streamer.stop()
                logger.info("已停止流处理器")
            except Exception as e:
                logger.error(f"停止流处理器失败: {e}")

        if self.decoder:
            try:
                self.decoder.close()
                logger.info("已关闭解码器")
            except Exception as e:
                logger.error(f"关闭解码器失败: {e}")

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


def main(args):
    """主函数"""

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

    # 解码器配置
    decoder_config = {
        'type': args.decoder_type,
        'id': args.decoder_id,
        'width': args.width,
        'height': args.height,
        'input_format': args.input_format,
        'output_format': args.output_format
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
        recording_buffer_name=source.recording_buffer_name if RECORDING_ENABLED else None,
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
        worker.start()
    except Exception as e:
        logger.error(f"工作进程异常退出: {e}", exc_info=True)
        sys.exit(1)

    logger.warning("停止 DECODER 工作进程")
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
    decoder_group.add_argument('--decoder-type', default='ffmpeg_sw',
                               choices=['ffmpeg_sw', 'ffmpeg_hw', 'nvdec'],
                               help='解码器类型 (默认: ffmpeg_sw)')
    decoder_group.add_argument('--decoder-id', type=int, default=401,
                               help='解码器 ID (默认: 401)')
    decoder_group.add_argument('--width', type=int, default=1920,
                               help='视频宽度 (默认: 1920)')
    decoder_group.add_argument('--height', type=int, default=1080,
                               help='视频高度 (默认: 1080)')
    decoder_group.add_argument('--input-format', default='h264',
                               choices=['h264', 'h265', 'mjpeg'],
                               help='输入格式 (默认: h264)')
    decoder_group.add_argument('--output-format', default='rgb24',
                               choices=['rgb24', 'bgr24', 'yuv420p'],
                               help='输出格式 (默认: rgb24)')

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

    # 日志级别
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        help='日志级别 (默认: INFO)')

    args = parser.parse_args()

    main(args)
