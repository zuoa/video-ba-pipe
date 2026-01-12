"""
工作流执行器 - 负责执行工作流的拓扑排序和节点调度
可以被实时执行模式和测试模式复用
"""
import json
import logging
import os
import time
import numpy as np
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import resource_tracker
from typing import Dict, List, Any, Optional

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
from app.core.workflow_types import create_node_data, NodeContext, SourceNodeData, AlgorithmNodeData, RoiDrawNodeData, FunctionNodeData, ConditionNodeData, OutputNodeData, AlertNodeData
from app.core.execution_log_collector import ExecutionLogCollector


class WorkflowExecutor:
    """
    工作流执行器 - 统一的执行引擎

    核心设计原则：
    1. 一套核心执行逻辑，测试和运行使用相同的代码路径
    2. test_mode 只控制副作用（数据库写入、视频录制等），不改变执行流程
    3. 避免代码重复，修改一次即可同时影响测试和运行
    """

    def __init__(self, workflow_id, test_mode=False):
        """
        初始化工作流执行器

        Args:
            workflow_id: 工作流ID
            test_mode: 是否为测试模式（测试模式下不初始化视频源和buffer，不产生副作用）
        """
        self.workflow_id = workflow_id
        self.test_mode = test_mode
        self.workflow = Workflow.get_by_id(workflow_id)
        self.workflow_data = self.workflow.data_dict

        # 创建节点数据字典
        workflow_nodes = self.workflow_data.get('nodes', [])
        logger.info(f"[WorkflowExecutor:{os.getpid()}] 工作流包含 {len(workflow_nodes)} 个节点")

        self.nodes = {}
        for n in workflow_nodes:
            try:
                node = create_node_data(n)
                self.nodes[n['id']] = node
                logger.debug(f"[WorkflowExecutor:{os.getpid()}] 成功创建节点: {n['id']} (类型: {n.get('type')})")
            except Exception as e:
                logger.error(f"[WorkflowExecutor:{os.getpid()}] 创建节点失败: {n['id']}, 错误: {e}")

        logger.info(f"[WorkflowExecutor:{os.getpid()}] 成功加载 {len(self.nodes)} 个节点")

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

        # ========== 执行状态追踪 ==========
        # node_results_cache: 存储节点执行结果（用于下游节点访问）
        self.node_results_cache = {}
        # execution_results: 记录执行状态（用于测试结果收集）
        self.execution_results = {}
        # executed_nodes: 记录实际执行过的节点（按顺序）
        self.executed_nodes = []
        # last_frame_timestamp: 记录最后处理的帧时间戳（用于多 workflow 共享 buffer 时避免重复处理）
        self.last_frame_timestamp = None

        self.node_handlers = {
            'source': self._handle_source_node,
            'algorithm': self._handle_algorithm_node,
            'condition': self._handle_condition_node,
            'output': self._handle_output_node,
            'roi_draw': self._handle_roi_draw_node,
            'roi': self._handle_roi_draw_node,  # 支持前后端两种类型名称
            'alert': self._handle_output_node,
            'function': self._handle_function_node,
        }

        self._build_execution_graph()

        # 只在非测试模式下初始化视频源和buffer
        if not test_mode:
            self._init_resources()
            logger.info(f"[WorkflowExecutor] 实时模式：已初始化视频源和buffer")
        else:
            logger.info(f"[WorkflowExecutor] 测试模式：跳过视频源和buffer初始化")
            # 测试模式下仍需要加载算法
            self._load_algorithms()

        self.node_last_exec_time = {}
        for node_id in self.nodes.keys():
            self.node_last_exec_time[node_id] = 0

    def _load_algorithms(self):
        """加载算法节点所需的算法（供测试模式和实时模式使用）"""
        for node_id, node in self.nodes.items():
            # 只加载算法节点，函数节点有独立的处理逻辑
            if node.node_type == 'algorithm':
                algo_id = node.data_id
                logger.info(f"[WorkflowExecutor] 检查算法节点 {node_id}, data_id: {algo_id}")

                if algo_id:
                    try:
                        algo = Algorithm.get_by_id(algo_id)

                        # 从工作流数据中获取完整的 node_data
                        node_data_dict = next((n for n in self.workflow_data.get('nodes', []) if n['id'] == node_id), {})

                        if not node_data_dict:
                            logger.warning(f"[WorkflowExecutor] 节点 {node_id} 在工作流数据中未找到配置")

                        # 获取节点配置（用户在工作流编辑器中配置的）
                        # 注意：当 config=None 时，get() 不会使用默认值，需要额外判断
                        node_config = node_data_dict.get('config')
                        if node_config is None:
                            node_config = {}

                        # 获取运行时配置（从节点配置）
                        runtime_config = self._get_node_runtime_config(node_data_dict)

                        # 提取各项配置
                        interval_seconds = runtime_config['interval_seconds']
                        runtime_timeout = runtime_config['runtime_timeout']
                        memory_limit_mb = runtime_config['memory_limit_mb']
                        label_config = runtime_config['label']
                        roi_regions = runtime_config['roi_regions']

                        # 存储ROI配置
                        self.algorithm_roi_configs[node_id] = roi_regions
                        if roi_regions:
                            logger.info(f"[WorkflowExecutor] 算法节点 {node_id} 配置了 {len(roi_regions)} 个ROI热区")
                        else:
                            logger.info(f"[WorkflowExecutor] 算法节点 {node_id} 未配置ROI热区，将使用全画面检测")

                        # 构建完整配置（算法固有配置 + 节点配置 + 运行时配置）
                        full_config = {
                            "id": algo_id,
                            "name": algo.name,
                            "source_id": getattr(self.video_source, 'id', 0),  # 测试模式下为 0
                            "script_path": algo.script_path,
                            "entry_function": 'process',
                            # 运行时配置
                            "interval_seconds": interval_seconds,
                            "runtime_timeout": runtime_timeout,
                            "memory_limit_mb": memory_limit_mb,
                            # 标签配置
                            "label_name": label_config['name'],
                            "label_color": label_config['color'],
                        }

                        # 合并算法固有配置（script_config）
                        full_config.update(algo.config_dict)

                        # 合并节点配置（用户在工作流编辑器中配置的，如 models 等）
                        full_config.update(node_config)

                        logger.info(f"[WorkflowExecutor] 节点 {node_id} 合并后的完整配置 models: {full_config.get('models', 'NOT_FOUND')}")

                        self.algorithms[node_id] = ScriptAlgorithm(full_config)

                        # 存储算法元数据（用于后续访问）
                        self.algorithm_datamap[node_id] = {
                            'id': algo_id,
                            'name': algo.name,
                            'interval_seconds': interval_seconds,
                            'label_name': label_config['name'],
                            'label_color': label_config['color']
                        }

                        self.algorithm_configs[node_id] = {
                            'algorithm_id': algo_id,
                            'node_id': node_id,
                            'runtime_config': runtime_config
                        }

                        logger.info(f"[WorkflowExecutor] 成功加载算法: {algo.name}, 脚本路径: {algo.script_path}, 节点ID: {node_id}")
                    except Exception as e:
                        logger.error(f"[WorkflowExecutor] 加载算法节点 {node_id} 失败: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    logger.warning(f"[WorkflowExecutor] 算法节点 {node_id} 没有 data_id，跳过加载")

    def _get_node_runtime_config(self, node_data_dict):
        """
        从节点配置中提取运行时配置

        Args:
            node_data_dict: 节点的 data_dict（来自 workflow JSON）

        Returns:
            运行时配置字典
        """
        # 配置可能存储在两个地方：
        # 1. 直接在 node_data_dict 中（旧格式）
        # 2. 在 node_data_dict['config'] 中（新格式）
        config = node_data_dict.get('config', {}) if isinstance(node_data_dict.get('config'), dict) else {}

        # 兼容两种 label 格式：
        # 1. 新格式: label_name + label_color (在 config 中)
        # 2. 旧格式: label (直接在 node_data_dict 中)
        if 'label_name' in config or 'label_color' in config:
            label_config = {
                'name': config.get('label_name', 'Object'),
                'color': config.get('label_color', '#FF0000')
            }
        elif 'label_name' in node_data_dict or 'label_color' in node_data_dict:
            label_config = {
                'name': node_data_dict.get('label_name', 'Object'),
                'color': node_data_dict.get('label_color', '#FF0000')
            }
        else:
            label_config = node_data_dict.get('label', {
                'name': 'Object',
                'color': '#FF0000'
            })

        return {
            'interval_seconds': config.get('interval_seconds', node_data_dict.get('interval_seconds', 1.0)),
            'runtime_timeout': config.get('runtime_timeout', node_data_dict.get('runtime_timeout', 30)),
            'memory_limit_mb': config.get('memory_limit_mb', node_data_dict.get('memory_limit_mb', 512)),
            'label': label_config,
            'roi_regions': config.get('roi_regions', node_data_dict.get('roi_regions', []))
        }

    def _build_execution_graph(self):
        for conn in self.connections:
            from_id = conn['from']
            to_id = conn['to']

            # condition: 连线中的条件配置
            # - None: 无条件，直接通过
            # - 'true'/'yes': 条件节点的 true 分支
            # - 'false'/'no': 条件节点的 false 分支
            # - 'detected': 检测到目标（向后兼容）
            # - 'not_detected': 未检测到目标（向后兼容）
            condition = conn.get('condition')

            # from_port 只是端口名称，不应该作为条件判断
            # 只有当 condition 明确设置时才使用

            self.execution_graph[from_id].append({
                'target': to_id,
                'condition': condition
            })

        for node_id, node in self.nodes.items():
            if node.node_type == 'source':
                self.source_node = node
                break
    
    def _init_resources(self):
        if not self.source_node:
            raise ValueError("Workflow must have a source node")

        source_id = self.source_node.data_id
        if not source_id:
            raise ValueError("Source node must have data_id")

        self.video_source = VideoSource.get_by_id(source_id)
        buffer_name = self.video_source.buffer_name

        logger.info(f"[WorkflowExecutor:{os.getpid()}] 启动 Workflow {self.workflow.name} (ID: {self.workflow_id})，处理视频源 {self.video_source.name} (ID: {self.video_source.source_code})")

        # 尝试连接到共享内存缓冲区，带重试机制
        max_retries = 10
        retry_interval = 1.0  # 秒

        for attempt in range(1, max_retries + 1):
            try:
                self.buffer = VideoRingBuffer(
                    name=buffer_name,
                    create=False,
                    frame_shape=(self.video_source.source_decode_height, self.video_source.source_decode_width, 3),
                    fps=self.video_source.source_fps,
                    duration_seconds=RINGBUFFER_DURATION
                )
                logger.info(f"已连接到缓冲区: {buffer_name} (fps={self.video_source.source_fps}, duration={RINGBUFFER_DURATION}s, capacity={self.buffer.capacity}, frame_shape={self.buffer.frame_shape})")
                break  # 成功连接，退出重试循环

            except FileNotFoundError:
                if attempt < max_retries:
                    logger.warning(
                        f"[WorkflowExecutor] 尝试 {attempt}/{max_retries}: "
                        f"缓冲区 /{buffer_name} 尚未就绪，等待 {retry_interval} 秒后重试..."
                    )
                    time.sleep(retry_interval)
                else:
                    # 最后一次尝试仍然失败
                    logger.error(f"[WorkflowExecutor] 无法连接到共享内存缓冲区: /{buffer_name}")
                    logger.error(f"[WorkflowExecutor] 这通常意味着视频源 {self.video_source.name} (ID={self.video_source.id}) 的 Decoder Worker 未运行或尚未创建缓冲区")
                    logger.error(f"[WorkflowExecutor] 请检查：")
                    logger.error(f"[WorkflowExecutor]   1. 视频源状态是否为 RUNNING（当前状态: {self.video_source.status}）")
                    logger.error(f"[WorkflowExecutor]   2. Decoder Worker 进程是否在运行（PID: {self.video_source.decoder_pid}）")
                    logger.error(f"[WorkflowExecutor]   3. Orchestrator 是否已正确启动该视频源")
                    raise

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

        # 加载算法
        self._load_algorithms()
    
    def _get_parallel_branch_nodes(self):
        branch_nodes = []
        for next_info in self.execution_graph.get(self.source_node.node_id, []):
            next_id = next_info['target']
            if next_id in self.nodes:
                branch_nodes.append(next_id)
        return branch_nodes

    def _calculate_node_indegrees(self):
        """计算每个节点的入度（前驱节点数量）"""
        indegrees = {node_id: 0 for node_id in self.nodes.keys()}
        for conn in self.connections:
            to_id = conn['to']
            if to_id in indegrees:
                indegrees[to_id] += 1
        return indegrees

    def _calculate_node_indegrees_for_subset(self, node_subset):
        """计算节点子集中每个节点的入度（只计算子集内的前驱节点）"""
        indegrees = {node_id: 0 for node_id in node_subset}
        for conn in self.connections:
            from_id = conn['from']
            to_id = conn['to']
            # 只统计两端都在子集中的连接
            if to_id in indegrees and from_id in node_subset:
                indegrees[to_id] += 1
        return indegrees

    def _get_node_dependencies(self, node_id):
        """获取节点的所有依赖节点（前驱节点）"""
        dependencies = []
        for conn in self.connections:
            if conn['to'] == node_id:
                from_id = conn['from']
                if from_id in self.nodes:
                    dependencies.append(from_id)
        return dependencies

    def _build_topology_levels(self):
        """
        构建拓扑层级
        返回: [[level0_nodes], [level1_nodes], [level2_nodes], ...]
        例如: [[source], [algo1, algo2, algo3], [function]]

        注意：不包含 alert 和 output 节点，因为它们会由上游节点通过 _execute_branch 自动执行
        """
        levels = []
        # 排除会被自动执行的节点（alert, output）
        remaining_nodes = {
            node_id for node_id in self.nodes.keys()
            if not isinstance(self.nodes[node_id], (OutputNodeData, AlertNodeData))
        }
        level_indegrees = self._calculate_node_indegrees_for_subset(remaining_nodes)

        while remaining_nodes:
            # 找出当前入度为0的节点（可以执行的节点）
            current_level = []
            for node_id in list(remaining_nodes):
                if level_indegrees.get(node_id, 0) == 0:
                    current_level.append(node_id)

            if not current_level:
                # 如果没有入度为0的节点，说明存在循环依赖
                logger.warning(f"[WorkflowWorker] 检测到循环依赖，剩余节点: {remaining_nodes}")
                # 强制添加剩余节点到当前层级
                current_level = list(remaining_nodes)

            # 按节点类型排序（source -> algorithm/roi_draw -> function -> condition -> output/alert）
            current_level.sort(key=lambda nid: self._get_node_type_priority(nid))

            levels.append(current_level)
            logger.info(f"[WorkflowWorker] 拓扑层级 {len(levels)-1}: {[f'{nid}({self.nodes[nid].node_type})' for nid in current_level]}")

            # 更新入度：移除当前层级的节点
            for node_id in current_level:
                remaining_nodes.remove(node_id)
                # 更新后继节点的入度
                for next_info in self.execution_graph.get(node_id, []):
                    next_id = next_info['target']
                    if next_id in level_indegrees:
                        level_indegrees[next_id] -= 1

        return levels

    def _get_node_type_priority(self, node_id):
        """获取节点类型的优先级（用于排序）"""
        node = self.nodes.get(node_id)
        if not node:
            return 999

        priority_map = {
            'source': 0,
            'roi_draw': 1,
            'roi': 1,  # 支持前后端两种类型名称
            'algorithm': 2,
            'function': 3,
            'condition': 4,
            'output': 5,
            'alert': 5
        }
        return priority_map.get(node.node_type, 999)

    def _can_execute_level_parallel(self, level_nodes):
        """
        判断某个层级的节点是否可以并行执行
        函数节点需要等待所有上游节点完成，不能并行
        """
        for node_id in level_nodes:
            node = self.nodes.get(node_id)
            if isinstance(node, FunctionNodeData):
                # 函数节点需要等待上游，不能与其他函数节点并行
                return False
        return True

    def _check_function_node_ready(self, node_id):
        """检查函数节点的所有上游节点是否都已执行完成

        只检查实际连线的上游节点（connections），忽略 input_nodes 配置
        因为前端可能配置了多个 input_nodes，但实际只连线了一部分
        """
        node = self.nodes.get(node_id)
        if not isinstance(node, FunctionNodeData):
            return True

        # 只从连线中获取上游节点（忽略 input_nodes 配置）
        connected_upstream = []
        for conn in self.connections:
            if conn['to'] == node_id:
                from_node_id = conn['from']
                connected_upstream.append(from_node_id)

        if not connected_upstream:
            # 没有连线，静默返回 False
            return False

        # 检查所有连线的上游节点是否都已完成
        for upstream_id in connected_upstream:
            if upstream_id not in self.node_results_cache:
                # 上游节点未完成（可能因为执行间隔跳过），静默返回 False
                return False

        return True
    
    def _get_node_interval(self, node_id):
        node = self.nodes.get(node_id)
        if not node:
            return 0

        if isinstance(node, AlgorithmNodeData) and node.interval_seconds is not None:
            return node.interval_seconds

        if node_id in self.algorithms:
            return self.algorithm_datamap[node_id].get('interval_seconds', 1)

        return 0
    
    def _should_execute_node(self, node_id):
        current_time = time.time()
        interval = self._get_node_interval(node_id)
        
        if interval <= 0:
            return True
        
        last_exec = self.node_last_exec_time.get(node_id, 0)
        if current_time - last_exec >= interval:
            self.node_last_exec_time[node_id] = current_time
            return True
        
        return False
    
    def _process_algorithm(self, node_id, frame, frame_timestamp, roi_regions=None, upstream_results=None):
        algo = self.algorithms.get(node_id)
        if not algo:
            logger.warning(f"[WorkflowWorker] 节点 {node_id} 不在 self.algorithms 中，已加载的算法节点: {list(self.algorithms.keys())}")
            return None

        try:
            effective_roi_regions = roi_regions if roi_regions is not None else self.algorithm_roi_configs.get(node_id, [])

            if roi_regions is not None:
                logger.info(f"[WorkflowWorker] 算法节点 {node_id} 使用context中的ROI配置，包含 {len(roi_regions)} 个区域")
                # 打印详细的ROI配置
                for idx, roi in enumerate(roi_regions):
                    polygon = roi.get('polygon', [])
                    if polygon and len(polygon) > 0:
                        logger.info(f"[WorkflowWorker] 算法节点 {node_id} ROI区域{idx + 1}: 顶点数={len(polygon)}, 数据={polygon[:2]}...")
            elif effective_roi_regions:
                logger.info(f"[WorkflowWorker] 算法节点 {node_id} 使用算法自带的ROI配置，包含 {len(effective_roi_regions)} 个区域")

            logger.info(f"[WorkflowWorker] 节点 {node_id} 调用 algo.process，upstream_results: {list(upstream_results.keys())} (共{len(upstream_results)}个上游)")
            logger.info(f"[WorkflowWorker] 节点 {node_id} 传递给算法的ROI配置: {effective_roi_regions}")
            result = algo.process(frame.copy(), effective_roi_regions, upstream_results=upstream_results)
            logger.info(f"[WorkflowWorker] 节点 {node_id} algo.process 返回: {result}")
            has_detection = bool(result and result.get("detections"))
            roi_mask = result.get('roi_mask')

            algo_id = self.algorithm_configs[node_id]['algorithm_id']
            algorithm_name = self.algorithm_datamap[node_id].get('name')
            label_color = self.algorithm_datamap[node_id].get('label_color', '#FF0000')

            logger.info(f"[WorkflowWorker] 算法节点 {node_id} 处理完成，检测到目标: {has_detection}")

            # 返回结果，包含节点 ID 和 label_color（用于下游 Alert 节点）
            return {
                'node_id': node_id,
                'has_detection': has_detection,
                'result': result,
                'frame': frame,
                'frame_timestamp': frame_timestamp,
                'roi_mask': roi_mask,
                'label_color': label_color,
                'upstream_node_id': node_id  # 上游节点 ID 就是当前节点 ID
            }

        except Exception as exc:
            logger.error(f"[WorkflowWorker] 错误：算法节点 {node_id} 在处理过程中发生异常: {exc}")
            logger.exception(exc, exc_info=True)
            return None
    
    def _evaluate_condition(self, condition, context):
        """
        评估条件是否满足

        Args:
            condition: 连线中的条件配置，可能包含:
                - None: 无条件，直接通过
                - 'true'/'yes': 条件节点的 true 分支
                - 'false'/'no': 条件节点的 false 分支
                - 'detected': 检测到目标（向后兼容）
                - 'not_detected': 未检测到目标（向后兼容）
                - {'node_id': xxx, 'port': 'yes'/'no'}: 条件节点判断
            context: 上下文数据，包含:
                - has_detection: 是否检测到目标
                - result: 检测结果字典，包含 detections 列表

        Returns:
            bool: 条件是否满足
        """
        if not condition:
            logger.debug(f"[WorkflowWorker] 条件判断: 无条件，通过")
            return True

        # 向后兼容：处理旧的字符串类型条件
        if isinstance(condition, str):
            has_detection = context.get('has_detection', False)
            logger.debug(f"[WorkflowWorker] 条件判断(字符串): condition={condition}, has_detection={has_detection}")

            # 条件节点的分支
            if condition == 'true' or condition == 'yes':
                result = has_detection
                logger.info(f"[WorkflowWorker] 条件判断(条件节点true): has_detection={has_detection}, result={result}")
                return result
            if condition == 'false' or condition == 'no':
                result = not has_detection
                logger.info(f"[WorkflowWorker] 条件判断(条件节点false): has_detection={has_detection}, result={result}")
                return result
            # 旧的条件格式（向后兼容）
            if condition == 'detected':
                result = has_detection
                logger.info(f"[WorkflowWorker] 条件判断(旧格式detected): has_detection={has_detection}, result={result}")
                return result
            if condition == 'not_detected':
                result = not has_detection
                logger.info(f"[WorkflowWorker] 条件判断(旧格式not_detected): has_detection={has_detection}, result={result}")
                return result
            # 未知条件，默认通过
            logger.warning(f"[WorkflowWorker] 条件判断: 未知条件 '{condition}'，默认通过")
            return True

        # 新逻辑：条件节点判断
        # 从 result 中获取检测数量
        result = context.get('result', {})
        detections = result.get('detections', [])
        detection_count = len(detections)

        # 获取条件节点配置
        node_id = condition.get('node_id')
        if node_id and node_id in self.nodes:
            node = self.nodes[node_id]
            if isinstance(node, ConditionNodeData):
                target_count = node.target_count
                comparison_type = node.comparison_type

                logger.info(
                    f"[WorkflowWorker] 条件判断(节点): node_id={node_id}, "
                    f"detection_count={detection_count}, target_count={target_count}, "
                    f"comparison_type={comparison_type}"
                )

                if comparison_type == "==":
                    # 精确匹配：数量必须等于阈值
                    passed = detection_count == target_count
                    logger.info(f"[WorkflowWorker] 条件判断结果: {detection_count} == {target_count} = {passed}")
                    return passed
                elif comparison_type == ">=":
                    # 大于等于：数量至少达到阈值
                    passed = detection_count >= target_count
                    logger.info(f"[WorkflowWorker] 条件判断结果: {detection_count} >= {target_count} = {passed}")
                    return passed

        # 默认：根据是否有检测结果判断
        passed = detection_count > 0
        logger.info(f"[WorkflowWorker] 条件判断(默认): detection_count={detection_count}, passed={passed}")
        return passed
    
    def _handle_source_node(self, node_id, context):
        return context
    
    def _find_upstream_roi(self, node_id):
        """
        查找上游节点的ROI配置

        从当前节点的直接上游开始，递归查找最近的ROI节点配置。
        这样可以让ROI节点只影响其下游分支，避免污染全局context。

        Args:
            node_id: 当前节点ID

        Returns:
            list: ROI配置列表，如果上游没有ROI节点则返回None
        """
        # 获取当前节点的直接上游节点
        connected_upstream = []
        for conn in self.connections:
            if conn['to'] == node_id:
                connected_upstream.append(conn['from'])

        # 递归检查每个上游分支
        for upstream_id in connected_upstream:
            upstream_node = self.nodes.get(upstream_id)
            if not upstream_node:
                continue

            # 如果上游节点是ROI节点，直接返回其配置
            if isinstance(upstream_node, RoiDrawNodeData) and upstream_node.roi_regions:
                logger.info(f"[WorkflowWorker] 节点 {node_id} 找到上游ROI节点 {upstream_id}，包含 {len(upstream_node.roi_regions)} 个区域")
                return upstream_node.roi_regions

            # 递归检查上游的上游（深度优先，找到第一个ROI就停止）
            upstream_roi = self._find_upstream_roi(upstream_id)
            if upstream_roi:
                return upstream_roi

        # 所有上游分支都没有找到ROI
        return None

    def _handle_algorithm_node(self, node_id, context):
        frame = context.get('frame')
        frame_timestamp = context.get('frame_timestamp')
        log_collector = context.get('log_collector')  # 获取日志收集器

        if frame is None:
            if log_collector:
                log_collector.add_warning(node_id, "输入帧为空")
            return None

        # 优先从上游获取ROI配置，只有上游没有ROI时才使用全局context
        upstream_roi = self._find_upstream_roi(node_id)
        if upstream_roi is not None:
            roi_regions = upstream_roi
            logger.info(f"[WorkflowWorker] 算法节点 {node_id} 使用上游ROI配置，包含 {len(roi_regions)} 个区域")
        else:
            # 向后兼容：使用全局context中的ROI配置
            roi_regions = context.get('roi_regions')
            if roi_regions:
                logger.info(f"[WorkflowWorker] 算法节点 {node_id} 使用全局context中的ROI配置，包含 {len(roi_regions)} 个区域")

        upstream_results = self._get_upstream_results(node_id)

        try:
            result = self._process_algorithm(node_id, frame, frame_timestamp, roi_regions, upstream_results)
            if result:
                self.node_results_cache[node_id] = result

                # 记录检测日志
                detection_count = len(result.get('result', {}).get('detections', []))
                if log_collector:
                    log_collector.add_info(
                        node_id,
                        f"检测到 {detection_count} 个目标",
                        metadata={'detection_count': detection_count}
                    )
                    logger.info(f"[WorkflowWorker] 算法节点 {node_id} 已记录日志: 检测到 {detection_count} 个目标")
            return result
        except Exception as e:
            if log_collector:
                log_collector.add_error(node_id, f"算法执行失败: {str(e)}")
                logger.info(f"[WorkflowWorker] 算法节点 {node_id} 已记录错误日志")
            logger.error(f"[WorkflowWorker] 算法节点 {node_id} 执行异常: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _handle_condition_node(self, node_id, context):
        """
        处理条件节点：评估条件并更新 context

        条件节点根据上游检测结果判断是否满足条件，并将结果存入 context
        """
        node = self.nodes.get(node_id)
        if not isinstance(node, ConditionNodeData):
            return context

        # 获取上游检测结果
        upstream_results = self._get_upstream_results(node_id)

        # 计算总的检测数量
        detection_count = 0
        for result in upstream_results.values():
            detection_count += len(result.get('detections', []))

        # 评估条件
        target_count = node.target_count
        comparison_type = node.comparison_type

        if comparison_type == '==':
            condition_passed = detection_count == target_count
        elif comparison_type == '>=':
            condition_passed = detection_count >= target_count
        elif comparison_type == '>':
            condition_passed = detection_count > target_count
        elif comparison_type == '<=':
            condition_passed = detection_count <= target_count
        elif comparison_type == '<':
            condition_passed = detection_count < target_count
        else:
            condition_passed = detection_count > 0

        # 更新 context（用于下游的条件判断）
        context['has_detection'] = condition_passed

        # 记录日志
        log_collector = context.get('log_collector')
        if log_collector:
            log_collector.add_info(
                node_id,
                f"条件判断: {detection_count} {comparison_type} {target_count} = {'✓ 通过' if condition_passed else '✗ 未通过'}",
                metadata={
                    'detection_count': detection_count,
                    'target_count': target_count,
                    'comparison_type': comparison_type,
                    'condition_passed': condition_passed
                }
            )

        logger.info(
            f"[WorkflowWorker] 条件节点 {node_id}: "
            f"{detection_count} {comparison_type} {target_count} = {condition_passed}"
        )

        return context
    
    def _handle_output_node(self, node_id, context):
        self._execute_output(node_id, context)
        return context

    def _handle_function_node(self, node_id, context):
        """处理函数节点：直接调用内置函数，不依赖 self.algorithms"""
        frame = context.get('frame')
        frame_timestamp = context.get('frame_timestamp')
        log_collector = context.get('log_collector')  # 获取日志收集器

        if frame is None:
            if log_collector:
                log_collector.add_warning(node_id, "输入帧为空")
            logger.warning(f"[WorkflowWorker] 函数节点 {node_id} 输入帧为空")
            return None

        # 获取节点配置（从 workflow_data 中读取完整配置）
        node_data_dict = next((n for n in self.workflow_data.get('nodes', []) if n['id'] == node_id), {})
        if not node_data_dict:
            if log_collector:
                log_collector.add_warning(node_id, "在工作流数据中未找到")
            logger.warning(f"[WorkflowWorker] 函数节点 {node_id} 在工作流数据中未找到")
            return None

        node_config = node_data_dict.get('config', {})
        function_name = node_config.get('function_name', 'area_ratio')

        logger.info(f"[WorkflowWorker] 函数节点 {node_id} 原始配置: {node_config}")
        logger.info(f"[WorkflowWorker] 函数节点 {node_id} 开始处理，函数类型: {function_name}")

        # 获取上游结果
        upstream_results = self._get_upstream_results(node_id)
        if not upstream_results:
            if log_collector:
                log_collector.add_warning(node_id, "没有上游结果")
            logger.warning(f"[WorkflowWorker] 函数节点 {node_id} 没有上游结果")
            return None

        logger.info(f"[WorkflowWorker] 函数节点 {node_id} 上游节点: {list(upstream_results.keys())}")

        # 导入内置函数模块
        try:
            from app.core.builtin_functions import BUILTIN_FUNCTIONS

            if function_name not in BUILTIN_FUNCTIONS:
                if log_collector:
                    log_collector.add_error(node_id, f"未知函数: {function_name}")
                logger.error(f"[WorkflowWorker] 未知函数: {function_name}")
                return None

            # 准备输入数据
            upstream_node_ids = list(upstream_results.keys())
            node_a_id = upstream_node_ids[0]
            result_a = upstream_results.get(node_a_id, {})
            detections_a = result_a.get('detections', [])

            logger.info(f"[WorkflowWorker] 函数节点 {node_id} 从节点 {node_a_id} 获取 {len(detections_a)} 个检测结果")

            # 判断是单输入还是双输入函数
            single_input_functions = ['height_ratio_frame', 'width_ratio_frame', 'area_ratio_frame', 'size_absolute']
            is_single_input = function_name in single_input_functions

            # 准备函数配置
            frame_height, frame_width = frame.shape[:2]
            function_config = {
                'threshold': node_config.get('threshold', 0.7),
                'operator': node_config.get('operator', 'less_than'),
                'frame_height': frame_height,
                'frame_width': frame_width,
                'dimension': node_config.get('dimension', 'height')
            }

            # 调用内置函数
            func = BUILTIN_FUNCTIONS[function_name]

            if is_single_input:
                # 单输入函数
                logger.info(f"[WorkflowWorker] 调用单输入函数 {function_name}")
                results = func(detections_a, [], function_config)

                # 收集匹配的检测框
                all_detections = []
                for r in results:
                    all_detections.append(r['object_a'])
            else:
                # 双输入函数
                if len(upstream_node_ids) < 2:
                    if log_collector:
                        log_collector.add_warning(node_id, f"双输入函数 {function_name} 需要两个上游节点，但只有 {len(upstream_node_ids)} 个")
                    logger.warning(f"[WorkflowWorker] 双输入函数 {function_name} 需要两个上游节点，但只有 {len(upstream_node_ids)} 个")
                    return None

                node_b_id = upstream_node_ids[1]
                result_b = upstream_results.get(node_b_id, {})
                detections_b = result_b.get('detections', [])

                logger.info(f"[WorkflowWorker] 调用双输入函数 {function_name}，节点A: {node_a_id}({len(detections_a)}个), 节点B: {node_b_id}({len(detections_b)}个)")

                results = func(detections_a, detections_b, function_config)

                # 收集匹配的检测框
                all_detections = []
                for r in results:
                    all_detections.append(r['object_a'])
                    all_detections.append(r['object_b'])

            logger.info(f"[WorkflowWorker] 函数节点 {node_id} 处理完成，匹配数: {len(results)}, 返回检测数: {len(all_detections)}")

            # 记录函数执行日志
            if log_collector:
                log_collector.add_info(
                    node_id,
                    f"函数 {function_name} 处理完成，匹配数: {len(results)}",
                    metadata={'function_name': function_name, 'matched_count': len(results)}
                )
                logger.info(f"[WorkflowWorker] 函数节点 {node_id} 已记录日志: 函数 {function_name} 处理完成，匹配数: {len(results)}")

            # 返回标准格式的结果
            result = {
                'node_id': node_id,
                'has_detection': len(all_detections) > 0,
                'result': {
                    'detections': all_detections,
                    'function_results': results,
                    'metadata': {
                        'function_name': function_name,
                        'matched_count': len(results)
                    }
                },
                'frame': frame,
                'frame_timestamp': frame_timestamp,
                'roi_mask': None,
                'label_color': '#00FF00',  # 函数节点使用绿色
                'upstream_node_id': node_a_id
            }

            self.node_results_cache[node_id] = result
            return result

        except Exception as exc:
            if log_collector:
                log_collector.add_error(node_id, f"处理异常: {str(exc)}")
            logger.error(f"[WorkflowWorker] 函数节点 {node_id} 处理异常: {exc}")
            import traceback
            traceback.print_exc()
            return None

    def _get_upstream_results(self, node_id):
        """
        获取上游节点的执行结果

        优先从连线（connections）中识别上游节点，如果没有则使用 input_nodes 配置

        Returns:
            dict: 上游节点结果字典，始终返回字典（可能为空）
        """
        upstream_results = {}

        # 优先从连线中获取上游节点（更可靠）
        for conn in self.connections:
            if conn['to'] == node_id:
                from_node_id = conn['from']
                if from_node_id in self.node_results_cache:
                    cached = self.node_results_cache[from_node_id]
                    # 只处理有 result 的缓存（source 节点没有 result）
                    if 'result' in cached:
                        upstream_results[from_node_id] = cached['result']

        # 如果连线中没有结果，回退到 input_nodes 配置（向后兼容）
        if not upstream_results:
            node = self.nodes.get(node_id)
            if isinstance(node, FunctionNodeData) and node.input_nodes:
                for input_node_id in node.input_nodes:
                    if input_node_id in self.node_results_cache:
                        cached = self.node_results_cache[input_node_id]
                        if 'result' in cached:
                            upstream_results[input_node_id] = cached['result']

        return upstream_results
    
    def _handle_roi_draw_node(self, node_id, context):
        frame = context.get('frame')
        if frame is None:
            logger.warning(f"[WorkflowWorker] 热区绘制节点 {node_id} 输入帧为空")
            return None

        node = self.nodes.get(node_id)
        if not isinstance(node, RoiDrawNodeData):
            logger.warning(f"[WorkflowWorker] 节点 {node_id} 不是RoiDrawNodeData类型")
            return context

        roi_regions = node.roi_regions
        if not roi_regions or len(roi_regions) == 0:
            logger.warning(f"[WorkflowWorker] 热区绘制节点 {node_id} 未配置ROI区域")
            return context

        # 不再写入全局context，避免污染同一层级的其他节点
        # ROI配置现在完全通过算法节点查找上游节点来获取
        # context['roi_regions'] = roi_regions

        # 打印详细的ROI配置信息
        for idx, roi in enumerate(roi_regions):
            polygon = roi.get('polygon', [])
            points = roi.get('points', [])

            # 优先使用polygon，其次points
            vertex_data = polygon if polygon else points

            if vertex_data:
                # 检查坐标格式
                if isinstance(vertex_data[0], dict):
                    # 相对坐标格式 [{"x": 0.1, "y": 0.2}, ...]
                    vertex_str = ", ".join([f"({p.get('x', 0):.3f}, {p.get('y', 0):.3f})" for p in vertex_data])
                    logger.info(
                        f"[WorkflowWorker] 热区绘制节点 {node_id} 区域{idx + 1}: "
                        f"名称={roi.get('name', '未命名')}, "
                        f"模式={roi.get('mode', 'N/A')}, "
                        f"顶点数={len(vertex_data)}, "
                        f"顶点坐标(相对): [{vertex_str}]"
                    )
                else:
                    # 绝对坐标格式 [[x1, y1], [x2, y2], ...]
                    vertex_str = ", ".join([f"({p[0]}, {p[1]})" for p in vertex_data])
                    logger.info(
                        f"[WorkflowWorker] 热区绘制节点 {node_id} 区域{idx + 1}: "
                        f"名称={roi.get('name', '未命名')}, "
                        f"模式={roi.get('mode', 'N/A')}, "
                        f"顶点数={len(vertex_data)}, "
                        f"顶点坐标(绝对): [{vertex_str}]"
                    )
            else:
                logger.warning(f"[WorkflowWorker] 热区绘制节点 {node_id} 区域{idx + 1}: 没有顶点数据")

        logger.info(f"[WorkflowWorker] 热区绘制节点 {node_id} 共配置了 {len(roi_regions)} 个ROI区域")
        logger.info(f"[WorkflowWorker] 热区绘制节点 {node_id} 完整ROI数据: {roi_regions}")

        return context
    
    def _execute_node(self, node_id, context):
        """
        执行单个节点（核心逻辑）

        测试和运行模式使用完全相同的执行路径
        """
        node = self.nodes.get(node_id)
        if not node:
            logger.warning(f"[WorkflowExecutor] 节点 {node_id} 不在 self.nodes 中，可用节点: {list(self.nodes.keys())}")
            return None

        if not self._should_execute_node(node_id):
            interval = self._get_node_interval(node_id)
            logger.debug(f"[WorkflowExecutor] 节点 {node_id} 未到执行间隔 ({interval}秒)，跳过")
            return None

        # ========== 追踪执行状态 ==========
        start_time = time.time()
        execution_success = True
        execution_error = None

        node_type = node.node_type
        handler = self.node_handlers.get(node_type)

        if not handler:
            # 从 context 中获取 log_collector 并记录警告
            log_collector = context.get('log_collector')
            if log_collector:
                log_collector.add_warning(node_id, f"未知节点类型: {node_type}")
            logger.warning(f"[WorkflowExecutor] 未知节点类型: {node_type} (节点 {node_id})")

            # 记录失败状态
            execution_success = False
            execution_error = f"未知节点类型: {node_type}"

            # 更新追踪状态
            self.execution_results[node_id] = {
                'success': False,
                'error': execution_error,
                'execution_time': int((time.time() - start_time) * 1000)
            }
            return context

        logger.info(f"[WorkflowExecutor] 执行节点 {node_id} (类型: {node_type})")

        try:
            result = handler(node_id, context)

            # 记录成功执行
            if node_id not in self.executed_nodes:
                self.executed_nodes.append(node_id)

            self.execution_results[node_id] = {
                'success': True,
                'execution_time': int((time.time() - start_time) * 1000)
            }

            return result

        except Exception as e:
            execution_success = False
            execution_error = str(e)
            logger.error(f"[WorkflowExecutor] 节点 {node_id} 执行异常: {e}")
            import traceback
            traceback.print_exc()

            # 记录失败状态
            self.execution_results[node_id] = {
                'success': False,
                'error': execution_error,
                'execution_time': int((time.time() - start_time) * 1000)
            }

            # 从 context 获取 log_collector 并记录错误
            log_collector = context.get('log_collector')
            if log_collector:
                log_collector.add_error(node_id, f"执行异常: {execution_error}")

            return None

    def _execute_single_node(self, node_id, context):
        """执行单个节点（不处理后续节点）"""
        return self._execute_node(node_id, context)

    def _execute_branch(self, node_id, context):
        """
        执行节点分支：从该节点开始，按连接关系递归执行所有下游节点
        用于算法节点和函数节点的分支执行
        """
        result = self._execute_node(node_id, context)
        if result is None:
            return

        # 更新 context 中的所有必要信息
        if isinstance(result, dict):
            # 提取检测结果和元数据
            has_detection = result.get('has_detection', False)
            result_data = result.get('result', {})

            # 更新 context 的核心字段
            context['has_detection'] = has_detection
            context['result'] = result_data

            # 更新可视化所需的重要字段
            if 'upstream_node_id' in result:
                context['upstream_node_id'] = result['upstream_node_id']
            if 'label_color' in result:
                context['label_color'] = result['label_color']
            if 'roi_mask' in result:
                context['roi_mask'] = result['roi_mask']

        next_nodes = self.execution_graph.get(node_id, [])
        for next_info in next_nodes:
            next_id = next_info['target']
            condition = next_info.get('condition')

            # 传递更新后的 context 进行条件判断
            if not self._evaluate_condition(condition, context):
                logger.info(f"[WorkflowWorker] {node_id} -> {next_id} 条件不满足: {condition}")
                continue

            logger.info(f"[WorkflowWorker] {node_id} -> {next_id} 条件满足，继续执行")
            # 继续执行下游节点
            self._execute_branch(next_id, context)

    def _execute_level_nodes(self, level_nodes, context, executor=None):
        """
        执行一个层级的所有节点
        - 如果可以并行且提供了executor，则并行执行
        - 否则串行执行
        """
        if not level_nodes:
            return

        # 检查是否可以并行执行
        can_parallel = self._can_execute_level_parallel(level_nodes) and executor is not None

        if can_parallel:
            # 并行执行当前层级的节点
            logger.info(f"[WorkflowWorker] 并行执行层级节点: {[f'{nid}({self.nodes[nid].node_type})' for nid in level_nodes]}")
            future_to_node = {
                executor.submit(self._execute_level_node, nid, context): nid
                for nid in level_nodes
            }

            for future in as_completed(future_to_node):
                node_id = future_to_node[future]
                try:
                    future.result()
                except Exception as exc:
                    logger.error(f"[WorkflowWorker] 节点 {node_id} 执行异常: {exc}", exc_info=True)
        else:
            # 串行执行当前层级的节点
            logger.info(f"[WorkflowWorker] 串行执行层级节点: {[f'{nid}({self.nodes[nid].node_type})' for nid in level_nodes]}")
            for node_id in level_nodes:
                self._execute_level_node(node_id, context)

    def _execute_level_node(self, node_id, context):
        """
        执行层级中的一个节点
        - 对于函数节点：检查上游是否完成，执行函数，不继续执行下游
        - 对于算法节点：执行算法，然后继续执行下游（形成分支）
        - 对于其他节点：直接执行
        """
        node = self.nodes.get(node_id)
        if not node:
            return

        # 对于函数节点，特殊处理
        if isinstance(node, FunctionNodeData):
            if not self._check_function_node_ready(node_id):
                # 上游节点未完成（可能因为执行间隔跳过），静默跳过
                return

            # 执行函数节点
            result = self._execute_single_node(node_id, context)
            if result is None:
                logger.debug(f"[WorkflowWorker] 函数节点 {node_id} 返回None")
                return

            # 函数节点执行完后，继续执行下游节点（通常是 alert）
            # 是否触发告警由 Alert 节点的条件判断决定，而不是在这里拦截
            # 更新 context 以便条件判断和可视化使用
            if isinstance(result, dict):
                has_detection = result.get('has_detection', False)
                result_data = result.get('result', {})
                context['has_detection'] = has_detection
                context['result'] = result_data

                # 更新可视化所需的重要字段
                if 'upstream_node_id' in result:
                    context['upstream_node_id'] = result['upstream_node_id']
                if 'label_color' in result:
                    context['label_color'] = result['label_color']
                if 'roi_mask' in result:
                    context['roi_mask'] = result['roi_mask']

            next_nodes = self.execution_graph.get(node_id, [])
            for next_info in next_nodes:
                next_id = next_info['target']
                condition = next_info.get('condition')
                if self._evaluate_condition(condition, context):
                    logger.info(f"[WorkflowWorker] 函数节点 {node_id} -> {next_id} 条件满足，继续执行")
                    # 传递原始 context 而不是 result，确保 log_collector 能被传递到 Alert 节点
                    self._execute_branch(next_id, context)
        elif isinstance(node, AlgorithmNodeData):
            # 算法节点：执行并继续执行下游（形成完整分支）
            self._execute_branch(node_id, context)
        else:
            # 其他节点（roi_draw, condition, output, alert）：直接执行单个节点
            self._execute_single_node(node_id, context)

    def _execute_by_topology_levels(self, executor, context):
        """
        按拓扑层级执行所有节点
        """
        levels = self._build_topology_levels()
        logger.info(f"[WorkflowWorker] 共有 {len(levels)} 个拓扑层级，开始按层级执行...")

        for level_idx, level_nodes in enumerate(levels):
            logger.info(f"[WorkflowWorker] 执行层级 {level_idx + 1}/{len(levels)}")

            # 特殊处理第一层（source节点）
            if level_idx == 0:
                # 第一层通常是source，直接执行
                for node_id in level_nodes:
                    self._execute_single_node(node_id, context)
            else:
                # 其他层级按并行或串行执行
                self._execute_level_nodes(level_nodes, context, executor)

    def _execute_output(self, node_id, context):
        """
        执行输出/告警节点

        Args:
            node_id: Alert 节点 ID
            context: 上下文数据，包含:
                - frame: 当前帧
                - frame_timestamp: 帧时间戳
                - has_detection: 是否检测到目标
                - result: 检测结果
                - roi_mask: ROI 掩码
                - label_color: 可视化颜色（可选）
                - upstream_node_id: 上游节点 ID（用于可视化）
                - log_collector: 日志收集器（用于生成告警消息）
        """
        # ========== 测试模式拦截 ==========
        # 测试模式下不访问 video_source、不创建数据库记录、不启动录制
        if self.test_mode:
            return self._execute_output_test_mode(node_id, context)
        # ========== 测试模式拦截结束 ==========

        # 获取日志收集器
        log_collector = context.get('log_collector')

        # 如果 context 中没有上游结果，从 node_results_cache 中获取
        if 'result' not in context or 'has_detection' not in context:
            # 查找 Alert 节点的上游节点
            upstream_node_id = None
            for conn in self.connections:
                if conn['to'] == node_id:
                    upstream_node_id = conn['from']
                    break

            if upstream_node_id and upstream_node_id in self.node_results_cache:
                cached_data = self.node_results_cache[upstream_node_id]
                # 将缓存的数据合并到 context
                context.update({
                    'result': cached_data.get('result'),
                    'has_detection': cached_data.get('has_detection'),
                    'roi_mask': cached_data.get('roi_mask'),
                    'label_color': cached_data.get('label_color', '#FF0000'),
                    'upstream_node_id': cached_data.get('upstream_node_id', upstream_node_id)
                })
                logger.info(f"[WorkflowWorker] Alert 节点 {node_id} 从缓存获取上游节点 {upstream_node_id} 的结果")

        # 再次检查必需数据
        if 'frame' not in context or 'result' not in context:
            if log_collector:
                log_collector.add_warning(node_id, "缺少必需数据：上游节点未产生结果")
            logger.warning(f"[WorkflowWorker] 输出节点 {node_id} 缺少必需数据：上游节点未执行或未产生结果")
            return

        # 获取 Alert 节点配置
        alert_node = self.nodes.get(node_id)
        if not isinstance(alert_node, AlertNodeData):
            if log_collector:
                log_collector.add_warning(node_id, "不是 Alert 节点")
            logger.warning(f"[WorkflowWorker] 节点 {node_id} 不是 Alert 节点")
            return

        # 从 context 获取检测数据
        has_detection = context.get('has_detection', False)
        frame = context['frame']
        frame_timestamp = context['frame_timestamp']
        result = context['result']
        roi_mask = context.get('roi_mask')
        label_color = context.get('label_color', '#FF0000')  # 默认红色

        logger.debug(
            f"[WorkflowWorker] Alert 节点 {node_id} 收到结果: has_detection={has_detection}, 检测数={len(result.get('detections', []))}")
        if not has_detection:
            logger.debug(f"[WorkflowWorker] Alert 节点 {node_id} 未检测到目标，跳过告警处理")
            return

        # 从 Alert 节点配置获取告警信息
        alert_type = alert_node.alert_type or "detection"
        alert_level = alert_node.alert_level or "info"

        # 使用日志收集器生成告警消息
        if log_collector:
            # 获取消息格式类型（默认 'detailed'）
            message_format = alert_node.message_format or 'detailed'

            logger.info(f"[WorkflowWorker] Alert节点 {node_id} 开始构建告警消息，格式: {message_format}")
            logger.info(f"[WorkflowWorker] 日志收集器 ID: {id(log_collector)}")
            logger.info(f"[WorkflowWorker] 日志收集器包含 {len(log_collector.logs)} 条日志")

            # 打印所有日志
            for idx, log in enumerate(log_collector.logs):
                logger.info(f"[WorkflowWorker] 日志 {idx + 1}: [{log['node_id']}] {log['content']}")

            # 构建执行详情消息
            execution_details = log_collector.build_alert_message(
                format_type=message_format,
                include_metadata=False
            )

            logger.info(f"[WorkflowWorker] 执行详情: {execution_details}")

            # 组合用户自定义消息和执行详情
            custom_message = alert_node.alert_message or ""
            if custom_message and execution_details and execution_details != "无执行日志":
                alert_message = f"{custom_message}\n\n执行详情:\n{execution_details}"
            elif execution_details and execution_details != "无执行日志":
                alert_message = execution_details
            else:
                alert_message = custom_message

            logger.info(f"[WorkflowWorker] 最终告警消息: {alert_message}")
        else:
            # 如果没有日志收集器，使用原始消息
            alert_message = alert_node.alert_message or ""
            logger.warning(f"[WorkflowWorker] Alert节点 {node_id} 没有日志收集器")

        # 加载触发条件配置（窗口检测）
        trigger_condition = alert_node.trigger_condition
        if trigger_condition:
            self.window_detector.load_trigger_condition(
                source_id=self.video_source.id,
                node_id=node_id,
                trigger_config=trigger_condition
            )
        else:
            # 未配置触发条件，使用默认配置（不进行窗口检测，直接通过）
            logger.info(f"[WorkflowWorker] 告警节点 {node_id} 未配置触发条件，所有检测都将触发")
            self.window_detector.load_trigger_condition(
                source_id=self.video_source.id,
                node_id=node_id,
                trigger_config={'enable': False}
            )

        # 加载告警抑制配置（触发后冷却期）
        suppression = alert_node.suppression
        if suppression:
            self.window_detector.load_suppression(
                source_id=self.video_source.id,
                node_id=node_id,
                suppression_config=suppression
            )
        else:
            # 未配置抑制，不启用抑制
            logger.info(f"[WorkflowWorker] 告警节点 {node_id} 未配置抑制，告警不会被抑制")
            self.window_detector.load_suppression(
                source_id=self.video_source.id,
                node_id=node_id,
                suppression_config={'enable': False}
            )



        trigger_time = time.time()

        # 记录本次检测结果到窗口检测器
        image_path = None
        if has_detection:
            # 如果检测到目标，可以保存检测图片（可选）
            pass  # 图片保存在后面

        # 记录到窗口检测器
        self.window_detector.add_record(
            source_id=self.video_source.id,
            node_id=node_id,
            timestamp=frame_timestamp,
            has_detection=has_detection,
            image_path=image_path
        )

        # 步骤1：检查触发条件（窗口检测）
        trigger_passed, trigger_stats = self.window_detector.check_condition(
            source_id=self.video_source.id,
            node_id=node_id,
            current_time=trigger_time
        )

        if not trigger_passed:
            if trigger_stats:
                logger.info(
                    f"[WorkflowWorker] 输出节点 {node_id} 不满足触发条件，跳过告警 "
                    f"(检测: {trigger_stats['detection_count']}/{trigger_stats['total_count']} 帧, "
                    f"比例: {trigger_stats['detection_ratio']:.2%}, "
                    f"连续: {trigger_stats['max_consecutive']} 次)"
                )
            return

        if trigger_stats:
            logger.info(
                f"[WorkflowWorker] 输出节点 {node_id} 满足触发条件 "
                f"(检测: {trigger_stats['detection_count']}/{trigger_stats['total_count']} 帧, "
                f"比例: {trigger_stats['detection_ratio']:.2%}, "
                f"连续: {trigger_stats['max_consecutive']} 次)"
            )

        # 步骤2：检查抑制期（触发后冷却期）
        not_suppressed, suppression_stats = self.window_detector.check_suppression(
            source_id=self.video_source.id,
            node_id=node_id,
            current_time=trigger_time
        )

        if not not_suppressed:
            # 在抑制期内，跳过告警
            if suppression_stats:
                logger.info(
                    f"[WorkflowWorker] 输出节点 {node_id} 在抑制期内，跳过告警 "
                    f"(剩余冷却时间: {suppression_stats['cooldown_remaining']:.2f}秒)"
                )
            return

        # 步骤3：记录触发时间（用于后续抑制计算）
        self.window_detector.record_trigger(
            source_id=self.video_source.id,
            node_id=node_id,
            trigger_time=trigger_time
        )

        # 获取窗口内的检测记录（用于保存检测图片）
        detection_records = self.window_detector.get_detection_records(
            source_id=self.video_source.id,
            node_id=node_id,
            current_time=trigger_time
        )

        logger.info(f"[WorkflowWorker] 窗口内检测到 {len(detection_records)} 次目标")

        # 处理检测图片
        detection_images = []
        for timestamp, has_det, img_path in detection_records:
            if has_det and img_path:
                img_ori_path = f"{img_path}.ori.jpg"
                detection_images.append({
                    'image_path': img_path,
                    'image_ori_path': img_ori_path,
                    'timestamp': timestamp,
                    'detection_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
                })

        # 如果没有检测图片，保存当前帧
        if not detection_images:
            filepath = f"{self.video_source.source_code}/{alert_type}/frame_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
            filepath_absolute = os.path.join(FRAME_SAVE_PATH, filepath)

            # 获取上游节点 ID（用于可视化）
            upstream_node_id = context.get('upstream_node_id')

            # 准备ROI配置：优先从上游ROI节点获取，其次使用算法节点配置
            effective_roi_regions = []
            if upstream_node_id:
                # 先查找上游的ROI节点配置
                upstream_roi = self._find_upstream_roi(upstream_node_id)
                if upstream_roi is not None:
                    effective_roi_regions = upstream_roi
                    logger.info(f"[WorkflowWorker] Alert可视化：使用上游ROI节点配置，包含 {len(effective_roi_regions)} 个区域")
                else:
                    # 回退到算法节点自身的ROI配置
                    effective_roi_regions = self.algorithm_roi_configs.get(upstream_node_id, [])
                    if effective_roi_regions:
                        logger.info(f"[WorkflowWorker] Alert可视化：使用算法节点配置，包含 {len(effective_roi_regions)} 个区域")

            # 如果有上游算法节点，使用其 visualize 方法
            if upstream_node_id and upstream_node_id in self.algorithms:
                self.algorithms[upstream_node_id].visualize(
                    frame, result.get("detections"),
                    save_path=filepath_absolute,
                    label_color=label_color,
                    roi_mask=roi_mask,
                    roi_regions=effective_roi_regions
                )
            else:
                # 没有上游算法节点，保存原始帧
                save_frame(frame, filepath_absolute)

            filepath_ori = f"{filepath}.ori.jpg"
            filepath_ori_absolute = os.path.join(FRAME_SAVE_PATH, filepath_ori)
            save_frame(frame, filepath_ori_absolute)

            detection_images.append({
                'image_path': filepath,
                'image_ori_path': filepath_ori,
                'timestamp': frame_timestamp,
                'detection_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(frame_timestamp))
            })

        main_image = detection_images[-1]['image_path'] if detection_images else ""
        main_image_ori = detection_images[-1]['image_ori_path'] if detection_images else ""

        # 创建告警记录
        logger.info(f"[WorkflowWorker] 准备创建 Alert，alert_message: {alert_message[:200] if alert_message else 'None'}...")
        alert = Alert.create(
            video_source=self.video_source,
            workflow=self.workflow,
            alert_time=time.strftime('%Y-%m-%d %H:%M:%S'),
            alert_type=alert_type,
            alert_level=alert_level,
            alert_message=alert_message,
            alert_image=main_image,
            alert_image_ori=main_image_ori,
            alert_video="",
            detection_count=len(detection_images),
            window_stats=json.dumps(trigger_stats) if trigger_stats else None,
            detection_images=json.dumps(detection_images) if detection_images else None
        )
        logger.info(f"[WorkflowWorker] Alert 创建成功，ID: {alert.id}")
        logger.info(f"[WorkflowWorker] 数据库中的 alert_message: {alert.alert_message[:200] if alert.alert_message else 'None'}...")
        logger.info(f"[WorkflowWorker] 输出节点 {node_id} 创建告警，类型: {alert_type}, 级别: {alert_level}, 检测序列包含 {len(detection_images)} 张图片")

        # 启动视频录制
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
                logger.info(f"[WorkflowWorker] 已启动视频录制任务: {video_path}")
            except Exception as rec_err:
                logger.error(f"[WorkflowWorker] 启动视频录制失败: {rec_err}", exc_info=True)

        # 发布到 RabbitMQ
        try:
            alert_message = format_alert_message(alert)
            if publish_alert_to_rabbitmq(alert_message):
                logger.info(f"[WorkflowWorker] 预警消息已发布到RabbitMQ: {alert.id}")
            else:
                logger.warning(f"[WorkflowWorker] 预警消息发布到RabbitMQ失败: {alert.id}")
        except Exception as e:
            logger.error(f"[WorkflowWorker] 发布预警消息到RabbitMQ时发生错误: {e}")

    def test_execute(self, test_frame: np.ndarray, test_image_bgr: np.ndarray = None):
        """
        测试模式执行：用单张图片测试工作流

        与运行模式使用完全相同的执行逻辑：
        - 调用相同的执行方法（_execute_by_topology_levels）
        - 使用相同的节点处理器（_handle_*_node）
        - 使用相同的条件判断（_evaluate_condition）

        唯一区别：test_mode=True 会拦截副作用操作

        Args:
            test_frame: RGB格式的测试图片
            test_image_bgr: BGR格式的测试图片（可选）

        Returns:
            测试结果字典，包含每个节点的执行结果
        """
        logger.info(f"[WorkflowTest] 开始测试工作流 {self.workflow_id}")

        # 使用BGR格式，如果没有提供则转换
        if test_image_bgr is None:
            test_image_bgr = cv2.cvtColor(test_frame, cv2.COLOR_RGB2BGR)

        # 创建日志收集器
        log_collector = ExecutionLogCollector()

        # 创建初始 context（与运行模式保持一致）
        context = {
            'frame': test_frame,
            'frame_bgr': test_image_bgr,
            'frame_timestamp': time.time(),
            'log_collector': log_collector,
            'roi_regions': [],
        }

        # ========== 清空执行状态 ==========
        self.execution_results.clear()
        self.executed_nodes.clear()
        self.node_results_cache.clear()

        # ========== 使用统一的执行逻辑（与运行模式完全相同） ==========
        # 注意：直接调用运行模式的执行方法，不需要任何特殊处理
        self._execute_by_topology_levels(executor=None, context=context)

        # ========== 收集测试结果 ==========
        final_result = self._collect_execution_results(context)

        logger.info(f"[WorkflowTest] 测试完成，共执行 {len(final_result['nodes'])} 个节点")
        return final_result

    def run(self):
        """
        实时运行模式主循环

        从 ring buffer 持续读取帧并执行工作流
        """
        if not self.buffer:
            logger.error(f"[WorkflowExecutor] 未初始化 buffer，无法运行")
            return

        logger.info(f"[WorkflowExecutor:{os.getpid()}] 开始实时运行工作流 {self.workflow_id}")

        # 创建日志收集器（每帧重新创建）
        import cv2

        frame_count = 0
        error_count = 0
        max_consecutive_errors = 10

        while True:
            try:
                # 从 buffer 读取最新帧
                # peek_with_timestamp(-1) 获取最新的帧和时间戳（不消费帧，支持多 workflow 共享）
                peek_result = self.buffer.peek_with_timestamp(-1)

                if peek_result is None:
                    # 缓冲区为空，短暂休眠后继续
                    time.sleep(0.01)
                    continue

                # Decoder 输出 rgb24 格式，实际是 RGB
                frame_rgb, frame_timestamp = peek_result

                # 检查是否为新的帧（避免重复处理同一帧）
                if self.last_frame_timestamp is not None and frame_timestamp == self.last_frame_timestamp:
                    # 帧未更新，短暂休眠后继续
                    time.sleep(0.001)  # 短暂休眠避免 CPU 占用过高
                    continue

                # 更新最后处理的帧时间戳
                self.last_frame_timestamp = frame_timestamp

                frame_count += 1
                error_count = 0  # 重置错误计数

                # 创建日志收集器
                log_collector = ExecutionLogCollector()

                # 创建 context
                context = {
                    'frame': frame_rgb,
                    'frame_bgr': cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR),
                    'frame_timestamp': frame_timestamp,
                    'log_collector': log_collector,
                    'roi_regions': [],
                }

                # 清空执行状态
                self.execution_results.clear()
                self.executed_nodes.clear()
                # 注意：不清空 node_results_cache，因为可能需要跨帧的数据

                # 执行工作流
                self._execute_by_topology_levels(executor=None, context=context)

                # 定期输出日志
                if frame_count % 100 == 0:
                    logger.info(f"[WorkflowExecutor] 已处理 {frame_count} 帧")

            except KeyboardInterrupt:
                logger.info(f"[WorkflowExecutor] 收到中断信号，停止运行")
                break
            except Exception as e:
                error_count += 1
                logger.error(f"[WorkflowExecutor] 处理帧时发生异常 (连续错误: {error_count}/{max_consecutive_errors}): {e}")
                import traceback
                traceback.print_exc()

                if error_count >= max_consecutive_errors:
                    logger.error(f"[WorkflowExecutor] 连续错误过多，停止运行")
                    break

                # 短暂休眠后继续
                time.sleep(0.1)

        logger.info(f"[WorkflowExecutor] 工作流 {self.workflow_id} 运行结束，共处理 {frame_count} 帧")

    def _collect_execution_results(self, context: dict) -> dict:
        """
        从执行状态中收集测试结果

        Args:
            context: 执行上下文

        Returns:
            测试结果字典
        """
        log_collector = context.get('log_collector')

        # 构建节点结果列表
        results = []
        for node_id in self.executed_nodes:
            node = self.nodes.get(node_id)
            if not node:
                continue

            # 获取执行状态
            exec_status = self.execution_results.get(node_id, {})

            # 构建测试结果格式
            node_result = {
                'node_id': node_id,
                'node_name': node.data.get('label', node_id) if hasattr(node, 'data') else node_id,
                'node_type': node.node_type,
                'success': exec_status.get('success', True),
                'execution_time': exec_status.get('execution_time', 0),
                'data': self._build_node_result_data(node_id, node, context)
            }

            # 添加错误信息（如果有）
            if 'error' in exec_status:
                node_result['error'] = exec_status['error']

            results.append(node_result)

        # 生成最终结果
        final_result = {
            'success': all(r.get('success', True) for r in results),
            'nodes': results,
            'logs': log_collector.logs if log_collector else [],
            'all_passed': len(results) > 0 and all(r.get('success', True) for r in results)
        }

        return final_result

    def _build_node_result_data(self, node_id: str, node, context: dict) -> dict:
        """
        构建节点结果数据（用于测试展示）

        Args:
            node_id: 节点ID
            node: 节点对象
            context: 执行上下文

        Returns:
            节点结果数据字典
        """
        node_type = node.node_type

        # Source 节点
        if node_type == 'source':
            return {'message': '视频源节点（测试模式）'}

        # ROI 节点
        elif node_type in ('roi_draw', 'roi'):
            roi_regions = getattr(node, 'roi_regions', [])
            return {
                'message': f'ROI热区配置，共 {len(roi_regions)} 个区域',
                'roi_regions': roi_regions,
                'roi_regions_count': len(roi_regions)
            }

        # 算法节点
        elif node_type == 'algorithm':
            if node_id in self.node_results_cache:
                cached = self.node_results_cache[node_id]
                detections = cached.get('result', {}).get('detections', [])

                result_data = {
                    'message': f'检测完成，检测到 {len(detections)} 个目标',
                    'detections': detections,
                    'detection_count': len(detections),
                    'roi_regions': context.get('roi_regions', []),
                    'roi_applied': len(context.get('roi_regions', [])) > 0,
                    'detections_detail': detections[:10],
                }

                # 添加 ROI 调试信息
                algo_metadata = cached.get('result', {}).get('metadata', {})
                if algo_metadata.get('detections_before_roi') is not None:
                    result_data['debug_info'] = {
                        'roi_configured': len(context.get('roi_regions', [])) > 0,
                        'roi_regions_count': len(context.get('roi_regions', [])),
                        'detections_before_roi': algo_metadata.get('detections_before_roi', len(detections)),
                        'roi_filtered_count': algo_metadata.get('roi_filtered_count', 0),
                        'detection_count': len(detections),
                        'roi_filter_enabled': len(context.get('roi_regions', [])) > 0
                    }

                return result_data
            else:
                return {'message': '算法未产生结果'}

        # 函数节点
        elif node_type == 'function':
            if node_id in self.node_results_cache:
                cached = self.node_results_cache[node_id]
                detections = cached.get('result', {}).get('detections', [])
                function_metadata = cached.get('result', {}).get('metadata', {})

                return {
                    'message': f"函数处理完成，匹配数: {function_metadata.get('matched_count', 0)}, 剩余 {len(detections)} 个目标",
                    'detections': detections,
                    'detection_count': len(detections),
                    'function_name': function_metadata.get('function_name', 'unknown'),
                    'matched_count': function_metadata.get('matched_count', 0)
                }
            else:
                return {'message': '函数未产生结果'}

        # 条件节点
        elif node_type == 'condition':
            target_count = getattr(node, 'target_count', 1)
            comparison_type = getattr(node, 'comparison_type', '>=')

            # 获取上游检测结果
            upstream_results = self._get_upstream_results(node_id)
            detection_count = sum(len(r.get('detections', [])) for r in upstream_results.values())

            # 判断条件
            if comparison_type == '==':
                condition_passed = detection_count == target_count
            elif comparison_type == '>=':
                condition_passed = detection_count >= target_count
            elif comparison_type == '>':
                condition_passed = detection_count > target_count
            elif comparison_type == '<=':
                condition_passed = detection_count <= target_count
            elif comparison_type == '<':
                condition_passed = detection_count < target_count
            else:
                condition_passed = detection_count > 0

            return {
                'message': f"条件配置: {comparison_type} {target_count} (当前: {detection_count} 个) - {'✓ 通过' if condition_passed else '✗ 未通过'}",
                'detection_count': detection_count,
                'target_count': target_count,
                'comparison_type': comparison_type,
                'condition_passed': condition_passed,
                'debug_info': {
                    'note': '条件节点定义判断规则',
                    'detection_count': detection_count,
                    'target_count': target_count,
                    'comparison_type': comparison_type,
                    'would_pass': condition_passed,
                    'condition_result': '通过' if condition_passed else '未通过'
                }
            }

        # Alert 节点
        elif node_type in ('alert', 'output'):
            log_collector = context.get('log_collector')
            has_detection = context.get('has_detection', False)

            # 从缓存获取检测数量
            detection_count = 0
            for conn in self.connections:
                if conn['to'] == node_id:
                    upstream_id = conn['from']
                    if upstream_id in self.node_results_cache:
                        cached = self.node_results_cache[upstream_id]
                        detection_count = len(cached.get('result', {}).get('detections', []))
                        break

            return {
                'message': f"告警输出: {detection_count} 个目标 → {'✓ 触发告警' if has_detection else '✗ 未触发'}",
                'detection_count': detection_count,
                'alert_triggered': has_detection,
                'debug_info': {
                    'has_detection': has_detection,
                    'detection_count': detection_count,
                    'alert_triggered': has_detection,
                    'trigger_reason': '满足触发条件' if has_detection else '不满足触发条件',
                    'log_count': len(log_collector.logs) if log_collector else 0
                }
            }

        else:
            return {'message': f'未知节点类型: {node_type}'}

    def _execute_output_test_mode(self, node_id: str, context: dict):
        """
        测试模式下的 Alert 节点处理

        与运行模式的区别：
        - 不访问 self.video_source（测试模式下为 None）
        - 不创建 Alert 数据库记录
        - 不启动视频录制
        - 不发布到 RabbitMQ
        - 只收集日志和模拟触发条件检查

        Args:
            node_id: Alert 节点 ID
            context: 上下文数据
        """
        log_collector = context.get('log_collector')

        # 如果 context 中没有上游结果，从 node_results_cache 中获取
        if 'result' not in context or 'has_detection' not in context:
            upstream_node_id = None
            for conn in self.connections:
                if conn['to'] == node_id:
                    upstream_node_id = conn['from']
                    break

            if upstream_node_id and upstream_node_id in self.node_results_cache:
                cached_data = self.node_results_cache[upstream_node_id]
                context.update({
                    'result': cached_data.get('result'),
                    'has_detection': cached_data.get('has_detection'),
                    'roi_mask': cached_data.get('roi_mask'),
                    'label_color': cached_data.get('label_color', '#FF0000'),
                    'upstream_node_id': cached_data.get('upstream_node_id', upstream_node_id)
                })

        # 检查必需数据
        if 'frame' not in context or 'result' not in context:
            if log_collector:
                log_collector.add_warning(node_id, "缺少必需数据：上游节点未产生结果")
            logger.warning(f"[WorkflowTest] 输出节点 {node_id} 缺少必需数据")
            return

        # 获取 Alert 节点配置
        alert_node = self.nodes.get(node_id)
        if not isinstance(alert_node, AlertNodeData):
            if log_collector:
                log_collector.add_warning(node_id, "不是 Alert 节点")
            return

        # 从 context 获取检测数据
        has_detection = context.get('has_detection', False)
        result = context['result']
        detection_count = len(result.get('detections', []))

        logger.info(f"[WorkflowTest] Alert 节点 {node_id} 测试结果: has_detection={has_detection}, 检测数={detection_count}")

        # 记录测试日志
        if log_collector:
            alert_type = alert_node.alert_type or "detection"
            alert_level = alert_node.alert_level or "info"

            # 获取消息格式类型
            message_format = alert_node.message_format or 'detailed'

            # 构建执行详情消息
            execution_details = log_collector.build_alert_message(
                format_type=message_format,
                include_metadata=False
            )

            # 组合消息
            custom_message = alert_node.alert_message or ""
            if custom_message and execution_details and execution_details != "无执行日志":
                alert_message = f"{custom_message}\n\n执行详情:\n{execution_details}"
            elif execution_details and execution_details != "无执行日志":
                alert_message = execution_details
            else:
                alert_message = custom_message

            # 记录告警消息（测试模式）
            log_collector.add_info(
                node_id,
                f"告警测试: {alert_type}/{alert_level} - {alert_message}",
                metadata={
                    'alert_type': alert_type,
                    'alert_level': alert_level,
                    'detection_count': detection_count,
                    'would_trigger': has_detection
                }
            )

            logger.info(f"[WorkflowTest] Alert 节点 {node_id} 测试消息: {alert_message[:200] if alert_message else 'None'}...")
