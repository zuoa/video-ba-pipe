import logging
import queue
import subprocess
from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional, Dict, Any

import numpy as np

from app import logger


class DecoderStatus(Enum):
    """解码器状态枚举"""
    UNINITIALIZED = 0
    READY = 1
    DECODING = 2
    ERROR = 3
    CLOSED = 4


class BaseDecoder(ABC):
    """
    硬件解码器基类 (已更新为异步接口)
    定义统一的解码器接口
    """

    def __init__(
            self,
            decoder_id: int,
            device_id: int = 0,
            width: int = 1920,
            height: int = 1080,
            **kwargs
    ):
        self.decoder_id = decoder_id
        self.device_id = device_id
        self.width = width
        self.height = height

        # 为每个解码器实例创建独立的 logger
        self.logger = logging.getLogger(f"Decoder-{self.decoder_id}")

        self.output_queue = queue.Queue(maxsize=30)

        self.frames_decoded = 0
        self.frames_dropped = 0
        self.errors = 0
        self.bytes_processed = 0
        self.status = DecoderStatus.UNINITIALIZED
        self.config = kwargs

        self.logger.info(f"初始化解码器 {self.decoder_id} (设备 {device_id})")

    @abstractmethod
    def _initialize(self) -> bool:
        """初始化具体的解码器实现"""
        pass

    # --- 修正开始 ---
    # 移除了旧的 abstractmethod 'decode'
    # 添加了新的异步接口作为 contract

    @abstractmethod
    def send_packet(self, data: bytes):
        """
        向解码器发送编码数据包。
        """
        pass

    @abstractmethod
    def get_frame(self, timeout=1.0) -> Optional[np.ndarray]:
        """
        从解码器获取解码后的帧。
        """
        pass

    @abstractmethod
    def _cleanup(self):
        """清理资源"""
        pass

    def initialize(self) -> bool:
        # ... (此方法无需修改)
        try:
            success = self._initialize()
            if success:
                self.status = DecoderStatus.READY
                self.logger.info(f"解码器 {self.decoder_id} 初始化成功")
            else:
                self.status = DecoderStatus.ERROR
                self.logger.error(f"解码器 {self.decoder_id} 初始化失败")
            return success
        except Exception as e:
            global_logger = logging.getLogger(__name__)
            global_logger.error(f"解码器 {self.decoder_id} 初始化异常: {e}", exc_info=True)
            self.status = DecoderStatus.ERROR
            return False

    def close(self):
        # ... (此方法无需修改)
        if self.status == DecoderStatus.CLOSED:
            return
        try:
            self._cleanup()
            self.status = DecoderStatus.CLOSED
            self.logger.info(f"解码器 {self.decoder_id} 已关闭")
            self._log_statistics()
        except Exception as e:
            self.logger.error(f"关闭解码器 {self.decoder_id} 时出错: {e}")

    def get_latest_frame(self, timeout=0.01) -> Optional[np.ndarray]:
        """
        从队列中获取最新的帧，丢弃所有旧的积压帧。
        """
        frame = None
        # 循环地从队列取帧，直到队列为空
        while True:
            try:
                # 使用非阻塞的 get_nowait，或者极短的超时
                new_frame = self.output_queue.get(block=True, timeout=timeout)
                frame = new_frame  # 持续更新为最新的帧
            except queue.Empty:
                # 当队列为空时，上次获取到的 frame 就是最新的
                break
        return frame

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'decoder_id': self.decoder_id,
            'frames_decoded': self.frames_decoded,
            'frames_dropped': self.frames_dropped,
            'errors': self.errors,
            'bytes_processed': self.bytes_processed,
            'status': self.status.name
        }

    def _log_statistics(self):
        """记录统计信息"""
        stats = self.get_statistics()
        logger.info(f"解码器 {self.decoder_id} 统计:")
        logger.info(f"  - 已解码帧数: {stats['frames_decoded']}")
        logger.info(f"  - 丢帧数: {stats['frames_dropped']}")
        logger.info(f"  - 错误数: {stats['errors']}")
        logger.info(f"  - 处理数据量: {stats['bytes_processed'] / 1024 / 1024:.2f} MB")

    def __enter__(self):
        """支持上下文管理器"""
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """支持上下文管理器"""
        self.close()
        return False


class FFmpegBaseDecoder(BaseDecoder):
    """
    FFmpeg 解码器基类
    提供FFmpeg通用功能
    """

    def __init__(
            self,
            decoder_id: int,
            input_format: str = 'h264',
            output_format: str = 'rgb24',
            device_id: int = 0,
            width: int = 1920,
            height: int = 1080,
            **kwargs
    ):
        """
        初始化 FFmpeg 解码器基类

        Args:
            input_format: 输入编码格式 (h264, hevc, vp9等)
            output_format: 输出像素格式 (nv12, yuv420p, rgb24等)
        """
        super().__init__(decoder_id, device_id, width, height, **kwargs)

        self.input_format = input_format
        self.output_format = output_format

        # FFmpeg进程相关
        self.ffmpeg_process = None
        self.pipe_stdin = None
        self.pipe_stdout = None
        self.pipe_stderr = None

        # 计算帧大小
        self.frame_size = self._calculate_frame_size()

    def _calculate_frame_size(self) -> int:
        """计算输出帧大小"""
        if self.output_format == 'nv12':
            return self.width * self.height * 3 // 2
        elif self.output_format == 'rgb24':
            return self.width * self.height * 3
        elif self.output_format == 'yuv420p':
            return self.width * self.height * 3 // 2
        else:
            return self.width * self.height * 3

    @abstractmethod
    def _build_ffmpeg_command(self) -> list:
        """构建FFmpeg命令（子类必须实现）"""
        pass

    def _initialize(self) -> bool:
        """初始化FFmpeg进程"""
        ffmpeg_cmd = self._build_ffmpeg_command()

        try:
            self.ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=10 ** 8
            )

            self.pipe_stdin = self.ffmpeg_process.stdin
            self.pipe_stdout = self.ffmpeg_process.stdout
            self.pipe_stderr = self.ffmpeg_process.stderr

            return True

        except Exception as e:
            logger.error(f"FFmpeg初始化失败: {e}")
            return False

    def decode(self, data: bytes) -> Optional[np.ndarray]:
        """解码数据包"""
        if self.status != DecoderStatus.READY or not self.pipe_stdin:
            logger.error("解码器未就绪")
            return None

        try:
            self.status = DecoderStatus.DECODING

            # 写入数据
            self.pipe_stdin.write(data)
            self.pipe_stdin.flush()
            self.bytes_processed += len(data)

            # 读取解码帧
            raw_frame = self.pipe_stdout.read(self.frame_size)

            if len(raw_frame) != self.frame_size:
                logger.warning(f"帧大小不匹配: {len(raw_frame)} != {self.frame_size}")
                self.frames_dropped += 1
                self.status = DecoderStatus.READY
                return None

            # 转换为numpy数组
            frame = self._parse_frame(raw_frame)

            self.frames_decoded += 1
            self.status = DecoderStatus.READY
            return frame

        except Exception as e:
            logger.error(f"解码错误: {e}")
            self.errors += 1
            self.status = DecoderStatus.READY
            return None

    def _parse_frame(self, raw_data: bytes) -> np.ndarray:
        """解析原始帧数据"""
        if self.output_format == 'rgb24':
            frame = np.frombuffer(raw_data, dtype=np.uint8)
            return frame.reshape((self.height, self.width, 3))
        elif self.output_format == 'nv12':
            # NV12格式处理
            return self._nv12_to_rgb(raw_data)
        else:
            return np.frombuffer(raw_data, dtype=np.uint8)

    def _nv12_to_rgb(self, nv12_data: bytes) -> np.ndarray:
        """NV12转RGB（简化实现）"""
        # 实际应使用GPU加速转换
        return np.zeros((self.height, self.width, 3), dtype=np.uint8)

    def _cleanup(self):
        """清理FFmpeg进程"""
        if self.pipe_stdin:
            try:
                self.pipe_stdin.close()
            except:
                pass

        if self.ffmpeg_process:
            try:
                self.ffmpeg_process.terminate()
                self.ffmpeg_process.wait(timeout=5)
            except:
                self.ffmpeg_process.kill()


class PyNvCodecDecoder(BaseDecoder):
    """
    使用 PyNvCodec 的解码器实现
    优点: 原生NVDEC绑定，性能最佳
    """

    def __init__(
            self,
            decoder_id: int,
            codec: str = 'h264',
            device_id: int = 0,
            width: int = 1920,
            height: int = 1080,
            use_converter: bool = False,
            **kwargs
    ):
        """
        初始化 PyNvCodec 解码器

        Args:
            codec: 编码格式 (h264, hevc)
            use_converter: 是否使用颜色空间转换
        """
        super().__init__(decoder_id, device_id, width, height, **kwargs)

        self.codec = codec
        self.use_converter = use_converter

        self.nvc = None
        self.decoder = None
        self.converter = None

    def _initialize(self) -> bool:
        """初始化PyNvCodec"""
        try:
            import PyNvCodec as nvc
            self.nvc = nvc

            # 选择编码格式
            codec_map = {
                'h264': nvc.CudaVideoCodec.H264,
                'hevc': nvc.CudaVideoCodec.HEVC,
                'h265': nvc.CudaVideoCodec.HEVC,
                'vp9': nvc.CudaVideoCodec.VP9,
            }

            codec_id = codec_map.get(self.codec.lower(), nvc.CudaVideoCodec.H264)

            # 创建解码器
            self.decoder = nvc.PyNvDecoder(
                codec=codec_id,
                gpu_id=self.device_id
            )

            # 创建颜色转换器（如果需要）
            if self.use_converter:
                self.converter = nvc.PySurfaceConverter(
                    self.width,
                    self.height,
                    nvc.PixelFormat.NV12,
                    nvc.PixelFormat.RGB,
                    self.device_id
                )

            return True

        except ImportError:
            logger.error("PyNvCodec未安装，请运行: pip install pynvcodec")
            return False
        except Exception as e:
            logger.error(f"PyNvCodec初始化失败: {e}")
            return False

    def decode(self, data: bytes) -> Optional[np.ndarray]:
        """解码数据包"""
        if self.status != DecoderStatus.READY or not self.decoder:
            return None

        try:
            self.status = DecoderStatus.DECODING
            self.bytes_processed += len(data)

            # 创建数据包
            enc_packet = self.nvc.PacketData(data)

            # 解码
            surface = self.decoder.DecodeSingleFrame(enc_packet)

            if surface.Empty():
                self.frames_dropped += 1
                self.status = DecoderStatus.READY
                return None

            # 颜色转换
            if self.converter:
                rgb_surface = self.converter.Execute(surface)
                frame = np.array(rgb_surface.PlanePtr())
            else:
                # 返回GPU Surface（需要进一步处理）
                frame = surface

            self.frames_decoded += 1
            self.status = DecoderStatus.READY
            return frame

        except Exception as e:
            logger.error(f"解码错误: {e}")
            self.errors += 1
            self.status = DecoderStatus.READY
            return None

    def _cleanup(self):
        """清理资源"""
        # PyNvCodec会自动管理资源
        self.decoder = None
        self.converter = None


class GStreamerNVDecoder(BaseDecoder):
    """
    使用 GStreamer + NVDEC 的解码器实现
    优点: 适合RTSP流，功能完整
    """

    def __init__(
            self,
            decoder_id: int,
            source: str,
            device_id: int = 0,
            width: int = 1920,
            height: int = 1080,
            latency: int = 0,
            **kwargs
    ):
        """
        初始化 GStreamer 解码器

        Args:
            source: 视频源（RTSP URL或文件路径）
            latency: 延迟（毫秒）
        """
        super().__init__(decoder_id, device_id, width, height, **kwargs)

        self.source = source
        self.latency = latency

        self.Gst = None
        self.pipeline = None
        self.appsink = None
        self.frame_queue = queue.Queue(maxsize=10)
        self.pipeline_thread = None
        self.running = False

    def _initialize(self) -> bool:
        """初始化GStreamer pipeline"""
        try:
            import gi
            gi.require_version('Gst', '1.0')
            from gi.repository import Gst

            Gst.init(None)
            self.Gst = Gst

            # 构建pipeline
            pipeline_str = f"""
                rtspsrc location={self.source} latency={self.latency} !
                rtph264depay !
                h264parse !
                nvh264dec gpu-id={self.device_id} !
                nvvidconv !
                video/x-raw,format=BGRx,width={self.width},height={self.height} !
                appsink name=sink emit-signals=true sync=false max-buffers=1 drop=true
            """

            self.pipeline = Gst.parse_launch(pipeline_str)
            self.appsink = self.pipeline.get_by_name('sink')
            self.appsink.connect('new-sample', self._on_new_sample)

            return True

        except Exception as e:
            logger.error(f"GStreamer初始化失败: {e}")
            return False

    def _on_new_sample(self, sink):
        """GStreamer回调"""
        sample = sink.emit('pull-sample')
        if not sample:
            return self.Gst.FlowReturn.OK

        try:
            buffer = sample.get_buffer()
            caps = sample.get_caps()

            structure = caps.get_structure(0)
            w = structure.get_int('width')[1]
            h = structure.get_int('height')[1]

            success, map_info = buffer.map(self.Gst.MapFlags.READ)
            if success:
                frame = np.ndarray(
                    shape=(h, w, 4),
                    dtype=np.uint8,
                    buffer=map_info.data
                )

                # BGR转RGB
                frame = frame[:, :, :3]

                try:
                    self.frame_queue.put_nowait(frame.copy())
                    self.frames_decoded += 1
                except queue.Full:
                    self.frames_dropped += 1

                buffer.unmap(map_info)
        except Exception as e:
            logger.error(f"处理帧错误: {e}")
            self.errors += 1

        return self.Gst.FlowReturn.OK

    def start(self):
        """启动pipeline"""
        if self.pipeline:
            self.pipeline.set_state(self.Gst.State.PLAYING)
            self.running = True
            self.status = DecoderStatus.READY

    def decode(self, data: bytes = None) -> Optional[np.ndarray]:
        """
        获取解码帧
        注意: GStreamer decoder不需要输入data，直接从源拉流
        """
        try:
            return self.frame_queue.get(timeout=1.0)
        except queue.Empty:
            return None

    def _cleanup(self):
        """停止pipeline"""
        self.running = False
        if self.pipeline:
            self.pipeline.set_state(self.Gst.State.NULL)


class FFmpegSoftwareDecoder(FFmpegBaseDecoder):
    """
    FFmpeg 软件解码器
    优点: 兼容性最好，无需GPU，适合CPU解码
    缺点: 性能较低，CPU占用高
    """

    def __init__(
            self,
            decoder_id: int,
            input_format: str = 'h264',
            output_format: str = 'rgb24',
            device_id: int = 0,  # 软解不使用，保留参数统一
            width: int = 1920,
            height: int = 1080,
            threads: int = 0,  # 0表示自动
            **kwargs
    ):
        """
        初始化 FFmpeg 软件解码器

        Args:
            threads: 解码线程数（0=自动，1=单线程，>1=多线程）
        """
        self.threads = threads
        super().__init__(decoder_id, input_format, output_format, device_id, width, height, **kwargs)

    def _build_ffmpeg_command(self) -> list:
        """构建软件解码命令"""
        cmd = [
            'ffmpeg',
            '-c:v', self.input_format,  # 使用软件解码器
        ]

        # 添加线程配置
        if self.threads > 0:
            cmd.extend(['-threads', str(self.threads)])

        cmd.extend([
            '-i', 'pipe:0',
            '-f', 'rawvideo',
            '-pix_fmt', self.output_format,
            '-s', f'{self.width}x{self.height}',
            'pipe:1'
        ])

        return cmd


class OpenCVDecoder(BaseDecoder):
    """
    OpenCV 软件解码器
    优点: 简单易用，无需外部依赖，适合JPEG/PNG等图片格式
    缺点: 视频流解码功能有限
    """

    def __init__(
            self,
            decoder_id: int,
            image_format: str = 'jpg',  # jpg, png, bmp等
            device_id: int = 0,
            width: int = 1920,
            height: int = 1080,
            **kwargs
    ):
        """
        初始化 OpenCV 解码器

        Args:
            image_format: 图像格式
        """
        super().__init__(decoder_id, device_id, width, height, **kwargs)
        self.image_format = image_format
        self.cv2 = None

    def _initialize(self) -> bool:
        """初始化OpenCV"""
        try:
            import cv2
            self.cv2 = cv2
            return True
        except ImportError:
            logger.error("OpenCV未安装，请运行: pip install opencv-python")
            return False

    def decode(self, data: bytes) -> Optional[np.ndarray]:
        """解码图像数据"""
        if self.status != DecoderStatus.READY or not self.cv2:
            return None

        try:
            self.status = DecoderStatus.DECODING
            self.bytes_processed += len(data)

            # 将字节数据转换为numpy数组
            nparr = np.frombuffer(data, np.uint8)

            # 解码
            frame = self.cv2.imdecode(nparr, self.cv2.IMREAD_COLOR)

            if frame is None:
                logger.warning("OpenCV解码失败")
                self.errors += 1
                self.status = DecoderStatus.READY
                return None

            # 调整大小（如果需要）
            if frame.shape[0] != self.height or frame.shape[1] != self.width:
                frame = self.cv2.resize(frame, (self.width, self.height))

            self.frames_decoded += 1
            self.status = DecoderStatus.READY
            return frame

        except Exception as e:
            logger.error(f"解码错误: {e}")
            self.errors += 1
            self.status = DecoderStatus.READY
            return None

    def _cleanup(self):
        """清理资源"""
        # OpenCV无需特殊清理
        pass
