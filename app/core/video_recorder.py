"""
视频录制模块
用于从VideoRingBuffer提取帧并编码为视频文件
支持录制预警前N秒和后M秒的视频
"""
import os
import threading
import time
from typing import List, Tuple, Optional
import numpy as np

from app import logger
from app.core.cv2_compat import cv2, require_cv2
from app.core.frame_utils import (
    detect_frame_pixel_format,
    frame_to_bgr,
    infer_frame_dimensions,
)
from app.core.ringbuffer import VideoRingBuffer


class VideoRecorder:
    """视频录制器，从RingBuffer提取帧并编码为视频"""
    
    def __init__(self, buffer: VideoRingBuffer, save_dir: str, fps: int = 10):
        """
        初始化视频录制器
        
        Args:
            buffer: VideoRingBuffer实例
            save_dir: 视频保存目录
            fps: 输出视频的帧率
        """
        self.buffer = buffer
        self.save_dir = save_dir
        self.fps = fps
        self.recording_tasks = {}  # 记录正在进行的录制任务
        self.lock = threading.Lock()
        
        os.makedirs(save_dir, exist_ok=True)
    
    def start_recording(
        self, 
        source_id: int,
        alert_id: int,
        trigger_time: float,
        pre_seconds: float,
        post_seconds: float,
        output_filename: Optional[str] = None
    ) -> str:
        """
        开始录制视频（异步）
        
        Args:
            source_id: 视频源ID
            alert_id: 预警ID
            trigger_time: 触发时间戳
            pre_seconds: 录制触发前N秒
            post_seconds: 录制触发后M秒
            output_filename: 输出文件名（可选，默认自动生成）
            
        Returns:
            视频文件相对路径
        """
        self.cleanup_completed_tasks(max_age_seconds=300)

        # 生成输出文件名
        if output_filename is None:
            timestamp_str = time.strftime('%Y%m%d_%H%M%S', time.localtime(trigger_time))
            output_filename = f"alert_{alert_id}_{timestamp_str}.mp4"
        
        # 构建完整路径
        output_path = os.path.join(self.save_dir, str(source_id), output_filename)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # 检查是否已有相同的录制任务
        with self.lock:
            if alert_id in self.recording_tasks:
                logger.warning(f"录制任务 {alert_id} 已存在，跳过")
                return self.recording_tasks[alert_id]['relative_path']
        
        # 创建录制任务信息
        recording_info = {
            'alert_id': alert_id,
            'trigger_time': trigger_time,
            'pre_seconds': pre_seconds,
            'post_seconds': post_seconds,
            'output_path': output_path,
            'relative_path': f"{source_id}/{output_filename}",
            'status': 'starting',
            'thread': None
        }
        
        with self.lock:
            self.recording_tasks[alert_id] = recording_info
        
        # 启动异步录制线程
        thread = threading.Thread(
            target=self._record_video_thread,
            args=(alert_id, recording_info),
            daemon=True
        )
        recording_info['thread'] = thread
        thread.start()
        
        logger.info(f"启动录制任务 {alert_id}，输出: {output_path}")
        
        return recording_info['relative_path']
    
    def _record_video_thread(self, alert_id: int, recording_info: dict):
        """
        录制视频的线程函数
        
        Args:
            alert_id: 预警ID
            recording_info: 录制任务信息
        """
        try:
            trigger_time = recording_info['trigger_time']
            pre_seconds = recording_info['pre_seconds']
            post_seconds = recording_info['post_seconds']
            output_path = recording_info['output_path']
            video_writer = None
            written_frame_count = 0
            
            # 检查buffer状态
            buffer_stats = self.buffer.get_stats()
            logger.info(f"[录制 {alert_id}] Buffer状态: {buffer_stats['count']}帧 / {buffer_stats['capacity']}容量")
            
            # 第一步：从RingBuffer获取历史帧（过去N秒）
            logger.info(f"[录制 {alert_id}] 正在提取过去 {pre_seconds} 秒的帧...")
            # 放宽结束时间，确保包含触发时刻的帧（考虑AI处理延迟）
            start_time = trigger_time - pre_seconds
            end_time_historical = trigger_time + 1.0  # 多留1秒余量

            # 第二步：等待并收集未来M秒的帧
            logger.info(f"[录制 {alert_id}] 正在等待并收集未来 {post_seconds} 秒的帧...")

            with self.lock:
                self.recording_tasks[alert_id]['status'] = 'collecting'

            end_time = trigger_time + post_seconds
            real_end_time = time.time() + post_seconds

            last_collected_timestamp = start_time - 0.001

            def write_frame(frame: np.ndarray, timestamp: float) -> bool:
                nonlocal video_writer, written_frame_count, last_collected_timestamp
                if timestamp <= last_collected_timestamp:
                    return True
                if timestamp > end_time:
                    return True

                if video_writer is None:
                    video_writer = self._open_video_writer(frame, output_path)
                    if video_writer is None:
                        return False

                if not self._write_frame(video_writer, frame):
                    return False

                written_frame_count += 1
                last_collected_timestamp = timestamp
                return True

            historical_written_count = 0
            for frame, timestamp in self.buffer.iter_frames_in_time_range(start_time, end_time_historical):
                if not write_frame(frame, timestamp):
                    raise RuntimeError("初始化视频写入器失败")
                historical_written_count += 1

            logger.info(
                f"[录制 {alert_id}] 历史帧写入完成: {historical_written_count} 帧 "
                f"(范围: {start_time:.2f} - {end_time_historical:.2f})"
            )

            logger.info(f"[录制 {alert_id}] 等待时间范围: {trigger_time:.2f} - {end_time:.2f} (实际等到 {real_end_time:.2f})")

            # 等待并收集未来的帧
            check_count = 0
            while time.time() < real_end_time:
                check_count += 1

                current_time = time.time()
                next_start_time = max(start_time, last_collected_timestamp + 0.001)
                future_written = 0
                for frame, timestamp in self.buffer.iter_frames_in_time_range(next_start_time, current_time):
                    if not write_frame(frame, timestamp):
                        raise RuntimeError("写入未来帧失败")
                    future_written += 1
                
                # 每秒记录一次进度
                if check_count % 20 == 0:
                    logger.debug(
                        f"[录制 {alert_id}] 已写入 {written_frame_count} 帧 "
                        f"(本轮新增 {future_written} 帧)，继续等待..."
                    )
                
                time.sleep(0.05)  # 短暂休眠，避免CPU占用过高

            logger.info(f"[录制 {alert_id}] 收集未来帧完成，累计写入 {written_frame_count} 帧 (检查了 {check_count} 次)")

            if written_frame_count <= 0:
                # 提供详细的诊断信息
                logger.error(f"[录制 {alert_id}] 没有收集到任何帧，取消录制")
                logger.error(f"[录制 {alert_id}] 诊断信息:")
                logger.error(f"  - Buffer状态: {buffer_stats}")
                logger.error(f"  - 历史写入帧数: {historical_written_count}")
                logger.error(f"  - 总写入帧数: {written_frame_count}")
                logger.error(f"  - 触发时间: {trigger_time:.2f}")
                logger.error(f"  - 时间范围: [{start_time:.2f}, {end_time:.2f}]")
                
                # 尝试获取buffer中任意帧来诊断问题
                if buffer_stats['count'] > 0:
                    oldest = self.buffer.peek_with_timestamp(0)
                    newest = self.buffer.peek_with_timestamp(-1)
                    if oldest and newest:
                        logger.error(f"  - Buffer最旧帧时间戳: {oldest[1]:.2f}")
                        logger.error(f"  - Buffer最新帧时间戳: {newest[1]:.2f}")
                        logger.error(f"  - Buffer时间跨度: {newest[1] - oldest[1]:.2f}秒")

                if video_writer is not None:
                    video_writer.release()
                
                with self.lock:
                    self.recording_tasks[alert_id]['status'] = 'failed'
                return
            
            with self.lock:
                self.recording_tasks[alert_id]['status'] = 'encoding'

            if video_writer is not None:
                video_writer.release()

            logger.info(f"[录制 {alert_id}] 视频录制完成: {output_path}, 共写入 {written_frame_count} 帧")
            with self.lock:
                self.recording_tasks[alert_id]['status'] = 'completed'
                    
        except Exception as e:
            logger.error(f"[录制 {alert_id}] 录制过程出错: {e}", exc_info=True)
            with self.lock:
                if alert_id in self.recording_tasks:
                    self.recording_tasks[alert_id]['status'] = 'failed'
            try:
                if video_writer is not None:
                    video_writer.release()
            except Exception:
                pass
            try:
                if video_writer is not None:
                    video_writer.release()
            except Exception:
                pass

    def _open_video_writer(self, first_frame: np.ndarray, output_path: str):
        """基于首帧创建视频写入器。"""
        require_cv2()
        pixel_format = self._get_frame_pixel_format(first_frame)
        width, height = infer_frame_dimensions(
            first_frame,
            pixel_format=pixel_format,
        )

        fourcc_options = [
            'avc1',
            'H264',
            'X264',
            'mp4v',
        ]

        for fourcc_str in fourcc_options:
            try:
                fourcc = cv2.VideoWriter_fourcc(*fourcc_str)
                video_writer = cv2.VideoWriter(
                    output_path,
                    fourcc,
                    self.fps,
                    (width, height)
                )
                if video_writer.isOpened():
                    logger.info(f"使用编码器: {fourcc_str}")
                    return video_writer
                video_writer.release()
            except Exception as exc:
                logger.debug(f"编码器 {fourcc_str} 不可用: {exc}")

        logger.error(f"无法创建视频写入器: {output_path} (尝试了所有编码器)")
        return None

    def _write_frame(self, video_writer, frame: np.ndarray) -> bool:
        if video_writer is None:
            return False

        try:
            require_cv2()
            pixel_format = self._get_frame_pixel_format(frame)
            bgr_frame = frame_to_bgr(
                frame,
                pixel_format=pixel_format,
                width=getattr(self.buffer, 'width', None) if pixel_format in {'nv12', 'yuv420p'} else None,
                height=getattr(self.buffer, 'height', None) if pixel_format in {'nv12', 'yuv420p'} else None,
            )
            video_writer.write(bgr_frame)
            return True
        except Exception as exc:
            logger.error(f"写入视频帧失败: {exc}", exc_info=True)
            return False
    
    def _encode_video(self, frames: List[Tuple[np.ndarray, float]], output_path: str) -> bool:
        """
        将帧列表编码为视频文件
        
        Args:
            frames: [(frame, timestamp), ...] 帧和时间戳列表
            output_path: 输出视频路径
            
        Returns:
            是否编码成功
        """
        if not frames:
            logger.error("没有帧可以编码")
            return False
        
        try:
            require_cv2()
            # 获取视频尺寸（从第一帧）
            first_frame = frames[0][0]
            pixel_format = self._get_frame_pixel_format(first_frame)
            width, height = infer_frame_dimensions(
                first_frame,
                pixel_format=pixel_format,
            )
            
            # 创建VideoWriter - 使用H.264编码器以确保浏览器兼容性
            # 尝试多个H.264编码器，按优先级顺序
            fourcc_options = [
                'avc1',  # H.264编码（macOS推荐）
                'H264',  # H.264编码（通用）
                'X264',  # H.264编码（x264库）
                'mp4v'   # MPEG-4编码（备选方案）
            ]
            
            video_writer = None
            for fourcc_str in fourcc_options:
                try:
                    fourcc = cv2.VideoWriter_fourcc(*fourcc_str)
                    video_writer = cv2.VideoWriter(
                        output_path,
                        fourcc,
                        self.fps,
                        (width, height)
                    )
                    if video_writer.isOpened():
                        logger.info(f"使用编码器: {fourcc_str}")
                        break
                    else:
                        video_writer.release()
                        video_writer = None
                except Exception as e:
                    logger.debug(f"编码器 {fourcc_str} 不可用: {e}")
                    continue
            
            if not video_writer or not video_writer.isOpened():
                logger.error(f"无法创建视频写入器: {output_path} (尝试了所有编码器)")
                return False
            
            # 写入所有帧
            for frame, timestamp in frames:
                pixel_format = self._get_frame_pixel_format(frame)
                bgr_frame = frame_to_bgr(
                    frame,
                    pixel_format=pixel_format,
                    width=getattr(self.buffer, 'width', None) if pixel_format in {'nv12', 'yuv420p'} else None,
                    height=getattr(self.buffer, 'height', None) if pixel_format in {'nv12', 'yuv420p'} else None,
                )
                video_writer.write(bgr_frame)
            
            # 释放资源
            video_writer.release()
            
            logger.info(f"视频编码完成: {output_path}, 共 {len(frames)} 帧")
            return True
            
        except Exception as e:
            logger.error(f"编码视频时出错: {e}", exc_info=True)
            return False

    def _get_frame_pixel_format(self, frame: np.ndarray) -> str:
        frame = np.asarray(frame)
        if frame.ndim == 3 and frame.shape[2] == 3:
            # 压缩录制缓冲区解码后的帧统一是 RGB，不能再按底层 buffer 像素格式解释。
            return 'rgb24'
        return detect_frame_pixel_format(
            frame,
            pixel_format=getattr(self.buffer, 'pixel_format', 'nv12'),
        )
    
    def get_recording_status(self, alert_id: int) -> Optional[dict]:
        """
        获取录制任务状态
        
        Args:
            alert_id: 预警ID
            
        Returns:
            任务状态信息或None
        """
        with self.lock:
            if alert_id in self.recording_tasks:
                info = self.recording_tasks[alert_id]
                return {
                    'alert_id': info['alert_id'],
                    'status': info['status'],
                    'output_path': info['output_path'],
                    'relative_path': info['relative_path']
                }
        return None
    
    def cleanup_completed_tasks(self, max_age_seconds: int = 3600):
        """
        清理已完成的录制任务
        
        Args:
            max_age_seconds: 保留任务的最大时长（秒）
        """
        with self.lock:
            current_time = time.time()
            to_remove = []
            
            for alert_id, info in self.recording_tasks.items():
                if info['status'] in ['completed', 'failed']:
                    # 检查任务年龄
                    task_age = current_time - info['trigger_time']
                    if task_age > max_age_seconds:
                        to_remove.append(alert_id)
            
            for alert_id in to_remove:
                del self.recording_tasks[alert_id]
                logger.debug(f"清理录制任务 {alert_id}")

    def shutdown(self):
        """关闭录制器，优先等待活跃录制线程退出。"""
        active_threads = []
        with self.lock:
            for info in self.recording_tasks.values():
                thread = info.get('thread')
                if thread is not None and thread.is_alive():
                    active_threads.append(thread)

        for thread in active_threads:
            thread.join(timeout=10)

        still_running = any(thread.is_alive() for thread in active_threads)
        if still_running:
            logger.warning("VideoRecorder 关闭时仍有活跃录制线程，暂不回收录制器")
            return False

        self.cleanup_completed_tasks(max_age_seconds=0)
        return True


class VideoRecorderManager:
    """视频录制管理器，管理多个任务的录制器"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self.recorders = {}  # recorder_key -> VideoRecorder
        self._initialized = True
    
    def get_recorder(
        self,
        source_id: int,
        buffer: VideoRingBuffer,
        save_dir: str,
        fps: int = 10,
        recorder_key=None,
    ) -> VideoRecorder:
        """
        获取或创建指定视频源的录制器
        
        Args:
            source_id: 视频源ID
            buffer: VideoRingBuffer实例
            save_dir: 保存目录
            fps: 视频帧率
            
        Returns:
            VideoRecorder实例
        """
        key = recorder_key if recorder_key is not None else source_id
        if key not in self.recorders:
            self.recorders[key] = VideoRecorder(buffer, save_dir, fps)
        
        return self.recorders[key]
    
    def cleanup_recorder(self, recorder_key):
        """清理指定视频源的录制器"""
        if recorder_key in self.recorders:
            if self.recorders[recorder_key].shutdown():
                del self.recorders[recorder_key]
                return True
            return False
        return True
