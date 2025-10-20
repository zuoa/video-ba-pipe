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
from app.core.window_detector import get_window_detector
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
        frame_shape=(task.source_decode_height, task.source_decode_width, 3),  # 使用任务的宽高参数
        fps=task.source_fps,  # 使用任务的FPS参数
        duration_seconds=RINGBUFFER_DURATION
    )
    logger.info(f"已连接到缓冲区: {buffer_name} (fps={task.source_fps}, duration={RINGBUFFER_DURATION}s, capacity={buffer.capacity}, frame_shape={buffer.frame_shape})")

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
    
    # 1.5 初始化时间窗口检测器
    window_detector = get_window_detector()

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
    
    # 加载所有算法的时间窗口配置
    for algo_id in algorithms.keys():
        window_detector.load_config(task_id, algo_id)

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
                        has_detection = bool(result and result.get("detections"))
                        
                        # 检查是否启用了窗口检测，只有启用时才记录
                        window_config = window_detector.configs.get((task_id, algo_id))
                        if window_config is None:
                            window_detector.load_config(task_id, algo_id)
                            window_config = window_detector.configs.get((task_id, algo_id))
                        
                        # 获取算法名称和标签颜色（在条件判断之前定义，确保后续代码可以访问）
                        algorithm_name = algorithm_datamap[algo_id].get('name')
                        label_color = algorithm_datamap[algo_id].get('label_color', '#FF0000')
                        
                        # 只有启用窗口检测时才记录检测结果
                        if window_config and window_config.get('enable', False):
                            # 如果检测到目标，先保存图片
                            image_path = None
                            if has_detection:
                                
                                # 生成图片路径
                                img_path = f"{source_code}/{algorithm_name}/det_{int(frame_timestamp)}.jpg"
                                img_path_absolute = os.path.join(FRAME_SAVE_PATH, img_path)
                                
                                # 保存检测图片
                                algorithms[algo_id].visualize(latest_frame, result.get("detections"), 
                                                            save_path=img_path_absolute, label_color=label_color)
                                
                                # 保存原始图片
                                img_ori_path = f"{img_path}.ori.jpg"
                                img_ori_path_absolute = os.path.join(FRAME_SAVE_PATH, img_ori_path)
                                save_frame(latest_frame, img_ori_path_absolute)
                                
                                image_path = img_path
                                logger.info(f"[AIWorker] 检测到目标，已保存图片: {img_path}")
                            
                            window_detector.add_record(
                                task_id=task_id,
                                algorithm_id=algo_id,
                                timestamp=frame_timestamp,
                                has_detection=has_detection,
                                image_path=image_path
                            )
                        
                        logger.info(f"[AIWorker] 收到来自算法 {algo_id} 的处理结果，检测到目标: {has_detection}")

                        # 根据结果进行后续操作，例如可视化
                        if has_detection:
                            
                            # 记录触发时间
                            trigger_time = time.time()
                            
                            # 检查时间窗口条件
                            window_passed, window_stats = window_detector.check_condition(
                                task_id=task_id,
                                algorithm_id=algo_id,
                                current_time=trigger_time
                            )
                            
                            if not window_passed:
                                # 未满足窗口条件，不触发告警
                                if window_stats:
                                    logger.info(
                                        f"[AIWorker] 算法 {algo_id} 检测到目标，但窗口条件未满足 "
                                        f"(检测: {window_stats['detection_count']}/{window_stats['total_count']} 帧, "
                                        f"比例: {window_stats['detection_ratio']:.2%}, "
                                        f"连续: {window_stats['max_consecutive']} 次)，跳过本次告警"
                                    )
                                else:
                                    logger.info(f"[AIWorker] 算法 {algo_id} 检测到目标，但窗口条件未满足，跳过本次告警")
                                continue
                            
                            # 满足窗口条件，记录日志
                            if window_stats:
                                logger.info(
                                    f"[AIWorker] 算法 {algo_id} 满足窗口条件 "
                                    f"(检测: {window_stats['detection_count']}/{window_stats['total_count']} 帧, "
                                    f"比例: {window_stats['detection_ratio']:.2%}, "
                                    f"连续: {window_stats['max_consecutive']} 次)"
                                )
                            
                            # 告警抑制检查
                            time_since_last_alert = trigger_time - algo_last_alert_time[algo_id]
                            if time_since_last_alert < ALERT_SUPPRESSION_DURATION:
                                logger.info(
                                    f"[AIWorker] 算法 {algo_id} 满足窗口条件，但处于告警抑制期 "
                                    f"(距上次告警 {time_since_last_alert:.1f}秒，需 {ALERT_SUPPRESSION_DURATION}秒)，跳过本次告警"
                                )
                                continue
                            
                            # 更新最后告警时间
                            algo_last_alert_time[algo_id] = trigger_time

                            # 获取窗口内的检测记录
                            detection_records = window_detector.get_detection_records(
                                task_id=task_id,
                                algorithm_id=algo_id,
                                current_time=trigger_time
                            )
                            
                            logger.info(f"[AIWorker] 窗口内检测到 {len(detection_records)} 次目标")

                            # 构建检测序列图片信息
                            detection_images = []
                            
                            # 使用已保存的检测图片路径
                            for i, (timestamp, has_detection, image_path) in enumerate(detection_records):
                                if has_detection and image_path:
                                    # 构建原始图片路径
                                    img_ori_path = f"{image_path}.ori.jpg"
                                    
                                    detection_images.append({
                                        'image_path': image_path,
                                        'image_ori_path': img_ori_path,
                                        'timestamp': timestamp,
                                        'detection_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
                                    })
                            
                            # 如果没有检测记录，保存当前检测
                            if not detection_images:
                                filepath = f"{source_code}/{algorithm_name}/frame_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
                                filepath_absolute = os.path.join(FRAME_SAVE_PATH, filepath)
                                algorithms[algo_id].visualize(latest_frame, result.get("detections"), 
                                                            save_path=filepath_absolute, label_color=label_color)
                                
                                filepath_ori = f"{filepath}.ori.jpg"
                                filepath_ori_absolute = os.path.join(FRAME_SAVE_PATH, filepath_ori)
                                save_frame(latest_frame, filepath_ori_absolute)
                                
                                detection_images.append({
                                    'image_path': filepath,
                                    'image_ori_path': filepath_ori,
                                    'timestamp': frame_timestamp
                                })

                            # 构建alert_message，包含时间窗口检测信息
                            alert_message = ""
                            if window_stats:
                                # 时间窗口检测启用且通过，记录详细信息
                                window_config = window_detector.configs.get((task_id, algo_id), {})
                                window_mode = window_config.get('mode', 'unknown')
                                window_threshold = window_config.get('threshold', 0)
                                window_size = window_config.get('window_size', 0)
                                
                                # 构建时间窗口检测信息
                                window_info = (
                                    f"时间窗口检测通过 - "
                                    f"模式: {window_mode}, "
                                    f"阈值: {window_threshold}, "
                                    f"窗口大小: {window_size}秒, "
                                    f"检测帧数: {window_stats['detection_count']}/{window_stats['total_count']}, "
                                    f"检测比例: {window_stats['detection_ratio']:.2%}, "
                                    f"最大连续: {window_stats['max_consecutive']}次"
                                )
                                alert_message = window_info
                                logger.info(f"[AIWorker] 记录时间窗口检测信息到alert_message: {alert_message}")
                            else:
                                # 时间窗口检测未启用，不记录信息（保持为空）
                                alert_message = ""
                                logger.info(f"[AIWorker] 时间窗口检测未启用，alert_message保持为空")

                            # 创建Alert记录
                            import json
                            main_image = detection_images[-1]['image_path'] if detection_images else ""
                            main_image_ori = detection_images[-1]['image_ori_path'] if detection_images else ""
                            
                            alert = Alert.create(
                                task=task,
                                alert_time=time.strftime('%Y-%m-%d %H:%M:%S'),
                                alert_type=algorithm_datamap[algo_id].get('name'),
                                alert_message=alert_message,
                                alert_image=main_image,
                                alert_image_ori=main_image_ori,
                                alert_video="",
                                detection_count=len(detection_images),
                                window_stats=json.dumps(window_stats) if window_stats else None,
                                detection_images=json.dumps(detection_images) if detection_images else None
                            )
                            logger.info(f"[AIWorker] 算法 {algo_id} 触发警报，检测序列包含 {len(detection_images)} 张图片。")

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
