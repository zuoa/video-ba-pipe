import queue
import subprocess
import threading
from abc import abstractmethod
from typing import Optional

import numpy as np

from app import logger
from app.core.decoder.base import BaseDecoder


class AsyncFFmpegDecoder(BaseDecoder):
    """
    一个真正异步的FFmpeg解码器，内部管理读写线程以避免死锁。
    """

    def __init__(self, decoder_id: int, width: int, height: int, **kwargs):
        self._writer_thread: Optional[threading.Thread] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
        self._ffmpeg_process = None

        super().__init__(decoder_id=decoder_id, width=width, height=height, **kwargs)

    @abstractmethod
    def _build_ffmpeg_command(self) -> list:
        """子类必须实现此方法来构建FFmpeg命令。"""
        pass

    def _initialize(self) -> bool:
        """重写初始化方法，以启动线程。"""
        command = self._build_ffmpeg_command()
        logger.info(f"启动FFmpeg解码器进程，命令: {' '.join(command)}")
        try:
            self._ffmpeg_process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10 ** 8
            )
            self._running = True

            # 启动读取解码帧的线程
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()

            # 启动监控ffmpeg错误输出的线程
            self._stderr_thread = threading.Thread(target=self._log_stderr, daemon=True)
            self._stderr_thread.start()

            return True
        except Exception as e:
            logger.error(f"启动FFmpeg进程失败: {e}")
            return False

    def _read_loop(self):
        """在独立线程中持续读取解码器的标准输出。"""
        logger.info("FFmpeg帧读取线程已启动。")
        while self._running:
            try:
                frame_size = self.width * self.height * 3  # 假设为rgb24
                raw_frame = self._ffmpeg_process.stdout.read(frame_size)

                if len(raw_frame) != frame_size:
                    logger.warning("从FFmpeg读取到的数据不完整，可能已结束。")
                    break

                frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((self.height, self.width, 3))

                try:
                    self.output_queue.put_nowait(frame)
                    self.frames_decoded += 1
                except queue.Full:
                    self.frames_dropped += 1

            except Exception as e:
                if self._running:  # 只有在还在运行时才报告错误
                    logger.error(f"FFmpeg帧读取线程异常: {e}")
                break
        logger.info("FFmpeg帧读取线程已退出。")

    def _log_stderr(self):
        """读取并记录FFmpeg的错误输出。"""
        while self._running:
            try:
                line = self._ffmpeg_process.stderr.readline()
                if not line:
                    break
                # 这里只记录警告，避免ffmpeg正常输出刷屏
                logger.warning(f"[FFmpeg Decoder Log]: {line.decode('utf-8', errors='ignore').strip()}")
            except Exception:
                break

    def send_packet(self, data: bytes):
        """向解码器进程写入数据包。"""
        if self._running and self._ffmpeg_process and self._ffmpeg_process.stdin:
            try:
                self._ffmpeg_process.stdin.write(data)
                self._ffmpeg_process.stdin.flush()
                self.bytes_processed += len(data)
            except (IOError, ValueError, BrokenPipeError) as e:
                logger.error(f"向FFmpeg写入数据失败: {e}")
                self.close()  # 写入失败，关闭解码器

    def get_frame(self, timeout=1.0) -> Optional[np.ndarray]:
        """从输出队列获取解码后的帧。"""
        try:
            return self.output_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _cleanup(self):
        """清理资源，停止线程和进程。"""
        self._running = False
        if self._ffmpeg_process:
            if self._ffmpeg_process.stdin:
                try:
                    self._ffmpeg_process.stdin.close()
                except Exception:
                    pass  # 忽略关闭时的错误
            try:
                self._ffmpeg_process.terminate()
                self._ffmpeg_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._ffmpeg_process.kill()
            self._ffmpeg_process = None

        # 等待线程结束
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1)


# --- 创建具体的软件解码器 ---
class AsyncSoftwareDecoder(AsyncFFmpegDecoder):
    def _build_ffmpeg_command(self) -> list:
        self.logger.info("构建异步FFmpeg软件解码命令 (仅解码关键帧 - 修正版)...")

        # 将输入参数放在 -i pipe:0 之前
        input_args = [
            '-skip_frame', 'nokey',  # 跳过非关键帧
            '-fflags', '+genpts'
            '-f', self.config.get('input_format', 'h264'),
        ]

        # 将输出参数放在 -i pipe:0 之后
        output_args = [
            '-f', 'rawvideo',
            '-pix_fmt', self.config.get('output_format', 'rgb24'),
            '-s', f'{self.width}x{self.height}',
        ]

        return [
            'ffmpeg',
            *input_args,
            '-i', 'pipe:0',
            *output_args,
            'pipe:1'
        ]
