import signal
import subprocess
import threading
import time
from queue import Queue

from playhouse.shortcuts import model_to_dict

from app import logger
from app.config import (
    RINGBUFFER_DURATION,
    RECORDING_FPS,
    NO_FRAME_WARNING_THRESHOLD,
    NO_FRAME_CRITICAL_THRESHOLD,
    HIGH_ERROR_COUNT_THRESHOLD,
    HEALTH_MONITOR_ENABLED
)
from app.core.database_models import db, VideoSource, Workflow, SourceHealthLog
from app.core.ringbuffer import VideoRingBuffer


class OutputReader(threading.Thread):
    """持续读取子进程输出的线程"""
    def __init__(self, process, workflow_id, stream_type='stdout'):
        super().__init__(daemon=True)
        self.process = process
        self.workflow_id = workflow_id
        self.stream_type = stream_type
        self.stream = getattr(process, stream_type)
        self.running = True

    def run(self):
        """持续读取并输出日志"""
        try:
            for line in iter(self.stream.readline, ''):
                if not self.running:
                    break
                if line:
                    log_msg = line.rstrip('\n\r')
                    if self.stream_type == 'stderr':
                        logger.error(f"[Workflow-{self.workflow_id}] {log_msg}")
                    else:
                        logger.info(f"[Workflow-{self.workflow_id}] {log_msg}")
        except Exception as e:
            if self.running:
                logger.warning(f"[Workflow-{self.workflow_id}] 读取{self.stream_type}时出错: {e}")

    def stop(self):
        """停止读取线程"""
        self.running = False


class Orchestrator:
    def __init__(self):
        self.running_processes = {}
        self.workflow_processes = {}
        self.buffers = {}
        self.source_start_times = {}  # 记录视频源启动时间
        self.last_health_log_times = {}  # 记录上次健康日志时间
        db.connect()

        VideoSource.update(status='STOPPED', decoder_pid=None).execute()

        # 健康监控配置
        self.health_check_enabled = HEALTH_MONITOR_ENABLED
        self.start_grace_period = 60  # 启动后60秒内不进行健康检查
        self.health_log_interval = 30  # 健康日志记录间隔（秒），避免日志泛滥

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

    def _start_source(self, source: VideoSource):
        print(f"  -> 正在启动视频源 ID {source.id}: {source.name}")

        # 创建共享内存环形缓冲区
        buffer = VideoRingBuffer(
            name=source.buffer_name,
            create=True,
            frame_shape=(source.source_decode_height, source.source_decode_width, 3),
            fps=source.source_fps,
            duration_seconds=RINGBUFFER_DURATION
        )
        self.buffers[source.id] = buffer

        logger.info(f"创建RingBuffer: fps={source.source_fps}, duration={RINGBUFFER_DURATION}s, capacity={buffer.capacity}帧, frame_shape={buffer.frame_shape}")

        # 启动解码器进程
        decoder_args = [
            'python', 'decoder_worker.py',
            '--url', source.source_url,
            '--source-id', str(source.id),
            '--sample-mode', 'fps',
            '--sample-fps', str(source.source_fps),
            '--width', str(source.source_decode_width),
            '--height', str(source.source_decode_height)
        ]
        logger.info(' '.join(decoder_args))
        decoder_p = subprocess.Popen(decoder_args)

        source.status = 'RUNNING'
        source.decoder_pid = decoder_p.pid
        source.save()

        self.running_processes[source.id] = {'decoder': decoder_p}

        # 记录启动时间（用于健康检查宽限期）
        self.source_start_times[source.id] = time.time()
        logger.info(f"视频源 {source.id} 已记录启动时间，宽限期 {self.start_grace_period} 秒")

    def _stop_source(self, source: VideoSource):
        print(f"  -> 正在停止视频源 ID {source.id}: {source.name}")

        if source.id in self.running_processes:
            self.running_processes[source.id]['decoder'].terminate()
            del self.running_processes[source.id]

        if source.id in self.buffers:
            self.buffers[source.id].close()
            self.buffers[source.id].unlink()
            del self.buffers[source.id]

        # 清除启动时间记录
        if source.id in self.source_start_times:
            del self.source_start_times[source.id]

        # 清除健康日志时间记录
        if source.id in self.last_health_log_times:
            del self.last_health_log_times[source.id]

        source.status = 'STOPPED'
        source.decoder_pid = None
        source.save()

    def manage_sources(self):
        # 查找需要启动的视频源
        sources_to_start = VideoSource.select().where(
            (VideoSource.enabled == True) & (VideoSource.status == 'STOPPED')
        )
        for source in sources_to_start:
            self._start_source(source)

        # 查找需要停止的视频源
        sources_to_stop = VideoSource.select().where(
            (VideoSource.enabled == False) & (VideoSource.status == 'RUNNING')
        )
        for source in sources_to_stop:
            logger.info(f"视频源 ID {source.id} 被禁用，正在停止...")
            self._stop_source(source)

        # 查找 ERROR 状态的视频源，尝试重启
        error_sources = VideoSource.select().where(VideoSource.status == 'ERROR')
        for source in error_sources:
            logger.info(f"尝试重启异常视频源 {source.id}")
            self._stop_source(source)
            source.status = 'STOPPED'
            source.decoder_pid = None
            source.save()

        # 健康检查
        running_sources = VideoSource.select().where(VideoSource.status == 'RUNNING')
        for source in running_sources:
            if source.id in self.running_processes:
                need_reboot = False

                # 检查1: 进程是否退出
                exit_code = self.running_processes[source.id]['decoder'].poll()
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
                elif self.health_check_enabled:
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
                    source.save()
                    logger.info(f"✅ 视频源 ID {source.id} 已标记为STOPPED，将在下一轮管理循环中自动重启")
    
    def _start_workflow(self, workflow: Workflow):
        logger.info(f"  -> 正在启动工作流 ID {workflow.id}: {workflow.name}")
        
        workflow_data = workflow.data_dict
        logger.debug(f"工作流数据: {workflow_data}")
        nodes = workflow_data.get('nodes', [])
        
        source_node = None
        for node in nodes:
            if node.get('type') == 'source':
                source_node = node
                break
        
        if not source_node:
            logger.error(f"工作流 {workflow.id} 没有视频源节点，跳过启动")
            return
        
        source_id = source_node.get('dataId')
        if not source_id:
            logger.error(f"工作流 {workflow.id} 的视频源节点未配置dataId，跳过启动")
            return
        
        try:
            source = VideoSource.get_by_id(source_id)
            if source.status != 'RUNNING':
                logger.warning(f"工作流 {workflow.id} 的视频源 {source.name} (状态: {source.status}) 未运行，跳过启动")
                return
        except VideoSource.DoesNotExist:
            logger.error(f"工作流 {workflow.id} 的视频源 ID {source_id} 不存在")
            return
        
        import sys
        workflow_args = [
            sys.executable, '-u', 'workflow_worker.py',
            '--workflow-id', str(workflow.id)
        ]
        logger.info(f"启动命令: {' '.join(workflow_args)}")
        
        try:
            workflow_p = subprocess.Popen(
                workflow_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1
            )

            # 启动输出读取线程
            stdout_reader = OutputReader(workflow_p, workflow.id, 'stdout')
            stderr_reader = OutputReader(workflow_p, workflow.id, 'stderr')
            stdout_reader.start()
            stderr_reader.start()

            self.workflow_processes[workflow.id] = {
                'process': workflow_p,
                'source_id': source_id,
                'stdout_reader': stdout_reader,
                'stderr_reader': stderr_reader
            }
            logger.info(f"工作流 {workflow.id} 已启动，PID: {workflow_p.pid}")

            time.sleep(0.5)
            exit_code = workflow_p.poll()
            if exit_code is not None:
                stdout_reader.stop()
                stderr_reader.stop()
                stdout, stderr = workflow_p.communicate()
                logger.error(f"工作流 {workflow.id} 启动失败，退出码: {exit_code}")
                logger.error(f"STDOUT: {stdout}")
                logger.error(f"STDERR: {stderr}")
                if workflow.id in self.workflow_processes:
                    del self.workflow_processes[workflow.id]
        except Exception as e:
            logger.error(f"启动工作流 {workflow.id} 时发生异常: {e}", exc_info=True)
    
    def _stop_workflow(self, workflow: Workflow):
        logger.info(f"  -> 正在停止工作流 ID {workflow.id}: {workflow.name}")

        if workflow.id in self.workflow_processes:
            # 停止输出读取线程
            if 'stdout_reader' in self.workflow_processes[workflow.id]:
                self.workflow_processes[workflow.id]['stdout_reader'].stop()
            if 'stderr_reader' in self.workflow_processes[workflow.id]:
                self.workflow_processes[workflow.id]['stderr_reader'].stop()

            # 终止进程
            self.workflow_processes[workflow.id]['process'].terminate()
            del self.workflow_processes[workflow.id]
    
    def manage_workflows(self):
        active_workflows = Workflow.select().where(Workflow.is_active == True)
        logger.debug(f"检测到 {active_workflows.count()} 个激活的工作流")
        
        for workflow in active_workflows:
            if workflow.id not in self.workflow_processes:
                logger.info(f"发现新的激活工作流: {workflow.name} (ID: {workflow.id})")
                self._start_workflow(workflow)
        
        inactive_workflows = Workflow.select().where(Workflow.is_active == False)
        for workflow in inactive_workflows:
            if workflow.id in self.workflow_processes:
                logger.info(f"工作流 ID {workflow.id} 已停用，正在停止...")
                self._stop_workflow(workflow)
        
        for workflow_id in list(self.workflow_processes.keys()):
            process_info = self.workflow_processes[workflow_id]
            exit_code = process_info['process'].poll()
            if exit_code is not None:
                logger.warning(f"🚨 工作流 ID {workflow_id} 的进程已退出 (退出码:{exit_code})，准备自动重启")

                # 停止输出读取线程
                if 'stdout_reader' in process_info:
                    process_info['stdout_reader'].stop()
                if 'stderr_reader' in process_info:
                    process_info['stderr_reader'].stop()

                try:
                    stdout, stderr = process_info['process'].communicate(timeout=1)
                    if stderr:
                        logger.error(f"工作流 {workflow_id} 错误输出: {stderr}")
                except:
                    pass

                # 清理进程记录，让manage_workflows在下一轮自动重启
                del self.workflow_processes[workflow_id]
                logger.info(f"✅ 工作流 ID {workflow_id} 已清理进程记录，将在下一轮管理循环中自动重启")

    def run(self):
        print("🚀 编排器启动，开始动态管理视频源和工作流...")
        while True:
            self.manage_sources()
            self.manage_workflows()
            time.sleep(5)

    def stop(self):
        print("\n优雅地关闭所有正在运行的工作流和视频源...")
        
        for workflow_id in list(self.workflow_processes.keys()):
            try:
                workflow = Workflow.get_by_id(workflow_id)
                self._stop_workflow(workflow)
            except:
                pass
        
        for source in VideoSource.select().where(VideoSource.status == 'RUNNING'):
            self._stop_source(source)
        
        db.close()
        print("所有工作流和视频源已停止。")


if __name__ == "__main__":
    orch = Orchestrator()
    signal.signal(signal.SIGINT, lambda s, f: orch.stop() or exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: orch.stop() or exit(0))
    orch.run()
