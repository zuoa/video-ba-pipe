import signal
import subprocess
import time

from playhouse.shortcuts import model_to_dict

from app import logger
from app.config import RINGBUFFER_DURATION, RECORDING_FPS
from app.core.database_models import db, VideoSource
from app.core.ringbuffer import VideoRingBuffer


class Orchestrator:
    def __init__(self):
        self.running_processes = {}
        self.buffers = {}
        db.connect()  # 在初始化时连接数据库

        ## 清理之前可能遗留的运行状态
        VideoSource.update(status='STOPPED', decoder_pid=None).execute()

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

    def _stop_source(self, source: VideoSource):
        print(f"  -> 正在停止视频源 ID {source.id}: {source.name}")

        if source.id in self.running_processes:
            self.running_processes[source.id]['decoder'].terminate()
            del self.running_processes[source.id]

        if source.id in self.buffers:
            self.buffers[source.id].close()
            self.buffers[source.id].unlink()
            del self.buffers[source.id]

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

        # 健康检查
        running_sources = VideoSource.select().where(VideoSource.status == 'RUNNING')
        for source in running_sources:
            if source.id in self.running_processes:
                need_reboot = False

                exit_code = self.running_processes[source.id]['decoder'].poll()
                if exit_code is not None:
                    logger.warn(f"🚨 视频源 ID {source.id} 的解码器工作进程已退出:{exit_code}！")
                    need_reboot = True

                if need_reboot:
                    source.status = 'FAILED'
                    source.save()
                    self._stop_source(source)

    def run(self):
        print("🚀 编排器启动，开始动态管理视频源...")
        while True:
            self.manage_sources()
            time.sleep(5)

    def stop(self):
        print("\n优雅地关闭所有正在运行的视频源...")
        for source in VideoSource.select().where(VideoSource.status == 'RUNNING'):
            self._stop_source(source)
        db.close()
        print("所有视频源已停止。")


if __name__ == "__main__":
    orch = Orchestrator()
    signal.signal(signal.SIGINT, lambda s, f: orch.stop() or exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: orch.stop() or exit(0))
    orch.run()
