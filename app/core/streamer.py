import subprocess
import threading
from abc import ABC, abstractmethod
from typing import Optional, Callable, List
from pathlib import Path
import requests

from app import logger


class BaseStreamer(ABC):
    """
    流处理器的抽象基类。
    定义了事件驱动的流处理框架，支持通过回调函数推送数据。
    """

    def __init__(self, source: str):
        """
        初始化流处理器。

        Args:
            source: 流的源地址（URL、文件路径等）
        """
        self.source = source
        self.process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._handlers: List[Callable[[bytes], None]] = []

    def add_packet_handler(self, callback: Callable[[bytes], None]):
        """
        注册一个回调函数，当接收到新的数据包时会被调用。

        Args:
            callback: 一个接受单个参数(bytes)的函数或方法。
        """
        self._handlers.append(callback)

    def remove_packet_handler(self, callback: Callable[[bytes], None]):
        """
        移除已注册的回调函数。

        Args:
            callback: 要移除的回调函数。
        """
        if callback in self._handlers:
            self._handlers.remove(callback)

    @abstractmethod
    def _build_command(self) -> list:
        """
        构建用于获取流的命令。
        子类必须实现此方法以返回适合其流类型的命令。

        Returns:
            命令列表，用于subprocess执行。
        """
        pass

    def _log_stderr(self):
        """
        从进程的stderr读取并记录日志。
        过滤掉常见的进度信息。
        """
        while self._running and self.process and self.process.stderr:
            try:
                line = self.process.stderr.readline().decode('utf-8', errors='ignore').strip()
                if line and not ('frame=' in line or 'fps=' in line or 'bitrate=' in line):
                    logger.warning(f"[{self.__class__.__name__} stderr]: {line}")
            except Exception:
                break

    def _distribute_packet(self, packet: bytes):
        """
        将数据包分发给所有注册的处理器。

        Args:
            packet: 要分发的数据包。
        """
        for handler in self._handlers:
            try:
                handler(packet)
            except Exception as e:
                logger.error(f"调用处理器 {handler.__name__} 时发生错误: {e}")

    def _reader_loop(self):
        """
        内部读取线程的主循环。
        读取数据并分发给所有注册的处理器。
        """
        logger.info(f"{self.__class__.__name__} 读取线程已启动。")
        while self._running:
            try:
                # 从进程读取数据块
                packet = self.process.stdout.read(65536)
                if not packet:
                    logger.warning(f"{self.__class__.__name__} 流已断开或结束。")
                    break

                # 分发数据包
                self._distribute_packet(packet)

            except Exception as e:
                logger.error(f"{self.__class__.__name__} 读取线程发生严重错误: {e}")
                break

        self._running = False
        logger.info(f"{self.__class__.__name__} 读取线程已退出。")

    def start(self):
        """启动流处理器。"""
        if self._running:
            logger.warning(f"{self.__class__.__name__} 已经在运行中。")
            return

        command = self._build_command()
        try:
            logger.info(f"启动 {self.__class__.__name__} 进程，命令: {' '.join(command)}")
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=10 ** 8
            )

            self._running = True

            # 启动stderr日志线程
            stderr_thread = threading.Thread(target=self._log_stderr, daemon=True)
            stderr_thread.start()

            # 启动主读取和分发线程
            self._thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._thread.start()

            logger.info(f"{self.__class__.__name__} 已启动 (PID: {self.process.pid})")

        except Exception as e:
            logger.error(f"启动 {self.__class__.__name__} 失败: {e}")
            self._running = False

    def stop(self):
        """停止流处理器。"""
        if not self._running:
            return

        logger.info(f"正在停止 {self.__class__.__name__}...")
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

        logger.info(f"{self.__class__.__name__} 已停止。")

    def is_running(self) -> bool:
        """
        检查流处理器是否正在运行。

        Returns:
            如果正在运行返回True，否则返回False。
        """
        if self.process and self.process.poll() is not None:
            self._running = False
        return self._running

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


class RTSPStreamer(BaseStreamer):
    """RTSP流处理器实现。"""

    def __init__(self, rtsp_url: str, transport: str = 'tcp'):
        """
        初始化RTSP流处理器。

        Args:
            rtsp_url: RTSP流地址
            transport: 传输协议，'tcp'或'udp'，默认'tcp'
        """
        super().__init__(rtsp_url)
        self.transport = transport

    def _build_command(self) -> list:
        """构建FFmpeg命令用于拉取RTSP流。"""
        # 自动检测编码格式
        output_format = 'h264'
        if 'h265' in self.source.lower() or 'hevc' in self.source.lower():
            output_format = 'hevc'

        logger.info(f"为RTSP流构建FFmpeg命令, 输出格式: {output_format}")

        return [
            'ffmpeg',
            '-rtsp_transport', self.transport,
            '-i', self.source,
            '-an',  # 禁用音频
            '-dn',  # 禁用数据流
            '-vcodec', 'copy',  # 复制视频编码
            '-f', output_format,
            'pipe:1'
        ]


class FileStreamer(BaseStreamer):
    """文件流处理器实现，用于读取本地视频文件。"""

    def __init__(self, file_path: str, loop: bool = False):
        """
        初始化文件流处理器。

        Args:
            file_path: 视频文件路径
            loop: 是否循环播放
        """
        super().__init__(file_path)
        self.loop = loop

        # 验证文件是否存在
        if not Path(file_path).exists():
            raise FileNotFoundError(f"视频文件不存在: {file_path}")

    def _build_command(self) -> list:
        """构建FFmpeg命令用于读取文件。"""
        logger.info(f"为文件流构建FFmpeg命令: {self.source}")

        command = ['ffmpeg']

        # 如果需要循环播放
        if self.loop:
            command.extend(['-stream_loop', '-1'])

        command.extend([
            '-re',  # 以原始帧率读取
            '-i', self.source,
            '-an',  # 禁用音频
            '-dn',  # 禁用数据流
            '-vcodec', 'copy',
            '-f', 'h264',  # 默认输出h264格式
            'pipe:1'
        ])

        return command


class HTTPFLVStreamer(BaseStreamer):
    """HTTP-FLV流处理器实现。"""

    def __init__(self, flv_url: str):
        """
        初始化HTTP-FLV流处理器。

        Args:
            flv_url: HTTP-FLV流地址
        """
        super().__init__(flv_url)

    def _build_command(self) -> list:
        """构建FFmpeg命令用于拉取HTTP-FLV流。"""
        logger.info(f"为HTTP-FLV流构建FFmpeg命令: {self.source}")

        return [
            'ffmpeg',
            '-i', self.source,
            '-an',  # 禁用音频
            '-dn',  # 禁用数据流
            '-vcodec', 'copy',
            '-f', 'h264',
            'pipe:1'
        ]


class HLSStreamer(BaseStreamer):
    """HLS (HTTP Live Streaming) 流处理器实现。"""

    def __init__(self, m3u8_url: str):
        """
        初始化HLS流处理器。

        Args:
            m3u8_url: HLS m3u8文件地址
        """
        super().__init__(m3u8_url)

    def _build_command(self) -> list:
        """构建FFmpeg命令用于拉取HLS流。"""
        logger.info(f"为HLS流构建FFmpeg命令: {self.source}")

        return [
            'ffmpeg',
            '-i', self.source,
            '-an',  # 禁用音频
            '-dn',  # 禁用数据流
            '-vcodec', 'copy',
            '-f', 'h264',
            'pipe:1'
        ]


class StreamerFactory:
    """流处理器工厂类，根据URL或配置自动创建合适的Streamer。"""

    @staticmethod
    def create_streamer(source: str, stream_type: Optional[str] = None, **kwargs) -> BaseStreamer:
        """
        根据源地址或显式类型创建Streamer实例。

        Args:
            source: 流源地址（URL或文件路径）
            stream_type: 显式指定的流类型，可选值: 'rtsp', 'file', 'http-flv', 'hls'
            **kwargs: 传递给具体Streamer的额外参数

        Returns:
            相应的Streamer实例

        Raises:
            ValueError: 当无法识别流类型时
        """
        # 如果显式指定了类型
        if stream_type:
            stream_type = stream_type.lower()
            if stream_type == 'rtsp':
                return RTSPStreamer(source, **kwargs)
            elif stream_type == 'file':
                return FileStreamer(source, **kwargs)
            elif stream_type in ['http-flv', 'flv']:
                return HTTPFLVStreamer(source, **kwargs)
            elif stream_type == 'hls':
                return HLSStreamer(source, **kwargs)
            else:
                raise ValueError(f"不支持的流类型: {stream_type}")

        # 自动检测流类型
        source_lower = source.lower()

        # RTSP
        if source_lower.startswith('rtsp://') or source_lower.startswith('rtsps://'):
            return RTSPStreamer(source, **kwargs)

        # HTTP-FLV
        if source_lower.endswith('.flv') or 'flv' in source_lower:
            return HTTPFLVStreamer(source, **kwargs)

        # HLS
        if source_lower.endswith('.m3u8') or source_lower.endswith('.m3u'):
            return HLSStreamer(source, **kwargs)

        # HTTP/HTTPS (其他情况)
        if source_lower.startswith('http://') or source_lower.startswith('https://'):
            # 默认尝试HTTP-FLV
            logger.warning(f"无法明确识别HTTP流类型，默认使用HTTP-FLV: {source}")
            return HTTPFLVStreamer(source, **kwargs)

        # 本地文件
        if Path(source).exists() or not source.startswith(('http://', 'https://', 'rtsp://')):
            return FileStreamer(source, **kwargs)

        raise ValueError(f"无法识别的流源: {source}")


# 使用示例
if __name__ == "__main__":


    # 示例1: RTSP流
    def on_packet_received(packet: bytes):
        print(f"收到数据包，大小: {len(packet)} 字节")

    # 示例1: 使用工厂自动识别
    streamer = StreamerFactory.create_streamer("rtsp://example.com/stream")

    streamer.add_packet_handler(on_packet_received)
    streamer.start()


    # 示例2: 显式指定类型
    streamer = StreamerFactory.create_streamer(
        "http://example.com/stream",
        stream_type="http-flv"
    )

    streamer.add_packet_handler(on_packet_received)
    streamer.start()

    # 示例3: 传递额外参数
    streamer = StreamerFactory.create_streamer(
        "/path/to/video.mp4",
        stream_type="file",
        loop=True
    )
    streamer.add_packet_handler(on_packet_received)
    streamer.start()
