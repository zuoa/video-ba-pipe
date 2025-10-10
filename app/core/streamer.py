# (保留所有解码器类、ThreadedDecoder 类和旧的 RTSPStreamer 的 _build_ffmpeg_command 方法等)
import subprocess
import threading
from typing import Optional

from app import logger


class RTSPStreamer:
    """
    一个解耦的、事件驱动的RTSP拉流器。
    它主动从视频源拉取数据，并通过回调函数将数据包推送给消费者。
    """

    def __init__(self, rtsp_url: str):
        self.rtsp_url = rtsp_url
        self.process = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._handlers = []  # 存储回调函数的列表

    # _build_ffmpeg_command 和 _log_stderr 方法与之前完全相同，这里省略...
    def _build_ffmpeg_command(self) -> list:
        # (代码和之前一样)
        output_format = 'h264'
        if 'h265' in self.rtsp_url or 'hevc' in self.rtsp_url:
            output_format = 'hevc'
        logger.info(f"为RTSP流构建FFmpeg命令, 输出格式: {output_format}")
        return [
            'ffmpeg', '-rtsp_transport', 'tcp', '-i', self.rtsp_url,
            '-an', '-dn', '-vcodec', 'copy', '-f', output_format, 'pipe:1'
        ]

    def _log_stderr(self):
        # (代码和之前一样)
        while self._running and self.process and self.process.stderr:
            try:
                line = self.process.stderr.readline().decode('utf-8', errors='ignore').strip()
                if line and not ('frame=' in line or 'fps=' in line):
                    logger.warning(f"[FFmpeg拉流日志]: {line}")
            except Exception:
                break

    def add_packet_handler(self, callback):
        """
        注册一个回调函数，当接收到新的数据包时会被调用。

        Args:
            callback: 一个接受单个参数(bytes)的函数或方法。
        """
        self._handlers.append(callback)

    def _reader_loop(self):
        """
        内部读取线程的主循环。
        读取数据并分发给所有注册的处理器。
        """
        logger.info("RTSP拉流读取线程已启动。")
        while self._running:
            try:
                # 从FFmpeg进程读取数据块
                packet = self.process.stdout.read(65536)
                if not packet:
                    logger.warning("RTSP流已断开或结束。")
                    break

                # 将数据包分发给所有处理器
                for handler in self._handlers:
                    try:
                        handler(packet)
                    except Exception as e:
                        logger.error(f"调用处理器 {handler.__name__} 时发生错误: {e}")

            except Exception as e:
                logger.error(f"RTSP读取线程发生严重错误: {e}")
                break

        self._running = False  # 确保循环退出后状态为停止
        logger.info("RTSP拉流读取线程已退出。")

    def start(self):
        if self._running:
            logger.warning("RTSPStreamer 已经在运行中。")
            return

        command = self._build_ffmpeg_command()
        try:
            self.process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10 ** 8)

            self._running = True

            # 启动stderr日志线程
            stderr_thread = threading.Thread(target=self._log_stderr, daemon=True)
            stderr_thread.start()

            # 启动主读取和分发线程
            self._thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._thread.start()

            logger.info(f"RTSPStreamer 已启动 (PID: {self.process.pid})")

        except Exception as e:
            logger.error(f"启动 RTSPStreamer 失败: {e}")
            self._running = False

    def stop(self):
        if not self._running:
            return
        logger.info("正在停止 RTSPStreamer...")
        self._running = False

        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

        logger.info("RTSPStreamer 已停止。")

    def is_running(self) -> bool:
        # 检查内部线程是否仍在运行，或者ffmpeg进程是否意外退出
        if self.process and self.process.poll() is not None:
            self._running = False  # 进程已退出
        return self._running

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
