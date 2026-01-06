import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import resource_tracker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
from app.core.database_models import Workflow, VideoSource, Algorithm, Alert
from app.core.ringbuffer import VideoRingBuffer
from app.core.utils import save_frame
from app.core.video_recorder import VideoRecorderManager
from app.core.rabbitmq_publisher import publish_alert_to_rabbitmq, format_alert_message
from app.core.window_detector import get_window_detector
from app.plugins.script_algorithm import ScriptAlgorithm


class WorkflowExecutor:
    def __init__(self, workflow_id):
        self.workflow_id = workflow_id
        self.workflow = Workflow.get_by_id(workflow_id)
        self.workflow_data = self.workflow.data_dict
        
        self.nodes = {n['id']: n for n in self.workflow_data.get('nodes', [])}
        self.connections = self.workflow_data.get('connections', [])
        
        self.source_node = None
        self.video_source = None
        self.buffer = None
        self.algorithms = {}
        self.algorithm_configs = {}
        self.algorithm_datamap = {}
        self.algorithm_roi_configs = {}
        self.execution_graph = defaultdict(list)
        self.video_recorder = None
        self.window_detector = get_window_detector()
        
        self._build_execution_graph()
        self._init_resources()
        
        self.algo_last_process_time = {}
        self.algo_last_alert_time = {}
        for node_id in self.algorithms.keys():
            self.algo_last_process_time[node_id] = 0
            self.algo_last_alert_time[node_id] = 0
    
    def _build_execution_graph(self):
        for conn in self.connections:
            from_id = conn['from']
            to_id = conn['to']
            condition = conn.get('condition')
            
            self.execution_graph[from_id].append({
                'target': to_id,
                'condition': condition
            })
        
        for node_id, node in self.nodes.items():
            if node['type'] == 'source':
                self.source_node = node
                break
    
    def _init_resources(self):
        if not self.source_node:
            raise ValueError("Workflow must have a source node")
        
        source_id = self.source_node.get('dataId')
        if not source_id:
            raise ValueError("Source node must have dataId")
        
        self.video_source = VideoSource.get_by_id(source_id)
        buffer_name = self.video_source.buffer_name
        
        logger.info(f"[WorkflowWorker:{os.getpid()}] 启动 Workflow {self.workflow.name} (ID: {self.workflow_id})，处理视频源 {self.video_source.name} (ID: {self.video_source.source_code})")
        
        self.buffer = VideoRingBuffer(
            name=buffer_name,
            create=False,
            frame_shape=(self.video_source.source_decode_height, self.video_source.source_decode_width, 3),
            fps=self.video_source.source_fps,
            duration_seconds=RINGBUFFER_DURATION
        )
        logger.info(f"已连接到缓冲区: {buffer_name} (fps={self.video_source.source_fps}, duration={RINGBUFFER_DURATION}s, capacity={self.buffer.capacity}, frame_shape={self.buffer.frame_shape})")
        
        shm_name = buffer_name if os.name == 'nt' else f"/{buffer_name}"
        resource_tracker.unregister(shm_name, 'shared_memory')
        
        if RECORDING_ENABLED:
            recorder_manager = VideoRecorderManager()
            self.video_recorder = recorder_manager.get_recorder(
                source_id=self.video_source.id,
                buffer=self.buffer,
                save_dir=VIDEO_SAVE_PATH,
                fps=RECORDING_FPS
            )
            logger.info(f"[WorkflowWorker:{os.getpid()}] 视频录制功能已启用 (前{PRE_ALERT_DURATION}秒 + 后{POST_ALERT_DURATION}秒)")
        
        for node_id, node in self.nodes.items():
            if node['type'] == 'algorithm':
                algo_id = node.get('dataId')
                if algo_id:
                    algo = Algorithm.get_by_id(algo_id)
                    
                    self.algorithm_roi_configs[node_id] = []
                    logger.info(f"[WorkflowWorker:{os.getpid()}] 算法节点 {node_id} (算法ID {algo_id}) 未配置ROI热区，将使用全画面检测")
                    
                    full_config = {
                        "id": algo_id,
                        "name": algo.name,
                        "label_name": algo.label_name,
                        "label_color": algo.label_color,
                        "interval_seconds": algo.interval_seconds,
                        "source_id": self.video_source.id,
                        "script_path": algo.script_path,
                        "entry_function": 'process',
                        "runtime_timeout": algo.runtime_timeout,
                        "memory_limit_mb": algo.memory_limit_mb,
                    }
                    full_config.update(algo.config_dict)
                    
                    self.algorithms[node_id] = ScriptAlgorithm(full_config)
                    self.algorithm_datamap[node_id] = model_to_dict(algo)
                    self.algorithm_configs[node_id] = {
                        'algorithm_id': algo_id,
                        'node_id': node_id
                    }
                    
                    logger.info(f"[WorkflowWorker:{os.getpid()}] 加载算法: {algo.name}, 脚本路径: {algo.script_path}")
                    self.window_detector.load_config(self.video_source.id, algo_id)
        
        logger.info(f"[WorkflowWorker:{os.getpid()}] 算法处理间隔配置:")
        for node_id in self.algorithms.keys():
            interval = self.algorithm_datamap[node_id].get('interval_seconds', 1)
            logger.info(f"  - 节点 {node_id} ({self.algorithm_datamap[node_id].get('name')}): {interval}秒/次")
        logger.info(f"[WorkflowWorker:{os.getpid()}] 告警抑制时长: {ALERT_SUPPRESSION_DURATION}秒")
    
    def _get_parallel_algorithm_nodes(self):
        algo_nodes = []
        for next_info in self.execution_graph.get(self.source_node['id'], []):
            next_id = next_info['target']
            if self.nodes.get(next_id, {}).get('type') == 'algorithm':
                algo_nodes.append(next_id)
        return algo_nodes
    
    def _process_algorithm(self, node_id, frame, frame_timestamp):
        algo = self.algorithms.get(node_id)
        if not algo:
            return None
        
        try:
            result = algo.process(frame.copy(), self.algorithm_roi_configs.get(node_id, []))
            logger.info(result)
            has_detection = bool(result and result.get("detections"))
            roi_mask = result.get('roi_mask')
            
            algo_id = self.algorithm_configs[node_id]['algorithm_id']
            algorithm_name = self.algorithm_datamap[node_id].get('name')
            label_color = self.algorithm_datamap[node_id].get('label_color', '#FF0000')
            
            window_config = self.window_detector.configs.get((self.video_source.id, algo_id))
            if window_config is None:
                self.window_detector.load_config(self.video_source.id, algo_id)
                window_config = self.window_detector.configs.get((self.video_source.id, algo_id))
            
            image_path = None
            if window_config and window_config.get('enable', False):
                if has_detection:
                    img_path = f"{self.video_source.source_code}/{algorithm_name}/det_{int(frame_timestamp)}.jpg"
                    img_path_absolute = os.path.join(FRAME_SAVE_PATH, img_path)
                    
                    algo.visualize(frame, result.get("detections"), 
                                 save_path=img_path_absolute, label_color=label_color,
                                 roi_mask=roi_mask)
                    
                    img_ori_path = f"{img_path}.ori.jpg"
                    img_ori_path_absolute = os.path.join(FRAME_SAVE_PATH, img_ori_path)
                    save_frame(frame, img_ori_path_absolute)
                    
                    image_path = img_path
                    logger.info(f"[WorkflowWorker] 检测到目标，已保存图片: {img_path}")
                
                self.window_detector.add_record(
                    source_id=self.video_source.id,
                    algorithm_id=algo_id,
                    timestamp=frame_timestamp,
                    has_detection=has_detection,
                    image_path=image_path
                )
            
            logger.info(f"[WorkflowWorker] 收到来自算法节点 {node_id} 的处理结果，检测到目标: {has_detection}")
            
            if has_detection:
                trigger_time = time.time()
                
                window_passed, window_stats = self.window_detector.check_condition(
                    source_id=self.video_source.id,
                    algorithm_id=algo_id,
                    current_time=trigger_time
                )
                
                if not window_passed:
                    if window_stats:
                        logger.info(
                            f"[WorkflowWorker] 算法节点 {node_id} 检测到目标，但窗口条件未满足 "
                            f"(检测: {window_stats['detection_count']}/{window_stats['total_count']} 帧, "
                            f"比例: {window_stats['detection_ratio']:.2%}, "
                            f"连续: {window_stats['max_consecutive']} 次)，跳过本次告警"
                        )
                    return None
                
                if window_stats:
                    logger.info(
                        f"[WorkflowWorker] 算法节点 {node_id} 满足窗口条件 "
                        f"(检测: {window_stats['detection_count']}/{window_stats['total_count']} 帧, "
                        f"比例: {window_stats['detection_ratio']:.2%}, "
                        f"连续: {window_stats['max_consecutive']} 次)"
                    )
                
                time_since_last_alert = trigger_time - self.algo_last_alert_time[node_id]
                if time_since_last_alert < ALERT_SUPPRESSION_DURATION:
                    logger.info(
                        f"[WorkflowWorker] 算法节点 {node_id} 满足窗口条件，但处于告警抑制期 "
                        f"(距上次告警 {time_since_last_alert:.1f}秒，需 {ALERT_SUPPRESSION_DURATION}秒)，跳过本次告警"
                    )
                    return None
                
                self.algo_last_alert_time[node_id] = trigger_time
                
                detection_records = self.window_detector.get_detection_records(
                    source_id=self.video_source.id,
                    algorithm_id=algo_id,
                    current_time=trigger_time
                )
                
                logger.info(f"[WorkflowWorker] 窗口内检测到 {len(detection_records)} 次目标")
                
                detection_images = []
                for i, (timestamp, has_detection, img_path) in enumerate(detection_records):
                    if has_detection and img_path:
                        img_ori_path = f"{img_path}.ori.jpg"
                        detection_images.append({
                            'image_path': img_path,
                            'image_ori_path': img_ori_path,
                            'timestamp': timestamp,
                            'detection_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
                        })
                
                if not detection_images:
                    filepath = f"{self.video_source.source_code}/{algorithm_name}/frame_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
                    filepath_absolute = os.path.join(FRAME_SAVE_PATH, filepath)
                    algo.visualize(frame, result.get("detections"), 
                                 save_path=filepath_absolute, label_color=label_color,
                                 roi_mask=roi_mask)
                    
                    filepath_ori = f"{filepath}.ori.jpg"
                    filepath_ori_absolute = os.path.join(FRAME_SAVE_PATH, filepath_ori)
                    save_frame(frame, filepath_ori_absolute)
                    
                    detection_images.append({
                        'image_path': filepath,
                        'image_ori_path': filepath_ori,
                        'timestamp': frame_timestamp
                    })
                
                main_image = detection_images[-1]['image_path'] if detection_images else ""
                main_image_ori = detection_images[-1]['image_ori_path'] if detection_images else ""
                
                alert = Alert.create(
                    video_source=self.video_source,
                    alert_time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    alert_type=self.algorithm_datamap[node_id].get('name'),
                    alert_message="",
                    alert_image=main_image,
                    alert_image_ori=main_image_ori,
                    alert_video="",
                    detection_count=len(detection_images),
                    window_stats=json.dumps(window_stats) if window_stats else None,
                    detection_images=json.dumps(detection_images) if detection_images else None
                )
                logger.info(f"[WorkflowWorker] 算法节点 {node_id} 触发警报，检测序列包含 {len(detection_images)} 张图片。")
                
                if self.video_recorder:
                    try:
                        video_path = self.video_recorder.start_recording(
                            source_id=self.video_source.id,
                            alert_id=alert.id,
                            trigger_time=trigger_time,
                            pre_seconds=PRE_ALERT_DURATION,
                            post_seconds=POST_ALERT_DURATION
                        )
                        alert.alert_video = video_path
                        alert.save()
                        logger.info(f"[WorkflowWorker] 已启动视频录制任务，预计保存到: {video_path}")
                    except Exception as rec_err:
                        logger.error(f"[WorkflowWorker] 启动视频录制失败: {rec_err}", exc_info=True)
                
                try:
                    alert_message = format_alert_message(alert)
                    if publish_alert_to_rabbitmq(alert_message):
                        logger.info(f"[WorkflowWorker] 预警消息已成功发布到RabbitMQ: {alert.id}")
                    else:
                        logger.warning(f"[WorkflowWorker] 预警消息发布到RabbitMQ失败: {alert.id}")
                except Exception as e:
                    logger.error(f"[WorkflowWorker] 发布预警消息到RabbitMQ时发生错误: {e}")
            
            return {'node_id': node_id, 'result': result}
        
        except Exception as exc:
            logger.error(f"[WorkflowWorker] 错误：算法节点 {node_id} 在处理过程中发生异常: {exc}")
            logger.exception(exc, exc_info=True)
            return None
    
    def run(self):
        logger.info(f"[WorkflowWorker:{os.getpid()}] 已加载 {len(self.algorithms)} 个算法，开始处理 {self.video_source.buffer_name}")

        parallel_algo_nodes = self._get_parallel_algorithm_nodes()
        logger.info(f"[WorkflowWorker:{os.getpid()}] 检测到 {len(parallel_algo_nodes)} 个并行算法节点")

        last_processed_frame_time = 0
        frame_count = 0
        last_log_time = time.time()

        with ThreadPoolExecutor(max_workers=len(parallel_algo_nodes)) as executor:
            while True:
                frame_with_timestamp = self.buffer.peek_with_timestamp(-1)

                if frame_with_timestamp is not None:
                    latest_frame, frame_timestamp = frame_with_timestamp

                    if frame_timestamp <= last_processed_frame_time:
                        time.sleep(0.05)
                        continue

                    last_processed_frame_time = frame_timestamp
                    current_time = time.time()

                    # 每秒输出一次帧读取日志
                    if current_time - last_log_time >= 1.0:
                        logger.info(f"[WorkflowWorker:{os.getpid()}] 正在读取帧... (已处理 {frame_count} 帧)")
                        last_log_time = current_time

                    algos_to_process = {}
                    for node_id in parallel_algo_nodes:
                        interval = self.algorithm_datamap[node_id].get('interval_seconds', 1)
                        if current_time - self.algo_last_process_time[node_id] >= interval:
                            algos_to_process[node_id] = self.algorithms[node_id]
                            self.algo_last_process_time[node_id] = current_time

                    if not algos_to_process:
                        frame_count += 1
                        time.sleep(0.01)
                        continue

                    logger.info(f"[WorkflowWorker:{os.getpid()}] 收到第 {frame_count} 帧，提交给 {len(algos_to_process)} 个算法处理...")
                    future_to_node = {
                        executor.submit(self._process_algorithm, node_id, latest_frame, frame_timestamp): node_id
                        for node_id in algos_to_process.keys()
                    }

                    for future in as_completed(future_to_node):
                        node_id = future_to_node[future]
                        try:
                            result = future.result()
                        except Exception as exc:
                            logger.error(f"[WorkflowWorker] 算法节点 {node_id} 执行异常: {exc}")

                    frame_count += 1
                else:
                    # buffer 为空时，sleep 稍长一点
                    time.sleep(0.1)


def main(args):
    logger.info(f"[WorkflowWorker:{os.getpid()}] 工作流工作进程启动，参数: {args}")
    workflow_id = args.workflow_id
    executor = WorkflowExecutor(workflow_id)
    executor.run()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workflow-id', required=True, help="Workflow ID")
    args = parser.parse_args()
    main(args)

