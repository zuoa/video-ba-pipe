import signal
import subprocess
import time

from playhouse.shortcuts import model_to_dict

from app import logger
from app.config import RINGBUFFER_DURATION, RECORDING_FPS
from app.core.database_models import db, Task, TaskAlgorithm  # 只需导入模型
from app.core.ringbuffer import VideoRingBuffer


class Orchestrator:
    def __init__(self):
        self.running_processes = {}
        self.buffers = {}
        db.connect()  # 在初始化时连接数据库

    def _start_task(self, task: Task):
        print(f"  -> 正在启动任务 ID {task.id}: {task.name}")

        # 创建共享内存环形缓冲区（使用任务参数）
        task_buffer_name = f"{task.buffer_name}.{task.id}"
        buffer = VideoRingBuffer(
            name=task_buffer_name, 
            create=True,
            frame_shape=(task.source_decode_height, task.source_decode_width, 3),  # 使用任务的宽高参数
            fps=task.source_fps,  # 使用任务的FPS参数
            duration_seconds=RINGBUFFER_DURATION  # 使用配置的缓冲时长
        )
        self.buffers[task.id] = buffer
        
        logger.info(f"创建RingBuffer: fps={task.source_fps}, duration={RINGBUFFER_DURATION}s, capacity={buffer.capacity}帧, frame_shape={buffer.frame_shape}")

        # 启动解码器工作进程（使用fps采样模式和任务参数）
        decoder_args = [
            'python', 'decoder_worker.py', 
            '--url', task.source_url,  
            '--task-id', str(task.id), 
            '--sample-mode', 'fps',  # 改为fps模式
            '--sample-fps', str(task.source_fps),  # 使用任务的FPS参数
            '--width', str(task.source_decode_width),  # 使用任务的宽度参数
            '--height', str(task.source_decode_height)  # 使用任务的高度参数
        ]
        logger.info(' '.join(decoder_args))
        decoder_p = subprocess.Popen(decoder_args)

        query = TaskAlgorithm.select().where(TaskAlgorithm.task == task)

        ai_ids = []
        for task_algorithm in query:
            ai_ids.append(str(task_algorithm.algorithm.id))

        ai_args = ['python', 'ai_worker.py', '--algo-ids', str(','.join(ai_ids)), '--task-id', str(task.id)]
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
                if self.running_processes[task.id]['decoder'].poll() is not None or  self.running_processes[task.id]['ai'].poll() is not None :
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
