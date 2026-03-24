"""
工作流工作进程入口
作为独立进程运行，使用 WorkflowExecutor 执行工作流
"""
import argparse
import logging
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import logger
from app.core.workflow_executor import WorkflowExecutor


def main(args):
    """工作流工作进程主函数"""
    logger.info(f"[WorkflowWorker:{os.getpid()}] 工作流工作进程启动，参数: {args}")
    workflow_id = args.workflow_id
    executor = WorkflowExecutor(workflow_id)

    def _handle_signal(signum, frame):
        logger.info(f"[WorkflowWorker:{os.getpid()}] 收到信号 {signum}，准备停止")
        executor.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    executor.run()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workflow-id', required=True, help="Workflow ID")
    args = parser.parse_args()
    main(args)
