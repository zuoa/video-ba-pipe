import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import resource_tracker

from playhouse.shortcuts import model_to_dict

from app import logger
from app.config import (
    FRAME_SAVE_PATH, 
    VIDEO_SAVE_PATH, 
    RECORDING_ENABLED, 
    PRE_ALERT_DURATION, 
    POST_ALERT_DURATION, 
    RECORDING_FPS,
    RINGBUFFER_DURATION,
    ALERT_SUPPRESSION_DURATION
)
from app.core.database_models import Algorithm, Task, Alert  # 导入 Algorithm 模型
from app.core.ringbuffer import VideoRingBuffer
from app.core.utils import save_frame
from app.core.video_recorder import VideoRecorderManager
from app.core.rabbitmq_publisher import publish_alert_to_rabbitmq, format_alert_message
from app.plugin_manager import PluginManager


def main(args):
    task_id = args.task_id

    task = Task.get_by_id(task_id)  # 确保任务存在，否则抛出异常
    source_code = task.source_code
    source_name = task.source_name
    buffer_name = f"{task.buffer_name}.{task.id}"

    logger.info(f"[AIWorker:{os.getpid()}] 启动，处理视频源 {source_name} (ID: {source_code})")

    # 连接到共享内存缓冲区（必须使用与创建时相同的参数）
    buffer = VideoRingBuffer(
        name=buffer_name, 
        create=False,
        fps=RECORDING_FPS,
        duration_seconds=RINGBUFFER_DURATION
    )
    logger.info(f"已连接到缓冲区: {buffer_name} (fps={RECORDING_FPS}, duration={RINGBUFFER_DURATION}s, capacity={buffer.capacity})")

    shm_name = buffer_name if os.name == 'nt' else f"/{buffer_name}"
    resource_tracker.unregister(shm_name, 'shared_memory')

    # 初始化视频录制器（如果启用）
    video_recorder = None
    if RECORDING_ENABLED:
        recorder_manager = VideoRecorderManager()
        video_recorder = recorder_manager.get_recorder(
            task_id=task_id,
            buffer=buffer,
            save_dir=VIDEO_SAVE_PATH,
            fps=RECORDING_FPS
        )
        logger.info(f"[AIWorker:{os.getpid()}] 视频录制功能已启用 (前{PRE_ALERT_DURATION}秒 + 后{POST_ALERT_DURATION}秒)")

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
            "name": algo_config_db.name,
            "label_name": algo_config_db.label_name,
            "label_color": algo_config_db.label_color,
            "ext_config": algo_config_db.ext_config_json,
            "models_config": algo_config_db.models_config,
            "interval_seconds": algo_config_db.interval_seconds,
        }
        algorithm = AlgorithmClass(full_config)
        logger.info(f"[AIWorker:{os.getpid()}] 已加载算法 '{plugin_module}'，开始处理 {buffer_name}")
        algorithms[algo_id] = algorithm

        algorithm_datamap[algo_id] = model_to_dict(algo_config_db)

    # 为每个算法维护处理时间和告警时间
    algo_last_process_time = {algo_id: 0 for algo_id in algorithms.keys()}
    algo_last_alert_time = {algo_id: 0 for algo_id in algorithms.keys()}
    
    check_interval = 10  # 每10秒检查一次插件更新
    last_check_time = time.time()

    logger.info(f"[AIWorker:{os.getpid()}] 算法处理间隔配置:")
    for algo_id in algorithms.keys():
        interval = algorithm_datamap[algo_id].get('interval_seconds', 1)
        logger.info(f"  - 算法 {algo_id} ({algorithm_datamap[algo_id].get('name')}): {interval}秒/次")
    logger.info(f"[AIWorker:{os.getpid()}] 告警抑制时长: {ALERT_SUPPRESSION_DURATION}秒")

    with ThreadPoolExecutor(max_workers=len(algorithms)) as executor:
        frame_count = 0
        last_processed_frame_time = 0  # 记录上次处理的帧时间，避免重复处理
        
        while True:
            # 热重载检查
            if time.time() - last_check_time > check_interval:
                plugin_manager.check_for_updates()
                # 可以在这里添加逻辑：如果当前算法被更新，则重新实例化
                last_check_time = time.time()

            # 使用 peek 而不是 read，避免消费 RingBuffer 中的历史帧
            # 这样可以保证录制功能能获取到完整的历史帧
            frame_with_timestamp = buffer.peek_with_timestamp(-1)
            
            if frame_with_timestamp is not None:
                latest_frame, frame_timestamp = frame_with_timestamp
                
                # 避免重复处理同一帧
                if frame_timestamp <= last_processed_frame_time:
                    time.sleep(0.05)  # 短暂休眠，避免CPU占用过高
                    continue
                
                last_processed_frame_time = frame_timestamp
                current_time = time.time()
                
                # 检查哪些算法需要处理这一帧（根据interval_seconds）
                algos_to_process = {}
                for algo_id, algo in algorithms.items():
                    interval = algorithm_datamap[algo_id].get('interval_seconds', 1)
                    if current_time - algo_last_process_time[algo_id] >= interval:
                        algos_to_process[algo_id] = algo
                        algo_last_process_time[algo_id] = current_time
                
                if not algos_to_process:
                    # 没有算法需要处理这一帧，跳过
                    continue
                
                logger.debug(f"\n[AIWorker] 收到第 {frame_count} 帧，提交给 {len(algos_to_process)} 个算法处理...")
                future_to_algo = {
                    executor.submit(algo.process, latest_frame.copy()): aid
                    for aid, algo in algos_to_process.items()
                }

                for future in as_completed(future_to_algo):
                    algo_id = future_to_algo[future]
                    try:
                        # 获取算法的处理结果
                        result = future.result()
                        logger.info(f"[AIWorker] 收到来自算法 {algo_id} 的处理结果。")

                        # 根据结果进行后续操作，例如可视化
                        if result and result.get("detections"):
                            
                            # 记录触发时间
                            trigger_time = time.time()
                            
                            # 告警抑制检查
                            time_since_last_alert = trigger_time - algo_last_alert_time[algo_id]
                            if time_since_last_alert < ALERT_SUPPRESSION_DURATION:
                                logger.info(
                                    f"[AIWorker] 算法 {algo_id} 检测到目标，但处于告警抑制期 "
                                    f"(距上次告警 {time_since_last_alert:.1f}秒，需 {ALERT_SUPPRESSION_DURATION}秒)，跳过本次告警"
                                )
                                continue
                            
                            # 更新最后告警时间
                            algo_last_alert_time[algo_id] = trigger_time

                            # 检测目标可视化并保存
                            filepath = f"{source_code}/{algorithm_datamap[algo_id].get('name')}/frame_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
                            filepath_absolute = os.path.join(FRAME_SAVE_PATH, filepath)
                            label_color = algorithm_datamap[algo_id].get('label_color', '#FF0000')
                            algorithms[algo_id].visualize(latest_frame, result.get("detections"), save_path=filepath_absolute, label_color=label_color)

                            # 保存原始图片
                            filepath_ori = f"{filepath}.ori.jpg"
                            filepath_ori_absolute = os.path.join(FRAME_SAVE_PATH, filepath_ori)
                            save_frame(latest_frame, filepath_ori_absolute)

                            # 创建Alert记录
                            alert = Alert.create(
                                task=task,
                                alert_time=time.strftime('%Y-%m-%d %H:%M:%S'),
                                alert_type=algorithm_datamap[algo_id].get('name'),
                                alert_message='',
                                alert_image=filepath,
                                alert_image_ori=filepath_ori,
                                alert_video="",
                            )
                            logger.info(f"[AIWorker] 算法 {algo_id} 触发警报，结果已保存到 {filepath}。")

                            # 启动视频录制（如果启用）
                            if video_recorder:
                                try:
                                    video_path = video_recorder.start_recording(
                                        task_id=task_id,
                                        alert_id=alert.id,
                                        trigger_time=trigger_time,
                                        pre_seconds=PRE_ALERT_DURATION,
                                        post_seconds=POST_ALERT_DURATION
                                    )
                                    
                                    # 更新Alert记录中的视频路径
                                    alert.alert_video = video_path
                                    alert.save()
                                    
                                    logger.info(f"[AIWorker] 已启动视频录制任务，预计保存到: {video_path}")
                                except Exception as rec_err:
                                    logger.error(f"[AIWorker] 启动视频录制失败: {rec_err}", exc_info=True)

                            # 发布预警消息到RabbitMQ
                            try:
                                alert_message = format_alert_message(alert)
                                if publish_alert_to_rabbitmq(alert_message):
                                    logger.info(f"[AIWorker] 预警消息已成功发布到RabbitMQ: {alert.id}")
                                else:
                                    logger.warning(f"[AIWorker] 预警消息发布到RabbitMQ失败: {alert.id}")
                            except Exception as e:
                                logger.error(f"[AIWorker] 发布预警消息到RabbitMQ时发生错误: {e}")

                    except Exception as exc:
                        logger.info(f"[AIWorker] 错误：算法 {algo_id} 在处理过程中发生异常: {exc}")
                        logger.exception(exc, exc_info=True)

                frame_count += 1
            time.sleep(0.1)  # 控制处理频率


if __name__ == '__main__':
    logger.info("=== AI 工作者启动 ===")
    parser = argparse.ArgumentParser()
    parser.add_argument('--algo-ids', required=True, help="要加载的算法名称")
    parser.add_argument('--task-id', required=True, help="任务ID")
    args = parser.parse_args()
    main(args)
