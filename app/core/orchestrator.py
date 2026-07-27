import signal
import os
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

from app import logger
from app.config import (
    APP_DIR,
    ANALYSIS_BUFFER_SECONDS,
    ANALYSIS_TARGET_FPS,
    VIDEO_DECODER_TYPE,
    VIDEO_FRAME_PIXEL_FORMAT,
    FFMPEG_SW_DECODER_THREADS,
    DECODER_OUTPUT_QUEUE_SIZE,
    RECORDING_BUFFER_DURATION,
    RECORDING_COMPRESSED_MAX_BYTES,
    RECORDING_ENABLED,
    RECORDING_FPS,
    RECORDING_JPEG_QUALITY,
    POST_ALERT_DURATION,
    NO_FRAME_WARNING_THRESHOLD,
    NO_FRAME_CRITICAL_THRESHOLD,
    HIGH_ERROR_COUNT_THRESHOLD,
    HEALTH_MONITOR_ENABLED,
    SOURCE_ROTATION_CONFIG_REFRESH_SECONDS,
    SOURCE_ROTATION_DRAIN_GRACE_SECONDS,
    SOURCE_ROTATION_STARTUP_TIMEOUT_SECONDS,
)
from app.core.alert_media_cleaner import AlertMediaCleaner
from app.core.compressed_ringbuffer import CompressedVideoRingBuffer
from app.core.database_models import db, VideoSource, Workflow, SourceHealthLog
from app.core.ringbuffer import VideoRingBuffer
from app.core.workflow_runtime import build_workflow_signature, extract_source_id_from_workflow_data
from app.core.source_rotation import (
    RoundRobinBatchSelector,
    get_source_rotation_config,
    normalize_source_rotation_config,
)
from app.core.video_probe import (
    VideoCodecProbeError,
    normalize_video_codec,
    probe_video_codec,
)


class OutputReader(threading.Thread):
    """持续读取子进程输出的线程"""
    def __init__(
        self,
        process,
        log_label,
        stream_type='stdout',
        on_line: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(daemon=True)
        self.process = process
        self.log_label = log_label
        self.stream_type = stream_type
        self.stream = getattr(process, stream_type)
        self.running = True
        self.on_line = on_line

    def run(self):
        """持续读取并输出日志"""
        try:
            for line in iter(self.stream.readline, ''):
                if not self.running:
                    break
                if line:
                    log_msg = line.rstrip('\n\r')
                    if self.on_line is not None:
                        try:
                            self.on_line(log_msg)
                        except Exception as exc:
                            logger.warning(f"[{self.log_label}] 处理子进程状态消息失败: {exc}")
                    if self.stream_type == 'stderr':
                        logger.error(f"[{self.log_label}] {log_msg}")
                    else:
                        logger.info(f"[{self.log_label}] {log_msg}")
        except Exception as e:
            if self.running:
                logger.warning(f"[{self.log_label}] 读取{self.stream_type}时出错: {e}")

    def stop(self):
        """停止读取线程"""
        self.running = False


class Orchestrator:
    def __init__(self):
        self.running_processes = {}
        self.workflow_hosts = {}
        self.buffers = {}
        self.recording_buffers = {}
        self.source_start_times = {}  # 记录视频源启动时间
        self.last_health_log_times = {}  # 记录上次健康日志时间
        self.source_probe_retry_at = {}
        self.workflow_host_signatures = {}
        self.draining_sources = {}
        self.rotation_selector = RoundRobinBatchSelector()
        self.rotation_config = normalize_source_rotation_config(
            get_source_rotation_config()
        )
        self.rotation_batch_ids = []
        self.rotation_phase = 'IDLE'
        self.rotation_batch_launch_at = None
        self.rotation_dwell_started_at = None
        self.last_rotation_config_refresh_at = 0.0
        self._rotation_was_enabled = self.rotation_config.enabled
        self.desired_source_ids = set()
        self.media_cleaner = AlertMediaCleaner()
        db.connect(reuse_if_open=True)
        VideoSource.update(status='STOPPED', decoder_pid=None).execute()

        # 健康监控配置
        self.health_check_enabled = HEALTH_MONITOR_ENABLED
        self.start_grace_period = 60  # 启动后60秒内不进行健康检查
        self.health_log_interval = 30  # 健康日志记录间隔（秒），避免日志泛滥

    def _stop_process(self, process_info: dict, wait_timeout: float = 3.0):
        if not process_info:
            return

        process = process_info.get('process')
        stdout_reader = process_info.get('stdout_reader')
        stderr_reader = process_info.get('stderr_reader')

        if stdout_reader:
            stdout_reader.stop()
        if stderr_reader:
            stderr_reader.stop()

        if process is None:
            return

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=wait_timeout)
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"子进程 PID {process.pid} 未在 {wait_timeout}s 内退出，执行 kill"
                )
                process.kill()
                process.wait(timeout=1)

        try:
            process.communicate(timeout=1)
        except Exception:
            pass

        if stdout_reader:
            stdout_reader.join(timeout=1)
        if stderr_reader:
            stderr_reader.join(timeout=1)

    @staticmethod
    def _extract_source_id(workflow: Workflow):
        return extract_source_id_from_workflow_data(workflow.data_dict)

    def _build_active_workflow_groups(self):
        groups = {}
        for workflow in Workflow.select().where(Workflow.is_active == True):
            source_id = self._extract_source_id(workflow)
            if source_id is None:
                logger.warning(f"工作流 {workflow.id} 没有合法视频源节点，跳过 host 分组")
                continue
            groups.setdefault(source_id, []).append(workflow)

        for workflows in groups.values():
            workflows.sort(key=lambda item: item.id)

        return groups

    def _check_source_health(self, source: VideoSource):
        """
        检查单个视频源的健康状态

        Args:
            source: 视频源对象

        Returns:
            bool: True 表示健康，False 表示需要重启
        """
        if source.id not in self.buffers:
            return True

        # 检查是否在启动宽限期内
        if source.id in self.source_start_times:
            time_since_start = time.time() - self.source_start_times[source.id]
            if time_since_start < self.start_grace_period:
                # 在宽限期内，跳过健康检查
                return True

        buffer = self.buffers[source.id]
        health_status = buffer.get_health_status()

        time_since_last_frame = health_status['time_since_last_frame']
        error_count = health_status['consecutive_errors']
        frame_count = health_status['frame_count']

        need_reboot = False

        # 如果从未写入过帧，跳过检查（可能在初始化）
        if frame_count == 0:
            return True

        # 检查1: 长时间无帧
        if time_since_last_frame > NO_FRAME_CRITICAL_THRESHOLD:
            # 检查日志记录频率
            last_log_time = self.last_health_log_times.get(source.id, 0)
            if time.time() - last_log_time >= self.health_log_interval:
                logger.critical(
                    f"🚨 视频源 {source.id} ({source.name}) "
                    f"已 {time_since_last_frame:.1f} 秒未出帧，判定为异常"
                )
                self._log_health_event(
                    source=source,
                    event_type='no_frame_critical',
                    details={
                        'no_frame_duration': time_since_last_frame,
                        'last_write_time': health_status['last_write_time']
                    },
                    severity='critical'
                )
                self.last_health_log_times[source.id] = time.time()
            need_reboot = True

        # 检查2: 即将超时警告
        elif time_since_last_frame > NO_FRAME_WARNING_THRESHOLD:
            # 检查日志记录频率
            last_log_time = self.last_health_log_times.get(source.id, 0)
            if time.time() - last_log_time >= self.health_log_interval:
                logger.warning(
                    f"⚠️  视频源 {source.id} ({source.name}) "
                    f"已 {time_since_last_frame:.1f} 秒未出帧"
                )
                self._log_health_event(
                    source=source,
                    event_type='no_frame_warning',
                    details={
                        'no_frame_duration': time_since_last_frame
                    },
                    severity='warning'
                )
                self.last_health_log_times[source.id] = time.time()

        # 检查3: 连续错误计数
        if error_count > HIGH_ERROR_COUNT_THRESHOLD:
            logger.warning(
                f"⚠️  视频源 {source.id} ({source.name}) "
                f"连续错误次数: {error_count}"
            )
            self._log_health_event(
                source=source,
                event_type='high_error_rate',
                details={
                    'error_count': error_count
                },
                severity='warning'
            )

        return not need_reboot

    def _log_health_event(self, source, event_type, details, severity='info'):
        """
        记录健康事件

        Args:
            source: 视频源对象
            event_type: 事件类型
            details: 事件详情（字典）
            severity: 严重级别：info, warning, critical, error
        """
        import json
        from datetime import datetime

        logger.info(
            f"健康事件 [{event_type}] - 视频源 {source.id} ({source.name}): {details}"
        )

        # 记录到数据库
        try:
            SourceHealthLog.create(
                source=source,
                event_type=event_type,
                details=json.dumps(details),
                severity=severity,
                created_at=datetime.now()
            )
        except Exception as e:
            logger.error(f"记录健康事件到数据库失败: {e}")

    def _save_source(self, source: VideoSource, operation_name: str):
        source.save()

    def _refresh_rotation_config(self, now: float):
        if (
            now - self.last_rotation_config_refresh_at
            < SOURCE_ROTATION_CONFIG_REFRESH_SECONDS
        ):
            return

        previous = self.rotation_config
        self.rotation_config = normalize_source_rotation_config(
            get_source_rotation_config()
        )
        self.last_rotation_config_refresh_at = now
        if previous != self.rotation_config:
            logger.info(
                f"视频轮转配置已更新: {previous.to_dict()} -> "
                f"{self.rotation_config.to_dict()}"
            )

    def _rotation_candidate_ids(self):
        workflow_source_ids = set(self._build_active_workflow_groups().keys())
        if not workflow_source_ids:
            return []
        return [
            source.id
            for source in VideoSource.select().where(VideoSource.enabled == True)
            if source.id in workflow_source_ids
        ]

    def _select_rotation_batch(self, candidate_ids):
        selectable_ids = [
            source_id
            for source_id in candidate_ids
            if source_id not in self.draining_sources
        ]
        self.rotation_batch_ids = self.rotation_selector.select(
            selectable_ids,
            self.rotation_config.batch_size,
        )
        self.rotation_phase = 'STARTING' if self.rotation_batch_ids else 'IDLE'
        self.rotation_batch_launch_at = time.monotonic() if self.rotation_batch_ids else None
        self.rotation_dwell_started_at = None
        if self.rotation_batch_ids:
            logger.info(f"视频轮转启动新批次: {self.rotation_batch_ids}")

    def _source_host_ready(self, source_id: int) -> bool:
        process_info = self.workflow_hosts.get(source_id)
        if not process_info:
            return False
        process = process_info.get('process')
        ready_event = process_info.get('ready_event')
        return bool(
            process is not None
            and process.poll() is None
            and ready_event is not None
            and ready_event.is_set()
        )

    def _mark_ready_sources_running(self):
        for source_id in self.rotation_batch_ids:
            if not self._source_host_ready(source_id):
                continue
            # 轮转批次已确认首帧和工作流就绪，不再沿用解码进程启动时的宽限期。
            # 否则默认 30 秒的批次会始终落在 60 秒宽限期内，完全跳过健康检查。
            self.source_start_times.pop(source_id, None)
            try:
                source = VideoSource.get_by_id(source_id)
            except VideoSource.DoesNotExist:
                continue
            if source.status == 'STARTING':
                source.status = 'RUNNING'
                self._save_source(source, f'保存视频源检测就绪状态:{source_id}')

    def _mark_rotation_batch_starting(
        self,
        source_id: int,
        *,
        now: Optional[float] = None,
        restart_deadline: bool = False,
    ):
        """将当前批次恢复为启动态，且避免崩溃重试无限延长截止时间。"""
        if not self.rotation_config.enabled or source_id not in self.rotation_batch_ids:
            return

        was_running = self.rotation_phase == 'RUNNING'
        self.rotation_phase = 'STARTING'
        self.rotation_dwell_started_at = None
        try:
            source = VideoSource.get_by_id(source_id)
            if getattr(source, 'status', None) == 'RUNNING':
                source.status = 'STARTING'
                self._save_source(source, f'保存视频源重新就绪状态:{source_id}')
        except VideoSource.DoesNotExist:
            pass
        if (
            restart_deadline
            or was_running
            or self.rotation_batch_launch_at is None
        ):
            self.rotation_batch_launch_at = (
                time.monotonic() if now is None else now
            )

    def _begin_rotation_drain(self, source: VideoSource, reason: str):
        if source.id in self.draining_sources:
            return

        logger.info(f"视频源 {source.id} 进入排空状态: {reason}")
        process_info = self.workflow_hosts.pop(source.id, None)
        self.workflow_host_signatures.pop(source.id, None)
        if process_info is not None:
            process = process_info.get('process')
            if process is not None and process.poll() is None:
                process.terminate()

        source.status = 'DRAINING'
        self._save_source(source, f'保存视频源排空状态:{source.id}')
        self.draining_sources[source.id] = {
            'host_info': process_info,
            'deadline': time.monotonic()
            + float(POST_ALERT_DURATION)
            + float(SOURCE_ROTATION_DRAIN_GRACE_SECONDS),
            'reason': reason,
        }

        # 尚未启动工作流宿主时没有待完成的录像，可以直接释放。
        if process_info is None:
            self._finalize_source_stop(source)
            self.draining_sources.pop(source.id, None)

    def _poll_draining_sources(self):
        now = time.monotonic()
        for source_id, drain_info in list(self.draining_sources.items()):
            host_info = drain_info.get('host_info')
            host_process = host_info.get('process') if host_info else None
            finished = host_process is None or host_process.poll() is not None
            timed_out = now >= drain_info['deadline']
            if not finished and not timed_out:
                continue

            try:
                source = VideoSource.get_by_id(source_id)
            except VideoSource.DoesNotExist:
                if host_info:
                    self._stop_process(host_info, wait_timeout=0.1)
                del self.draining_sources[source_id]
                continue

            if timed_out and not finished:
                logger.warning(f"视频源 {source_id} 排空超时，强制停止")
                self._log_health_event(
                    source,
                    'rotation_drain_timeout',
                    {'reason': drain_info.get('reason')},
                    severity='warning',
                )
            if host_info:
                self._stop_process(host_info, wait_timeout=0.1)
            self._finalize_source_stop(source)
            del self.draining_sources[source_id]

    def _update_rotation_schedule(self, now: float):
        self._refresh_rotation_config(now)
        config = self.rotation_config

        if not config.enabled:
            if self._rotation_was_enabled:
                logger.info("视频轮转已关闭，恢复全部启用视频源常驻运行")
                self.rotation_selector.reset()
                self.rotation_batch_ids = []
                self.rotation_phase = 'IDLE'
                self.rotation_batch_launch_at = None
                self.rotation_dwell_started_at = None
            self._rotation_was_enabled = False
            return {
                source.id
                for source in VideoSource.select().where(VideoSource.enabled == True)
            }

        self._rotation_was_enabled = True
        candidate_ids = self._rotation_candidate_ids()
        candidate_set = set(candidate_ids)

        removed_ids = [
            source_id
            for source_id in self.rotation_batch_ids
            if source_id not in candidate_set
        ]
        for source_id in removed_ids:
            try:
                self._begin_rotation_drain(
                    VideoSource.get_by_id(source_id),
                    '视频源已禁用或已无活动工作流',
                )
            except VideoSource.DoesNotExist:
                pass
        if removed_ids:
            self.rotation_batch_ids = [
                source_id
                for source_id in self.rotation_batch_ids
                if source_id in candidate_set
            ]

        if not self.rotation_batch_ids:
            self._select_rotation_batch(candidate_ids)
            return set(self.rotation_batch_ids)

        target_batch_size = min(config.batch_size, len(candidate_ids))
        if (
            len(candidate_ids) <= config.batch_size
            and len(self.rotation_batch_ids) < target_batch_size
        ):
            remaining_ids = [
                source_id
                for source_id in candidate_ids
                if source_id not in self.rotation_batch_ids
                and source_id not in self.draining_sources
            ]
            additions = self.rotation_selector.select(
                remaining_ids,
                target_batch_size - len(self.rotation_batch_ids),
            )
            if additions:
                self.rotation_batch_ids.extend(additions)
                self.rotation_phase = 'STARTING'
                self.rotation_batch_launch_at = now
                self.rotation_dwell_started_at = None
                logger.info(f"视频轮转批次补位: {additions}")

        self._mark_ready_sources_running()
        all_ready = all(
            self._source_host_ready(source_id)
            for source_id in self.rotation_batch_ids
        )
        if self.rotation_phase == 'RUNNING' and not all_ready:
            missing_source_id = next(
                source_id
                for source_id in self.rotation_batch_ids
                if not self._source_host_ready(source_id)
            )
            self._mark_rotation_batch_starting(missing_source_id, now=now)

        if self.rotation_phase == 'STARTING' and all_ready:
            self.rotation_phase = 'RUNNING'
            self.rotation_dwell_started_at = now
            logger.info(
                f"视频轮转批次已就绪，开始计时: {self.rotation_batch_ids}, "
                f"持续 {config.dwell_seconds}s"
            )

        if (
            self.rotation_phase == 'STARTING'
            and self.rotation_batch_launch_at is not None
            and now - self.rotation_batch_launch_at
            >= SOURCE_ROTATION_STARTUP_TIMEOUT_SECONDS
        ):
            failed_ids = [
                source_id
                for source_id in self.rotation_batch_ids
                if not self._source_host_ready(source_id)
            ]
            for source_id in failed_ids:
                try:
                    self._begin_rotation_drain(
                        VideoSource.get_by_id(source_id),
                        '轮转批次启动超时',
                    )
                except VideoSource.DoesNotExist:
                    pass
            self.rotation_batch_ids = [
                source_id
                for source_id in self.rotation_batch_ids
                if source_id not in failed_ids
            ]
            replacement_ids = self.rotation_selector.select(
                [
                    source_id
                    for source_id in candidate_ids
                    if source_id not in self.rotation_batch_ids
                    and source_id not in self.draining_sources
                ],
                config.batch_size - len(self.rotation_batch_ids),
            ) if len(self.rotation_batch_ids) < config.batch_size else []
            if replacement_ids:
                self.rotation_batch_ids.extend(replacement_ids)
                self.rotation_phase = 'STARTING'
                self.rotation_batch_launch_at = now
                self.rotation_dwell_started_at = None
                logger.info(f"轮转启动超时后补位: {replacement_ids}")
            elif self.rotation_batch_ids and all(
                self._source_host_ready(source_id)
                for source_id in self.rotation_batch_ids
            ):
                self.rotation_phase = 'RUNNING'
                self.rotation_dwell_started_at = now
            else:
                self.rotation_batch_ids = []
                self._select_rotation_batch(candidate_ids)

        should_rotate = (
            self.rotation_phase == 'RUNNING'
            and self.rotation_dwell_started_at is not None
            and len(candidate_ids) > config.batch_size
            and now - self.rotation_dwell_started_at >= config.dwell_seconds
        )
        if should_rotate:
            old_batch = list(self.rotation_batch_ids)
            for source_id in old_batch:
                try:
                    self._begin_rotation_drain(
                        VideoSource.get_by_id(source_id),
                        '轮转检测时段结束',
                    )
                except VideoSource.DoesNotExist:
                    pass
            self.rotation_batch_ids = []
            self._select_rotation_batch(candidate_ids)

        return set(self.rotation_batch_ids)

    def _start_source(self, source: VideoSource, starting: bool = False):
        print(f"  -> 正在启动视频源 ID {source.id}: {source.name}")

        retry_at = self.source_probe_retry_at.get(source.id, 0.0)
        if time.monotonic() < retry_at:
            return False

        try:
            input_format = self._resolve_source_codec(source)
            if (
                VIDEO_DECODER_TYPE in {'jetson_gst', 'jetson', 'nvv4l2'}
                and input_format not in {'h264', 'h265'}
            ):
                raise VideoCodecProbeError(
                    f"Jetson hardware decoding does not support {input_format}"
                )
        except (VideoCodecProbeError, ValueError) as exc:
            self.source_probe_retry_at[source.id] = time.monotonic() + 30.0
            logger.error(
                f"视频源 {source.id} 编码探测失败，30 秒后重试: {exc}"
            )
            return False

        self.source_probe_retry_at.pop(source.id, None)
        analysis_fps = max(1, min(int(source.source_fps), int(ANALYSIS_TARGET_FPS)))
        analysis_buffer = VideoRingBuffer(
            name=source.analysis_buffer_name,
            create=True,
            width=source.source_decode_width,
            height=source.source_decode_height,
            pixel_format=VIDEO_FRAME_PIXEL_FORMAT,
            fps=analysis_fps,
            duration_seconds=ANALYSIS_BUFFER_SECONDS
        )
        self.buffers[source.id] = analysis_buffer

        logger.debug(
            f"创建分析RingBuffer: fps={analysis_fps}, duration={ANALYSIS_BUFFER_SECONDS}s, "
            f"capacity={analysis_buffer.capacity}帧, frame_shape={analysis_buffer.frame_shape}, "
            f"pixel_format={analysis_buffer.pixel_format}"
        )

        if RECORDING_ENABLED:
            recording_buffer = CompressedVideoRingBuffer(
                name=source.recording_buffer_name,
                create=True,
                width=source.source_decode_width,
                height=source.source_decode_height,
                pixel_format=VIDEO_FRAME_PIXEL_FORMAT,
                fps=RECORDING_FPS,
                duration_seconds=RECORDING_BUFFER_DURATION,
                max_frame_bytes=RECORDING_COMPRESSED_MAX_BYTES,
                jpeg_quality=RECORDING_JPEG_QUALITY,
            )
            self.recording_buffers[source.id] = recording_buffer
            logger.debug(
                f"创建录制CompressedRingBuffer: fps={RECORDING_FPS}, duration={RECORDING_BUFFER_DURATION}s, "
                f"capacity={recording_buffer.capacity}帧, frame_shape={recording_buffer.frame_shape}, "
                f"pixel_format={recording_buffer.pixel_format}"
            )

        # 启动解码器进程
        decoder_args = self._build_decoder_args(
            source,
            analysis_fps=analysis_fps,
            input_format=input_format,
        )
        logger.debug(' '.join(decoder_args))
        decoder_p = subprocess.Popen(decoder_args, cwd=APP_DIR)

        source.status = 'STARTING' if starting else 'RUNNING'
        source.decoder_pid = decoder_p.pid
        self._save_source(source, f'保存视频源启动状态:{source.id}')

        self.running_processes[source.id] = {
            'process': decoder_p,
            'decoder': decoder_p,
        }

        # 记录启动时间（用于健康检查宽限期）
        self.source_start_times[source.id] = time.time()
        logger.debug(f"视频源 {source.id} 已记录启动时间，宽限期 {self.start_grace_period} 秒")
        return True

    def _resolve_source_codec(self, source: VideoSource) -> str:
        cached_codec = normalize_video_codec(
            getattr(source, 'source_codec', 'unknown'),
            allow_unknown=True,
        )
        try:
            detected_codec = probe_video_codec(source.source_url)
        except VideoCodecProbeError:
            if cached_codec != 'unknown':
                logger.warning(
                    f"视频源 {source.id} 编码重新探测失败，使用上次结果: {cached_codec}"
                )
                return cached_codec
            raise

        if detected_codec != cached_codec:
            source.source_codec = detected_codec
            self._save_source(source, f'保存视频源编码探测结果:{source.id}')
        logger.info(f"视频源 {source.id} 编码格式: {detected_codec}")
        return detected_codec

    @staticmethod
    def _build_decoder_args(
        source: VideoSource,
        *,
        analysis_fps: int,
        input_format: str,
    ):
        decoder_entry = os.path.join(APP_DIR, 'decoder_worker.py')
        return [
            sys.executable, decoder_entry,
            '--url', source.source_url,
            '--source-id', str(source.id),
            '--decoder-type', VIDEO_DECODER_TYPE,
            '--input-format', input_format,
            '--decoder-threads', str(FFMPEG_SW_DECODER_THREADS),
            '--decoder-output-queue-size', str(DECODER_OUTPUT_QUEUE_SIZE),
            '--sample-mode', 'fps',
            '--analysis-fps', str(analysis_fps),
            '--recording-fps', str(RECORDING_FPS),
            '--width', str(source.source_decode_width),
            '--height', str(source.source_decode_height),
            '--output-format', VIDEO_FRAME_PIXEL_FORMAT,
        ]

    def _finalize_source_stop(self, source: VideoSource):
        if source.id in self.running_processes:
            self._stop_process(self.running_processes[source.id], wait_timeout=5.0)
            del self.running_processes[source.id]

        if source.id in self.buffers:
            self.buffers[source.id].close()
            self.buffers[source.id].unlink()
            del self.buffers[source.id]

        if source.id in self.recording_buffers:
            self.recording_buffers[source.id].close()
            self.recording_buffers[source.id].unlink()
            del self.recording_buffers[source.id]

        self.source_start_times.pop(source.id, None)
        self.source_probe_retry_at.pop(source.id, None)
        self.last_health_log_times.pop(source.id, None)
        source.status = 'STOPPED'
        source.decoder_pid = None
        self._save_source(source, f'保存视频源停止状态:{source.id}')

    def _stop_source(self, source: VideoSource):
        print(f"  -> 正在停止视频源 ID {source.id}: {source.name}")

        self._stop_source_host(source.id)

        self._finalize_source_stop(source)

    def manage_sources(self):
        self._poll_draining_sources()
        now = time.monotonic()
        self.desired_source_ids = self._update_rotation_schedule(now)

        for source in VideoSource.select().where(VideoSource.status == 'STOPPED'):
            if source.id not in self.desired_source_ids:
                continue
            if source.id in self.draining_sources:
                continue
            self._start_source(source, starting=self.rotation_config.enabled)

        for source in VideoSource.select().where(
            VideoSource.status.in_(['STARTING', 'RUNNING'])
        ):
            if source.id in self.desired_source_ids:
                continue
            if self.rotation_config.enabled:
                self._begin_rotation_drain(source, '不在当前轮转批次')
            else:
                logger.info(f"视频源 ID {source.id} 被禁用，正在停止...")
                self._stop_source(source)

        # 查找 ERROR 状态的视频源，尝试重启
        error_sources = VideoSource.select().where(VideoSource.status == 'ERROR')
        for source in error_sources:
            if source.id not in self.desired_source_ids:
                self._stop_source(source)
                continue
            logger.info(f"尝试重启异常视频源 {source.id}")
            self._stop_source(source)
            source.status = 'STOPPED'
            source.decoder_pid = None
            self._save_source(source, f'保存视频源异常恢复状态:{source.id}')

        # 健康检查
        running_sources = VideoSource.select().where(
            VideoSource.status.in_(['STARTING', 'RUNNING'])
        )
        for source in running_sources:
            if source.id in self.running_processes:
                need_reboot = False

                # 检查1: 进程是否退出
                exit_code = self.running_processes[source.id]['process'].poll()
                if exit_code is not None:
                    logger.warning(
                        f"🚨 视频源 ID {source.id} 的解码器进程已退出 "
                        f"(退出码:{exit_code})，准备自动重启"
                    )
                    self._log_health_event(
                        source=source,
                        event_type='process_exit',
                        details={'exit_code': exit_code},
                        severity='error'
                    )
                    need_reboot = True

                # 检查2: 健康状态检查（仅在启用且进程正常运行时）
                elif self.health_check_enabled and source.status == 'RUNNING':
                    is_healthy = self._check_source_health(source)

                    # 重新获取 source，因为 _check_source_health 可能修改了状态
                    source = VideoSource.get_by_id(source.id)

                    if not is_healthy or source.status == 'ERROR':
                        need_reboot = True

                if need_reboot:
                    # 清理旧进程和资源
                    self._stop_source(source)
                    # 重置状态为STOPPED，让manage_sources在下一轮自动重启
                    source.status = 'STOPPED'
                    source.decoder_pid = None
                    self._save_source(source, f'保存视频源重启状态:{source.id}')
                    logger.debug(f"✅ 视频源 ID {source.id} 已标记为STOPPED，将在下一轮管理循环中自动重启")
    
    def _start_source_host(self, source_id: int, workflows):
        try:
            source = VideoSource.get_by_id(source_id)
        except VideoSource.DoesNotExist:
            logger.error(f"视频源 {source_id} 不存在，无法启动 source host")
            return

        if source.status not in ('STARTING', 'RUNNING'):
            logger.warning(f"视频源 {source.name} (状态: {source.status}) 未运行，跳过启动 source host")
            return

        logger.info(
            f"  -> 正在启动视频源宿主进程 Source={source_id}, "
            f"workflows={[workflow.id for workflow in workflows]}"
        )

        workflow_entry = os.path.join(APP_DIR, 'source_workflow_host.py')
        workflow_args = [
            sys.executable, '-u', workflow_entry,
            '--source-id', str(source_id)
        ]
        logger.debug(f"启动命令: {' '.join(workflow_args)}")

        try:
            workflow_p = subprocess.Popen(
                workflow_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1,
                cwd=APP_DIR,
            )

            ready_event = threading.Event()

            def handle_stdout_line(line: str):
                if line.strip() == f"SOURCE_HOST_READY:{source_id}":
                    ready_event.set()
                    logger.info(f"Source host {source_id} 已完成检测就绪")

            # 启动输出读取线程
            log_label = f"SourceHost-{source_id}"
            stdout_reader = OutputReader(
                workflow_p,
                log_label,
                'stdout',
                on_line=handle_stdout_line,
            )
            stderr_reader = OutputReader(workflow_p, log_label, 'stderr')

            self.workflow_hosts[source_id] = {
                'process': workflow_p,
                'source_id': source_id,
                'workflow_ids': [workflow.id for workflow in workflows],
                'stdout_reader': stdout_reader,
                'stderr_reader': stderr_reader,
                'ready_event': ready_event,
            }
            self.workflow_host_signatures[source_id] = build_workflow_signature(workflows)
            stdout_reader.start()
            stderr_reader.start()

            logger.debug(
                f"Source host {source_id} 已启动，PID: {workflow_p.pid}, "
                f"signature={self.workflow_host_signatures[source_id]}"
            )

        except Exception as e:
            logger.error(f"启动 Source host {source_id} 时发生异常: {e}", exc_info=True)

    def _stop_source_host(self, source_id: int):
        if source_id not in self.workflow_hosts:
            return

        logger.info(f"  -> 正在停止视频源宿主进程 Source={source_id}")
        process_info = self.workflow_hosts[source_id]
        host_wait_timeout = max(35.0, float(POST_ALERT_DURATION) + 10.0)
        self._stop_process(process_info, wait_timeout=host_wait_timeout)
        del self.workflow_hosts[source_id]
        self.workflow_host_signatures.pop(source_id, None)
    
    def manage_workflows(self):
        active_groups = self._build_active_workflow_groups()
        active_groups = {
            source_id: workflows
            for source_id, workflows in active_groups.items()
            if source_id in self.desired_source_ids
        }
        logger.debug(
            f"检测到 {sum(len(workflows) for workflows in active_groups.values())} 个激活工作流，"
            f"分布在 {len(active_groups)} 个视频源"
        )

        for source_id, workflows in active_groups.items():
            signature = build_workflow_signature(workflows)
            running_info = self.workflow_hosts.get(source_id)

            if running_info is None:
                self._mark_rotation_batch_starting(source_id)
                self._start_source_host(source_id, workflows)
                continue

            running_signature = self.workflow_host_signatures.get(source_id)
            if running_signature != signature:
                logger.info(
                    f"🔄 Source {source_id} 的工作流集合已变更 "
                    f"({running_signature} -> {signature})，重启 source host"
                )
                self._stop_source_host(source_id)
                self._start_source_host(source_id, workflows)
                self._mark_rotation_batch_starting(
                    source_id,
                    restart_deadline=True,
                )

        for source_id in list(self.workflow_hosts.keys()):
            if source_id not in active_groups:
                logger.info(f"Source {source_id} 已无激活工作流，停止 source host")
                self._stop_source_host(source_id)

        for source_id in list(self.workflow_hosts.keys()):
            process_info = self.workflow_hosts[source_id]
            exit_code = process_info['process'].poll()
            if exit_code is not None:
                logger.warning(
                    f"🚨 Source host {source_id} 的进程已退出 (退出码:{exit_code})，准备自动重启"
                )
                try:
                    stdout, stderr = process_info['process'].communicate(timeout=1)
                    if stderr:
                        logger.error(f"Source host {source_id} 错误输出: {stderr}")
                except Exception:
                    pass

                del self.workflow_hosts[source_id]
                self.workflow_host_signatures.pop(source_id, None)
                self._mark_rotation_batch_starting(source_id)
                logger.info(f"✅ Source host {source_id} 已清理进程记录，将在下一轮管理循环中自动重启")

    def run(self):
        print("🚀 编排器启动，开始动态管理视频源和工作流...")
        self.media_cleaner.start()
        while True:
            self.manage_sources()
            self.manage_workflows()
            time.sleep(1)

    def stop(self):
        print("\n优雅地关闭所有正在运行的工作流和视频源...")
        self.media_cleaner.stop()

        for source_id in list(self.workflow_hosts.keys()):
            self._stop_source_host(source_id)

        for source_id, drain_info in list(self.draining_sources.items()):
            host_info = drain_info.get('host_info')
            if host_info:
                self._stop_process(host_info, wait_timeout=1.0)
            self.draining_sources.pop(source_id, None)

        for source in VideoSource.select().where(
            VideoSource.status.in_(['STARTING', 'RUNNING', 'DRAINING', 'ERROR'])
        ):
            self._stop_source(source)
        
        db.close()
        print("所有工作流和视频源已停止。")


if __name__ == "__main__":
    orch = Orchestrator()
    signal.signal(signal.SIGINT, lambda s, f: orch.stop() or exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: orch.stop() or exit(0))
    orch.run()
