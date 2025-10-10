import signal
import subprocess
import time

from app import logger
from app.core.database_models import db, Task  # 只需导入模型
from app.core.ringbuffer import VideoRingBuffer


class Orchestrator:
    def __init__(self):
        self.running_processes = {}
        self.buffers = {}
        db.connect()  # 在初始化时连接数据库

    def _start_task(self, task: Task):
        print(f"  -> 正在启动任务 ID {task.id}: {task.name}")

        # 创建共享内存环形缓冲区
        task_buffer_name = f"{task.buffer_name}.{task.id}"
        buffer = VideoRingBuffer(name=task_buffer_name, create=True)
        self.buffers[task.id] = buffer

        decoder_args = ['python', 'decoder_worker.py', '--url', task.source_url, '--buffer', task_buffer_name, '--source-code', task.source_code, '--source-name', task.source_name or '', '--sample-mode', 'interval', '--sample-interval', '5']
        decoder_p = subprocess.Popen(decoder_args)

        # 将 algorithm id 传递给工作者
        ai_args = ['python', 'ai_worker.py', '--algo-id', str(task.algorithm.id), '--buffer', task_buffer_name, '--source-code', task.source_code, '--source-name', task.source_name or '']
        ai_p = subprocess.Popen(ai_args)

        # 更新任务状态，就像操作一个普通Python对象一样
        task.status = 'RUNNING'
        task.decoder_pid = decoder_p.pid
        task.ai_pid = ai_p.pid
        task.save()  # .save() 会将更改写入数据库

        self.running_processes[task.id] = {'decoder': decoder_p, 'ai': ai_p}

    def _stop_task(self, task: Task):
        print(f"  -> 正在停止任务 ID {task.id}: {task.name}")

        if task.id in self.running_processes:
            self.running_processes[task.id]['decoder'].terminate()
            self.running_processes[task.id]['ai'].terminate()
            del self.running_processes[task.id]

        if task.id in self.buffers:
            self.buffers[task.id].close()
            self.buffers[task.id].unlink()
            del self.buffers[task.id]

        task.status = 'STOPPED'
        task.decoder_pid = None
        task.ai_pid = None
        task.save()

    def manage_tasks(self):
        # 查找需要启动的任务 (查询变得非常直观)
        tasks_to_start = Task.select().where(
            (Task.enabled == True) & (Task.status == 'STOPPED')
        )
        for task in tasks_to_start:
            self._start_task(task)

        # 查找需要停止的任务
        tasks_to_stop = Task.select().where(
            (Task.enabled == False) & (Task.status == 'RUNNING')
        )
        for task in tasks_to_stop:
            self._stop_task(task)

        # 健康检查
        running_tasks = Task.select().where(Task.status == 'RUNNING')
        for task in running_tasks:
            if task.id in self.running_processes:
                if self.running_processes[task.id]['decoder'].poll() is not None or \
                        self.running_processes[task.id]['ai'].poll() is not None:
                    logger.warn(f"🚨 任务 ID {task.id} 的某个工作进程已退出！")
                    print(f"[警告] 任务 ID {task.id} 的某个工作进程已退出！")
                    task.status = 'FAILED'
                    task.save()
                    # 可以在这里触发停止和重启逻辑
                    self._stop_task(task)

    def run(self):
        print("🚀 编排器启动 (Peewee 模式)，开始动态管理任务...")
        while True:
            self.manage_tasks()
            time.sleep(5)

    def stop(self):
        print("\n优雅地关闭所有正在运行的任务...")
        for task in Task.select().where(Task.status == 'RUNNING'):
            self._stop_task(task)
        db.close()  # 在退出时关闭数据库连接
        print("所有任务已停止。")


if __name__ == "__main__":
    orch = Orchestrator()
    signal.signal(signal.SIGINT, lambda s, f: orch.stop() or exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: orch.stop() or exit(0))
    orch.run()
