"""
按视频源聚合的工作流宿主进程。

一个 source 只启动一个 host，host 读取一次最新帧并在进程内依次驱动该 source
下所有激活工作流，避免为每个工作流重复起进程和重复读取 ring buffer。
"""
import argparse
import os
import signal
import sys
import threading
import time
from multiprocessing import resource_tracker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import logger
from app.config import ANALYSIS_BUFFER_SECONDS, ANALYSIS_TARGET_FPS, VIDEO_FRAME_PIXEL_FORMAT
from app.core.database_models import VideoSource, Workflow
from app.core.ringbuffer import VideoRingBuffer
from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_runtime import extract_source_id_from_workflow_data


WORKFLOW_RETRY_INTERVAL_SECONDS = 30
WORKFLOW_MAX_CONSECUTIVE_ERRORS = 10
BUFFER_CONNECT_MAX_RETRIES = 10
BUFFER_CONNECT_RETRY_INTERVAL_SECONDS = 1.0
RUNNER_CLEANUP_WAIT_TIMEOUT_SECONDS = 5.0


class WorkflowRunner:
    def __init__(
        self,
        workflow,
        executor: WorkflowExecutor,
        max_consecutive_errors: int = WORKFLOW_MAX_CONSECUTIVE_ERRORS,
    ):
        self.workflow = workflow
        self.workflow_id = workflow.id
        self.executor = executor
        self.max_consecutive_errors = max_consecutive_errors
        self._condition = threading.Condition()
        self._pending_frame = None
        self._pending_timestamp = None
        self._failure = None
        self._running = True
        self._consecutive_errors = 0
        self._thread = threading.Thread(
            target=self._run,
            name=f"workflow-runner-{self.workflow_id}",
            daemon=True,
        )

    def start(self):
        self._thread.start()

    def submit_frame(self, frame_nv12, frame_timestamp: float):
        with self._condition:
            if not self._running:
                return
            self._pending_frame = frame_nv12
            self._pending_timestamp = frame_timestamp
            self._condition.notify()

    def pop_failure(self):
        with self._condition:
            failure = self._failure
            self._failure = None
            return failure

    def stop(self):
        with self._condition:
            self._running = False
            self._pending_frame = None
            self._pending_timestamp = None
            self._condition.notify_all()
        self.executor.stop()

    def join(self, timeout=None):
        self._thread.join(timeout=timeout)

    def is_alive(self):
        return self._thread.is_alive()

    def cleanup(self, stop_first: bool = True, wait_timeout: float = RUNNER_CLEANUP_WAIT_TIMEOUT_SECONDS):
        if stop_first:
            self.stop()

        self.join(timeout=wait_timeout)
        if self.is_alive():
            logger.warning(
                f"[SourceHost:{self.workflow_id}] runner 在线程未退出时跳过 cleanup，"
                f"避免与正在执行的工作流竞争资源"
            )
            return False

        self.executor.cleanup()
        return True

    def _run(self):
        while True:
            with self._condition:
                while self._running and self._pending_frame is None:
                    self._condition.wait(timeout=1.0)

                if not self._running:
                    return

                frame_nv12 = self._pending_frame
                frame_timestamp = self._pending_timestamp
                self._pending_frame = None
                self._pending_timestamp = None

            try:
                self.executor.run_once(frame_nv12, frame_timestamp)
                self._consecutive_errors = 0
            except Exception as exc:
                self._consecutive_errors += 1
                logger.warning(
                    f"[SourceHost:{self.workflow_id}] 单帧执行失败 "
                    f"({self._consecutive_errors}/{self.max_consecutive_errors}): {exc}",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                if self._consecutive_errors < self.max_consecutive_errors:
                    continue

                with self._condition:
                    self._failure = exc
                    self._running = False
                self.executor.stop()
                return


class SourceWorkflowHost:
    def __init__(self, source_id: int):
        self.source_id = int(source_id)
        self.running = True
        self.source = VideoSource.get_by_id(self.source_id)
        self.buffer = None
        self.runners = {}
        self.workflows = {}
        self.failed_workflows = {}
        self.last_frame_timestamp = None

    def _load_workflows(self):
        workflows = []
        for workflow in Workflow.select().where(Workflow.is_active == True):
            workflow_data = workflow.data_dict
            if extract_source_id_from_workflow_data(workflow_data) != self.source_id:
                continue
            workflows.append(workflow)

        return workflows

    def _schedule_workflow_retry(self, workflow, error, delay_seconds=WORKFLOW_RETRY_INTERVAL_SECONDS):
        workflow_id = workflow.id
        self.failed_workflows[workflow_id] = {
            'workflow': workflow,
            'next_retry_at': time.time() + delay_seconds,
            'last_error': str(error),
        }
        logger.warning(
            f"[SourceHost:{self.source_id}] 工作流 {workflow_id} 已隔离，"
            f"{delay_seconds}s 后重试，原因: {error}"
        )

    def _activate_workflow(self, workflow):
        workflow_id = workflow.id
        try:
            executor = WorkflowExecutor(workflow_id)
        except Exception as exc:
            self._schedule_workflow_retry(workflow, exc)
            return False

        runner = WorkflowRunner(workflow, executor)
        runner.start()
        self.runners[workflow_id] = runner
        self.failed_workflows.pop(workflow_id, None)
        logger.info(f"[SourceHost:{self.source_id}] 工作流 {workflow_id} 已加载")
        return True

    def _retry_failed_workflows(self):
        if not self.failed_workflows:
            return

        now = time.time()
        for workflow_id, failed_info in list(self.failed_workflows.items()):
            if failed_info['next_retry_at'] > now:
                continue

            workflow = failed_info['workflow']
            logger.info(
                f"[SourceHost:{self.source_id}] 重试加载工作流 {workflow_id}，"
                f"上次错误: {failed_info['last_error']}"
            )
            self._activate_workflow(workflow)

    def _setup_buffer(self):
        analysis_fps = max(1, min(int(self.source.source_fps), int(ANALYSIS_TARGET_FPS)))
        last_error = None
        for attempt in range(1, BUFFER_CONNECT_MAX_RETRIES + 1):
            try:
                self.buffer = VideoRingBuffer(
                    name=self.source.analysis_buffer_name,
                    create=False,
                    width=self.source.source_decode_width,
                    height=self.source.source_decode_height,
                    pixel_format=VIDEO_FRAME_PIXEL_FORMAT,
                    fps=analysis_fps,
                    duration_seconds=ANALYSIS_BUFFER_SECONDS,
                )
                break
            except FileNotFoundError as exc:
                last_error = exc
                if attempt < BUFFER_CONNECT_MAX_RETRIES:
                    logger.warning(
                        f"[SourceHost:{self.source_id}] 尝试 {attempt}/{BUFFER_CONNECT_MAX_RETRIES}: "
                        f"分析缓冲区 /{self.source.analysis_buffer_name} 尚未就绪，"
                        f"等待 {BUFFER_CONNECT_RETRY_INTERVAL_SECONDS} 秒后重试..."
                    )
                    time.sleep(BUFFER_CONNECT_RETRY_INTERVAL_SECONDS)
                else:
                    logger.error(
                        f"[SourceHost:{self.source_id}] 无法连接分析缓冲区 "
                        f"/{self.source.analysis_buffer_name}"
                    )
                    raise

        if self.buffer is None and last_error is not None:
            raise last_error

        shm_name = self.source.analysis_buffer_name if os.name == 'nt' else f"/{self.source.analysis_buffer_name}"
        resource_tracker.unregister(shm_name, 'shared_memory')

    def _setup_executors(self):
        workflows = self._load_workflows()
        if not workflows:
            logger.warning(
                f"[SourceHost:{self.source_id}] 当前没有激活工作流，宿主进程将退出"
            )
            self.running = False
            return

        self.workflows = {workflow.id: workflow for workflow in workflows}
        for workflow in workflows:
            self._activate_workflow(workflow)

        logger.info(
            f"[SourceHost:{self.source_id}] 已加载 {len(self.runners)} 个工作流, "
            f"失败 {len(self.failed_workflows)} 个: active={sorted(self.runners.keys())}, "
            f"failed={sorted(self.failed_workflows.keys())}"
        )

    def _collect_runner_failures(self):
        for workflow_id, runner in list(self.runners.items()):
            failure = runner.pop_failure()
            if failure is None:
                continue

            logger.error(
                f"[SourceHost:{self.source_id}] 工作流 {workflow_id} 后台执行失败: {failure}",
                exc_info=(type(failure), failure, failure.__traceback__),
            )
            workflow = self.workflows.get(workflow_id)
            runner.cleanup(stop_first=False)
            del self.runners[workflow_id]
            if workflow is not None:
                self._schedule_workflow_retry(workflow, failure)

    def setup(self):
        self._setup_buffer()
        self._setup_executors()

    def run(self):
        if not self.running:
            return

        logger.info(
            f"[SourceHost:{self.source_id}] 宿主进程启动，"
            f"source={self.source.name}, workflows={sorted(self.runners.keys())}"
        )

        while self.running:
            try:
                self._retry_failed_workflows()
                self._collect_runner_failures()

                if not self.runners:
                    if self.failed_workflows:
                        time.sleep(1.0)
                        continue
                    logger.warning(f"[SourceHost:{self.source_id}] 无可运行工作流，宿主进程退出")
                    break

                peek_result = self.buffer.peek_with_timestamp(-1)
                if peek_result is None:
                    time.sleep(0.01)
                    continue

                frame_nv12, frame_timestamp = peek_result
                if self.last_frame_timestamp == frame_timestamp:
                    time.sleep(0.001)
                    continue

                self.last_frame_timestamp = frame_timestamp

                for workflow_id, runner in list(self.runners.items()):
                    if not self.running:
                        break
                    runner.submit_frame(frame_nv12, frame_timestamp)

            except KeyboardInterrupt:
                logger.info(f"[SourceHost:{self.source_id}] 收到中断信号，准备退出")
                break
            except Exception as exc:
                logger.error(
                    f"[SourceHost:{self.source_id}] 主循环异常: {exc}",
                    exc_info=True,
                )
                self.running = False
                break

    def cleanup(self):
        for workflow_id, runner in list(self.runners.items()):
            try:
                runner.cleanup()
            except Exception as exc:
                logger.error(
                    f"[SourceHost:{self.source_id}] 清理工作流 {workflow_id} 失败: {exc}",
                    exc_info=True,
                )

        self.runners.clear()
        self.failed_workflows.clear()
        self.workflows.clear()

        if self.buffer is not None:
            try:
                self.buffer.close()
            except Exception as exc:
                logger.error(
                    f"[SourceHost:{self.source_id}] 关闭分析缓冲区失败: {exc}",
                    exc_info=True,
                )
            self.buffer = None

    def signal_handler(self, signum, frame):
        logger.info(f"[SourceHost:{self.source_id}] 收到信号 {signum}，准备停止")
        self.running = False


def main(args):
    host = SourceWorkflowHost(args.source_id)
    signal.signal(signal.SIGINT, host.signal_handler)
    signal.signal(signal.SIGTERM, host.signal_handler)

    try:
        host.setup()
        host.run()
    finally:
        host.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-id', required=True, type=int, help='视频源 ID')
    main(parser.parse_args())
