import argparse
import os
import time
from multiprocessing import resource_tracker

import cv2

from app import logger
from app.config import FRAME_SAVE_PATH
from app.core.database_models import Algorithm  # 导入 Algorithm 模型
from app.core.ringbuffer import VideoRingBuffer
from app.plugin_manager import PluginManager




def main(args):
    source_code = args.source_code
    source_name = args.source_name if args.source_name else args.source_code
    print(f"[AIWorker:{os.getpid()}] 启动，处理视频源 {source_name} (ID: {source_code})")


    buffer = VideoRingBuffer(name=args.buffer, create=False)

    shm_name = args.buffer if os.name == 'nt' else f"/{args.buffer}"
    resource_tracker.unregister(shm_name, 'shared_memory')

    # 1. 初始化插件管理器
    plugin_manager = PluginManager()

    # 2. 从数据库获取此工作者被指定的算法配置
    algo_config_db = Algorithm.get_by_id(args.algo_id)
    algo_name = algo_config_db.name

    # 3. 从插件管理器获取对应的算法类
    AlgorithmClass = plugin_manager.get_algorithm_class(algo_name)
    if not AlgorithmClass:
        print(f"[AIWorker:{os.getpid()}] 错误：找不到名为 '{algo_name}' 的算法插件。")
        return

    # 4. 实例化算法插件
    # 将数据库中的配置（model_path, config_json）传递给插件
    full_config = {
        "models_config": algo_config_db.models_config,
        "interval_seconds": algo_config_db.interval_seconds,
    }
    algorithm = AlgorithmClass(full_config)
    print(f"[AIWorker:{os.getpid()}] 已加载算法 '{algo_name}'，开始处理 {args.buffer}")

    check_interval = 10  # 每10秒检查一次插件更新
    last_check_time = time.time()
    frame_count = 0
    while True:
        # 热重载检查
        if time.time() - last_check_time > check_interval:
            plugin_manager.check_for_updates()
            # 可以在这里添加逻辑：如果当前算法被更新，则重新实例化
            last_check_time = time.time()

        latest_frame = buffer.read()
        if latest_frame is not None:
            logger.debug("[AIWorker] 处理新帧")
            logger.debug(f"[{time.strftime('%H:%M:%S')}] 已处理一帧, "
                         f"尺寸: {latest_frame.shape}")

            result = algorithm.process(latest_frame)
            if result and result.get("detections"):
                filepath = os.path.join(FRAME_SAVE_PATH, f"{source_code}/frame_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
                algorithm.visualize(latest_frame, result.get("detections"), save_path=filepath)

            frame_count += 1
        time.sleep(0.1)  # 控制处理频率


if __name__ == '__main__':
    logger.info("=== AI 工作者启动 ===")
    parser = argparse.ArgumentParser()
    parser.add_argument('--algo-id', required=True, help="要加载的算法名称")
    parser.add_argument('--buffer', required=True, help="共享内存缓冲区名称")
    parser.add_argument('--source-code', required=True, help="视频源ID")
    parser.add_argument('--source-name', required=True, help="视频源名称")
    args = parser.parse_args()
    main(args)
