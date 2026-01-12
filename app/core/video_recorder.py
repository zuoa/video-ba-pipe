"""
视频录制模块
用于从VideoRingBuffer提取帧并编码为视频文件
支持录制预警前N秒和后M秒的视频
"""
import os
import threading
import time
from typing import List, Tuple, Optional
import cv2
import numpy as np

from app import logger
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
            
            # 检查buffer状态
            buffer_stats = self.buffer.get_stats()
            logger.info(f"[录制 {alert_id}] Buffer状态: {buffer_stats['count']}帧 / {buffer_stats['capacity']}容量")
            
            # 第一步：从RingBuffer获取历史帧（过去N秒）
            logger.info(f"[录制 {alert_id}] 正在提取过去 {pre_seconds} 秒的帧...")
            # 放宽结束时间，确保包含触发时刻的帧（考虑AI处理延迟）
            start_time = trigger_time - pre_seconds
            end_time_historical = trigger_time + 1.0  # 多留1秒余量

            # 获取历史帧（放宽结束时间边界）
            historical_frames = self.buffer.get_frames_in_time_range(
                start_time=start_time,
                end_time=end_time_historical
            )

            logger.info(f"[录制 {alert_id}] 提取到 {len(historical_frames)} 个历史帧 (范围: {start_time:.2f} - {end_time_historical:.2f})")

            # 如果没有历史帧，尝试获取最近的所有帧
            if not historical_frames and buffer_stats['count'] > 0:
                logger.warning(f"[录制 {alert_id}] 时间范围内无历史帧，尝试获取最近 {pre_seconds} 秒的所有帧")
                historical_frames = self.buffer.get_recent_frames(pre_seconds)
                logger.info(f"[录制 {alert_id}] 重新提取到 {len(historical_frames)} 个历史帧")

            # 第二步：等待并收集未来M秒的帧
            logger.info(f"[录制 {alert_id}] 正在等待并收集未来 {post_seconds} 秒的帧...")

            with self.lock:
                self.recording_tasks[alert_id]['status'] = 'collecting'

            future_frames = []
            end_time = trigger_time + post_seconds
            real_end_time = time.time() + post_seconds

            # 关键修复：从历史帧的最新时间戳继续收集，避免重复收集
            # 如果历史帧为空，从 trigger_time 开始
            if historical_frames:
                # 从历史帧的最新时间戳继续（加0.001避免重复收集最后一帧）
                last_collected_timestamp = historical_frames[-1][1] + 0.001
                logger.info(f"[录制 {alert_id}] 从历史帧最新时间戳继续: {last_collected_timestamp - 0.001:.3f}")
            else:
                # 没有历史帧，从当前开始
                last_collected_timestamp = trigger_time
                logger.info(f"[录制 {alert_id}] 无历史帧，从trigger_time开始收集")

            logger.info(f"[录制 {alert_id}] 等待时间范围: {trigger_time:.2f} - {end_time:.2f} (实际等到 {real_end_time:.2f})")

            # 等待并收集未来的帧
            check_count = 0
            while time.time() < real_end_time:
                check_count += 1

                # 获取buffer中所有在时间范围内的新帧
                current_time = time.time()
                buffer_frames = self.buffer.get_frames_in_time_range(
                    start_time=last_collected_timestamp,
                    end_time=current_time
                )
                
                # 添加新帧到列表
                for frame, timestamp in buffer_frames:
                    if timestamp > last_collected_timestamp and timestamp <= end_time:
                        future_frames.append((frame, timestamp))
                        last_collected_timestamp = timestamp
                
                # 每秒记录一次进度
                if check_count % 20 == 0:
                    logger.debug(f"[录制 {alert_id}] 已收集 {len(future_frames)} 帧，继续等待...")
                
                time.sleep(0.05)  # 短暂休眠，避免CPU占用过高
            
            logger.info(f"[录制 {alert_id}] 收集到 {len(future_frames)} 个未来帧 (检查了 {check_count} 次)")
            
            # 第三步：合并所有帧并编码为视频
            all_frames = historical_frames + future_frames
            
            if not all_frames:
                # 提供详细的诊断信息
                logger.error(f"[录制 {alert_id}] 没有收集到任何帧，取消录制")
                logger.error(f"[录制 {alert_id}] 诊断信息:")
                logger.error(f"  - Buffer状态: {buffer_stats}")
                logger.error(f"  - 历史帧数: {len(historical_frames)}")
                logger.error(f"  - 未来帧数: {len(future_frames)}")
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
                
                with self.lock:
                    self.recording_tasks[alert_id]['status'] = 'failed'
                return
            
            logger.info(f"[录制 {alert_id}] 开始编码视频，共 {len(all_frames)} 帧")
            
            with self.lock:
                self.recording_tasks[alert_id]['status'] = 'encoding'
            
            # 编码视频
            success = self._encode_video(all_frames, output_path)
            
            if success:
                logger.info(f"[录制 {alert_id}] 视频录制完成: {output_path}")
                with self.lock:
                    self.recording_tasks[alert_id]['status'] = 'completed'
            else:
                logger.error(f"[录制 {alert_id}] 视频编码失败")
                with self.lock:
                    self.recording_tasks[alert_id]['status'] = 'failed'
                    
        except Exception as e:
            logger.error(f"[录制 {alert_id}] 录制过程出错: {e}", exc_info=True)
            with self.lock:
                if alert_id in self.recording_tasks:
                    self.recording_tasks[alert_id]['status'] = 'failed'
    
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
            # 获取视频尺寸（从第一帧）
            first_frame = frames[0][0]
            height, width = first_frame.shape[:2]
            
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
                # 确保帧格式正确（RGB转BGR）
                if len(frame.shape) == 3 and frame.shape[2] == 3:
                    # 假设输入是RGB格式，转换为BGR
                    bgr_frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                else:
                    bgr_frame = frame
                
                video_writer.write(bgr_frame)
            
            # 释放资源
            video_writer.release()
            
            logger.info(f"视频编码完成: {output_path}, 共 {len(frames)} 帧")
            return True
            
        except Exception as e:
            logger.error(f"编码视频时出错: {e}", exc_info=True)
            return False
    
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
        
        self.recorders = {}  # source_id -> VideoRecorder
        self._initialized = True
    
    def get_recorder(self, source_id: int, buffer: VideoRingBuffer, 
                     save_dir: str, fps: int = 10) -> VideoRecorder:
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
        if source_id not in self.recorders:
            self.recorders[source_id] = VideoRecorder(buffer, save_dir, fps)
        
        return self.recorders[source_id]
    
    def cleanup_recorder(self, source_id: int):
        """清理指定视频源的录制器"""
        if source_id in self.recorders:
            del self.recorders[source_id]

