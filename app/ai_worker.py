import argparse
import os
import time
from multiprocessing import resource_tracker

import cv2

from app import logger
from app.core.database_models import Algorithm  # 导入 Algorithm 模型
from app.core.ringbuffer import VideoRingBuffer


def get_algorithm_config(algo_id):
    # 使用 Peewee 的 get_by_id，如果找不到会直接抛出异常
    try:
        algo = Algorithm.get_by_id(algo_id)
        # 直接使用 @property 方法获取解析后的配置
        return algo.name, algo.model_path, algo.config
    except Algorithm.DoesNotExist:
        print(f"错误：在数据库中找不到 ID 为 {algo_id} 的算法。")
        return None, None, None


def main(args):
    buffer = VideoRingBuffer(name=args.buffer, create=False)

    shm_name = args.buffer if os.name == 'nt' else f"/{args.buffer}"
    resource_tracker.unregister(shm_name, 'shared_memory')

    algo_name, model_path, algo_config = get_algorithm_config(args.algo_id)
    if not algo_name:
        logger.error(f"[AIWorker:{os.getpid()}] 无法加载算法 ID {args.algo_id}，请检查数据库配置。")
        exit(1)

    logger.info(f"[AIWorker:{os.getpid()}] 已加载算法 '{algo_name}' (模型: {model_path})")

    while True:
        latest_frame = buffer.peek(-1)
        frame_count = 0
        if latest_frame is not None:
            logger.debug("[AIWorker] 处理新帧")
            logger.debug(f"[{time.strftime('%H:%M:%S')}] 已处理一帧, "
                  f"尺寸: {latest_frame.shape}")

            # 1. 将帧从 RGB 转换为 BGR
            bgr_frame = cv2.cvtColor(latest_frame, cv2.COLOR_RGB2BGR)

            # 2. 创建文件名 (例如：frame_00010.jpg)
            filename = os.path.join("data/frames", f"frame_{frame_count:05d}.jpg")

            # 3. 将 BGR 帧写入文件
            cv2.imwrite(filename, bgr_frame)

            print(f"✅ 已保存帧: {filename}")
            # result = algorithm.process(latest_frame)
            # if result:
            #     # 触发保存视频等后续操作
            #     trigger_save_action(buffer, result)
            frame_count += 1
        time.sleep(0.1)  # 控制处理频率


if __name__ == '__main__':
    logger.info("=== AI 工作者启动 ===")
    parser = argparse.ArgumentParser()
    parser.add_argument('--algo_id', required=True, help="要加载的算法名称")
    parser.add_argument('--buffer', required=True, help="共享内存缓冲区名称")
    args = parser.parse_args()
    main(args)

