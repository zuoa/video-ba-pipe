import argparse
import logging
import os
import signal
import sys
import time
from multiprocessing import resource_tracker

from app import logger
from app.core.decoder import DecoderFactory
from app.core.ringbuffer import VideoRingBuffer
from app.core.streamer import RTSPStreamer

class DecoderWorker:
    """RTSP 视频流解码工作进程"""

    def __init__(self, rtsp_url, buffer_name, decoder_config=None, sample_config=None):
        self.rtsp_url = rtsp_url
        self.buffer_name = buffer_name
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

    def setup(self):
        """初始化所有组件"""
        try:
            # 连接到共享内存缓冲区
            self.buffer = VideoRingBuffer(name=self.buffer_name, create=False)
            logger.info(f"已连接到缓冲区: {self.buffer_name}")

            # 注销资源跟踪器(避免进程退出时的警告)
            shm_name = self.buffer_name if os.name == 'nt' else f"/{self.buffer_name}"
            resource_tracker.unregister(shm_name, 'shared_memory')

            # 初始化 RTSP 流接收器
            self.streamer = RTSPStreamer(rtsp_url=self.rtsp_url)
            logger.info(f"已初始化 RTSP 流: {self.rtsp_url}")

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
                logger.info(f"采样模式: 按时间间隔 ({self.sample_interval}毫秒)")
            elif self.sample_mode == 'fps':
                logger.info(f"采样模式: 按目标帧率 ({self.target_fps} fps)")

        except Exception as e:
            logger.error(f"初始化失败: {e}", exc_info=True)
            self.cleanup()
            raise

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

    def start(self):
        """启动解码工作流程"""
        try:
            self.streamer.start()
            self.running = True
            logger.info(f"[PID:{os.getpid()}] 开始解码 {self.rtsp_url} -> {self.buffer_name}")

            frame_count = 0
            written_count = 0
            skipped_count = 0
            error_count = 0
            max_consecutive_errors = 60

            while self.running:
                try:
                    # 获取解码后的帧
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
                        else:
                            skipped_count += 1

                    else:
                        # 检查流是否仍在运行
                        if not self.streamer.is_running():
                            logger.warning("RTSP 流已停止")
                            break

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
                logger.info("已停止 RTSP 流接收器")
            except Exception as e:
                logger.error(f"停止流接收器失败: {e}")

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
        rtsp_url=args.url,
        buffer_name=args.buffer,
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
    parser = argparse.ArgumentParser(description='RTSP 视频流解码工作进程')

    # 必需参数
    parser.add_argument('--url', required=True, help='RTSP 源地址')
    parser.add_argument('--buffer', required=True, help='共享内存缓冲区名称')

    # 解码器配置参数
    parser.add_argument('--decoder-type', default='ffmpeg_sw',
                        choices=['ffmpeg_sw', 'ffmpeg_hw', 'nvdec'],
                        help='解码器类型 (默认: ffmpeg_sw)')
    parser.add_argument('--decoder-id', type=int, default=401,
                        help='解码器 ID (默认: 401)')
    parser.add_argument('--width', type=int, default=1920,
                        help='视频宽度 (默认: 1920)')
    parser.add_argument('--height', type=int, default=1080,
                        help='视频高度 (默认: 1080)')
    parser.add_argument('--input-format', default='h264',
                        choices=['h264', 'h265', 'mjpeg'],
                        help='输入格式 (默认: h264)')
    parser.add_argument('--output-format', default='rgb24',
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

