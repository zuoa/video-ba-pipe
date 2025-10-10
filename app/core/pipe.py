import numpy as np
import cv2
import time
import threading
from typing import Dict, Optional, Callable
from dataclasses import dataclass
import logging

# 假设已经导入了之前实现的模块
# from decoder_pool import DecoderPool, Priority
# from ring_buffer import VideoRingBuffer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class StreamConfig:
    """视频流配置"""
    stream_id: str
    source: str  # RTSP URL 或文件路径
    priority: int  # Priority枚举值
    frame_shape: tuple = (1080, 1920, 3)
    fps: int = 30
    buffer_seconds: int = 10
    codec: str = 'h264'


class VideoPipeline:
    """视频处理管道 - 集成解码器池和环形缓冲区"""

    def __init__(
            self,
            num_decoders: int = 4,
            use_cuda: bool = True,
            enable_shm: bool = True
    ):
        """
        初始化视频处理管道

        Args:
            num_decoders: 解码器数量
            use_cuda: 是否使用CUDA加速
            enable_shm: 是否使用共享内存
        """
        # 解码器池（使用前面实现的DecoderPool）
        self.decoder_pool = None  # DecoderPool(num_decoders, use_cuda)

        # 为每个视频流创建环形缓冲区
        self.ring_buffers: Dict[str, object] = {}  # VideoRingBuffer实例

        # 视频流配置
        self.stream_configs: Dict[str, StreamConfig] = {}

        # 视频捕获对象
        self.captures: Dict[str, cv2.VideoCapture] = {}

        # 捕获线程
        self.capture_threads: Dict[str, threading.Thread] = {}
        self.running = False

        # 性能监控
        self.metrics = {
            'frame_counts': {},
            'decode_times': {},
            'buffer_usage': {}
        }

        self.enable_shm = enable_shm
        self._lock = threading.Lock()

    def add_stream(self, config: StreamConfig):
        """
        添加视频流

        Args:
            config: 流配置
        """
        stream_id = config.stream_id

        with self._lock:
            # 保存配置
            self.stream_configs[stream_id] = config

            # 创建环形缓冲区
            # buffer = VideoRingBuffer(
            #     name=f"buffer_{stream_id}",
            #     frame_shape=config.frame_shape,
            #     fps=config.fps,
            #     duration_seconds=config.buffer_seconds,
            #     create=True
            # )
            # self.ring_buffers[stream_id] = buffer

            # 初始化指标
            self.metrics['frame_counts'][stream_id] = 0
            self.metrics['decode_times'][stream_id] = []
            self.metrics['buffer_usage'][stream_id] = 0.0

            logger.info(f"添加视频流: {stream_id} (优先级: {config.priority})")

    def start(self):
        """启动管道"""
        if self.running:
            logger.warning("管道已在运行")
            return

        self.running = True

        # 启动每个流的捕获线程
        for stream_id, config in self.stream_configs.items():
            thread = threading.Thread(
                target=self._capture_thread,
                args=(stream_id,),
                daemon=True,
                name=f"Capture-{stream_id}"
            )
            thread.start()
            self.capture_threads[stream_id] = thread

        logger.info(f"管道已启动: {len(self.capture_threads)} 个视频流")

    def _capture_thread(self, stream_id: str):
        """
        视频捕获线程
        从视频源读取原始帧并提交到解码器池
        """
        config = self.stream_configs[stream_id]

        # 打开视频源
        cap = cv2.VideoCapture(config.source)
        if not cap.isOpened():
            logger.error(f"无法打开视频源: {config.source}")
            return

        self.captures[stream_id] = cap
        logger.info(f"流 {stream_id} 开始捕获")

        frame_count = 0
        target_interval = 1.0 / config.fps
        last_time = time.time()

        while self.running:
            try:
                # 读取帧
                ret, frame = cap.read()
                if not ret:
                    logger.warning(f"流 {stream_id} 读取失败，尝试重连...")
                    time.sleep(1)
                    cap = cv2.VideoCapture(config.source)
                    continue

                # 编码帧（模拟网络传输的编码数据）
                _, encoded = cv2.imencode('.jpg', frame,
                                          [cv2.IMWRITE_JPEG_QUALITY, 90])
                frame_data = encoded.tobytes()

                # 提交到解码器池
                # success = self.decoder_pool.submit_task(
                #     stream_id=stream_id,
                #     frame_data=frame_data,
                #     priority=config.priority,
                #     codec=config.codec
                # )

                # 模拟直接解码（用于演示）
                success = self._decode_and_buffer(
                    stream_id, frame_data, frame
                )

                if success:
                    frame_count += 1
                    self.metrics['frame_counts'][stream_id] = frame_count

                # 帧率控制
                elapsed = time.time() - last_time
                if elapsed < target_interval:
                    time.sleep(target_interval - elapsed)
                last_time = time.time()

            except Exception as e:
                logger.error(f"流 {stream_id} 捕获错误: {e}")
                time.sleep(0.1)

        cap.release()
        logger.info(f"流 {stream_id} 捕获线程退出")

    def _decode_and_buffer(
            self,
            stream_id: str,
            frame_data: bytes,
            frame: np.ndarray = None
    ) -> bool:
        """
        解码帧并写入缓冲区
        （这个方法会被解码器池的回调调用）
        """
        start_time = time.time()

        try:
            # 如果没有提供解码后的帧，则解码
            if frame is None:
                nparr = np.frombuffer(frame_data, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                if frame is None:
                    return False

            # 写入环形缓冲区
            # buffer = self.ring_buffers.get(stream_id)
            # if buffer:
            #     buffer.write(frame)

            # 更新指标
            decode_time = time.time() - start_time
            self.metrics['decode_times'][stream_id].append(decode_time)

            # 只保留最近100个样本
            if len(self.metrics['decode_times'][stream_id]) > 100:
                self.metrics['decode_times'][stream_id].pop(0)

            # 更新缓冲区使用率
            # stats = buffer.get_stats()
            # self.metrics['buffer_usage'][stream_id] = stats['usage_percent']

            return True

        except Exception as e:
            logger.error(f"流 {stream_id} 解码/缓冲错误: {e}")
            return False

    def get_frame(self, stream_id: str, offset: int = -1) -> Optional[np.ndarray]:
        """
        从缓冲区获取帧

        Args:
            stream_id: 流ID
            offset: 帧偏移（-1表示最新帧，0表示最旧帧）

        Returns:
            视频帧
        """
        # buffer = self.ring_buffers.get(stream_id)
        # if buffer:
        #     return buffer.peek(offset)
        return None

    def get_pipeline_stats(self) -> Dict:
        """获取管道统计信息"""
        stats = {
            'streams': {},
            'total_frames': 0,
            'avg_decode_time': 0.0
        }

        total_decode_time = 0.0
        decode_count = 0

        for stream_id in self.stream_configs.keys():
            frame_count = self.metrics['frame_counts'].get(stream_id, 0)
            decode_times = self.metrics['decode_times'].get(stream_id, [])
            buffer_usage = self.metrics['buffer_usage'].get(stream_id, 0.0)

            avg_decode_time = (
                sum(decode_times) / len(decode_times) * 1000
                if decode_times else 0.0
            )

            stats['streams'][stream_id] = {
                'frame_count': frame_count,
                'avg_decode_time_ms': avg_decode_time,
                'buffer_usage_percent': buffer_usage,
                'fps': frame_count / max(time.time() - getattr(self, 'start_time', time.time()), 1)
            }

            stats['total_frames'] += frame_count
            if decode_times:
                total_decode_time += sum(decode_times)
                decode_count += len(decode_times)

        if decode_count > 0:
            stats['avg_decode_time'] = (total_decode_time / decode_count) * 1000

        # 添加解码器池统计
        # if self.decoder_pool:
        #     stats['decoder_pool'] = self.decoder_pool.get_pool_stats()

        return stats

    def stop(self):
        """停止管道"""
        logger.info("正在停止管道...")
        self.running = False

        # 等待捕获线程结束
        for thread in self.capture_threads.values():
            thread.join(timeout=2.0)

        # 释放视频捕获
        for cap in self.captures.values():
            cap.release()

        # 停止解码器池
        # if self.decoder_pool:
        #     self.decoder_pool.stop()

        # 清理环形缓冲区
        for stream_id, buffer in self.ring_buffers.items():
            # buffer.close()
            # buffer.unlink()
            pass

        logger.info("管道已停止")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


class AdvancedDecoderScheduler:
    """
    高级解码调度器
    - 动态优先级调整
    - GPU利用率监控
    - 自适应负载均衡
    """

    def __init__(self, decoder_pool):
        self.decoder_pool = decoder_pool
        self.stream_priorities: Dict[str, float] = {}
        self.gpu_utilization = 0.0
        self.monitoring_thread = None
        self.running = False

    def start_monitoring(self):
        """启动GPU监控线程"""
        self.running = True
        self.monitoring_thread = threading.Thread(
            target=self._monitor_gpu,
            daemon=True
        )
        self.monitoring_thread.start()

    def _monitor_gpu(self):
        """监控GPU利用率"""
        try:
            import pynvml
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)

            while self.running:
                # 获取GPU利用率
                utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                self.gpu_utilization = utilization.gpu

                # 根据利用率调整策略
                if self.gpu_utilization > 90:
                    logger.warning(f"GPU利用率过高: {self.gpu_utilization}%")
                    self._reduce_load()

                time.sleep(1.0)

        except Exception as e:
            logger.warning(f"GPU监控不可用: {e}")

    def _reduce_load(self):
        """降低负载策略"""
        # 临时降低低优先级流的帧率
        for stream_id, priority in self.stream_priorities.items():
            if priority > 2:  # 低优先级
                logger.info(f"降低流 {stream_id} 的处理优先级")

    def adjust_priority(self, stream_id: str, priority: float):
        """动态调整流优先级"""
        self.stream_priorities[stream_id] = priority

    def get_optimal_decoder(self, codec: str) -> int:
        """
        选择最优解码器
        考虑因素：
        1. 解码器负载
        2. GPU利用率
        3. 编码格式匹配
        """
        # 实现智能调度逻辑
        pass

    def stop(self):
        """停止监控"""
        self.running = False
        if self.monitoring_thread:
            self.monitoring_thread.join()


# ============= 使用示例 =============

if __name__ == "__main__":
    print("=== 视频处理管道演示 ===\n")

    # 创建管道
    pipeline = VideoPipeline(
        num_decoders=4,
        use_cuda=True,
        enable_shm=True
    )

    # 配置多路视频流
    streams = [
        StreamConfig(
            stream_id="camera_main",
            source=0,  # 使用摄像头0（可改为RTSP URL）
            priority=0,  # Priority.CRITICAL
            frame_shape=(720, 1280, 3),
            fps=30,
            buffer_seconds=10
        ),
        StreamConfig(
            stream_id="camera_aux1",
            source="test_video.mp4",  # 或 "rtsp://..."
            priority=2,  # Priority.NORMAL
            frame_shape=(480, 640, 3),
            fps=25,
            buffer_seconds=10
        ),
        StreamConfig(
            stream_id="camera_aux2",
            source="test_video2.mp4",
            priority=3,  # Priority.LOW
            frame_shape=(480, 640, 3),
            fps=20,
            buffer_seconds=10
        )
    ]

    # 添加视频流
    print("配置视频流...")
    for config in streams:
        pipeline.add_stream(config)

    # 启动管道
    print("\n启动管道...\n")
    pipeline.start()

    # 运行一段时间并监控
    try:
        for i in range(10):
            time.sleep(2)

            # 获取统计信息
            stats = pipeline.get_pipeline_stats()

            print(f"\n--- 第 {i + 1} 次统计 (运行 {(i + 1) * 2}s) ---")
            print(f"总解码帧数: {stats['total_frames']}")
            print(f"平均解码时间: {stats['avg_decode_time']:.2f} ms")

            print("\n各流状态:")
            for stream_id, stream_stats in stats['streams'].items():
                print(f"  {stream_id}:")
                print(f"    帧数: {stream_stats['frame_count']}")
                print(f"    实时FPS: {stream_stats['fps']:.1f}")
                print(f"    解码时间: {stream_stats['avg_decode_time_ms']:.2f} ms")
                print(f"    缓冲区使用: {stream_stats['buffer_usage_percent']:.1f}%")

        # 演示获取特定帧
        print("\n\n=== 帧获取测试 ===")
        frame = pipeline.get_frame("camera_main", offset=-1)
        if frame is not None:
            print(f"获取最新帧成功: shape={frame.shape}")

        oldest_frame = pipeline.get_frame("camera_main", offset=0)
        if oldest_frame is not None:
            print(f"获取最旧帧成功: shape={oldest_frame.shape}")

    except KeyboardInterrupt:
        print("\n\n用户中断")
    finally:
        # 停止管道
        pipeline.stop()
        print("\n管道已清理")


# ============= 高级用法示例 =============

class VideoProcessor:
    """
    视频处理器示例
    展示如何使用管道进行实时处理
    """

    def __init__(self, pipeline: VideoPipeline):
        self.pipeline = pipeline
        self.processing_thread = None
        self.running = False

    def start_processing(self):
        """启动处理线程"""
        self.running = True
        self.processing_thread = threading.Thread(
            target=self._process_loop,
            daemon=True
        )
        self.processing_thread.start()

    def _process_loop(self):
        """处理循环"""
        while self.running:
            # 获取所有流的最新帧
            for stream_id in self.pipeline.stream_configs.keys():
                frame = self.pipeline.get_frame(stream_id, offset=-1)

                if frame is not None:
                    # 执行处理（目标检测、跟踪等）
                    processed = self._process_frame(frame)

                    # 可选：显示结果
                    # cv2.imshow(stream_id, processed)

            time.sleep(0.033)  # ~30 FPS处理

    def _process_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        帧处理逻辑
        例如：目标检测、人脸识别、OCR等
        """
        # 示例：简单的边缘检测
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 100, 200)
        return cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)

    def stop(self):
        """停止处理"""
        self.running = False
        if self.processing_thread:
            self.processing_thread.join()
