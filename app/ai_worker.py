import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import resource_tracker

from playhouse.shortcuts import model_to_dict

from app import logger
from app.config import FRAME_SAVE_PATH
from app.core.database_models import Algorithm, Task, Alert  # 导入 Algorithm 模型
from app.core.ringbuffer import VideoRingBuffer
from app.core.utils import save_frame
from app.plugin_manager import PluginManager


def main(args):
    task_id = args.task_id

    task = Task.get_by_id(task_id)  # 确保任务存在，否则抛出异常
    source_code = task.source_code
    source_name = task.source_name
    buffer_name = f"{task.buffer_name}.{task.id}"

    logger.info(f"[AIWorker:{os.getpid()}] 启动，处理视频源 {source_name} (ID: {source_code})")

    buffer = VideoRingBuffer(name=buffer_name, create=False)

    shm_name = buffer_name if os.name == 'nt' else f"/{buffer_name}"
    resource_tracker.unregister(shm_name, 'shared_memory')

    # 1. 初始化插件管理器
    plugin_manager = PluginManager()

    # 2. 从数据库获取此工作者被指定的算法配置
    algo_id_list = args.algo_ids.split(',')
    algorithms = {}
    algorithm_datamap = {}

    for algo_id in algo_id_list:
        algo_config_db = Algorithm.get_by_id(algo_id)
        plugin_module = algo_config_db.plugin_module

        # 3. 从插件管理器获取对应的算法类
        AlgorithmClass = plugin_manager.get_algorithm_class(plugin_module)
        if not AlgorithmClass:
            logger.error(f"[AIWorker:{os.getpid()}] 错误：找不到名为 '{plugin_module}' 的算法插件。")
            return

        # 4. 实例化算法插件
        # 将数据库中的配置（model_path, config_json）传递给插件
        full_config = {
            "models_config": algo_config_db.models_config,
            "interval_seconds": algo_config_db.interval_seconds,
        }
        algorithm = AlgorithmClass(full_config)
        logger.info(f"[AIWorker:{os.getpid()}] 已加载算法 '{plugin_module}'，开始处理 {buffer_name}")
        algorithms[algo_id] = algorithm

        algorithm_datamap[algo_id] = model_to_dict(algo_config_db)

    check_interval = 10  # 每10秒检查一次插件更新
    last_check_time = time.time()

    with ThreadPoolExecutor(max_workers=len(algorithms)) as executor:
        frame_count = 0
        while True:
            # 热重载检查
            if time.time() - last_check_time > check_interval:
                plugin_manager.check_for_updates()
                # 可以在这里添加逻辑：如果当前算法被更新，则重新实例化
                last_check_time = time.time()

            latest_frame = buffer.read()
            if latest_frame is not None:
                logger.debug(f"\n[AIWorker] 收到第 {frame_count} 帧，提交给 {len(algorithms)} 个算法并行处理...")
                future_to_algo = {
                    executor.submit(algo.process, latest_frame.copy()): aid
                    for aid, algo in algorithms.items()
                }

                for future in as_completed(future_to_algo):
                    algo_id = future_to_algo[future]
                    try:
                        # 获取算法的处理结果
                        result = future.result()
                        logger.info(f"[AIWorker] 收到来自算法 {algo_id} 的处理结果。")

                        # 根据结果进行后续操作，例如可视化
                        if result and result.get("detections"):

                            # 检测目标可视化并保存
                            filepath = f"{source_code}/{algorithm_datamap[algo_id].get('name')}/frame_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
                            filepath_absolute = os.path.join(FRAME_SAVE_PATH, filepath)
                            algorithms[algo_id].visualize(latest_frame, result.get("detections"), save_path=filepath_absolute)

                            # 保存原始图片
                            filepath_ori = f"{filepath}.ori.jpg"
                            filepath_ori_absolute = os.path.join(FRAME_SAVE_PATH, filepath_ori)
                            save_frame(latest_frame, filepath_ori_absolute)

                            Alert.create(
                                task=task,
                                alert_time=time.strftime('%Y-%m-%d %H:%M:%S'),
                                alert_type=algorithm_datamap[algo_id].get('name'),
                                alert_message='',
                                alert_image=filepath,
                                alert_image_ori=filepath_ori,
                                alert_video="",
                            )
                            logger.info(f"[AIWorker] 算法 {algo_id} 触发警报，结果已保存到 {filepath}。")

                    except Exception as exc:
                        logger.info(f"[AIWorker] 错误：算法 {algo_id} 在处理过程中发生异常: {exc}")

                frame_count += 1
            time.sleep(0.1)  # 控制处理频率


if __name__ == '__main__':
    logger.info("=== AI 工作者启动 ===")
    parser = argparse.ArgumentParser()
    parser.add_argument('--algo-ids', required=True, help="要加载的算法名称")
    parser.add_argument('--task-id', required=True, help="任务ID")
    args = parser.parse_args()
    main(args)
