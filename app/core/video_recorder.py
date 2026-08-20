"""
视频录制模块
用于从VideoRingBuffer提取帧并编码为视频文件
支持录制预警前N秒和后M秒的视频
"""
import os
import re
import shutil
import subprocess
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

# HTML5 <video> in Chrome/Safari/Firefox plays H.264 in MP4, not MPEG-4 Part 2
# (mp4v). Prefer software x264, then common hardware H.264 encoders.
BROWSER_H264_ENCODERS = (
    'libx264',
    'libopenh264',
    'h264_nvenc',
    'h264_videotoolbox',
    'h264_qsv',
    'h264_rkmpp',
)
OPENCV_H264_FOURCCS = ('avc1', 'H264', 'X264')


def even_frame_size(width: int, height: int) -> Tuple[int, int]:
    """H.264 yuv420p requires even width and height."""
    even_width = int(width) - (int(width) % 2)
    even_height = int(height) - (int(height) % 2)
    if even_width < 2 or even_height < 2:
        raise ValueError(f"帧尺寸过小，无法编码 H.264: {width}x{height}")
    return even_width, even_height


def build_ffmpeg_h264_output_args(encoder: str) -> List[str]:
    args = ['-an', '-c:v', encoder]
    if encoder == 'libx264':
        args.extend(['-preset', 'veryfast', '-profile:v', 'baseline', '-level', '3.1'])
    elif encoder == 'h264_nvenc':
        args.extend(['-preset', 'p4', '-profile:v', 'baseline'])
    elif encoder == 'h264_videotoolbox':
        args.extend(['-profile:v', 'baseline'])
    args.extend([
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        '-tag:v', 'avc1',
        '-f', 'mp4',
    ])
    return args


def build_ffmpeg_raw_encode_command(
    ffmpeg_path: str,
    output_path: str,
    fps: float,
    frame_size: Tuple[int, int],
    encoder: str,
) -> List[str]:
    width, height = frame_size
    return [
        ffmpeg_path,
        '-hide_banner',
        '-loglevel', 'error',
        '-y',
        '-f', 'rawvideo',
        '-pix_fmt', 'bgr24',
        '-video_size', f'{width}x{height}',
        '-framerate', str(fps),
        '-i', 'pipe:0',
        *build_ffmpeg_h264_output_args(encoder),
        output_path,
    ]


def probe_mp4_video_codec(path: str, timeout_seconds: float = 10.0) -> Optional[str]:
    ffprobe_path = shutil.which('ffprobe')
    if not ffprobe_path:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=codec_name',
                '-of', 'csv=p=0',
                path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    codec = (result.stdout or '').strip().splitlines()
    return codec[0].strip().lower() if codec else None


def ensure_browser_compatible_mp4(
    path: str,
    ffmpeg_path: Optional[str] = None,
) -> bool:
    """Rewrite an alert mp4 as baseline H.264 + faststart so browsers can play it."""
    if not path or not os.path.isfile(path):
        return False

    ffmpeg_path = ffmpeg_path or shutil.which('ffmpeg')
    codec = probe_mp4_video_codec(path)
    if not ffmpeg_path:
        if codec == 'h264':
            return True
        logger.error(f"无法检查录像兼容性，未找到 ffmpeg: {path}")
        return False

    encoder = VideoRecorder._select_ffmpeg_encoder(ffmpeg_path)
    temp_path = f'{path}.browser.tmp.mp4'
    try:
        if codec == 'h264':
            command = [
                ffmpeg_path,
                '-hide_banner',
                '-loglevel', 'error',
                '-y',
                '-i', path,
                '-an',
                '-c:v', 'copy',
                '-movflags', '+faststart',
                '-tag:v', 'avc1',
                '-f', 'mp4',
                temp_path,
            ]
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=120,
                check=False,
            )
            if result.returncode == 0 and os.path.isfile(temp_path) and os.path.getsize(temp_path) > 0:
                os.replace(temp_path, path)
            return True

        if encoder is None:
            logger.error(f"无法转码为浏览器可播放的 H.264: {path}")
            return False

        if codec:
            logger.warning(
                f"告警录像编码为 {codec}，浏览器无法播放，正在转码为 H.264: {path}"
            )
        command = [
            ffmpeg_path,
            '-hide_banner',
            '-loglevel', 'error',
            '-y',
            '-i', path,
            *build_ffmpeg_h264_output_args(encoder),
            temp_path,
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        if result.returncode != 0 or not os.path.isfile(temp_path) or os.path.getsize(temp_path) <= 0:
            stderr = (result.stderr or b'').decode('utf-8', errors='replace').strip()
            logger.error(
                f"转码浏览器兼容 MP4 失败: {path}; "
                f"退出码 {result.returncode}; {stderr}"
            )
            return False

        os.replace(temp_path, path)
        return probe_mp4_video_codec(path) == 'h264'
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error(f"转码浏览器兼容 MP4 失败: {path}; {exc}", exc_info=True)
        return False
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


class _FFmpegVideoWriter:
    """通过独立 FFmpeg 进程写入 BGR 帧，输出浏览器可播放的 H.264 MP4。"""

    def __init__(
        self,
        ffmpeg_path: str,
        output_path: str,
        fps: float,
        frame_size: Tuple[int, int],
        encoder: str,
    ):
        width, height = even_frame_size(*frame_size)
        command = build_ffmpeg_raw_encode_command(
            ffmpeg_path=ffmpeg_path,
            output_path=output_path,
            fps=fps,
            frame_size=(width, height),
            encoder=encoder,
        )

        self.output_path = output_path
        self.encoder = encoder
        self.width = int(width)
        self.height = int(height)
        self._released = False
        self._stderr = ''
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    def isOpened(self) -> bool:
        return (
            not self._released
            and self._process.stdin is not None
            and self._process.poll() is None
        )

    def write(self, frame: np.ndarray):
        if not self.isOpened():
            raise RuntimeError(self._failure_message())

        frame = np.asarray(frame)
        expected_shape = (self.height, self.width, 3)
        if frame.shape != expected_shape:
            raise ValueError(
                f"FFmpeg 写入帧尺寸不匹配: {frame.shape} != {expected_shape}"
            )

        frame = np.ascontiguousarray(frame, dtype=np.uint8)
        try:
            remaining = memoryview(frame).cast('B')
            while remaining:
                written = self._process.stdin.write(remaining)
                if not written:
                    raise BrokenPipeError("FFmpeg stdin 已关闭")
                remaining = remaining[written:]
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(self._failure_message()) from exc

    def release(self) -> bool:
        if self._released:
            return self._process.returncode == 0

        self._released = True
        stdin = self._process.stdin
        if stdin is not None:
            try:
                stdin.close()
            except OSError:
                pass
            # communicate() 会尝试刷新仍挂在 Popen 上的 stdin。
            self._process.stdin = None

        try:
            _, stderr = self._process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            self._process.kill()
            _, stderr = self._process.communicate()
            self._stderr = self._decode_stderr(stderr)
            logger.error(
                f"FFmpeg 视频编码超时并已终止: {self.output_path}"
            )
            return False

        self._stderr = self._decode_stderr(stderr)
        if self._process.returncode != 0:
            logger.error(self._failure_message())
            return False
        return True

    def _failure_message(self) -> str:
        if (
            not self._stderr
            and self._process.stderr is not None
            and self._process.poll() is not None
        ):
            try:
                self._stderr = self._decode_stderr(self._process.stderr.read())
            except OSError:
                pass
        detail = self._stderr or f"进程退出码 {self._process.returncode}"
        return (
            f"FFmpeg 视频编码失败 ({self.encoder}): {self.output_path}; "
            f"{detail}"
        )

    @staticmethod
    def _decode_stderr(stderr) -> str:
        if not stderr:
            return ''
        if isinstance(stderr, bytes):
            stderr = stderr.decode('utf-8', errors='replace')
        return str(stderr).strip()


class VideoRecorder:
    """视频录制器，从RingBuffer提取帧并编码为视频"""
    
    def __init__(
        self,
        buffer: VideoRingBuffer,
        save_dir: str,
        fps: int = 10,
        max_disk_used_percent: float = 80.0,
    ):
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
        self.max_disk_used_percent = float(max_disk_used_percent)
        self._last_disk_check_at = float('-inf')
        self._last_disk_allowed = True
        self._output_frame_size: Optional[Tuple[int, int]] = None
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
                if not self._disk_allows_recording():
                    raise RuntimeError(
                        f"磁盘已达到 {self.max_disk_used_percent:g}% 停录像水位"
                    )

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

            if video_writer is not None and not self._release_video_writer(video_writer):
                raise RuntimeError("结束视频编码失败")

            if not ensure_browser_compatible_mp4(output_path):
                raise RuntimeError("告警录像无法转换为浏览器可播放的 H.264")

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

    def _open_video_writer(self, first_frame: np.ndarray, output_path: str):
        """基于首帧创建视频写入器。优先独立 FFmpeg H.264，避免 OpenCV mp4v。"""
        pixel_format = self._get_frame_pixel_format(first_frame)
        width, height = even_frame_size(
            *infer_frame_dimensions(
                first_frame,
                pixel_format=pixel_format,
            )
        )
        self._output_frame_size = (width, height)

        ffmpeg_writer = self._open_ffmpeg_video_writer(
            output_path,
            width=width,
            height=height,
        )
        if ffmpeg_writer is not None:
            return ffmpeg_writer

        require_cv2()
        for fourcc_str in OPENCV_H264_FOURCCS:
            try:
                fourcc = cv2.VideoWriter_fourcc(*fourcc_str)
                video_writer = cv2.VideoWriter(
                    output_path,
                    fourcc,
                    self.fps,
                    (width, height)
                )
                if video_writer.isOpened():
                    logger.info(f"使用 OpenCV 编码器: {fourcc_str}")
                    return video_writer
                video_writer.release()
            except Exception as exc:
                logger.debug(f"编码器 {fourcc_str} 不可用: {exc}")

        logger.error(
            f"无法创建浏览器可播放的视频写入器: {output_path} "
            f"(FFmpeg H.264 不可用; {self._opencv_videoio_summary()})"
        )
        return None

    def _disk_allows_recording(self) -> bool:
        now = time.monotonic()
        if now - self._last_disk_check_at < 1.0:
            return self._last_disk_allowed
        self._last_disk_check_at = now
        try:
            disk = shutil.disk_usage(self.save_dir)
            used_percent = (disk.used / disk.total * 100.0) if disk.total else 100.0
            self._last_disk_allowed = used_percent < self.max_disk_used_percent
        except OSError as exc:
            logger.error(f"读取录像目录磁盘水位失败，停止录像: {exc}")
            self._last_disk_allowed = False
        return self._last_disk_allowed

    def _open_ffmpeg_video_writer(
        self,
        output_path: str,
        width: int,
        height: int,
    ):
        ffmpeg_path = shutil.which('ffmpeg')
        if not ffmpeg_path:
            logger.error(
                f"无法创建视频写入器: {output_path}; "
                "OpenCV 编码不可用，且未找到 ffmpeg 命令"
            )
            return None

        encoder = self._select_ffmpeg_encoder(ffmpeg_path)
        if encoder is None:
            logger.error(
                f"无法创建视频写入器: {output_path}; "
                "FFmpeg 未提供浏览器可播放的 H.264 编码器"
            )
            return None

        try:
            writer = _FFmpegVideoWriter(
                ffmpeg_path=ffmpeg_path,
                output_path=output_path,
                fps=self.fps,
                frame_size=(width, height),
                encoder=encoder,
            )
            if writer.isOpened():
                logger.info(f"使用独立 FFmpeg 编码器: {encoder}")
                return writer
            writer.release()
        except Exception as exc:
            logger.error(
                f"启动独立 FFmpeg 视频写入器失败: {output_path}; {exc}",
                exc_info=True,
            )

        logger.error(f"无法创建视频写入器: {output_path}")
        return None

    @staticmethod
    def _select_ffmpeg_encoder(ffmpeg_path: str) -> Optional[str]:
        try:
            result = subprocess.run(
                [ffmpeg_path, '-hide_banner', '-encoders'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error(f"查询 FFmpeg 编码器失败: {exc}")
            return None

        if result.returncode != 0:
            logger.error(
                f"查询 FFmpeg 编码器失败，退出码: {result.returncode}"
            )
            return None

        encoders = set()
        for line in result.stdout.splitlines():
            match = re.match(r'^\s*[A-Z.]{6}\s+(\S+)', line)
            if match:
                encoders.add(match.group(1))

        for encoder in BROWSER_H264_ENCODERS:
            if encoder in encoders:
                return encoder
        return None

    @staticmethod
    def _opencv_videoio_summary() -> str:
        try:
            version = getattr(cv2, '__version__', 'unknown')
            interesting_lines = []
            for line in cv2.getBuildInformation().splitlines():
                stripped = line.strip()
                if stripped.startswith(('FFMPEG:', 'GStreamer:')):
                    interesting_lines.append(stripped)
            details = ', '.join(interesting_lines) or '视频后端信息未知'
            return f"cv2={version}, {details}"
        except Exception:
            return "无法读取 OpenCV 视频后端信息"

    @staticmethod
    def _release_video_writer(video_writer) -> bool:
        result = video_writer.release()
        return result is not False

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
            output_size = getattr(self, '_output_frame_size', None)
            if output_size:
                target_width, target_height = output_size
                if bgr_frame.shape[1] != target_width or bgr_frame.shape[0] != target_height:
                    bgr_frame = cv2.resize(bgr_frame, (target_width, target_height))
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

            video_writer = self._open_video_writer(first_frame, output_path)
            if video_writer is None:
                return False
            
            # 写入所有帧
            for frame, timestamp in frames:
                if not self._write_frame(video_writer, frame):
                    video_writer.release()
                    return False
            
            # 释放资源
            if not self._release_video_writer(video_writer):
                return False

            if not ensure_browser_compatible_mp4(output_path):
                logger.error(f"告警录像无法转换为浏览器可播放的 H.264: {output_path}")
                return False
            
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

    def shutdown(self, wait_timeout: float = 10.0):
        """关闭录制器，优先等待活跃录制线程退出。"""
        active_threads = []
        with self.lock:
            for info in self.recording_tasks.values():
                thread = info.get('thread')
                if thread is not None and thread.is_alive():
                    active_threads.append(thread)

        deadline = time.monotonic() + max(0.0, float(wait_timeout))
        for thread in active_threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)

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
        max_disk_used_percent: float = 80.0,
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
            self.recorders[key] = VideoRecorder(
                buffer,
                save_dir,
                fps,
                max_disk_used_percent=max_disk_used_percent,
            )
        
        return self.recorders[key]
    
    def cleanup_recorder(self, recorder_key, wait_timeout: float = 10.0):
        """清理指定视频源的录制器"""
        if recorder_key in self.recorders:
            if self.recorders[recorder_key].shutdown(wait_timeout=wait_timeout):
                del self.recorders[recorder_key]
                return True
            return False
        return True
