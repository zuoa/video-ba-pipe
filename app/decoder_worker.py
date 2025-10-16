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
    IS_EXTREME_DECODE_MODE,
    RECORDING_FPS
)
from app.core.database_models import Task
from app.core.decoder import DecoderFactory
from app.core.ringbuffer import VideoRingBuffer
from app.core.streamer import StreamerFactory  # 使用工厂模式
from app.core.utils import save_frame


class DecoderWorker:
    """通用视频流解码工作进程，支持 RTSP、文件、HTTP-FLV、HLS 等多种流类型"""

    def __init__(self, stream_url, buffer_name, source_info,
                 stream_config=None, decoder_config=None, sample_config=None):
        """
        初始化解码工作进程。

        Args:
            stream_url: 流源地址（RTSP URL、文件路径、HTTP-FLV URL等）
            buffer_name: 共享内存缓冲区名称
            source_info: 源信息字典，包含 code 和 name
            stream_config: 流配置字典，包含 type, transport, loop 等参数
            decoder_config: 解码器配置字典
            sample_config: 采样配置字典
        """
        self.stream_url = stream_url
        self.buffer_name = buffer_name
        self.source_info = source_info or {}
        self.stream_config = stream_config or {}
        self.decoder_config = decoder_config or {}
        self.sample_config = sample_config or {}

        self.buffer = None
        self.streamer = None
        self.decoder = None
        self.running = False

        # 采样配置
        self.sample_mode = self.sample_config.get('mode', 'interval')  # all, interval, fps
        self.sample_interval = self.sample_config.get('interval', 1.0)  # 秒
        self.target_fps = self.sample_config.get('fps', 1)

        # 采样状态
        self.last_write_time = 0
        self.frame_interval = 0
        if self.target_fps and self.target_fps > 0:
            self.frame_interval = 1.0 / self.target_fps

        # Snapshot
        self.last_snapshot_time = 0
        self.snapshot_interval = SNAPSHOT_INTERVAL

    def setup(self):
        """初始化所有组件"""
        try:
            # 连接到共享内存缓冲区（必须使用与创建时相同的参数）
            from app.config import RINGBUFFER_DURATION, RECORDING_FPS
            self.buffer = VideoRingBuffer(
                name=self.buffer_name, 
                create=False,
                fps=RECORDING_FPS,
                duration_seconds=RINGBUFFER_DURATION
            )
            logger.info(f"已连接到缓冲区: {self.buffer_name} (fps={RECORDING_FPS}, duration={RINGBUFFER_DURATION}s, capacity={self.buffer.capacity})")

            # 注销资源跟踪器(避免进程退出时的警告)
            shm_name = self.buffer_name if os.name == 'nt' else f"/{self.buffer_name}"
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
            width = self.decoder_config.get('width', 1920)
            height = self.decoder_config.get('height', 1080)
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

            # 日志采样配置
            if self.sample_mode == 'all':
                logger.info("采样模式: 写入所有帧")
            elif self.sample_mode == 'interval':
                logger.info(f"采样模式: 按时间间隔 ({self.sample_interval}秒)")
            elif self.sample_mode == 'fps':
                logger.info(f"采样模式: 按目标帧率 ({self.target_fps} fps)")

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
            # 文件流特定参数
            if 'loop' in self.stream_config:
                kwargs['loop'] = self.stream_config['loop']

        # HTTP-FLV 和 HLS 目前没有特定参数，但预留扩展空间

        return kwargs

    def should_write_frame(self):
        """判断是否应该写入当前帧"""
        if self.sample_mode == 'all':
            return True

        current_time = time.time()

        if self.sample_mode == 'interval':
            # 按时间间隔采样
            if current_time - self.last_write_time >= self.sample_interval:
                self.last_write_time = current_time
                return True
            return False

        elif self.sample_mode == 'fps':
            # 按目标帧率采样
            if self.frame_interval == 0:
                return True
            if current_time - self.last_write_time >= self.frame_interval:
                self.last_write_time = current_time
                return True
            return False

        return True

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
            logger.info(f"[PID:{os.getpid()}] 开始解码 {stream_type}: {self.stream_url} -> {self.buffer_name}")

            frame_count = 0
            written_count = 0
            skipped_count = 0
            error_count = 0
            max_consecutive_errors = 60

            while self.running:
                try:
                    # 获取解码后的帧
                    if IS_EXTREME_DECODE_MODE:
                        frame = self.decoder.get_latest_frame()
                    else:
                        frame = self.decoder.get_frame(timeout=1.0)

                    if frame is not None:
                        frame_count += 1

                        # 判断是否写入此帧
                        if self.should_write_frame():
                            # 写入共享内存
                            self.buffer.write(frame)
                            written_count += 1
                            error_count = 0  # 重置错误计数

                            logger.info(f"已写入 {written_count} 帧 (总解码: {frame_count}, 跳过: {skipped_count})")

                            self.snapshot(frame)
                        else:
                            skipped_count += 1

                    else:
                        # 检查流是否仍在运行
                        if not self.streamer.is_running():
                            logger.warning("视频流已停止")
                            break

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
                    if error_count >= max_consecutive_errors:
                        logger.error("错误过多，停止工作")
                        break

            logger.info(f"解码完成，共处理 {frame_count} 帧")

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

        if self.buffer:
            try:
                self.buffer.close()
                logger.info("已断开缓冲区连接")
            except Exception as e:
                logger.error(f"关闭缓冲区失败: {e}")

        logger.info("资源清理完成")

    def signal_handler(self, signum, frame):
        """处理系统信号"""
        logger.info(f"收到信号 {signum}，准备退出...")
        self.running = False


def main(args):
    """主函数"""

    logger.info("启动 DECODER 工作进程")

    task_id = args.task_id

    task = Task.get_by_id(task_id)  # 确保任务存在，否则抛出异常
    source_code = task.source_code
    source_name = task.source_name
    buffer_name = f"{task.buffer_name}.{task.id}"

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

    # 采样配置
    sample_config = {
        'mode': args.sample_mode,
        'interval': args.sample_interval,
        'fps': args.sample_fps
    }

    # 创建工作进程
    worker = DecoderWorker(
        stream_url=args.url,
        buffer_name=buffer_name,
        source_info=source_info,
        stream_config=stream_config,
        decoder_config=decoder_config,
        sample_config=sample_config
    )

    # 注册信号处理器
    signal.signal(signal.SIGINT, worker.signal_handler)
    signal.signal(signal.SIGTERM, worker.signal_handler)

    try:
        worker.setup()
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
    parser.add_argument('--task-id', required=True, help='任务ID')

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

    # 日志级别
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        help='日志级别 (默认: INFO)')

    args = parser.parse_args()

    main(args)
