"""
工作流执行器 - 负责执行工作流的拓扑排序和节点调度
可以被实时执行模式和测试模式复用
"""
import base64
import inspect
import json
import logging
import os
import re
import time
import numpy as np
import requests
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from multiprocessing import resource_tracker
from typing import Collection, Dict, List, Any, Optional

from app import logger as main_logger
from app import logging
logger = logging.getLogger("workflow_executor")
from app.config import (
    ANALYSIS_BUFFER_SECONDS,
    ANALYSIS_TARGET_FPS,
    FRAME_SAVE_PATH,
    VIDEO_SAVE_PATH,
    RECORDING_BUFFER_DURATION,
    RECORDING_COMPRESSED_MAX_BYTES,
    RECORDING_JPEG_QUALITY,
    ALERT_SUPPRESSION_DURATION,
    LOG_SAVE_PATH,
    DETECTION_JSONL_LOG_ENABLED,
    VIDEO_FRAME_PIXEL_FORMAT,
    WORKFLOW_FRAME_LOGS_ENABLED,
    RESOURCE_PROFILING_ENABLED,
    RESOURCE_PROFILE_LOG_INTERVAL_SECONDS,
    SOURCE_ROTATION_DRAIN_GRACE_SECONDS,
    DETECTION_SNAPSHOT_ENABLED,
    DETECTION_SNAPSHOT_INTERVAL,
    DETECTION_SNAPSHOT_SAVE_PATH,
)
from app.core.compressed_ringbuffer import CompressedVideoRingBuffer
from app.core.cv2_compat import cv2, require_cv2
from app.core.algorithm import BaseAlgorithm
from app.core.frame_utils import (
    detect_frame_pixel_format,
    frame_to_bgr,
    frame_to_rgb,
    infer_frame_dimensions,
    normalize_pixel_format,
    rgb_to_frame_format,
)
from app.core.ringbuffer import VideoRingBuffer
from app.core.utils import save_frame
from app.core.video_recorder import VideoRecorderManager
from app.core.alert_delivery import enqueue_alert_delivery
from app.core.recording_storage_config import get_recording_storage_config
from app.core.storage_pressure import (
    StoragePressure,
    StoragePressureLevel,
    measure_storage_pressure,
)
from app.core.vl_validator import get_vl_service_config, validate_frame_with_vl
from app.core.window_detector import WindowDetector
from app.core.numeric_window_detector import NumericWindowDetector
from app.plugins.script_algorithm import ScriptAlgorithm
from app.core.workflow_types import create_node_data, NodeContext, SourceNodeData, AlgorithmNodeData, RoiDrawNodeData, FunctionNodeData, ConditionNodeData, TimeScheduleNodeData, OutputNodeData, AlertNodeData, ExternalApiNodeData, WebhookNodeData
from app.core.time_schedule import evaluate_weekly_schedule
from app.core.execution_log_collector import ExecutionLogCollector
from app.core.webhook_notifier import (
    apply_public_media_urls,
    build_alert_webhook_event,
    prepare_webhook_request,
    validate_webhook_config,
    webhook_dispatcher,
)
from app.core.public_media_config import build_public_media_url

try:
    from app.core.database_models import Workflow, VideoSource, Algorithm, Alert, ExternalApi, db
except ImportError as exc:  # pragma: no cover - optional in lightweight test envs
    _WORKFLOW_EXECUTOR_IMPORT_ERROR = exc

    class _MissingDatabaseModel:
        @classmethod
        def get_by_id(cls, *args, **kwargs):
            raise ImportError("WorkflowExecutor requires peewee/database dependencies") from _WORKFLOW_EXECUTOR_IMPORT_ERROR

        @classmethod
        def create(cls, *args, **kwargs):
            raise ImportError("WorkflowExecutor requires peewee/database dependencies") from _WORKFLOW_EXECUTOR_IMPORT_ERROR

    Workflow = VideoSource = Algorithm = Alert = ExternalApi = _MissingDatabaseModel

DETECTION_JSONL_LOG_LOCK = threading.Lock()
DETECTION_SNAPSHOT_COORDINATOR_LOCK = threading.Lock()
DETECTION_SNAPSHOT_SOURCE_STATES = {}

_GATE_CONDITIONS = frozenset({'detected', 'not_detected', 'true', 'false', 'yes', 'no'})
_GATE_SKIP_REASON_MESSAGES = {
    'upstream_not_executed': '已跳过：上游未执行',
    'upstream_empty': '已跳过：上游无目标',
    'gate_failed': '已跳过：门控未通过',
}


class FrameExecutionContext(dict):
    """按需从主帧派生 RGB/BGR 视图，避免实时链路无条件转换。"""

    def get(self, key, default=None):
        if key in {'frame', 'frame_rgb'}:
            return self._get_frame_rgb(default)
        if key == 'frame_bgr':
            return self._get_frame_bgr(default)
        if key == 'frame_width':
            return self._get_frame_width(default)
        if key == 'frame_height':
            return self._get_frame_height(default)
        return super().get(key, default)

    def __getitem__(self, key):
        if key in {'frame', 'frame_rgb'}:
            return self._get_frame_rgb()
        if key == 'frame_bgr':
            return self._get_frame_bgr()
        if key == 'frame_width':
            return self._get_frame_width()
        if key == 'frame_height':
            return self._get_frame_height()
        return super().__getitem__(key)

    def __contains__(self, key):
        if key in {'frame', 'frame_rgb'}:
            return super().__contains__('frame_rgb') or super().__contains__('frame_nv12')
        if key == 'frame_bgr':
            return (
                super().__contains__('frame_bgr')
                or super().__contains__('frame_rgb')
                or super().__contains__('frame_nv12')
            )
        if key in {'frame_width', 'frame_height'}:
            return (
                super().__contains__(key)
                or super().__contains__('frame_nv12')
                or super().__contains__('frame_rgb')
                or super().__contains__('frame')
            )
        return super().__contains__(key)

    def copy(self):
        return FrameExecutionContext(super().copy())

    def _get_native_frame(self):
        return super().get('frame_nv12')

    def _get_frame_pixel_format(self) -> str:
        frame = self._get_native_frame()
        declared = super().get('frame_pixel_format', VIDEO_FRAME_PIXEL_FORMAT)
        if frame is None:
            return normalize_pixel_format(declared)
        return detect_frame_pixel_format(frame, pixel_format=declared)

    def _get_frame_width(self, default=None):
        width = super().get('frame_width')
        if width is not None:
            return width

        frame_native = self._get_native_frame()
        if frame_native is not None:
            width, height = infer_frame_dimensions(
                frame_native,
                pixel_format=self._get_frame_pixel_format(),
            )
            super().__setitem__('frame_width', width)
            super().__setitem__('frame_height', height)
            return width

        frame_rgb = super().get('frame_rgb')
        if frame_rgb is not None:
            width = frame_rgb.shape[1]
            super().__setitem__('frame_width', width)
            return width
        return default

    def _get_frame_height(self, default=None):
        height = super().get('frame_height')
        if height is not None:
            return height

        frame_native = self._get_native_frame()
        if frame_native is not None:
            width, height = infer_frame_dimensions(
                frame_native,
                pixel_format=self._get_frame_pixel_format(),
            )
            super().__setitem__('frame_width', width)
            super().__setitem__('frame_height', height)
            return height

        frame_rgb = super().get('frame_rgb')
        if frame_rgb is not None:
            height = frame_rgb.shape[0]
            super().__setitem__('frame_height', height)
            return height
        return default

    def _get_frame_rgb(self, default=None):
        frame_rgb = super().get('frame_rgb')
        if frame_rgb is not None:
            return frame_rgb

        frame_native = self._get_native_frame()
        if frame_native is None:
            return default

        width = self._get_frame_width()
        height = self._get_frame_height()
        frame_rgb = frame_to_rgb(
            frame_native,
            pixel_format=self._get_frame_pixel_format(),
            width=width,
            height=height,
        )
        super().__setitem__('frame_rgb', frame_rgb)
        return frame_rgb

    def _get_frame_bgr(self, default=None):
        frame_bgr = super().get('frame_bgr')
        if frame_bgr is not None:
            return frame_bgr

        frame_native = self._get_native_frame()
        if frame_native is not None:
            width = self._get_frame_width()
            height = self._get_frame_height()
            frame_bgr = frame_to_bgr(
                frame_native,
                pixel_format=self._get_frame_pixel_format(),
                width=width,
                height=height,
            )
            super().__setitem__('frame_bgr', frame_bgr)
            return frame_bgr

        frame_rgb = self._get_frame_rgb(default=None)
        if frame_rgb is None:
            return default
        require_cv2()
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        super().__setitem__('frame_bgr', frame_bgr)
        return frame_bgr


class WorkflowExecutor:
    """
    工作流执行器 - 统一的执行引擎

    核心设计原则：
    1. 一套核心执行逻辑，测试和运行使用相同的代码路径
    2. test_mode 只控制副作用（数据库写入、视频录制等），不改变执行流程
    3. 避免代码重复，修改一次即可同时影响测试和运行
    """

    _GATE_CONDITIONS = _GATE_CONDITIONS

    def __init__(self, workflow_id, test_mode=False, window_detector=None):
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
        self.recording_config = get_recording_storage_config()
        self._storage_pressure: Optional[StoragePressure] = None
        self._storage_pressure_checked_at = 0.0

        # 创建节点数据字典
        workflow_nodes = self.workflow_data.get('nodes', [])
        logger.info(f"[Workflow-{self.workflow_id}] 工作流包含 {len(workflow_nodes)} 个节点")

        self.nodes = {}
        for n in workflow_nodes:
            try:
                node = create_node_data(n)
                self.nodes[n['id']] = node
                logger.debug(f"[Workflow-{self.workflow_id}] 成功创建节点: {n['id']} (类型: {n.get('type')})")
            except Exception as e:
                logger.error(f"[Workflow-{self.workflow_id}] 创建节点失败: {n['id']}, 错误: {e}")

        logger.info(f"[Workflow-{self.workflow_id}] 成功加载 {len(self.nodes)} 个节点")

        self.connections = self.workflow_data.get('connections', [])

        self.source_node = None
        self.video_source = None
        self.buffer = None
        self.recording_buffer = None
        self.algorithms = {}
        self.algorithm_configs = {}
        self.algorithm_datamap = {}
        self.algorithm_roi_configs = {}
        self.external_api_configs = {}
        self.external_api_datamap = {}
        self.execution_graph = defaultdict(list)
        self.video_recorder = None
        self.window_detector = window_detector or WindowDetector()
        self.numeric_window_detector = NumericWindowDetector()
        self.recorder_key = f"workflow:{self.workflow_id}"
        self.running = True
        self._cleaned_up = False
        self._async_submit_executor = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix=f"workflow-{self.workflow_id}-external-api",
        )

        # ========== 执行状态追踪 ==========
        # node_results_cache: 存储节点执行结果（用于下游节点访问）
        self.node_results_cache = {}
        # condition_diagnostics_cache: 存储有状态条件的诊断信息，不参与下游结果传递。
        self.condition_diagnostics_cache = {}
        # execution_results: 记录执行状态（用于测试结果收集）
        self.execution_results = {}
        # executed_nodes: 记录实际执行过的节点（按顺序）
        self.executed_nodes = []
        # skipped_nodes: 本帧门控/插件 skip 的节点，不进窗口样本，禁止二次 interval 清 cache
        self.skipped_nodes = set()
        # last_frame_timestamp: 记录最后处理的帧时间戳（用于多 workflow 共享 buffer 时避免重复处理）
        self.last_frame_timestamp = None
        # _latest_algorithm_results: 各算法节点最近一次检测结果及其原始帧时间戳。
        self._latest_algorithm_results = {}

        self.node_handlers = {
            'source': self._handle_source_node,
            'algorithm': self._handle_algorithm_node,
            'condition': self._handle_condition_node,
            'time_schedule': self._handle_time_schedule_node,
            'output': self._handle_output_node,
            'roi_draw': self._handle_roi_draw_node,
            'roi': self._handle_roi_draw_node,  # 支持前后端两种类型名称
            'alert': self._handle_output_node,
            'function': self._handle_function_node,
            'external_api': self._handle_external_api_node,
            'webhook': self._handle_webhook_node,
        }

        self._build_execution_graph()

        # 只在非测试模式下初始化视频源和buffer
        if not test_mode:
            self._init_resources()
            logger.info(f"[Workflow-{self.workflow_id}] 实时模式：已初始化视频源和buffer")
        else:
            logger.info(f"[Workflow-{self.workflow_id}] 测试模式：跳过视频源和buffer初始化")
            # 测试模式下仍需要加载算法
            self._load_algorithms()
            self._load_external_apis()

        self.node_last_exec_time = {}
        for node_id in self.nodes.keys():
            self.node_last_exec_time[node_id] = 0
        self._state_lock = threading.Lock()
        self._profile_next_log_at = time.monotonic() + RESOURCE_PROFILE_LOG_INTERVAL_SECONDS
        self._profile_run_once_count = 0
        self._profile_run_once_total_ms = 0.0
        self._profile_run_once_max_ms = 0.0

    def stop(self):
        self.running = False

    def begin_drain(self):
        """停止检测并尽早释放模型；录像线程仍可继续读取录制缓冲区。"""
        self.running = False
        self._cleanup_algorithms()

    def _cleanup_algorithms(self):
        for node_id, algorithm in list(getattr(self, 'algorithms', {}).items()):
            cleanup = getattr(algorithm, 'cleanup', None)
            if not callable(cleanup):
                continue

            try:
                cleanup()
            except Exception as exc:
                logger.warning(
                    f"[Workflow-{self.workflow_id}] 清理算法节点 {node_id} 失败: {exc}",
                    exc_info=True,
                )

        if hasattr(self, 'algorithms'):
            self.algorithms.clear()
        if hasattr(self, 'external_api_configs'):
            self.external_api_configs.clear()
        if hasattr(self, 'external_api_datamap'):
            self.external_api_datamap.clear()

    def _cleanup_runtime_state(self):
        with self._state_lock:
            self.node_results_cache.clear()
            self.execution_results.clear()
            self.executed_nodes.clear()
            skipped_nodes = getattr(self, 'skipped_nodes', None)
            if skipped_nodes is not None:
                skipped_nodes.clear()
            diagnostics_cache = getattr(self, 'condition_diagnostics_cache', None)
            if diagnostics_cache is not None:
                diagnostics_cache.clear()
        numeric_window_detector = getattr(self, 'numeric_window_detector', None)
        if numeric_window_detector is not None:
            numeric_window_detector.clear()

    def _cleanup_window_detector(self):
        if self.video_source is None:
            return

        for node_id, node in self.nodes.items():
            if isinstance(node, AlertNodeData):
                try:
                    self.window_detector.clear_buffer(self.video_source.id, node_id)
                except Exception as exc:
                    logger.warning(
                        f"[Workflow-{self.workflow_id}] 清理窗口检测缓存失败: node={node_id}, error={exc}",
                        exc_info=True,
                    )

    def cleanup(self):
        if self._cleaned_up:
            return

        self.running = False
        self._cleaned_up = True

        self._cleanup_algorithms()
        self._cleanup_window_detector()
        self._cleanup_runtime_state()

        if self.buffer is not None:
            try:
                self.buffer.close()
            except Exception as exc:
                logger.warning(
                    f"[Workflow-{self.workflow_id}] 关闭分析缓冲区失败: {exc}",
                    exc_info=True,
                )
            self.buffer = None

        if self.recording_buffer is not None:
            should_close_recording_buffer = True
        else:
            should_close_recording_buffer = False

        if self.video_source is not None and self.video_recorder is not None:
            try:
                recorder_manager = VideoRecorderManager()
                recorder_cleaned = recorder_manager.cleanup_recorder(
                    self.recorder_key,
                    wait_timeout=(
                        float(self.recording_config.post_alert_seconds)
                        + float(SOURCE_ROTATION_DRAIN_GRACE_SECONDS)
                    ),
                )
                if not recorder_cleaned:
                    should_close_recording_buffer = False
            except Exception as exc:
                logger.warning(
                    f"[Workflow-{self.workflow_id}] 清理录制器失败: {exc}",
                    exc_info=True,
                )
        self.video_recorder = None

        async_submit_executor = getattr(self, '_async_submit_executor', None)
        if async_submit_executor is not None:
            try:
                async_submit_executor.shutdown(wait=False, cancel_futures=True)
            except Exception as exc:
                logger.warning(
                    f"[Workflow-{self.workflow_id}] 关闭外部 API 异步线程池失败: {exc}",
                    exc_info=True,
                )
            self._async_submit_executor = None

        if self.recording_buffer is not None and should_close_recording_buffer:
            try:
                self.recording_buffer.close()
            except Exception as exc:
                logger.warning(
                    f"[Workflow-{self.workflow_id}] 关闭录制缓冲区失败: {exc}",
                    exc_info=True,
                )
            self.recording_buffer = None

    def _load_algorithms(self):
        """加载算法节点所需的算法（供测试模式和实时模式使用）"""
        for node_id, node in self.nodes.items():
            # 只加载算法节点，函数节点有独立的处理逻辑
            if node.node_type == 'algorithm':
                algo_id = node.data_id
                logger.info(f"[Workflow-{self.workflow_id}] 检查算法节点 {node_id}, data_id: {algo_id}")

                if algo_id:
                    try:
                        algo = Algorithm.get_by_id(algo_id)

                        # 从工作流数据中获取完整的 node_data
                        node_data_dict = next((n for n in self.workflow_data.get('nodes', []) if n['id'] == node_id), {})

                        if not node_data_dict:
                            logger.warning(f"[Workflow-{self.workflow_id}] 节点 {node_id} 在工作流数据中未找到配置")

                        # 获取节点配置（用户在工作流编辑器中配置的）
                        # 注意：当 config=None 时，get() 不会使用默认值，需要额外判断
                        node_config = node_data_dict.get('config')
                        if node_config is None:
                            node_config = {}

                        legacy_confidence = node_data_dict.get('confidence')
                        if legacy_confidence is not None and 'confidence' not in node_config:
                            node_config = dict(node_config)
                            node_config['confidence'] = legacy_confidence
                            logger.info(
                                f"[Workflow-{self.workflow_id}] 算法节点 {node_id} 使用旧版 confidence 配置: {legacy_confidence}"
                            )

                        # 获取运行时配置（从节点配置）
                        runtime_config = self._get_node_runtime_config(node_data_dict)

                        # 提取各项配置
                        interval_seconds = runtime_config['interval_seconds']
                        runtime_timeout = runtime_config['runtime_timeout']
                        memory_limit_mb = runtime_config['memory_limit_mb']
                        label_config = runtime_config['label']
                        roi_regions = runtime_config['roi_regions']

                        # 调试日志：显示 interval 配置
                        logger.info(f"[Workflow-{self.workflow_id}] 算法节点 {node_id} interval_seconds={interval_seconds}（从 runtime_config 获取）")
                        logger.info(f"[Workflow-{self.workflow_id}] 算法节点 {node_id} node.interval_seconds={node.interval_seconds}（从 node 对象获取）")

                        # 存储ROI配置
                        self.algorithm_roi_configs[node_id] = roi_regions
                        if roi_regions:
                            logger.info(f"[Workflow-{self.workflow_id}] 算法节点 {node_id} 配置了 {len(roi_regions)} 个ROI热区")
                        else:
                            logger.info(f"[Workflow-{self.workflow_id}] 算法节点 {node_id} 未配置ROI热区，将使用全画面检测")

                        # 构建完整配置（算法固有配置 + 节点配置 + 运行时配置）
                        full_config = {
                            "id": algo_id,
                            "name": algo.name,
                            "source_id": getattr(self.video_source, 'id', 0),  # 测试模式下为 0
                            "source_name": getattr(self.video_source, 'name', ''),
                            "source_code": getattr(self.video_source, 'source_code', ''),
                            "workflow_name": getattr(self.workflow, 'name', ''),
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

                        algorithm_type = algo.ext_config.get('algorithm_type') or 'script'
                        if algorithm_type in ('vl', 'ocr', 'cascade'):
                            full_config.update(algo.ext_config)

                        # 合并节点配置（用户在工作流编辑器中配置的，如 models 等）
                        full_config.update(node_config)
                        if (
                            algorithm_type == 'vl'
                            and node_config.get('runtime_timeout_override_enabled') is True
                        ):
                            full_config['vl_timeout_override_seconds'] = node_config.get('runtime_timeout')
                        full_config, effective_confidence_threshold = self._sync_single_model_confidence(full_config)

                        logger.info(f"[Workflow-{self.workflow_id}] 节点 {node_id} 合并后的完整配置 models: {full_config.get('models', 'NOT_FOUND')}")

                        if algorithm_type == 'vl':
                            from app.plugins.vl_algorithm import VLAlgorithm
                            self.algorithms[node_id] = VLAlgorithm(full_config)
                        elif algorithm_type == 'ocr':
                            from app.plugins.ocr_algorithm import OCRAlgorithm
                            self.algorithms[node_id] = OCRAlgorithm(full_config)
                        elif algorithm_type == 'cascade':
                            from app.plugins.cascade_algorithm import CascadeAlgorithm
                            self.algorithms[node_id] = CascadeAlgorithm(full_config)
                        else:
                            self.algorithms[node_id] = ScriptAlgorithm(full_config)

                        # 存储算法元数据（用于后续访问）
                        self.algorithm_datamap[node_id] = {
                            'id': algo_id,
                            'name': algo.name,
                            'algorithm_type': algorithm_type,
                            'interval_seconds': interval_seconds,
                            'label_name': label_config['name'],
                            'label_color': label_config['color']
                        }

                        self.algorithm_configs[node_id] = {
                            'algorithm_id': algo_id,
                            'node_id': node_id,
                            'runtime_config': runtime_config,
                            'effective_confidence_threshold': effective_confidence_threshold,
                        }

                        logger.info(
                            f"[Workflow-{self.workflow_id}] 成功加载算法: {algo.name}, "
                            f"类型: {algorithm_type}, 节点ID: {node_id}"
                        )
                    except Exception as e:
                        logger.error(f"[Workflow-{self.workflow_id}] 加载算法节点 {node_id} 失败: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    logger.warning(f"[Workflow-{self.workflow_id}] 算法节点 {node_id} 没有 data_id，跳过加载")

    def _load_external_apis(self):
        """加载外部 API 节点所需的配置。"""
        for node_id, node in self.nodes.items():
            if node.node_type != 'external_api':
                continue

            api_id = node.data_id
            if not api_id:
                logger.warning(f"[Workflow-{self.workflow_id}] 外部 API 节点 {node_id} 没有 data_id，跳过加载")
                continue

            try:
                api_config = ExternalApi.get_by_id(api_id)
                node_data_dict = self._get_workflow_node_dict(node_id)
                node_config = node_data_dict.get('config')
                if not isinstance(node_config, dict):
                    node_config = {}

                merged_output_mapping = dict(api_config.output_mapping)
                node_output_mapping = node_config.get('output_mapping')
                if isinstance(node_output_mapping, dict):
                    merged_output_mapping.update(node_output_mapping)

                runtime_timeout = int(node_config.get('timeout_seconds') or api_config.timeout_seconds or 30)
                interval_seconds = node_config.get('interval_seconds', 1.0)

                self.external_api_datamap[node_id] = {
                    'id': api_config.id,
                    'name': api_config.name,
                    'endpoint_url': api_config.endpoint_url,
                    'method': (api_config.method or 'POST').upper(),
                    'headers': api_config.headers,
                    'request_template': api_config.request_template,
                    'input_schema': api_config.input_schema,
                    'output_schema': api_config.output_schema,
                    'timeout_seconds': api_config.timeout_seconds,
                    'enabled': api_config.enabled,
                }
                self.external_api_configs[node_id] = {
                    'external_api_id': api_config.id,
                    'node_id': node_id,
                    'interval_seconds': interval_seconds,
                    'timeout_seconds': runtime_timeout,
                    'execution_mode': node_config.get('execution_mode') or 'sync',
                    'include_image': node_config.get('include_image', True),
                    'include_upstream_results': node_config.get('include_upstream_results', True),
                    'payload_template': node_config.get('payload_template') if isinstance(node_config.get('payload_template'), dict) else {},
                    'output_mapping': merged_output_mapping,
                    'label_color': node_config.get('label_color') or '#1677ff',
                }
                logger.info(
                    f"[Workflow-{self.workflow_id}] 成功加载外部 API 节点 {node_id}: "
                    f"{api_config.name} ({api_config.endpoint_url})"
                )
            except Exception as exc:
                logger.error(f"[Workflow-{self.workflow_id}] 加载外部 API 节点 {node_id} 失败: {exc}", exc_info=True)

    def _sync_single_model_confidence(self, config: dict) -> tuple[dict, Optional[float]]:
        """单模型脚本同步 confidence，并兼容历史上未显式覆盖的节点配置。"""
        if not isinstance(config, dict):
            return config, None

        raw_threshold = config.get('confidence')
        override_enabled = bool(config.get('confidence_override_enabled'))
        models = config.get('models')
        if not isinstance(models, list) or len(models) != 1:
            try:
                threshold = None if raw_threshold is None else max(0.0, min(1.0, float(raw_threshold)))
            except (TypeError, ValueError):
                threshold = None
            return config, threshold

        model_config = models[0]
        if not isinstance(model_config, dict):
            try:
                threshold = None if raw_threshold is None else max(0.0, min(1.0, float(raw_threshold)))
            except (TypeError, ValueError):
                threshold = None
            return config, threshold

        try:
            node_threshold = None if raw_threshold is None else float(raw_threshold)
        except (TypeError, ValueError):
            node_threshold = None

        try:
            model_threshold = None if model_config.get('confidence') is None else float(model_config.get('confidence'))
        except (TypeError, ValueError):
            model_threshold = None

        if override_enabled:
            effective_threshold = node_threshold if node_threshold is not None else model_threshold
        else:
            candidates = [value for value in (node_threshold, model_threshold) if value is not None]
            effective_threshold = max(candidates) if candidates else None
        if effective_threshold is None:
            return config, None

        threshold = max(0.0, min(1.0, effective_threshold))

        synced_config = dict(config)
        synced_models = [dict(model_config)]
        synced_models[0]['confidence'] = threshold
        synced_config['models'] = synced_models
        synced_config['confidence'] = threshold

        logger.info(
            f"[Workflow-{self.workflow_id}] 单模型配置同步最终 confidence={threshold:.2f} 到 models[0]"
        )
        return synced_config, threshold

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
        analysis_buffer_name = self.video_source.analysis_buffer_name
        analysis_fps = max(1, min(int(self.video_source.source_fps), int(ANALYSIS_TARGET_FPS)))

        logger.info(f"[Workflow-{self.workflow_id}] 启动 Workflow {self.workflow.name} (ID: {self.workflow_id})，处理视频源 {self.video_source.name} (ID: {self.video_source.source_code})")

        # 尝试连接到共享内存缓冲区，带重试机制
        max_retries = 10
        retry_interval = 1.0  # 秒

        for attempt in range(1, max_retries + 1):
            try:
                self.buffer = VideoRingBuffer(
                    name=analysis_buffer_name,
                    create=False,
                    width=self.video_source.source_decode_width,
                    height=self.video_source.source_decode_height,
                    pixel_format=VIDEO_FRAME_PIXEL_FORMAT,
                    fps=analysis_fps,
                    duration_seconds=ANALYSIS_BUFFER_SECONDS
                )
                logger.info(
                    f"已连接分析缓冲区: {analysis_buffer_name} "
                    f"(fps={analysis_fps}, duration={ANALYSIS_BUFFER_SECONDS}s, "
                    f"capacity={self.buffer.capacity}, frame_shape={self.buffer.frame_shape}, "
                    f"pixel_format={self.buffer.pixel_format})"
                )
                break  # 成功连接，退出重试循环

            except FileNotFoundError:
                if attempt < max_retries:
                    logger.warning(
                        f"[Workflow-{self.workflow_id}] 尝试 {attempt}/{max_retries}: "
                        f"分析缓冲区 /{analysis_buffer_name} 尚未就绪，等待 {retry_interval} 秒后重试..."
                    )
                    time.sleep(retry_interval)
                else:
                    # 最后一次尝试仍然失败
                    logger.error(f"[Workflow-{self.workflow_id}] 无法连接到分析共享内存缓冲区: /{analysis_buffer_name}")
                    logger.error(f"[Workflow-{self.workflow_id}] 这通常意味着视频源 {self.video_source.name} (ID={self.video_source.id}) 的 Decoder Worker 未运行或尚未创建缓冲区")
                    logger.error(f"[Workflow-{self.workflow_id}] 请检查：")
                    logger.error(f"[Workflow-{self.workflow_id}]   1. 视频源状态是否为 RUNNING（当前状态: {self.video_source.status}）")
                    logger.error(f"[Workflow-{self.workflow_id}]   2. Decoder Worker 进程是否在运行（PID: {self.video_source.decoder_pid}）")
                    logger.error(f"[Workflow-{self.workflow_id}]   3. Orchestrator 是否已正确启动该视频源")
                    raise

        shm_name = analysis_buffer_name if os.name == 'nt' else f"/{analysis_buffer_name}"
        resource_tracker.unregister(shm_name, 'shared_memory')

        if self.recording_config.recording_enabled:
            recording_buffer_duration = max(
                RECORDING_BUFFER_DURATION,
                self.recording_config.pre_alert_seconds
                + self.recording_config.post_alert_seconds
                + 2,
            )
            recording_buffer_name = self.video_source.recording_buffer_name
            try:
                self.recording_buffer = CompressedVideoRingBuffer(
                    name=recording_buffer_name,
                    create=False,
                    width=self.video_source.source_decode_width,
                    height=self.video_source.source_decode_height,
                    pixel_format=VIDEO_FRAME_PIXEL_FORMAT,
                    fps=self.recording_config.recording_fps,
                    duration_seconds=recording_buffer_duration,
                    max_frame_bytes=RECORDING_COMPRESSED_MAX_BYTES,
                    jpeg_quality=RECORDING_JPEG_QUALITY,
                )
                shm_name = recording_buffer_name if os.name == 'nt' else f"/{recording_buffer_name}"
                resource_tracker.unregister(shm_name, 'shared_memory')
                logger.info(
                    f"已连接录制缓冲区: {recording_buffer_name} "
                    f"(compressed jpeg, fps={self.recording_config.recording_fps}, "
                    f"duration={recording_buffer_duration}s, "
                    f"capacity={self.recording_buffer.capacity}, frame_shape={self.recording_buffer.frame_shape}, "
                    f"pixel_format={self.recording_buffer.pixel_format})"
                )
            except FileNotFoundError:
                logger.warning(
                    f"[Workflow-{self.workflow_id}] 录制缓冲区 /{recording_buffer_name} 不可用，本次仅启用分析链路"
                )

            if self.recording_buffer is not None:
                recorder_manager = VideoRecorderManager()
                self.video_recorder = recorder_manager.get_recorder(
                    recorder_key=self.recorder_key,
                    source_id=self.video_source.id,
                    buffer=self.recording_buffer,
                    save_dir=VIDEO_SAVE_PATH,
                    fps=self.recording_config.recording_fps,
                    max_disk_used_percent=self.recording_config.stop_recording_percent,
                )
                logger.info(
                    f"[WorkflowWorker:{os.getpid()}] 视频录制功能已启用 "
                    f"(前{self.recording_config.pre_alert_seconds}秒 + "
                    f"后{self.recording_config.post_alert_seconds}秒)"
                )
            else:
                self.video_recorder = None
                logger.warning(
                    f"[Workflow-{self.workflow_id}] 录制缓冲区不可用，已禁用告警视频录制，避免回退到分析缓冲区导致录像不完整"
                )

        # 加载算法
        self._load_algorithms()
        self._load_external_apis()
    
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
        # 排除会被上游分支自动执行的终端节点（alert, output, webhook）
        remaining_nodes = {
            node_id for node_id in self.nodes.keys()
            if not isinstance(self.nodes[node_id], (OutputNodeData, AlertNodeData, WebhookNodeData))
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
                logger.warning(f"[Workflow-{self.workflow_id}] 检测到循环依赖，剩余节点: {remaining_nodes}")
                # 强制添加剩余节点到当前层级
                current_level = list(remaining_nodes)

            # 按节点类型排序（source -> algorithm/roi_draw -> function -> condition -> output/alert）
            current_level.sort(key=lambda nid: self._get_node_type_priority(nid))

            levels.append(current_level)
            logger.debug(f"[Workflow-{self.workflow_id}] 拓扑层级 {len(levels)-1}: {[f'{nid}({self.nodes[nid].node_type})' for nid in current_level]}")

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
            'external_api': 2,
            'function': 3,
            'condition': 4,
            'time_schedule': 4,
            'output': 5,
            'alert': 5,
            'webhook': 6,
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
            logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} 不存在，返回 interval=0")
            return 0

        if isinstance(node, AlgorithmNodeData):
            logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} 是算法节点，interval_seconds={node.interval_seconds}（类型: {type(node.interval_seconds)}）")
            if node.interval_seconds is not None:
                return node.interval_seconds

        if isinstance(node, ExternalApiNodeData):
            logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} 是外部 API 节点，interval_seconds={node.interval_seconds}（类型: {type(node.interval_seconds)}）")
            if node.interval_seconds is not None:
                return node.interval_seconds

        if node_id in self.algorithms:
            interval_from_map = self.algorithm_datamap[node_id].get('interval_seconds', 1)
            logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} 从 algorithm_datamap 获取 interval={interval_from_map}")
            return interval_from_map

        if node_id in self.external_api_configs:
            interval_from_map = self.external_api_configs[node_id].get('interval_seconds', 1)
            logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} 从 external_api_configs 获取 interval={interval_from_map}")
            return interval_from_map

        # logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} 不是算法节点且不在 algorithms 中，返回 interval=0")
        return 0

    def _get_workflow_node_dict(self, node_id: str) -> dict:
        """从 workflow_data 中读取节点原始配置。"""
        return next((n for n in self.workflow_data.get('nodes', []) if n.get('id') == node_id), {}) or {}

    def _get_algorithm_confidence_threshold(self, node_id: str) -> Optional[float]:
        """获取算法节点生效的置信度阈值，兼容旧版顶层字段。"""
        runtime_config = self.algorithm_configs.get(node_id) or {}
        runtime_threshold = runtime_config.get('effective_confidence_threshold')
        if runtime_threshold is not None:
            try:
                return max(0.0, min(1.0, float(runtime_threshold)))
            except (TypeError, ValueError):
                pass

        node_data_dict = self._get_workflow_node_dict(node_id)
        if not node_data_dict:
            return None

        config = node_data_dict.get('config')
        if not isinstance(config, dict):
            config = {}

        raw_threshold = config.get('confidence')
        if raw_threshold is None:
            raw_threshold = node_data_dict.get('confidence')
        if raw_threshold is None:
            return None

        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError):
            logger.warning(
                f"[Workflow-{self.workflow_id}] 算法节点 {node_id} confidence 配置无效: {raw_threshold}"
            )
            return None

        return max(0.0, min(1.0, threshold))

    def _apply_algorithm_confidence_filter(self, node_id: str, result: dict) -> dict:
        """按算法节点配置的 confidence 阈值统一过滤检测结果。"""
        if not isinstance(result, dict):
            return result

        detections = result.get('detections')
        if not isinstance(detections, list) or not detections:
            return result

        threshold = self._get_algorithm_confidence_threshold(node_id)
        if threshold is None:
            return result

        filtered = []
        filtered_count = 0
        stage_filtered_count = 0

        for det in detections:
            if BaseAlgorithm._get_detection_confidence(det, 1.0) < threshold:
                filtered_count += 1
                continue

            filtered_det = dict(det)
            stages = det.get('stages')
            if isinstance(stages, list):
                filtered_stages = [
                    stage for stage in stages
                    if BaseAlgorithm._get_detection_confidence(stage, 1.0) >= threshold
                ]
                stage_filtered_count += len(stages) - len(filtered_stages)
                filtered_det['stages'] = filtered_stages

            filtered.append(filtered_det)

        metadata = dict(result.get('metadata') or {})
        metadata.setdefault('confidence_threshold', threshold)
        if filtered_count <= 0 and stage_filtered_count <= 0:
            filtered_result = dict(result)
            filtered_result['metadata'] = metadata
            return filtered_result

        if filtered_count > 0:
            metadata['detections_before_confidence_filter'] = len(detections)
            metadata['confidence_filtered_count'] = filtered_count
        if stage_filtered_count > 0:
            metadata['stages_before_confidence_filter'] = sum(
                len(det.get('stages', [])) for det in detections if isinstance(det.get('stages'), list)
            )
            metadata['stage_confidence_filtered_count'] = stage_filtered_count
        if metadata.get('ocr_checked') is True:
            metadata['full_text'] = '\n'.join(
                str(detection.get('text') or detection.get('label_name') or '')
                for detection in filtered
                if isinstance(detection, dict)
            )
            metadata['text_count'] = len(filtered)

        filtered_result = dict(result)
        filtered_result['detections'] = filtered
        filtered_result['metadata'] = metadata

        logger.info(
            f"[Workflow-{self.workflow_id}] 算法节点 {node_id} 按 confidence={threshold:.2f} "
            f"过滤检测结果: {len(detections)} -> {len(filtered)}, "
            f"stages 过滤 {stage_filtered_count} 个"
        )
        return filtered_result
    
    def _should_execute_node(self, node_id):
        current_time = time.time()
        interval = self._get_node_interval(node_id)

        if interval <= 0:
            logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} interval={interval}<=0，总是执行")
            return True

        last_exec = self.node_last_exec_time.get(node_id, 0)
        time_since_last = current_time - last_exec
        if time_since_last >= interval:
            logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} 距离上次执行{time_since_last:.2f}秒>=interval({interval}秒)，执行")
            self.node_last_exec_time[node_id] = current_time
            return True

        logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} 未到执行间隔 ({interval}秒)，跳过")
        return False
    
    def _process_algorithm(
        self,
        node_id,
        frame_nv12=None,
        frame_timestamp=None,
        roi_regions=None,
        upstream_results=None,
        frame=None,
    ):
        if frame_nv12 is None:
            frame_nv12 = frame

        algo = self.algorithms.get(node_id)
        if not algo:
            logger.warning(f"[Workflow-{self.workflow_id}] 节点 {node_id} 不在 self.algorithms 中，已加载的算法节点: {list(self.algorithms.keys())}")
            return None

        try:
            effective_roi_regions = roi_regions if roi_regions is not None else self.algorithm_roi_configs.get(node_id, [])

            if roi_regions is not None:
                logger.debug(f"[Workflow-{self.workflow_id}] 算法节点 {node_id} 使用context中的ROI配置，包含 {len(roi_regions)} 个区域")
                # 打印详细的ROI配置
                for idx, roi in enumerate(roi_regions):
                    polygon = roi.get('polygon', [])
                    if polygon and len(polygon) > 0:
                        logger.debug(f"[Workflow-{self.workflow_id}] 算法节点 {node_id} ROI区域{idx + 1}: 顶点数={len(polygon)}, 数据={polygon[:2]}...")
            elif effective_roi_regions:
                logger.debug(f"[Workflow-{self.workflow_id}] 算法节点 {node_id} 使用算法自带的ROI配置，包含 {len(effective_roi_regions)} 个区域")

            logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} 调用 algo.process，upstream_results: {list(upstream_results.keys())} (共{len(upstream_results)}个上游)")
            logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} 传递给算法的ROI配置: {effective_roi_regions}")
            process_kwargs = {'upstream_results': upstream_results}
            try:
                process_params = inspect.signature(algo.process).parameters
            except (TypeError, ValueError):
                process_params = {}
            if 'frame_timestamp' in process_params:
                process_kwargs['frame_timestamp'] = frame_timestamp
            result = algo.process(frame_nv12, effective_roi_regions, **process_kwargs)
            result = self._apply_algorithm_confidence_filter(node_id, result)
            if isinstance(result, dict):
                result = dict(result)
                result['detections'] = BaseAlgorithm.normalize_detection_results(result.get('detections', []))
            logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} algo.process 返回: {result}")
            if result is None:
                raise RuntimeError(f"algo.process returned None for node {node_id}")
            has_detection = bool(result and result.get("detections"))
            roi_mask = result.get('roi_mask')

            algo_id = self.algorithm_configs[node_id]['algorithm_id']
            algorithm_name = self.algorithm_datamap[node_id].get('name')
            label_color = self.algorithm_datamap[node_id].get('label_color', '#FF0000')

            if WORKFLOW_FRAME_LOGS_ENABLED:
                logger.info(
                    f"[Workflow-{self.workflow_id}] 算法节点 {node_id} "
                    f"处理完成，检测到目标: {has_detection}"
                )

            # 记录本节点最近一次检测结果，供「最新检测帧」快照聚合使用。
            try:
                with self._state_lock:
                    self._latest_algorithm_results[node_id] = {
                        'detections': result.get('detections', []) if isinstance(result, dict) else [],
                        'roi_mask': roi_mask,
                        'label_color': label_color,
                        'roi_regions': effective_roi_regions or [],
                        'frame_timestamp': frame_timestamp,
                    }
            except Exception:
                pass

            # 返回结果，包含节点 ID 和 label_color（用于下游 Alert 节点）
            return {
                'node_id': node_id,
                'has_detection': has_detection,
                'result': result,
                'roi_mask': roi_mask,
                'label_color': label_color,
                'upstream_node_id': node_id  # 上游节点 ID 就是当前节点 ID
            }

        except Exception as exc:
            logger.error(f"[Workflow-{self.workflow_id}] 错误：算法节点 {node_id} 在处理过程中发生异常: {exc}")
            logger.exception(exc, exc_info=True)
            raise

    @staticmethod
    def _to_jsonable(value):
        """将 numpy / tuple 等对象转换为 JSON 可序列化结构。"""
        if isinstance(value, dict):
            return {str(k): WorkflowExecutor._to_jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [WorkflowExecutor._to_jsonable(v) for v in value]
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        return value

    def _append_detection_result_jsonl(self, node_id: str, result: Dict[str, Any], frame_timestamp: Optional[float]):
        """按行追加算法检测结果，便于对比不同部署环境的输出差异。"""
        if not DETECTION_JSONL_LOG_ENABLED or not isinstance(result, dict):
            return

        result_payload = result.get('result') or {}
        detections = result_payload.get('detections') or []
        metadata = result_payload.get('metadata') or {}
        ts = float(frame_timestamp) if isinstance(frame_timestamp, (int, float)) else time.time()
        date_str = time.strftime('%Y%m%d', time.localtime(ts))
        log_path = os.path.join(LOG_SAVE_PATH, f'detection_results_{date_str}.jsonl')

        source = self.video_source
        source_id = getattr(source, 'id', None)
        source_code = getattr(source, 'source_code', None)
        source_name = getattr(source, 'name', None)
        algorithm_info = self.algorithm_datamap.get(node_id, {}) if isinstance(self.algorithm_datamap, dict) else {}

        payload = {
            'logged_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())),
            'workflow_id': self.workflow_id,
            'workflow_name': getattr(self.workflow, 'name', None),
            'source_id': source_id,
            'source_code': source_code,
            'source_name': source_name,
            'node_id': node_id,
            'algorithm_name': algorithm_info.get('name'),
            'frame_timestamp': ts,
            'has_detection': bool(result.get('has_detection')),
            'detection_count': len(detections),
            'label_color': result.get('label_color'),
            'detections': detections,
            'metadata': metadata,
        }

        json_line = json.dumps(self._to_jsonable(payload), ensure_ascii=False, default=str)

        try:
            with DETECTION_JSONL_LOG_LOCK:
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(json_line + '\n')
        except Exception as e:
            logger.warning(f"[Workflow-{self.workflow_id}] 写入检测结果 JSONL 失败: {e}")
    
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
        if context.get('_skip_condition_routing'):
            logger.debug(
                f"[Workflow-{self.workflow_id}] 条件节点本次无有效新样本，跳过分支路由"
            )
            return False

        if not condition:
            logger.debug(f"[Workflow-{self.workflow_id}] 条件判断: 无条件，通过")
            return True

        # 向后兼容：处理旧的字符串类型条件
        if isinstance(condition, str):
            if context.get('condition_error'):
                logger.warning(
                    f"[Workflow-{self.workflow_id}] 条件节点执行失败，阻止分支 {condition}: "
                    f"{context.get('condition_error')}"
                )
                return False
            has_detection = context.get('has_detection', False)
            logger.debug(f"[Workflow-{self.workflow_id}] 条件判断(字符串): condition={condition}, has_detection={has_detection}")

            # 条件节点的分支
            if condition == 'true' or condition == 'yes':
                result = has_detection
                logger.debug(f"[Workflow-{self.workflow_id}] 条件判断(条件节点true): has_detection={has_detection}, result={result}")
                return result
            if condition == 'false' or condition == 'no':
                result = not has_detection
                logger.debug(f"[Workflow-{self.workflow_id}] 条件判断(条件节点false): has_detection={has_detection}, result={result}")
                return result
            # 旧的条件格式（向后兼容）
            if condition == 'detected':
                result = has_detection
                logger.debug(f"[Workflow-{self.workflow_id}] 条件判断(旧格式detected): has_detection={has_detection}, result={result}")
                return result
            if condition == 'not_detected':
                result = not has_detection
                logger.debug(f"[Workflow-{self.workflow_id}] 条件判断(旧格式not_detected): has_detection={has_detection}, result={result}")
                return result
            # 未知条件，默认通过
            logger.warning(f"[Workflow-{self.workflow_id}] 条件判断: 未知条件 '{condition}'，默认通过")
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

                logger.debug(
                    f"[Workflow-{self.workflow_id}] 条件判断(节点): node_id={node_id}, "
                    f"detection_count={detection_count}, target_count={target_count}, "
                    f"comparison_type={comparison_type}"
                )

                if comparison_type == "==":
                    # 精确匹配：数量必须等于阈值
                    passed = detection_count == target_count
                    logger.debug(f"[Workflow-{self.workflow_id}] 条件判断结果: {detection_count} == {target_count} = {passed}")
                    return passed
                elif comparison_type == ">=":
                    # 大于等于：数量至少达到阈值
                    passed = detection_count >= target_count
                    logger.debug(f"[Workflow-{self.workflow_id}] 条件判断结果: {detection_count} >= {target_count} = {passed}")
                    return passed

        # 默认：根据是否有检测结果判断
        passed = detection_count > 0
        logger.debug(f"[Workflow-{self.workflow_id}] 条件判断(默认): detection_count={detection_count}, passed={passed}")
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
                logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} 找到上游ROI节点 {upstream_id}，包含 {len(upstream_node.roi_regions)} 个区域")
                return upstream_node.roi_regions

            # 递归检查上游的上游（深度优先，找到第一个ROI就停止）
            upstream_roi = self._find_upstream_roi(upstream_id)
            if upstream_roi:
                return upstream_roi

        # 所有上游分支都没有找到ROI
        return None

    @staticmethod
    def _get_nested_value(payload: Any, path: Optional[str], default=None):
        if path in (None, '', '.'):
            return payload

        current = payload
        for segment in str(path).split('.'):
            if isinstance(current, dict):
                if segment not in current:
                    return default
                current = current[segment]
                continue
            if isinstance(current, list) and segment.isdigit():
                idx = int(segment)
                if idx < 0 or idx >= len(current):
                    return default
                current = current[idx]
                continue
            return default

        return current

    @staticmethod
    def _encode_frame_to_base64_jpeg(frame_bgr: Optional[np.ndarray], quality: int = 85) -> Optional[str]:
        if frame_bgr is None:
            return None

        success, encoded = cv2.imencode('.jpg', frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        if not success:
            return None
        return base64.b64encode(encoded.tobytes()).decode('ascii')

    def _build_external_api_payload(
        self,
        node_id: str,
        frame_timestamp: Optional[float],
        frame_bgr: Optional[np.ndarray],
        upstream_results: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        api_meta = self.external_api_datamap.get(node_id, {})
        node_config = self.external_api_configs.get(node_id, {})

        payload = {}
        request_template = api_meta.get('request_template')
        if isinstance(request_template, dict):
            payload.update(request_template)

        payload_template = node_config.get('payload_template')
        if isinstance(payload_template, dict):
            payload.update(payload_template)

        payload.setdefault('workflow_id', self.workflow_id)
        payload.setdefault('node_id', node_id)
        payload.setdefault('frame_timestamp', frame_timestamp)

        if self.video_source is not None:
            payload.setdefault('source_id', self.video_source.id)
            payload.setdefault('source_code', self.video_source.source_code)

        if node_config.get('include_image', True):
            image_base64 = self._encode_frame_to_base64_jpeg(frame_bgr)
            if image_base64:
                payload['image_base64'] = image_base64

        if node_config.get('include_upstream_results', True):
            payload['upstream_results'] = self._to_jsonable(upstream_results or {})

        return payload

    def _submit_external_api_request(self, node_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        api_meta = self.external_api_datamap.get(node_id, {})
        node_config = self.external_api_configs.get(node_id, {})
        endpoint_url = api_meta.get('endpoint_url')
        method = (api_meta.get('method') or 'POST').upper()
        headers = dict(api_meta.get('headers') or {})
        timeout_seconds = int(node_config.get('timeout_seconds') or api_meta.get('timeout_seconds') or 30)

        response = requests.request(
            method=method,
            url=endpoint_url,
            headers=headers,
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()

        content_type = (response.headers.get('Content-Type') or '').lower()
        if 'json' in content_type:
            body = response.json()
        else:
            try:
                body = response.json()
            except ValueError:
                body = {'raw_text': response.text}

        return {
            'status_code': response.status_code,
            'headers': dict(response.headers),
            'body': body,
        }

    def _normalize_external_api_result(self, node_id: str, response_payload: Dict[str, Any]) -> Dict[str, Any]:
        api_meta = self.external_api_datamap.get(node_id, {})
        node_config = self.external_api_configs.get(node_id, {})
        mapping = node_config.get('output_mapping') or {}
        body = response_payload.get('body') or {}

        detections = self._get_nested_value(body, mapping.get('detections_path', 'detections'), [])
        if not isinstance(detections, list):
            detections = []
        detections = BaseAlgorithm.normalize_detection_results(detections)

        metadata = self._get_nested_value(body, mapping.get('metadata_path'), None)
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {'value': metadata}

        has_detection = self._get_nested_value(body, mapping.get('has_detection_path'), None)
        if has_detection is None:
            has_detection = len(detections) > 0
        else:
            has_detection = bool(has_detection)

        label_color = self._get_nested_value(body, mapping.get('label_color_path'), None)
        if not label_color:
            label_color = node_config.get('label_color') or '#1677ff'

        metadata = {
            **metadata,
            'external_api_name': api_meta.get('name'),
            'external_api_id': api_meta.get('id'),
            'external_api_status_code': response_payload.get('status_code'),
            'execution_mode': node_config.get('execution_mode') or 'sync',
        }

        return {
            'node_id': node_id,
            'has_detection': has_detection,
            'result': {
                'detections': detections,
                'metadata': metadata,
                'raw_response': body,
            },
            'label_color': label_color,
            'upstream_node_id': node_id,
        }

    def _handle_external_api_async_submit(self, node_id: str, payload: Dict[str, Any]):
        try:
            response_payload = self._submit_external_api_request(node_id, payload)
            logger.info(
                f"[Workflow-{self.workflow_id}] 外部 API 节点 {node_id} 异步提交完成: "
                f"status={response_payload.get('status_code')}"
            )
        except Exception as exc:
            logger.error(
                f"[Workflow-{self.workflow_id}] 外部 API 节点 {node_id} 异步提交失败: {exc}",
                exc_info=True,
            )

    def _handle_external_api_node(self, node_id, context):
        frame_nv12 = context.get('frame_nv12')
        if frame_nv12 is None:
            frame_nv12 = context.get('frame')
        frame_timestamp = context.get('frame_timestamp')
        log_collector = context.get('log_collector')

        if frame_nv12 is None:
            if log_collector:
                log_collector.add_warning(node_id, "输入帧为空")
            return None

        upstream_results = self._get_upstream_results(node_id)

        try:
            api_meta = self.external_api_datamap.get(node_id, {})
            if not api_meta.get('enabled', True):
                raise RuntimeError('外部 API 已禁用')

            node_config = self.external_api_configs.get(node_id, {})
            frame_bgr = context.get('frame_bgr') if node_config.get('include_image', True) else None
            payload = self._build_external_api_payload(node_id, frame_timestamp, frame_bgr, upstream_results)
            execution_mode = node_config.get('execution_mode') or 'sync'

            if execution_mode == 'async_submit':
                if self._async_submit_executor is not None:
                    self._async_submit_executor.submit(self._handle_external_api_async_submit, node_id, payload)

                result = {
                    'node_id': node_id,
                    'has_detection': False,
                    'result': {
                        'detections': [],
                        'metadata': {
                            'execution_mode': 'async_submit',
                            'submitted': True,
                            'external_api_name': self.external_api_datamap.get(node_id, {}).get('name'),
                        },
                    },
                    'label_color': node_config.get('label_color') or '#1677ff',
                    'upstream_node_id': node_id,
                }
            else:
                response_payload = self._submit_external_api_request(node_id, payload)
                result = self._normalize_external_api_result(node_id, response_payload)

            if getattr(self, 'test_mode', False):
                result_image = self._save_test_result_image(
                    node_id=node_id,
                    frame_rgb=context.get('frame'),
                    detections=result.get('result', {}).get('detections', []),
                    label_color=result.get('label_color', '#1677ff'),
                    upstream_node_id=node_id,
                )
                if result_image:
                    result['result_image'] = result_image

            with self._state_lock:
                self.node_results_cache[node_id] = result

            if log_collector:
                detections = result.get('result', {}).get('detections', [])
                detection_count = len(detections)
                log_collector.add_detection_result(
                    node_id,
                    detections,
                    node_name=api_meta.get('name') or '外部 API 检测',
                    has_detection=result.get('has_detection', False),
                    metadata={
                        'execution_mode': execution_mode,
                        'detection_count': detection_count,
                    },
                )

            return result
        except Exception as exc:
            if log_collector:
                log_collector.add_error(node_id, f"外部 API 调用失败: {str(exc)}")
            logger.error(f"[Workflow-{self.workflow_id}] 外部 API 节点 {node_id} 执行异常: {exc}", exc_info=True)
            raise

    def _handle_algorithm_node(self, node_id, context):
        frame_nv12 = context.get('frame_nv12')
        frame_timestamp = context.get('frame_timestamp')
        log_collector = context.get('log_collector')  # 获取日志收集器

        if frame_nv12 is None:
            if log_collector:
                log_collector.add_warning(node_id, "输入帧为空")
            return None

        # 优先从上游获取ROI配置，只有上游没有ROI时才使用全局context
        upstream_roi = self._find_upstream_roi(node_id)
        if upstream_roi is not None:
            roi_regions = upstream_roi
            logger.debug(f"[Workflow-{self.workflow_id}] 算法节点 {node_id} 使用上游ROI配置，包含 {len(roi_regions)} 个区域")
        else:
            # 向后兼容：使用全局context中的ROI配置
            roi_regions = context.get('roi_regions')
            if roi_regions:
                logger.debug(f"[Workflow-{self.workflow_id}] 算法节点 {node_id} 使用全局context中的ROI配置，包含 {len(roi_regions)} 个区域")

        upstream_results = self._get_upstream_results(node_id)

        try:
            result = self._process_algorithm(node_id, frame_nv12, frame_timestamp, roi_regions, upstream_results)
            if result:
                self._append_detection_result_jsonl(node_id, result, frame_timestamp)

                if self.test_mode:
                    frame_rgb = context.get('frame')
                    detections = result.get('result', {}).get('detections', [])
                    result_image = self._save_test_result_image(
                        node_id=node_id,
                        frame_rgb=frame_rgb,
                        detections=detections,
                        label_color=result.get('label_color', '#FF0000'),
                        roi_mask=result.get('roi_mask'),
                        roi_regions=roi_regions or [],
                        upstream_node_id=node_id
                    )
                    if result_image:
                        result['result_image'] = result_image

                with self._state_lock:
                    self.node_results_cache[node_id] = result

                # 记录检测日志
                detection_count = len(result.get('result', {}).get('detections', []))
                if log_collector:
                    result_metadata = result.get('result', {}).get('metadata', {})
                    algorithm_error = result_metadata.get('error') if isinstance(result_metadata, dict) else None
                    if algorithm_error:
                        log_collector.add_error(
                            node_id,
                            f"算法调用失败，本帧按无命中处理: {algorithm_error}",
                            metadata={'detection_count': 0, **result_metadata},
                        )
                    else:
                        detections = result.get('result', {}).get('detections', [])
                        algorithm_name = self.algorithm_datamap.get(node_id, {}).get('name')
                        log_collector.add_detection_result(
                            node_id,
                            detections,
                            node_name=algorithm_name or '目标检测',
                            has_detection=result.get('has_detection', False),
                            metadata={
                                'detection_count': detection_count,
                                'algorithm_name': algorithm_name,
                            },
                        )
                        logger.debug(f"[Workflow-{self.workflow_id}] 算法节点 {node_id} 已记录检测摘要，命中 {detection_count} 个目标")
            return result
        except Exception as e:
            if log_collector:
                log_collector.add_error(node_id, f"算法执行失败: {str(e)}")
                logger.info(f"[Workflow-{self.workflow_id}] 算法节点 {node_id} 已记录错误日志")
            logger.error(f"[Workflow-{self.workflow_id}] 算法节点 {node_id} 执行异常: {e}")
            import traceback
            traceback.print_exc()
            raise
    
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
        log_collector = context.get('log_collector')

        if node.condition_kind == 'count_change':
            return self._handle_count_change_condition(node, context, log_collector)

        if node.condition_kind == 'ocr_text':
            condition_passed, metadata, error = self._evaluate_ocr_text_condition(node, upstream_results)
            context['has_detection'] = condition_passed
            context['condition_error'] = error
            if log_collector:
                if error:
                    log_collector.add_error(node_id, f"OCR 文字条件失败: {error}", metadata=metadata)
                else:
                    log_collector.add_info(
                        node_id,
                        f"OCR 文字条件{'通过' if condition_passed else '未通过'}",
                        metadata={**metadata, 'event_type': 'condition'},
                    )
            logger.info(
                f"[Workflow-{self.workflow_id}] OCR 文字条件 {node_id}: "
                f"passed={condition_passed}, error={error}, metadata={metadata}"
            )
            # 诊断写入 cache；第一次仍返回 context，避免 _execute_branch 覆盖上游 OCR 框
            with self._state_lock:
                self.node_results_cache[node_id] = {
                    'node_id': node_id,
                    'has_detection': condition_passed,
                    'result': {
                        'detections': [],
                        'metadata': {**metadata, **({'error': error} if error else {})},
                    },
                }
            return context

        context['condition_error'] = None

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
        if log_collector:
            log_collector.add_info(
                node_id,
                f"条件判断: {detection_count} {comparison_type} {target_count} = {'✓ 通过' if condition_passed else '✗ 未通过'}",
                metadata={
                    'event_type': 'condition',
                    'detection_count': detection_count,
                    'target_count': target_count,
                    'comparison_type': comparison_type,
                    'condition_passed': condition_passed
                }
            )

        logger.debug(
            f"[Workflow-{self.workflow_id}] 条件节点 {node_id}: "
            f"{detection_count} {comparison_type} {target_count} = {condition_passed}"
        )

        # 诊断写入 cache；第一次仍返回 context，避免 _execute_branch 覆盖上游检测框
        with self._state_lock:
            self.node_results_cache[node_id] = {
                'node_id': node_id,
                'has_detection': condition_passed,
                'result': {
                    'detections': [],
                    'metadata': {
                        'condition_kind': 'count',
                        'detection_count': detection_count,
                        'target_count': target_count,
                        'comparison_type': comparison_type,
                        'condition_passed': condition_passed,
                    },
                },
            }

        return context

    @staticmethod
    def _get_detection_labels_for_count(det: dict) -> set:
        if not isinstance(det, dict):
            return set()
        return {
            str(det.get(key)).strip().casefold()
            for key in ('label_name', 'label', 'class_name', 'class')
            if det.get(key) is not None and str(det.get(key)).strip()
        }

    def _handle_count_change_condition(self, node, context, log_collector=None):
        """处理单一显式上游的数量骤变条件。"""
        node_id = node.node_id
        source_node_id = node.source_node_id
        context.pop('_skip_condition_routing', None)
        connected_sources = [
            conn.get('from')
            for conn in self.connections
            if conn.get('to') == node_id
        ]
        if len(connected_sources) != 1 or not source_node_id or connected_sources[0] != source_node_id:
            error = '数量骤变条件必须且只能连接一个与配置一致的上游结果节点'
            context['has_detection'] = False
            context['condition_error'] = error
            context['_skip_condition_routing'] = True
            with self._state_lock:
                self.condition_diagnostics_cache[node_id] = {
                    'condition_kind': 'count_change',
                    'condition_passed': False,
                    'error': error,
                }
            if log_collector:
                log_collector.add_error(node_id, error, metadata={'event_type': 'condition'})
            return context

        routing_source = context.get('_routing_from_node_id')
        if routing_source is not None and routing_source != source_node_id:
            context['has_detection'] = False
            context['condition_error'] = None
            context['_skip_condition_routing'] = True
            return context

        with self._state_lock:
            source_executed = source_node_id in self.executed_nodes
            cached_source = dict(self.node_results_cache.get(source_node_id) or {})

        source_result = cached_source.get('result')
        if not source_executed or not isinstance(source_result, dict):
            context['has_detection'] = False
            context['condition_error'] = None
            context['_skip_condition_routing'] = True
            metadata = {
                'event_type': 'condition',
                'condition_kind': 'count_change',
                'sampled': False,
                'waiting_for_sample': True,
                'source_node_id': source_node_id,
                'condition_passed': False,
            }
            with self._state_lock:
                self.condition_diagnostics_cache[node_id] = metadata
            return context

        detections = source_result.get('detections') or []
        if not isinstance(detections, list):
            detections = []
        label_filter = {item.casefold() for item in node.labels if item}
        if label_filter:
            counted_detections = [
                det for det in detections
                if self._get_detection_labels_for_count(det) & label_filter
            ]
        else:
            counted_detections = detections

        frame_timestamp = context.get('frame_timestamp')
        sample_id = (source_node_id, frame_timestamp)
        metadata = self.numeric_window_detector.evaluate(
            node_id,
            sample_id,
            len(counted_detections),
            window_size=node.window_size,
            direction=node.direction,
            relative_threshold=node.relative_threshold,
            absolute_threshold=node.absolute_threshold,
            confirmation_count=node.confirmation_count,
        )
        condition_passed = bool(metadata.get('emitted', metadata.get('triggered')))
        metadata.update({
            'event_type': 'condition',
            'condition_kind': 'count_change',
            'source_node_id': source_node_id,
            'labels': list(node.labels),
            'condition_passed': condition_passed,
        })

        context['has_detection'] = condition_passed
        context['condition_error'] = None
        if metadata.get('duplicate_sample'):
            context['_skip_condition_routing'] = True
        context['result'] = source_result
        context['upstream_node_id'] = source_node_id
        with self._state_lock:
            self.condition_diagnostics_cache[node_id] = metadata

        if log_collector and not metadata.get('duplicate_sample'):
            if not metadata.get('warmed_up'):
                message = f"数量骤变预热中: {metadata['warmup_count']}/{metadata['window_size']}"
            else:
                relative_percent = metadata['relative_change'] * 100
                message = (
                    f"数量骤变: 当前 {int(metadata['current_count'])}，"
                    f"基线 {metadata['baseline']:g}，变化 {metadata['delta']:+g} "
                    f"({relative_percent:.1f}%)，"
                    f"{'✓ 触发' if condition_passed else '✗ 未触发'}"
                )
            log_collector.add_info(node_id, message, metadata=metadata)

        logger.debug(
            f"[Workflow-{self.workflow_id}] 数量骤变条件 {node_id}: {metadata}"
        )
        return context

    def _handle_time_schedule_node(self, node_id, context):
        """Pass the branch only when the current server-local minute is enabled."""
        node = self.nodes.get(node_id)
        if not isinstance(node, TimeScheduleNodeData):
            return context

        schedule_results = context.get('_time_schedule_results', {})
        outcome = schedule_results.get(node_id)
        if outcome is None:
            now = context.get('_time_schedule_now') or datetime.now().astimezone()
            enabled, matched_period = evaluate_weekly_schedule(node.weekly_schedule, now)
            outcome = {
                'enabled': enabled,
                'matched_period': matched_period,
                'current_time': now.isoformat(),
                'weekday': now.isoweekday(),
            }

        with self._state_lock:
            self.node_results_cache[node_id] = dict(outcome)

        enabled = bool(outcome.get('enabled'))
        matched_period = outcome.get('matched_period')
        log_collector = context.get('log_collector')
        if log_collector:
            if matched_period:
                detail = f"，命中 {matched_period['start']}–{matched_period['end']}"
            else:
                detail = ""
            log_collector.add_info(
                node_id,
                f"时间启用区间{'通过' if enabled else '未通过'}{detail}",
                metadata={
                    **outcome,
                    'event_type': 'time_schedule',
                },
            )

        logger.debug(
            f"[Workflow-{self.workflow_id}] 时间节点 {node_id}: "
            f"enabled={enabled}, current_time={outcome.get('current_time')}, "
            f"matched_period={matched_period}"
        )
        return context if enabled else None

    def _prepare_time_schedule_gates(self, context):
        """Evaluate schedule gates once and identify exclusively blocked descendants."""
        schedule_nodes = {
            node_id: node
            for node_id, node in self.nodes.items()
            if isinstance(node, TimeScheduleNodeData)
        }
        if not schedule_nodes:
            return

        now = context.get('_time_schedule_now') or datetime.now().astimezone()
        context['_time_schedule_now'] = now

        schedule_results = {}
        disabled_gate_ids = set()
        for node_id, node in schedule_nodes.items():
            enabled, matched_period = evaluate_weekly_schedule(node.weekly_schedule, now)
            schedule_results[node_id] = {
                'enabled': enabled,
                'matched_period': matched_period,
                'current_time': now.isoformat(),
                'weekday': now.isoweekday(),
            }
            if not enabled:
                disabled_gate_ids.add(node_id)

        context['_time_schedule_results'] = schedule_results
        if not disabled_gate_ids:
            context['_time_schedule_blocked_nodes'] = set()
            return

        blocked_candidates = set()
        pending = list(disabled_gate_ids)
        visited = set(disabled_gate_ids)
        while pending:
            current = pending.pop()
            for next_info in self.execution_graph.get(current, []):
                next_id = next_info['target']
                blocked_candidates.add(next_id)
                if next_id not in visited:
                    visited.add(next_id)
                    pending.append(next_id)

        source_ids = [
            node_id for node_id, node in self.nodes.items()
            if isinstance(node, SourceNodeData)
        ]
        active_reachable = set(source_ids)
        pending = list(source_ids)
        while pending:
            current = pending.pop()
            if current in disabled_gate_ids:
                continue
            for next_info in self.execution_graph.get(current, []):
                next_id = next_info['target']
                if next_id not in active_reachable:
                    active_reachable.add(next_id)
                    pending.append(next_id)

        context['_time_schedule_blocked_nodes'] = blocked_candidates - active_reachable

    @staticmethod
    def _evaluate_ocr_text_condition(node, upstream_results):
        source_node_id = node.source_node_id
        result = upstream_results.get(source_node_id) if source_node_id else None
        base_metadata = {
            'condition_kind': 'ocr_text',
            'source_node_id': source_node_id,
            'text_operator': node.text_operator,
            'pattern_type': node.pattern_type,
        }
        if not source_node_id:
            return False, base_metadata, '未指定 OCR 来源节点'
        if not isinstance(result, dict):
            return False, base_metadata, f'OCR 来源节点 {source_node_id} 本帧没有结果'

        result_metadata = result.get('metadata') or {}
        if result_metadata.get('ocr_checked') is not True:
            error = result_metadata.get('error') or 'OCR 未成功执行'
            return False, {**base_metadata, 'ocr_checked': False}, error

        full_text = result_metadata.get('full_text')
        if full_text is None:
            full_text = '\n'.join(
                str(item.get('text') or item.get('label_name') or '')
                for item in result.get('detections') or []
                if isinstance(item, dict)
            )
        full_text = str(full_text)
        matched = False
        matched_terms = []
        try:
            if node.pattern_type == 'regex':
                flags = 0 if node.case_sensitive else re.IGNORECASE
                matched = re.search(node.regex_pattern, full_text, flags=flags) is not None
            else:
                keywords = [str(keyword).strip() for keyword in node.keywords if str(keyword).strip()]
                haystack = full_text if node.case_sensitive else full_text.lower()
                evaluations = []
                for keyword in keywords:
                    needle = keyword if node.case_sensitive else keyword.lower()
                    hit = needle in haystack
                    evaluations.append(hit)
                    if hit:
                        matched_terms.append(keyword)
                matched = all(evaluations) if node.keyword_logic == 'all' else any(evaluations)
        except re.error as exc:
            return False, {**base_metadata, 'full_text': full_text}, f'正则表达式无效: {exc}'

        condition_passed = not matched if node.text_operator == 'not_contains' else matched
        metadata = {
            **base_metadata,
            'ocr_checked': True,
            'full_text': full_text,
            'matched': matched,
            'matched_terms': matched_terms,
            'keyword_logic': node.keyword_logic,
            'case_sensitive': node.case_sensitive,
            'condition_passed': condition_passed,
        }
        return condition_passed, metadata, None
    
    def _handle_output_node(self, node_id, context):
        self._execute_output(node_id, context)
        return context

    def _handle_webhook_node(self, node_id, context):
        """Queue a real alert event, or render a side-effect-free test preview."""
        node = self.nodes.get(node_id)
        if not isinstance(node, WebhookNodeData):
            raise ValueError(f"节点 {node_id} 不是 Webhook 节点")

        upstream_ids = [
            conn.get('from') or conn.get('from_node_id')
            for conn in self.connections
            if (conn.get('to') or conn.get('to_node_id')) == node_id
        ]
        if len(upstream_ids) != 1:
            raise ValueError("Webhook 节点必须且只能连接一个告警输出节点")
        upstream_id = upstream_ids[0]
        if not isinstance(self.nodes.get(upstream_id), AlertNodeData):
            raise ValueError("Webhook 节点只能直接连接告警输出节点")

        with self._state_lock:
            upstream_result = dict(self.node_results_cache.get(upstream_id) or {})

        if not upstream_result.get('alert_triggered'):
            cache_data = {
                'delivery_status': 'skipped',
                'provider': (node.config or {}).get('provider', 'generic'),
                'trigger_reason': upstream_result.get('trigger_reason') or '上游告警未触发',
            }
            with self._state_lock:
                self.node_results_cache[node_id] = cache_data
            return context

        alert_event = upstream_result.get('alert_event')
        if not isinstance(alert_event, dict):
            raise ValueError("上游告警节点未产生标准告警事件")

        config = validate_webhook_config(node.config or {})
        event = apply_public_media_urls(
            alert_event,
            public_base_url=config.get('public_base_url', ''),
            include_media_urls=config.get('include_media_urls', True) is not False,
        )
        prepared = prepare_webhook_request(config, event)

        if self.test_mode:
            preview_payload = self._to_jsonable(prepared.payload)
            if config['provider'] == 'bark' and isinstance(preview_payload, dict):
                preview_payload['device_key'] = '******'
            cache_data = {
                'delivery_status': 'preview',
                'provider': config['provider'],
                'event': event,
                'request_preview': {
                    'method': 'POST',
                    'payload': preview_payload,
                },
            }
        else:
            queued = webhook_dispatcher.submit(config, event)
            cache_data = {
                'delivery_status': 'queued' if queued else 'dropped',
                'provider': config['provider'],
                'event_id': event.get('event_id'),
                'trigger_reason': '已加入异步推送队列' if queued else '推送队列已满',
            }

        with self._state_lock:
            self.node_results_cache[node_id] = cache_data
        return context

    def _handle_function_node(self, node_id, context):
        """处理函数节点：直接调用内置函数，不依赖 self.algorithms"""
        frame = context.get('frame')
        frame_timestamp = context.get('frame_timestamp')
        log_collector = context.get('log_collector')  # 获取日志收集器

        if frame is None:
            if log_collector:
                log_collector.add_warning(node_id, "输入帧为空")
            logger.warning(f"[Workflow-{self.workflow_id}] 函数节点 {node_id} 输入帧为空")
            return None

        # 获取节点配置（从 workflow_data 中读取完整配置）
        node_data_dict = next((n for n in self.workflow_data.get('nodes', []) if n['id'] == node_id), {})
        if not node_data_dict:
            if log_collector:
                log_collector.add_warning(node_id, "在工作流数据中未找到")
            logger.warning(f"[Workflow-{self.workflow_id}] 函数节点 {node_id} 在工作流数据中未找到")
            return None

        node_config = node_data_dict.get('config', {})
        function_name = node_config.get('function_name', 'area_ratio')

        logger.debug(f"[Workflow-{self.workflow_id}] 函数节点 {node_id} 原始配置: {node_config}")
        logger.debug(f"[Workflow-{self.workflow_id}] 函数节点 {node_id} 开始处理，函数类型: {function_name}")

        # 获取上游结果
        upstream_results = self._get_upstream_results(node_id)
        if not upstream_results:
            if log_collector:
                log_collector.add_warning(node_id, "没有上游结果")
            logger.warning(f"[Workflow-{self.workflow_id}] 函数节点 {node_id} 没有上游结果")
            return None

        logger.debug(f"[Workflow-{self.workflow_id}] 函数节点 {node_id} 上游节点: {list(upstream_results.keys())}")

        # 导入内置函数模块
        try:
            from app.core.builtin_functions import BUILTIN_FUNCTIONS

            if function_name not in BUILTIN_FUNCTIONS:
                if log_collector:
                    log_collector.add_error(node_id, f"未知函数: {function_name}")
                logger.error(f"[Workflow-{self.workflow_id}] 未知函数: {function_name}")
                return None

            # 准备输入数据
            upstream_node_ids = list(upstream_results.keys())
            node_a_id = upstream_node_ids[0]
            result_a = upstream_results.get(node_a_id, {})
            detections_a = result_a.get('detections', [])

            logger.debug(f"[Workflow-{self.workflow_id}] 函数节点 {node_id} 从节点 {node_a_id} 获取 {len(detections_a)} 个检测结果")

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
                logger.debug(f"[Workflow-{self.workflow_id}] 调用单输入函数 {function_name}")
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
                    logger.warning(f"[Workflow-{self.workflow_id}] 双输入函数 {function_name} 需要两个上游节点，但只有 {len(upstream_node_ids)} 个")
                    return None

                node_b_id = upstream_node_ids[1]
                result_b = upstream_results.get(node_b_id, {})
                detections_b = result_b.get('detections', [])

                logger.debug(f"[Workflow-{self.workflow_id}] 调用双输入函数 {function_name}，节点A: {node_a_id}({len(detections_a)}个), 节点B: {node_b_id}({len(detections_b)}个)")

                results = func(detections_a, detections_b, function_config)

                # 收集匹配的检测框
                all_detections = []
                for r in results:
                    all_detections.append(r['object_a'])
                    all_detections.append(r['object_b'])

            logger.debug(f"[Workflow-{self.workflow_id}] 函数节点 {node_id} 处理完成，匹配数: {len(results)}, 返回检测数: {len(all_detections)}")

            # 记录函数执行日志
            if log_collector:
                log_collector.add_info(
                    node_id,
                    f"函数 {function_name} 处理完成，匹配数: {len(results)}",
                    metadata={
                        'event_type': 'function',
                        'function_name': function_name,
                        'matched_count': len(results),
                    }
                )
                log_collector.add_detection_result(
                    node_id,
                    all_detections,
                    node_name=f"函数 {function_name} 输出",
                    has_detection=len(all_detections) > 0,
                    metadata={
                        'function_name': function_name,
                        'matched_count': len(results),
                    },
                )
                logger.debug(f"[Workflow-{self.workflow_id}] 函数节点 {node_id} 已记录日志: 函数 {function_name} 处理完成，匹配数: {len(results)}")

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
                'roi_mask': None,
                'label_color': '#00FF00',  # 函数节点使用绿色
                'upstream_node_id': node_a_id
            }

            with self._state_lock:
                self.node_results_cache[node_id] = result
            return result

        except Exception as exc:
            if log_collector:
                log_collector.add_error(node_id, f"处理异常: {str(exc)}")
            logger.error(f"[Workflow-{self.workflow_id}] 函数节点 {node_id} 处理异常: {exc}")
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
        with self._state_lock:
            cache_snapshot = dict(self.node_results_cache)

        # 优先从连线中获取上游节点（更可靠）
        for conn in self.connections:
            if conn['to'] == node_id:
                from_node_id = conn['from']
                if from_node_id in cache_snapshot:
                    cached = cache_snapshot[from_node_id]
                    # 只处理有 result 的缓存（source 节点没有 result）
                    if 'result' in cached:
                        upstream_results[from_node_id] = cached['result']

        # 如果连线中没有结果，回退到 input_nodes 配置（向后兼容）
        if not upstream_results:
            node = getattr(self, 'nodes', {}).get(node_id)
            if isinstance(node, FunctionNodeData) and node.input_nodes:
                for input_node_id in node.input_nodes:
                    if input_node_id in cache_snapshot:
                        cached = cache_snapshot[input_node_id]
                        if 'result' in cached:
                            upstream_results[input_node_id] = cached['result']

        return upstream_results
    
    def _handle_roi_draw_node(self, node_id, context):
        frame = context.get('frame')
        if frame is None:
            logger.warning(f"[Workflow-{self.workflow_id}] 热区绘制节点 {node_id} 输入帧为空")
            return None

        node = self.nodes.get(node_id)
        if not isinstance(node, RoiDrawNodeData):
            logger.warning(f"[Workflow-{self.workflow_id}] 节点 {node_id} 不是RoiDrawNodeData类型")
            return context

        roi_regions = node.roi_regions
        if not roi_regions or len(roi_regions) == 0:
            logger.warning(f"[Workflow-{self.workflow_id}] 热区绘制节点 {node_id} 未配置ROI区域")
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
                    logger.debug(
                        f"[Workflow-{self.workflow_id}] 热区绘制节点 {node_id} 区域{idx + 1}: "
                        f"名称={roi.get('name', '未命名')}, "
                        f"模式={roi.get('mode', 'N/A')}, "
                        f"顶点数={len(vertex_data)}, "
                        f"顶点坐标(相对): [{vertex_str}]"
                    )
                else:
                    # 绝对坐标格式 [[x1, y1], [x2, y2], ...]
                    vertex_str = ", ".join([f"({p[0]}, {p[1]})" for p in vertex_data])
                    logger.debug(
                        f"[Workflow-{self.workflow_id}] 热区绘制节点 {node_id} 区域{idx + 1}: "
                        f"名称={roi.get('name', '未命名')}, "
                        f"模式={roi.get('mode', 'N/A')}, "
                        f"顶点数={len(vertex_data)}, "
                        f"顶点坐标(绝对): [{vertex_str}]"
                    )
            else:
                logger.warning(f"[Workflow-{self.workflow_id}] 热区绘制节点 {node_id} 区域{idx + 1}: 没有顶点数据")

        logger.debug(f"[Workflow-{self.workflow_id}] 热区绘制节点 {node_id} 共配置了 {len(roi_regions)} 个ROI区域")
        logger.debug(f"[Workflow-{self.workflow_id}] 热区绘制节点 {node_id} 完整ROI数据: {roi_regions}")

        return context

    def _has_gated_incoming(self, node_id) -> bool:
        """是否存在显式门控入边。只阻止拓扑层独立调度，分支层仍是 per-edge OR。"""
        for conn in self.connections:
            if (conn.get('to') or conn.get('to_node_id')) != node_id:
                continue
            if conn.get('condition') in self._GATE_CONDITIONS:
                return True
        return False

    def _first_gated_incoming(self, node_id):
        """第一条门控入边的 (from_id, condition)。"""
        for conn in self.connections:
            if (conn.get('to') or conn.get('to_node_id')) != node_id:
                continue
            condition = conn.get('condition')
            if condition in self._GATE_CONDITIONS:
                return conn.get('from') or conn.get('from_node_id'), condition
        return None, None

    def _is_ocr_algorithm_node(self, node_id) -> bool:
        info = (getattr(self, 'algorithm_datamap', None) or {}).get(node_id) or {}
        if info.get('algorithm_type') == 'ocr':
            return True
        algo = (getattr(self, 'algorithms', None) or {}).get(node_id)
        return type(algo).__name__ == 'OCRAlgorithm'

    @staticmethod
    def _is_skip_result(result) -> bool:
        if not isinstance(result, dict):
            return False
        if result.get('skipped') is True:
            return True
        inner = result.get('result')
        if isinstance(inner, dict):
            metadata = inner.get('metadata') or {}
            if isinstance(metadata, dict) and metadata.get('execution_state') == 'skipped':
                return True
        return False

    def _cache_detections(self, cached) -> list:
        if not isinstance(cached, dict):
            return []
        result = cached.get('result')
        if isinstance(result, dict):
            detections = result.get('detections')
            if isinstance(detections, list):
                return detections
        return []

    def _resolve_gate_reason_code(self, node_id):
        upstream_id, _condition = self._first_gated_incoming(node_id)
        if not upstream_id:
            return 'gate_failed', None

        with self._state_lock:
            executed = upstream_id in self.executed_nodes
            cached = (
                dict(self.node_results_cache[upstream_id])
                if upstream_id in self.node_results_cache
                else None
            )

        if not executed:
            return 'upstream_not_executed', upstream_id
        if cached is not None and not self._cache_detections(cached):
            return 'upstream_empty', upstream_id
        return 'gate_failed', upstream_id

    def _write_gate_skip_sentinel(self, node_id, context):
        reason_code, upstream_id = self._resolve_gate_reason_code(node_id)
        metadata = {
            'execution_state': 'skipped',
            'reason_code': reason_code,
            'skipped': True,
        }
        if self._is_ocr_algorithm_node(node_id):
            metadata['ocr_checked'] = False

        sentinel = {
            'node_id': node_id,
            'has_detection': False,
            'skipped': True,
            'result': {
                'detections': [],
                'metadata': metadata,
            },
        }

        logger.info(
            f"[Workflow-{self.workflow_id}] 节点 {node_id} 门控跳过: "
            f"reason={reason_code}, from={upstream_id}"
        )

        log_collector = context.get('log_collector') if isinstance(context, dict) else None
        if log_collector:
            log_collector.add_info(
                node_id,
                _GATE_SKIP_REASON_MESSAGES.get(reason_code, f'已跳过：{reason_code}'),
                metadata={'event_type': 'skipped', 'reason_code': reason_code},
            )

        with self._state_lock:
            self.node_results_cache[node_id] = sentinel
            self.skipped_nodes.add(node_id)
            self.execution_results[node_id] = {
                'success': True,
                'skipped': True,
                'execution_time': 0,
            }

    def _copy_cached_node_result(self, node_id, context):
        cached = self.node_results_cache.get(node_id)
        if isinstance(cached, dict):
            return dict(cached)
        return context
    
    def _execute_node(self, node_id, context):
        """
        执行单个节点（核心逻辑）

        测试和运行模式使用完全相同的执行路径
        """
        node = self.nodes.get(node_id)
        if not node:
            logger.warning(f"[Workflow-{self.workflow_id}] 节点 {node_id} 不在 self.nodes 中，可用节点: {list(self.nodes.keys())}")
            return None

        if node_id in context.get('_time_schedule_blocked_nodes', set()):
            logger.debug(
                f"[Workflow-{self.workflow_id}] 节点 {node_id} 位于未启用的时间分支，跳过执行"
            )
            return None

        with self._state_lock:
            already = node_id in self.executed_nodes or node_id in self.skipped_nodes
            cached = (
                dict(self.node_results_cache[node_id])
                if already and node_id in self.node_results_cache
                else None
            )
        if already:
            logger.debug(
                f"[Workflow-{self.workflow_id}] 节点 {node_id} 本帧已执行或已 skip，跳过重复调度"
            )
            return cached if cached is not None else context

        had_last_exec = node_id in self.node_last_exec_time
        prev_last_exec = self.node_last_exec_time.get(node_id)

        if not self._should_execute_node(node_id):
            # 禁止对本帧 skip 哨兵 del cache（skipped_nodes 已在入口返回，此处再守一层）
            if node_id in self.skipped_nodes:
                return self._copy_cached_node_result(node_id, context)
            # 算法节点或函数节点因间隔被跳过时，清除旧缓存结果，避免下游节点使用过期数据
            if (isinstance(node, (AlgorithmNodeData, ExternalApiNodeData, FunctionNodeData))
                and node_id in self.node_results_cache):
                logger.debug(f"[Workflow-{self.workflow_id}] 节点 {node_id} 因间隔被跳过，清除旧缓存结果")
                with self._state_lock:
                    if node_id in self.node_results_cache:
                        del self.node_results_cache[node_id]
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
            logger.warning(f"[Workflow-{self.workflow_id}] 未知节点类型: {node_type} (节点 {node_id})")

            # 记录失败状态
            execution_success = False
            execution_error = f"未知节点类型: {node_type}"

            # 更新追踪状态
            with self._state_lock:
                self.execution_results[node_id] = {
                    'success': False,
                    'error': execution_error,
                    'execution_time': int((time.time() - start_time) * 1000)
                }
            return context

        if WORKFLOW_FRAME_LOGS_ENABLED:
            logger.info(
                f"[Workflow-{self.workflow_id}] 执行节点 {node_id} "
                f"(类型: {node_type})"
            )

        try:
            result = handler(node_id, context)

            if self._is_skip_result(result):
                with self._state_lock:
                    if had_last_exec:
                        self.node_last_exec_time[node_id] = prev_last_exec
                    else:
                        self.node_last_exec_time.pop(node_id, None)
                    self.skipped_nodes.add(node_id)
                    if isinstance(result, dict):
                        self.node_results_cache[node_id] = result
                    self.execution_results[node_id] = {
                        'success': True,
                        'skipped': True,
                        'execution_time': 0,
                    }
                return result

            # 记录成功执行
            if node_id not in self.executed_nodes:
                with self._state_lock:
                    if node_id not in self.executed_nodes:
                        self.executed_nodes.append(node_id)

            with self._state_lock:
                self.execution_results[node_id] = {
                    'success': True,
                    'execution_time': int((time.time() - start_time) * 1000)
                }

            return result

        except Exception as e:
            execution_success = False
            execution_error = str(e)
            logger.error(f"[Workflow-{self.workflow_id}] 节点 {node_id} 执行异常: {e}")
            import traceback
            traceback.print_exc()

            # 记录失败状态
            with self._state_lock:
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
                logger.debug(f"[Workflow-{self.workflow_id}] {node_id} -> {next_id} 条件不满足: {condition}")
                continue

            logger.debug(f"[Workflow-{self.workflow_id}] {node_id} -> {next_id} 条件满足，继续执行")
            # 继续执行下游节点（分支隔离 context，避免污染）
            branch_context = context.copy()
            branch_context['_routing_from_node_id'] = node_id
            self._execute_branch(next_id, branch_context)

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
            logger.debug(f"[Workflow-{self.workflow_id}] 并行执行层级节点: {[f'{nid}({self.nodes[nid].node_type})' for nid in level_nodes]}")
            future_to_node = {
                executor.submit(self._execute_level_node, nid, context.copy()): nid
                for nid in level_nodes
            }

            for future in as_completed(future_to_node):
                node_id = future_to_node[future]
                try:
                    future.result()
                except Exception as exc:
                    logger.error(f"[Workflow-{self.workflow_id}] 节点 {node_id} 执行异常: {exc}", exc_info=True)
        else:
            # 串行执行当前层级的节点
            logger.debug(f"[Workflow-{self.workflow_id}] 串行执行层级节点: {[f'{nid}({self.nodes[nid].node_type})' for nid in level_nodes]}")
            for node_id in level_nodes:
                self._execute_level_node(node_id, context.copy())

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

        if node_id in context.get('_time_schedule_blocked_nodes', set()):
            logger.debug(
                f"[Workflow-{self.workflow_id}] 节点 {node_id} 位于未启用的时间分支，跳过独立调度"
            )
            return

        if self._has_gated_incoming(node_id):
            with self._state_lock:
                already_executed = node_id in self.executed_nodes
                already_skipped = node_id in self.skipped_nodes
            if already_skipped:
                return
            if not already_executed:
                if isinstance(node, (AlgorithmNodeData, FunctionNodeData, ExternalApiNodeData)):
                    self._write_gate_skip_sentinel(node_id, context)
                    return
                if isinstance(node, ConditionNodeData):
                    return

        # 对于函数节点，特殊处理
        if isinstance(node, FunctionNodeData):
            if not self._check_function_node_ready(node_id):
                # 上游节点未完成（可能因为执行间隔跳过），静默跳过
                return

            # 执行函数节点
            result = self._execute_single_node(node_id, context)
            if result is None:
                logger.debug(f"[Workflow-{self.workflow_id}] 函数节点 {node_id} 返回None")
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
                    logger.debug(f"[Workflow-{self.workflow_id}] 函数节点 {node_id} -> {next_id} 条件满足，继续执行")
                    # 传递原始 context 而不是 result，确保 log_collector 能被传递到 Alert 节点
                    branch_context = context.copy()
                    branch_context['_routing_from_node_id'] = node_id
                    self._execute_branch(next_id, branch_context)
        elif isinstance(node, (AlgorithmNodeData, ExternalApiNodeData)):
            # 算法型节点：执行并继续执行下游（形成完整分支）
            self._execute_branch(node_id, context)
        else:
            # 其他节点（roi_draw, condition, output, alert）：直接执行单个节点
            self._execute_single_node(node_id, context)

    def _execute_by_topology_levels(self, executor, context):
        """
        按拓扑层级执行所有节点
        """
        self._prepare_time_schedule_gates(context)
        levels = self._build_topology_levels()
        logger.debug(f"[Workflow-{self.workflow_id}] 共有 {len(levels)} 个拓扑层级，开始按层级执行...")

        for level_idx, level_nodes in enumerate(levels):
            logger.debug(f"[Workflow-{self.workflow_id}] 执行层级 {level_idx + 1}/{len(levels)}")

            # 特殊处理第一层（source节点）
            if level_idx == 0:
                # 第一层通常是source，直接执行
                for node_id in level_nodes:
                    self._execute_single_node(node_id, context)
            else:
                # 其他层级按并行或串行执行
                self._execute_level_nodes(level_nodes, context, executor)

    def _cache_output_result(self, node_id: str, alert_triggered: bool, detection_count: int, trigger_reason: str,
                             result_image: Optional[str] = None, upstream_node_id: Optional[str] = None,
                             alert_event: Optional[Dict[str, Any]] = None):
        """缓存输出节点结果，供测试结果汇总使用"""
        cache_data = {
            'alert_triggered': bool(alert_triggered),
            'has_detection': bool(alert_triggered),
            'detection_count': int(detection_count or 0),
            'trigger_reason': trigger_reason
        }
        if result_image:
            cache_data['result_image'] = result_image
        if upstream_node_id:
            cache_data['upstream_node_id'] = upstream_node_id
        if alert_event is not None:
            cache_data['alert_event'] = alert_event

        with self._state_lock:
            self.node_results_cache[node_id] = cache_data

    @staticmethod
    def _parse_label_color_bgr(color_hex: str):
        """将 #RRGGBB 颜色转换为 OpenCV 使用的 BGR 元组"""
        if not color_hex:
            return 0, 0, 255
        try:
            color = color_hex.lstrip('#')
            if len(color) != 6:
                return 0, 0, 255
            r = int(color[0:2], 16)
            g = int(color[2:4], 16)
            b = int(color[4:6], 16)
            return b, g, r
        except Exception:
            return 0, 0, 255

    def _draw_test_detections(self, frame_rgb: np.ndarray, detections: List[dict], label_color: str) -> Optional[np.ndarray]:
        """在测试帧上绘制检测框（兜底方案）"""
        if frame_rgb is None:
            return None

        canvas = cv2.cvtColor(frame_rgb.copy(), cv2.COLOR_RGB2BGR)
        box_color = self._parse_label_color_bgr(label_color)

        for det in detections or []:
            box = det.get('box') or det.get('bbox')
            box_coords = BaseAlgorithm._normalize_box_for_canvas(box, canvas.shape[1], canvas.shape[0])
            if box_coords is None:
                continue

            x1, y1, x2, y2 = box_coords

            cv2.rectangle(canvas, (x1, y1), (x2, y2), box_color, 2)

            label_name = det.get('label_name') or det.get('label') or 'object'
            confidence = det.get('confidence')
            text = f"{label_name} {confidence:.2f}" if isinstance(confidence, (int, float)) else str(label_name)
            text_y = y1 - 8 if y1 > 20 else y1 + 18
            cv2.putText(canvas, text, (x1, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, box_color, 2, cv2.LINE_AA)

        return canvas

    def _resolve_visualizer_node_id(self, node_id: Optional[str]) -> Optional[str]:
        """递归向上查找最近的算法节点，用于告警图片可视化。"""
        if not node_id:
            return None

        visited = set()
        stack = [node_id]

        while stack:
            current_id = stack.pop()
            if not current_id or current_id in visited:
                continue
            visited.add(current_id)

            if current_id in self.algorithms:
                return current_id

            for conn in self.connections:
                if conn['to'] == current_id:
                    upstream_id = conn['from']
                    if upstream_id not in visited:
                        stack.append(upstream_id)

        return None

    def _save_visualized_frame(self, frame_rgb: np.ndarray, detections: List[dict], save_path: str,
                               label_color: str = '#FF0000', roi_mask=None, roi_regions=None,
                               upstream_node_id: Optional[str] = None) -> bool:
        """
        保存带标注的告警图片。
        优先复用上游算法的 visualize；找不到算法节点时回退到通用框绘制，避免退化成原始帧。
        """
        if frame_rgb is None:
            return False

        visualizer_node_id = self._resolve_visualizer_node_id(upstream_node_id)

        try:
            if visualizer_node_id and visualizer_node_id in self.algorithms:
                self.algorithms[visualizer_node_id].visualize(
                    frame_rgb,
                    detections or [],
                    save_path=save_path,
                    label_color=label_color,
                    roi_mask=roi_mask,
                    roi_regions=roi_regions or []
                )
            else:
                BaseAlgorithm.visualize(
                    frame_rgb,
                    detections or [],
                    save_path=save_path,
                    label_color=label_color,
                    roi_mask=roi_mask,
                    roi_regions=roi_regions or []
                )
            return os.path.exists(save_path)
        except Exception as e:
            logger.warning(f"[Workflow-{self.workflow_id}] 保存告警可视化图片失败，回退到原始帧: {e}")
            try:
                save_frame(frame_rgb, save_path)
                return os.path.exists(save_path)
            except Exception:
                return False

    def _get_storage_pressure(self) -> StoragePressure:
        """短周期缓存磁盘检查，兼顾落盘保护与实时告警吞吐。"""
        now = time.monotonic()
        if self._storage_pressure is None or now - self._storage_pressure_checked_at >= 2.0:
            self.recording_config = get_recording_storage_config()
            try:
                self._storage_pressure = measure_storage_pressure(self.recording_config)
            except Exception as exc:
                logger.error(f"[Workflow-{self.workflow_id}] 无法读取磁盘水位，降级为仅保留元数据: {exc}")
                self._storage_pressure = StoragePressure(
                    level=StoragePressureLevel.METADATA_ONLY,
                    used_percent=100.0,
                    total_bytes=0,
                    used_bytes=0,
                    free_bytes=0,
                )
            self._storage_pressure_checked_at = now
        return self._storage_pressure

    def _save_test_result_image(self, node_id: str, frame_rgb: np.ndarray, detections: List[dict], label_color: str = '#FF0000',
                                roi_mask=None, roi_regions=None, upstream_node_id: Optional[str] = None) -> Optional[str]:
        """保存测试可视化图片并返回可访问 URL"""
        if frame_rgb is None:
            return None

        date_dir = time.strftime('%Y%m%d')
        rel_dir = os.path.join('workflow_test', f'workflow_{self.workflow_id}', date_dir, 'result_images')
        os.makedirs(os.path.join(FRAME_SAVE_PATH, rel_dir), exist_ok=True)

        ts = int(time.time() * 1000)
        rel_path = os.path.join(rel_dir, f'{node_id}_{ts}.jpg')
        abs_path = os.path.join(FRAME_SAVE_PATH, rel_path)

        try:
            if not self._save_visualized_frame(
                frame_rgb=frame_rgb,
                detections=detections or [],
                save_path=abs_path,
                label_color=label_color,
                roi_mask=roi_mask,
                roi_regions=roi_regions or [],
                upstream_node_id=upstream_node_id
            ):
                return None

            if os.path.exists(abs_path):
                return build_public_media_url('image', rel_path)
        except Exception as e:
            logger.warning(f"[Workflow-{self.workflow_id}] 生成测试可视化图片失败: {e}")

        return None

    def _get_workflow_node_name(self, node_id: Optional[str], default: str = '检测节点') -> str:
        """从工作流原始配置中取用户可读的节点名称。"""
        if not node_id:
            return default
        for node_data in self.workflow_data.get('nodes', []):
            if node_data.get('id') == node_id:
                return str(node_data.get('name') or default)
        return default

    def _get_alert_log_scope(self, alert_node_id: str) -> set:
        """返回当前告警节点及其所有上游祖先，用于隔离兄弟分支日志。"""
        reverse_graph = defaultdict(set)
        for connection in self.connections:
            source_id = connection.get('from') or connection.get('from_node_id')
            target_id = connection.get('to') or connection.get('to_node_id')
            if source_id and target_id:
                reverse_graph[target_id].add(source_id)

        scope = {alert_node_id}
        pending = [alert_node_id]
        while pending:
            current_id = pending.pop()
            for upstream_id in reverse_graph.get(current_id, ()):
                if upstream_id not in scope:
                    scope.add(upstream_id)
                    pending.append(upstream_id)
        return scope

    @staticmethod
    def _compose_alert_message(
        alert_node: AlertNodeData,
        log_collector: Optional[ExecutionLogCollector],
        node_ids: Optional[Collection[str]] = None,
    ) -> str:
        """组合用户自定义文案与实际执行过程。"""
        custom_message = (alert_node.alert_message or '').strip()
        execution_details = ''
        if log_collector:
            execution_details = log_collector.build_alert_message(
                format_type=alert_node.message_format or 'detailed',
                include_metadata=False,
                node_ids=node_ids,
            )
            if execution_details == '无执行日志':
                execution_details = ''

        # 历史工作流普遍保存了默认占位文案“检测到目标”。
        # 存在真实执行详情时不再把占位文案当成告警主体。
        placeholder_messages = {'检测到目标', '检测到目标。', '告警触发', '告警触发。'}
        if execution_details and custom_message in placeholder_messages:
            custom_message = ''

        if custom_message and execution_details:
            return f"{custom_message}\n\n执行详情:\n{execution_details}"
        return execution_details or custom_message or '告警触发（未记录执行详情）'

    @staticmethod
    def _format_window_trigger_log(trigger_condition: Dict[str, Any], trigger_stats: Dict[str, Any]) -> str:
        """把时间窗口统计和通过规则格式化为可读记录。"""
        mode = trigger_condition.get('mode', 'ratio')
        threshold = trigger_condition.get('threshold', 0.3)
        mode_rules = {
            'ratio': f"命中比例 ≥ {float(threshold):.1%}",
            'count': f"命中帧数 ≥ {threshold}",
            'consecutive': f"最大连续命中 ≥ {threshold} 帧",
        }
        return (
            f"时间窗口条件通过：{trigger_stats.get('window_size', trigger_condition.get('window_size', 30))} 秒内"
            f"处理 {trigger_stats.get('total_count', 0)} 帧，命中 {trigger_stats.get('detection_count', 0)} 帧"
            f"（{trigger_stats.get('detection_ratio', 0):.1%}），最大连续命中 "
            f"{trigger_stats.get('max_consecutive', 0)} 帧；规则：{mode_rules.get(mode, mode)}"
        )

    @staticmethod
    def _format_direct_trigger_log(has_detection: bool, detection_count: int) -> str:
        """格式化未启用时间窗口时的当前帧命中原因。"""
        if has_detection and detection_count == 0:
            return "当前帧触发条件通过：上游命中信号为真，但未返回目标明细；未启用时间窗口"
        return f"当前帧触发条件通过：上游有效结果 {detection_count} 个，未启用时间窗口"

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

            cached_data = None
            if upstream_node_id:
                with self._state_lock:
                    if upstream_node_id in self.node_results_cache:
                        cached_data = self.node_results_cache[upstream_node_id]
            if cached_data:
                # 将缓存的数据合并到 context
                context.update({
                    'result': cached_data.get('result'),
                    'has_detection': cached_data.get('has_detection'),
                    'roi_mask': cached_data.get('roi_mask'),
                    'label_color': cached_data.get('label_color', '#FF0000'),
                    'upstream_node_id': cached_data.get('upstream_node_id', upstream_node_id)
                })
                logger.info(f"[Workflow-{self.workflow_id}] Alert 节点 {node_id} 从缓存获取上游节点 {upstream_node_id} 的结果")

        # 再次检查必需数据
        if 'frame' not in context or 'result' not in context:
            if log_collector:
                log_collector.add_warning(node_id, "缺少必需数据：上游节点未产生结果")
            logger.warning(f"[Workflow-{self.workflow_id}] 输出节点 {node_id} 缺少必需数据：上游节点未执行或未产生结果")
            return

        # 获取 Alert 节点配置
        alert_node = self.nodes.get(node_id)
        if not isinstance(alert_node, AlertNodeData):
            if log_collector:
                log_collector.add_warning(node_id, "不是 Alert 节点")
            logger.warning(f"[Workflow-{self.workflow_id}] 节点 {node_id} 不是 Alert 节点")
            return

        alert_log_scope = self._get_alert_log_scope(node_id)

        # 从 context 获取检测数据
        has_detection = context.get('has_detection', False)
        frame = context['frame']
        frame_timestamp = context['frame_timestamp']
        result = context['result']
        detection_count = len(result.get('detections', []))
        roi_mask = context.get('roi_mask')
        label_color = context.get('label_color', '#FF0000')  # 默认红色
        storage_pressure = self._get_storage_pressure() if has_detection else None
        media_allowed = storage_pressure is None or storage_pressure.allow_media

        # 兼容缓存结果、旧节点和其他未主动写入日志的执行器。
        # 只要当前 Alert 收到了检测结果，最终正文就不能只剩固定文案。
        if log_collector and not log_collector.has_event('detection', node_ids=alert_log_scope):
            upstream_node_id = context.get('upstream_node_id')
            log_collector.add_detection_result(
                upstream_node_id or node_id,
                result.get('detections', []),
                node_name=self._get_workflow_node_name(upstream_node_id),
                has_detection=has_detection,
                metadata={'recovered_at_alert_node': True},
            )

        logger.debug(
            f"[Workflow-{self.workflow_id}] Alert 节点 {node_id} 收到结果: has_detection={has_detection}, 检测数={detection_count}")

        # 加载触发条件配置（窗口检测）- 必须在记录之前加载
        trigger_condition = alert_node.trigger_condition
        if trigger_condition:
            self.window_detector.load_trigger_condition(
                source_id=self.video_source.id,
                node_id=node_id,
                trigger_config=trigger_condition
            )
        else:
            # 未配置触发条件，使用默认配置（不进行窗口检测，直接通过）
            logger.debug(f"[Workflow-{self.workflow_id}] 告警节点 {node_id} 未配置触发条件，所有检测都将触发")
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
            logger.debug(f"[Workflow-{self.workflow_id}] 告警节点 {node_id} 未配置抑制，告警不会被抑制")
            self.window_detector.load_suppression(
                source_id=self.video_source.id,
                node_id=node_id,
                suppression_config={'enable': False}
            )

        trigger_time = frame_timestamp

        # 检查是否启用了窗口检测
        window_detection_enabled = trigger_condition and trigger_condition.get('enable', False)

        # 如果启用了窗口检测，保存检测图片（用于窗口检测图片序列）
        # 注意：每帧的记录已在 _record_to_window_detector_for_all_alerts 中完成
        # 这里只负责保存检测到目标时的图片
        if window_detection_enabled and has_detection and media_allowed:
            # 保存检测图片到临时路径（用于窗口检测图片序列）
            filepath = f"{self.video_source.source_code}/.window_detection/frame_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 10000}_wf{self.workflow_id}.jpg"
            filepath_absolute = os.path.join(FRAME_SAVE_PATH, filepath)

            # 获取上游节点 ID（用于可视化）
            upstream_node_id = context.get('upstream_node_id')

            # 准备ROI配置
            effective_roi_regions = []
            if upstream_node_id:
                # 先查找上游的ROI节点配置
                upstream_roi = self._find_upstream_roi(upstream_node_id)
                if upstream_roi is not None:
                    effective_roi_regions = upstream_roi
                else:
                    # 回退到算法节点自身的ROI配置
                    effective_roi_regions = self.algorithm_roi_configs.get(upstream_node_id, [])

            self._save_visualized_frame(
                frame_rgb=frame,
                detections=result.get("detections"),
                save_path=filepath_absolute,
                label_color=label_color,
                roi_mask=roi_mask,
                roi_regions=effective_roi_regions,
                upstream_node_id=upstream_node_id
            )

            # 同时保存原始图片（.ori.jpg）
            filepath_ori = f"{filepath}.ori.jpg"
            filepath_ori_absolute = os.path.join(FRAME_SAVE_PATH, filepath_ori)
            save_frame(frame, filepath_ori_absolute)

            logger.debug(f"[Workflow-{self.workflow_id}] 保存窗口检测图片: {filepath}, 原始图片: {filepath_ori}")

            # 更新窗口检测器中的图片路径
            self.window_detector.update_last_image_path(
                source_id=self.video_source.id,
                node_id=node_id,
                image_path=filepath
            )

        # 如果没有检测，直接返回（不进行后续的告警处理）
        # 注意：已经记录到窗口检测器，用于窗口统计
        if not has_detection:
            self._cache_output_result(
                node_id=node_id,
                alert_triggered=False,
                detection_count=detection_count,
                trigger_reason='上游条件未通过（has_detection=False）'
            )
            logger.debug(f"[Workflow-{self.workflow_id}] Alert 节点 {node_id} 未检测到目标，跳过告警处理")
            return

        # 从 Alert 节点配置获取告警信息
        alert_type = alert_node.alert_type or "detection"
        alert_level = alert_node.alert_level or "info"

        # 使用日志收集器生成告警消息
        if log_collector:
            # 获取消息格式类型（默认 'detailed'）
            message_format = alert_node.message_format or 'detailed'

            logger.debug(f"[Workflow-{self.workflow_id}] Alert节点 {node_id} 开始构建告警消息，格式: {message_format}")
            logger.debug(f"[Workflow-{self.workflow_id}] 日志收集器 ID: {id(log_collector)}")
            logger.debug(f"[Workflow-{self.workflow_id}] 日志收集器包含 {len(log_collector.logs)} 条日志")

            # 打印所有日志
            for idx, log in enumerate(log_collector.logs):
                logger.debug(f"[Workflow-{self.workflow_id}] 日志 {idx + 1}: [{log['node_id']}] {log['content']}")

            alert_message = self._compose_alert_message(alert_node, log_collector, alert_log_scope)

            logger.debug(f"[Workflow-{self.workflow_id}] 最终告警消息: {alert_message}")
        else:
            # 如果没有日志收集器，使用原始消息
            alert_message = alert_node.alert_message or ""
            logger.warning(f"[Workflow-{self.workflow_id}] Alert节点 {node_id} 没有日志收集器")

        # 步骤1：检查触发条件（窗口检测）
        trigger_passed, trigger_stats = self.window_detector.check_condition(
            source_id=self.video_source.id,
            node_id=node_id,
            current_time=trigger_time
        )

        if not trigger_passed:
            self._cache_output_result(
                node_id=node_id,
                alert_triggered=False,
                detection_count=detection_count,
                trigger_reason='不满足窗口触发条件'
            )
            if trigger_stats:
                logger.debug(
                    f"[Workflow-{self.workflow_id}] 输出节点 {node_id} 不满足触发条件，跳过告警 "
                    f"(检测: {trigger_stats['detection_count']}/{trigger_stats['total_count']} 帧, "
                    f"比例: {trigger_stats['detection_ratio']:.2%}, "
                    f"连续: {trigger_stats['max_consecutive']} 次)"
                )
            return

        if trigger_stats:
            logger.info(
                f"[Workflow-{self.workflow_id}] 输出节点 {node_id} 满足触发条件 "
                f"(检测: {trigger_stats['detection_count']}/{trigger_stats['total_count']} 帧, "
                f"比例: {trigger_stats['detection_ratio']:.2%}, "
                f"连续: {trigger_stats['max_consecutive']} 次)"
            )

        if log_collector:
            if trigger_stats:
                trigger_log = self._format_window_trigger_log(trigger_condition, trigger_stats)
            else:
                trigger_log = self._format_direct_trigger_log(has_detection, detection_count)
            log_collector.add_info(
                node_id,
                trigger_log,
                metadata={
                    'event_type': 'trigger',
                    'condition_passed': True,
                    'trigger_stats': trigger_stats or {},
                },
            )

        # 步骤2：检查抑制期（触发后冷却期）
        not_suppressed, suppression_stats = self.window_detector.check_suppression(
            source_id=self.video_source.id,
            node_id=node_id,
            current_time=trigger_time
        )

        if not not_suppressed:
            self._cache_output_result(
                node_id=node_id,
                alert_triggered=False,
                detection_count=detection_count,
                trigger_reason='命中抑制期，告警被跳过'
            )
            # 在抑制期内，跳过告警
            if suppression_stats:
                logger.debug(
                    f"[Workflow-{self.workflow_id}] 输出节点 {node_id} 在抑制期内，跳过告警 "
                    f"(剩余冷却时间: {suppression_stats['cooldown_remaining']:.2f}秒)"
                )
            return

        if log_collector and suppression and suppression.get('enable', False):
            log_collector.add_info(
                node_id,
                f"告警抑制检查通过：当前不在 {suppression.get('seconds', 60)} 秒冷却期内",
                metadata={
                    'event_type': 'suppression',
                    'suppression_passed': True,
                    'cooldown_seconds': suppression.get('seconds', 60),
                },
            )

        vl_validation = alert_node.vl_validation or {}
        if vl_validation.get('enable'):
            prompt_template = (vl_validation.get('prompt_template') or '').strip()
            if not prompt_template:
                if log_collector:
                    log_collector.add_warning(node_id, "VL核验已启用，但未配置核验提示词")
                self._cache_output_result(
                    node_id=node_id,
                    alert_triggered=False,
                    detection_count=detection_count,
                    trigger_reason='VL核验已启用，但未配置核验提示词'
                )
                logger.warning(f"[Workflow-{self.workflow_id}] 输出节点 {node_id} 已启用 VL 核验，但未配置提示词")
                return

            vl_result = validate_frame_with_vl(
                frame_rgb=frame,
                alert_type=alert_type,
                alert_message=alert_message,
                result=result,
                config=get_vl_service_config(),
                prompt_template=prompt_template,
                extra_context={
                    'workflow_id': self.workflow_id,
                    'workflow_name': self.workflow.name if self.workflow else '',
                    'node_id': node_id,
                },
            )

            if log_collector:
                if vl_result.checked:
                    if vl_result.allowed:
                        log_collector.add_info(
                            node_id,
                            f"VL核验通过: {vl_result.reason or '允许告警'}",
                            metadata={
                                'event_type': 'validation',
                                'vl_checked': True,
                                'vl_allowed': True,
                                'vl_confidence': vl_result.confidence,
                            }
                        )
                    else:
                        log_collector.add_warning(
                            node_id,
                            f"VL核验未通过: {vl_result.reason or '判定为非真实告警'}",
                            metadata={
                                'event_type': 'validation',
                                'vl_checked': True,
                                'vl_allowed': False,
                                'vl_confidence': vl_result.confidence,
                            }
                        )
                else:
                    log_collector.add_warning(
                        node_id,
                        f"VL核验已跳过: {vl_result.reason or '未执行'}",
                        metadata={'event_type': 'validation', 'vl_checked': False}
                    )

            if not vl_result.allowed:
                self._cache_output_result(
                    node_id=node_id,
                    alert_triggered=False,
                    detection_count=detection_count,
                    trigger_reason=f"VL核验未通过: {vl_result.reason or '判定为非真实告警'}"
                )
                logger.info(
                    f"[Workflow-{self.workflow_id}] 输出节点 {node_id} 被 VL 核验拦截: "
                    f"{vl_result.reason or '判定为非真实告警'}"
                )
                return

        # 步骤3：记录触发时间（用于后续抑制计算）
        self.window_detector.record_trigger(
            source_id=self.video_source.id,
            node_id=node_id,
            trigger_time=trigger_time
        )

        # 获取窗口内的检测记录（用于保存检测图片）
        # 注意：只有启用窗口检测时（trigger_stats不为None）才获取历史检测记录
        # 如果未启用窗口检测，detection_records为空，后续会保存当前触发帧
        if trigger_stats and media_allowed:
            # 启用了窗口检测，获取窗口内的所有历史检测记录
            detection_records = self.window_detector.get_detection_records(
                source_id=self.video_source.id,
                node_id=node_id,
                current_time=trigger_time
            )
            logger.debug(f"[Workflow-{self.workflow_id}] 窗口检测已启用，窗口内检测到 {len(detection_records)} 次目标")
        else:
            # 未启用窗口检测，不获取历史记录，只保存当前触发帧
            detection_records = []
            logger.debug(f"[Workflow-{self.workflow_id}] 窗口检测未启用，将保存当前触发帧")

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
        if not detection_images and media_allowed:
            filepath = f"{self.video_source.source_code}/{alert_type}/frame_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 10000}_wf{self.workflow_id}.jpg"
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
                    logger.info(f"[Workflow-{self.workflow_id}] Alert可视化：使用上游ROI节点配置，包含 {len(effective_roi_regions)} 个区域")
                else:
                    # 回退到算法节点自身的ROI配置
                    effective_roi_regions = self.algorithm_roi_configs.get(upstream_node_id, [])
                    if effective_roi_regions:
                        logger.info(f"[Workflow-{self.workflow_id}] Alert可视化：使用算法节点配置，包含 {len(effective_roi_regions)} 个区域")

            self._save_visualized_frame(
                frame_rgb=frame,
                detections=result.get("detections"),
                save_path=filepath_absolute,
                label_color=label_color,
                roi_mask=roi_mask,
                roi_regions=effective_roi_regions,
                upstream_node_id=upstream_node_id
            )

            filepath_ori = f"{filepath}.ori.jpg"
            filepath_ori_absolute = os.path.join(FRAME_SAVE_PATH, filepath_ori)
            save_frame(frame, filepath_ori_absolute)

            detection_images.append({
                'image_path': filepath,
                'image_ori_path': filepath_ori,
                'timestamp': frame_timestamp,
                'detection_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(frame_timestamp))
            })

        main_image = detection_images[-1]['image_path'] if detection_images else None
        main_image_ori = detection_images[-1]['image_ori_path'] if detection_images else None

        if not media_allowed and storage_pressure is not None:
            logger.warning(
                f"[Workflow-{self.workflow_id}] 磁盘使用率 {storage_pressure.used_percent:.1f}% "
                "已进入仅保留告警元数据模式"
            )

        # 窗口条件、抑制检查和 VL 复核都在候选消息之后发生，
        # 创建记录前必须重新构建，才能保存完整的实际触发链路。
        alert_message = self._compose_alert_message(alert_node, log_collector, alert_log_scope)

        # 创建告警记录
        logger.info(f"[Workflow-{self.workflow_id}] 准备创建 Alert，alert_message: {alert_message[:200] if alert_message else 'None'}...")
        with db.atomic():
            alert = Alert.create(
                video_source=self.video_source,
                workflow=self.workflow,
                alert_time=time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(trigger_time)),
                alert_type=alert_type,
                alert_level=alert_level,
                alert_message=alert_message,
                alert_image=main_image,
                alert_image_ori=main_image_ori,
                alert_video=None,
                detection_count=(detection_count if not media_allowed else len(detection_images)),
                window_stats=json.dumps(trigger_stats) if trigger_stats else None,
                detection_images=json.dumps(detection_images) if detection_images else None,
                created_by=getattr(self.video_source, 'created_by', 'admin'),
            )

            # 先取得并保存录像路径，再创建 outbox。事务提交前 delivery worker
            # 看不到任务，因此 URL 模式不会发布缺少 alert_video_url 的半成品消息。
            if self.video_recorder and storage_pressure is not None and storage_pressure.allow_recording:
                try:
                    video_path = self.video_recorder.start_recording(
                        source_id=self.video_source.id,
                        alert_id=alert.id,
                        trigger_time=trigger_time,
                        pre_seconds=self.recording_config.pre_alert_seconds,
                        post_seconds=self.recording_config.post_alert_seconds
                    )
                    alert.alert_video = video_path
                    alert.save(only=[Alert.alert_video])
                    logger.info(f"[Workflow-{self.workflow_id}] 已启动视频录制任务: {video_path}")
                except Exception as rec_err:
                    logger.error(f"[Workflow-{self.workflow_id}] 启动视频录制失败: {rec_err}", exc_info=True)
            elif self.video_recorder and storage_pressure is not None:
                logger.warning(
                    f"[Workflow-{self.workflow_id}] 磁盘使用率 {storage_pressure.used_percent:.1f}% "
                    "已达到停录像水位，本次告警不录像"
                )

            if getattr(alert_node, 'publish_to_mq', True):
                enqueue_alert_delivery(alert)
        self._cache_output_result(
            node_id=node_id,
            alert_triggered=True,
            detection_count=detection_count,
            trigger_reason='满足触发条件并创建告警'
        )
        logger.info(f"[Workflow-{self.workflow_id}] Alert 创建成功，ID: {alert.id}")
        logger.info(f"[Workflow-{self.workflow_id}] 数据库中的 alert_message: {alert.alert_message[:200] if alert.alert_message else 'None'}...")
        logger.info(f"[Workflow-{self.workflow_id}] 输出节点 {node_id} 创建告警，类型: {alert_type}, 级别: {alert_level}, 检测序列包含 {len(detection_images)} 张图片")

        # 缓存标准事件供下游 Webhook 节点消费。绝对媒体 URL 在每个 Webhook
        # 节点执行时按其 public_base_url 独立补齐。
        alert_event = self._to_jsonable(build_alert_webhook_event(alert, result))
        self._cache_output_result(
            node_id=node_id,
            alert_triggered=True,
            detection_count=detection_count,
            trigger_reason='满足触发条件并创建告警',
            alert_event=alert_event,
        )

        # 告警与 outbox 已在同一事务落库；网络投递由主 worker 异步执行。
        if not getattr(alert_node, 'publish_to_mq', True):
            logger.info(f"[Workflow-{self.workflow_id}] 输出节点 {node_id} 已关闭 MQ 输出，跳过消息队列发布: {alert.id}")
        else:
            logger.info(f"[Workflow-{self.workflow_id}] 预警消息已进入异步投递队列: {alert.id}")

    def test_execute(self, test_frame: np.ndarray, test_image_bgr: np.ndarray = None):
        """
        测试模式执行：用单张图片测试工作流

        与运行模式使用完全相同的执行逻辑：
        - 调用相同的执行方法（_execute_by_topology_levels）
        - 使用相同的节点处理器（_handle_*_node）
        - 使用相同的条件判断（_evaluate_condition）

        唯一区别：test_mode=True 会拦截副作用操作

        Args:
            test_frame: RGB 格式的测试图片；入口会立即转换为 NV12 主帧
            test_image_bgr: BGR 格式的测试图片（可选）

        Returns:
            测试结果字典，包含每个节点的执行结果
        """
        logger.info(f"[Workflow-{self.workflow_id}] 开始测试工作流 {self.workflow_id}")

        # 使用BGR格式，如果没有提供则转换
        if test_image_bgr is None:
            test_image_bgr = cv2.cvtColor(test_frame, cv2.COLOR_RGB2BGR)

        frame_pixel_format = normalize_pixel_format(VIDEO_FRAME_PIXEL_FORMAT)
        test_frame_native = rgb_to_frame_format(test_frame, frame_pixel_format)
        readonly_frame_native = test_frame_native.view()
        readonly_frame_native.setflags(write=False)

        # 创建日志收集器
        log_collector = ExecutionLogCollector()

        # 创建初始 context（与运行模式保持一致）
        context = FrameExecutionContext({
            'frame_nv12': readonly_frame_native,
            'frame_pixel_format': frame_pixel_format,
            'frame_rgb': test_frame,
            'frame_bgr': test_image_bgr,
            'frame_width': test_frame.shape[1],
            'frame_height': test_frame.shape[0],
            'frame_timestamp': time.time(),
            'log_collector': log_collector,
            'roi_regions': [],
        })

        # ========== 清空执行状态 ==========
        with self._state_lock:
            self.execution_results.clear()
            self.executed_nodes.clear()
            self.skipped_nodes.clear()
            self.node_results_cache.clear()
            self.condition_diagnostics_cache.clear()
            # 测试模式每次都应完整执行，避免 interval 导致后续帧被跳过
            for node_id in self.nodes.keys():
                self.node_last_exec_time[node_id] = 0
        self.numeric_window_detector.clear()

        # ========== 使用统一的执行逻辑（与运行模式完全相同） ==========
        # 注意：直接调用运行模式的执行方法，不需要任何特殊处理
        self._execute_by_topology_levels(executor=None, context=context)

        # ========== 收集测试结果 ==========
        final_result = self._collect_execution_results(context)

        logger.info(f"[Workflow-{self.workflow_id}] 测试完成，共执行 {len(final_result['nodes'])} 个节点")
        return final_result

    def run_once(self, frame_nv12: np.ndarray, frame_timestamp: float, executor=None, source_code: str = None):
        """执行单帧工作流，用于 source host 统一驱动多个工作流。"""
        if not self.running:
            return

        started_at = time.perf_counter()
        frame_pixel_format = normalize_pixel_format(VIDEO_FRAME_PIXEL_FORMAT)
        readonly_frame = frame_nv12.view()
        readonly_frame.setflags(write=False)
        frame_width, frame_height = infer_frame_dimensions(
            readonly_frame,
            pixel_format=frame_pixel_format,
        )

        log_collector = ExecutionLogCollector()
        context = FrameExecutionContext({
            'frame_nv12': readonly_frame,
            'frame_pixel_format': frame_pixel_format,
            'frame_width': frame_width,
            'frame_height': frame_height,
            'frame_timestamp': frame_timestamp,
            'log_collector': log_collector,
            'roi_regions': [],
        })

        with self._state_lock:
            self.execution_results.clear()
            self.executed_nodes.clear()
            self.skipped_nodes.clear()

        self._execute_by_topology_levels(executor=executor, context=context)
        self._record_to_window_detector_for_all_alerts(context)
        self._record_run_once_profile((time.perf_counter() - started_at) * 1000)

        # 周期性写入「最新检测帧」快照（供视频源管理页预览）。节流 + 异常吞掉，绝不影响主流程。
        if source_code:
            try:
                self._maybe_save_detection_snapshot(context, source_code)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[Workflow-{self.workflow_id}] 写入检测帧快照失败（忽略）: {exc}")

    def _maybe_save_detection_snapshot(self, context, source_code: str):
        """周期性写入「最新检测帧」快照（带检测框 + ROI）到 detection_snapshots/{source_code}.jpg。

        - 只聚合与当前帧时间戳一致的算法结果，绝不把缓存框画到后续帧上。
        - 同一 source 的多个 workflow 按 frame timestamp 聚合，并串行、原子地替换快照。
        - 节流在 source 级共享；同帧后到的 workflow 可补全该帧的聚合结果。
        - 无算法节点/无结果时不写（保留旧文件或留空，由前端回退到原始快照）。
        - 复用 _save_visualized_frame 的绘图链（BaseAlgorithm.visualize）。
        """
        if not DETECTION_SNAPSHOT_ENABLED or not source_code:
            return

        frame_timestamp = context.get('frame_timestamp')
        if not isinstance(frame_timestamp, (int, float)):
            return
        frame_timestamp = float(frame_timestamp)

        with self._state_lock:
            results_snapshot = sorted(
                (
                    (str(node_id), item)
                    for node_id, item in self._latest_algorithm_results.items()
                    if item.get('frame_timestamp') == frame_timestamp
                ),
                key=lambda pair: pair[0],
            )
        if not results_snapshot:
            return  # 本工作流的算法节点没有在当前帧执行

        try:
            frame_rgb = context.get('frame')
        except Exception:
            frame_rgb = None
        if frame_rgb is None:
            return

        safe_code = "".join(c for c in str(source_code) if c not in ('/', '\\', '\x00')).strip()
        if not safe_code:
            return
        save_path = os.path.join(DETECTION_SNAPSHOT_SAVE_PATH, f"{safe_code}.jpg")

        with DETECTION_SNAPSHOT_COORDINATOR_LOCK:
            source_state = DETECTION_SNAPSHOT_SOURCE_STATES.setdefault(
                safe_code,
                {
                    'lock': threading.Lock(),
                    'frame_timestamp': None,
                    'candidates': {},
                    'last_saved_at': 0.0,
                    'last_saved_frame_timestamp': None,
                },
            )

        with source_state['lock']:
            coordinated_timestamp = source_state['frame_timestamp']
            if coordinated_timestamp is not None and frame_timestamp < coordinated_timestamp:
                return  # 较慢 workflow 的旧帧不得覆盖较新的 source 快照
            if coordinated_timestamp != frame_timestamp:
                source_state['frame_timestamp'] = frame_timestamp
                source_state['candidates'] = {}

            source_state['candidates'][str(self.workflow_id)] = results_snapshot
            now = time.monotonic()
            completing_saved_frame = (
                source_state['last_saved_frame_timestamp'] == frame_timestamp
            )
            if (
                not completing_saved_frame
                and now - source_state['last_saved_at'] < DETECTION_SNAPSHOT_INTERVAL
            ):
                return

            all_detections = []
            roi_regions = []
            first_label_color = '#FF0000'
            roi_mask = None
            for workflow_id in sorted(source_state['candidates']):
                for _node_id, item in source_state['candidates'][workflow_id]:
                    dets = item.get('detections') or []
                    if dets:
                        all_detections.extend(dets)
                    if item.get('roi_regions'):
                        roi_regions.extend(item['roi_regions'])
                    if roi_mask is None and item.get('roi_mask') is not None:
                        roi_mask = item['roi_mask']
                    if first_label_color == '#FF0000' and item.get('label_color'):
                        first_label_color = item['label_color']

            os.makedirs(DETECTION_SNAPSHOT_SAVE_PATH, exist_ok=True)
            temp_path = os.path.join(
                DETECTION_SNAPSHOT_SAVE_PATH,
                f".{safe_code}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp.jpg",
            )
            try:
                # upstream_node_id=None -> 通用绘制，覆盖所有 workflow 的检测框与 ROI。
                ok = self._save_visualized_frame(
                    frame_rgb,
                    all_detections,
                    temp_path,
                    label_color=first_label_color,
                    roi_mask=roi_mask,
                    roi_regions=roi_regions,
                    upstream_node_id=None,
                )
                if not ok:
                    return
                os.replace(temp_path, save_path)
                source_state['last_saved_at'] = now
                source_state['last_saved_frame_timestamp'] = frame_timestamp
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

    def _record_run_once_profile(self, elapsed_ms: float):
        if not RESOURCE_PROFILING_ENABLED:
            return

        self._profile_run_once_count += 1
        self._profile_run_once_total_ms += elapsed_ms
        self._profile_run_once_max_ms = max(self._profile_run_once_max_ms, elapsed_ms)

        now = time.monotonic()
        if now < self._profile_next_log_at:
            return

        avg_ms = (
            self._profile_run_once_total_ms / self._profile_run_once_count
            if self._profile_run_once_count
            else 0.0
        )
        logger.info(
            f"[Workflow-{self.workflow_id}] run_once profile: "
            f"count={self._profile_run_once_count}, avg_ms={avg_ms:.2f}, "
            f"max_ms={self._profile_run_once_max_ms:.2f}"
        )
        self._profile_next_log_at = now + RESOURCE_PROFILE_LOG_INTERVAL_SECONDS
        self._profile_run_once_count = 0
        self._profile_run_once_total_ms = 0.0
        self._profile_run_once_max_ms = 0.0

    def run(self):
        """
        实时运行模式主循环

        从 ring buffer 持续读取帧并执行工作流
        """
        if not self.buffer:
            logger.error(f"[Workflow-{self.workflow_id}] 未初始化 buffer，无法运行")
            return

        logger.info(f"[Workflow-{self.workflow_id}] 开始实时运行工作流 {self.workflow_id}")

        frame_count = 0
        error_count = 0
        max_consecutive_errors = 10

        try:
            while self.running:
                try:
                    # 从 buffer 读取最新帧
                    # peek_with_timestamp(-1) 获取最新的帧和时间戳（不消费帧，支持多 workflow 共享）
                    peek_result = self.buffer.peek_with_timestamp(-1)

                    if peek_result is None:
                        # 缓冲区为空，短暂休眠后继续
                        time.sleep(0.01)
                        continue

                    frame_nv12, frame_timestamp = peek_result

                    # 检查是否为新的帧（避免重复处理同一帧）
                    if self.last_frame_timestamp is not None and frame_timestamp == self.last_frame_timestamp:
                        # 帧未更新，短暂休眠后继续
                        time.sleep(0.001)  # 短暂休眠避免 CPU 占用过高
                        continue

                    # 更新最后处理的帧时间戳
                    self.last_frame_timestamp = frame_timestamp

                    frame_count += 1
                    error_count = 0  # 重置错误计数

                    self.run_once(frame_nv12, frame_timestamp)

                    # 定期输出日志
                    if frame_count % 100 == 0:
                        logger.info(f"[Workflow-{self.workflow_id}] 已处理 {frame_count} 帧")

                except KeyboardInterrupt:
                    logger.info(f"[Workflow-{self.workflow_id}] 收到中断信号，停止运行")
                    break
                except Exception as e:
                    error_count += 1
                    logger.error(f"[Workflow-{self.workflow_id}] 处理帧时发生异常 (连续错误: {error_count}/{max_consecutive_errors}): {e}")
                    import traceback
                    traceback.print_exc()

                    if error_count >= max_consecutive_errors:
                        logger.error(f"[Workflow-{self.workflow_id}] 连续错误过多，停止运行")
                        break
                    time.sleep(0.1)
        finally:
            self.cleanup()

        logger.info(f"[Workflow-{self.workflow_id}] 工作流 {self.workflow_id} 运行结束，共处理 {frame_count} 帧")

    def _record_to_window_detector_for_all_alerts(self, context: dict):
        """
        每帧执行后，为所有启用了窗口检测的 Alert 节点记录到窗口检测器

        这样窗口检测器可以准确统计实际处理的帧数，而不仅仅是检测到目标的帧数

        Args:
            context: 帧上下文，包含 frame, frame_timestamp, has_detection, result 等
        """
        frame_timestamp = context.get('frame_timestamp')
        frame = context.get('frame')

        # 读取当前帧执行快照（避免并发修改）
        with self._state_lock:
            executed_nodes_snapshot = set(self.executed_nodes)
            node_results_snapshot = dict(self.node_results_cache)

        # 遍历所有 Alert 节点
        for node_id, node in self.nodes.items():
            if not isinstance(node, AlertNodeData):
                continue

            # 检查是否启用了窗口检测
            trigger_condition = node.trigger_condition
            window_detection_enabled = trigger_condition and trigger_condition.get('enable', False)

            if not window_detection_enabled:
                # 未启用窗口检测，跳过记录
                continue

            # 获取该 Alert 节点的检测结果
            # 查找该 Alert 节点的上游节点
            has_detection = False
            result = None

            # 只使用当前帧真正执行的上游节点结果（不包括缓存的旧结果）
            # 这样窗口检测器统计的才是真正的算法执行次数
            for conn in self.connections:
                if conn['to'] == node_id:
                    upstream_node_id = conn['from']
                    # 关键：检查上游节点是否在当前帧被执行过
                    # 只有在 executed_nodes 中，才说明是当前帧产生的检测结果
                    if upstream_node_id in executed_nodes_snapshot and upstream_node_id in node_results_snapshot:
                        upstream_result = node_results_snapshot[upstream_node_id]
                        # 检查是否有检测
                        if 'result' in upstream_result:
                            detections = upstream_result['result'].get('detections', [])
                            if detections:
                                has_detection = True
                                result = upstream_result['result']
                    break

            # 只在启用窗口检测时才记录
            # 不保存图片（图片在 Alert 节点触发时保存）
            # 只记录帧级别的统计信息
            self.window_detector.add_record(
                source_id=self.video_source.id,
                node_id=node_id,
                timestamp=frame_timestamp,
                has_detection=has_detection,
                image_path=None  # 不在这里保存图片
            )

    def _collect_execution_results(self, context: dict) -> dict:
        """
        从执行状态中收集测试结果

        Args:
            context: 执行上下文

        Returns:
            测试结果字典
        """
        log_collector = context.get('log_collector')

        with self._state_lock:
            executed_nodes_snapshot = list(self.executed_nodes)
            execution_results_snapshot = dict(self.execution_results)
            skipped_nodes_snapshot = set(getattr(self, 'skipped_nodes', set()))
            cache_snapshot = dict(self.node_results_cache)

        def _is_skipped_node(node_id, exec_status, cached):
            if node_id in skipped_nodes_snapshot:
                return True
            if exec_status.get('skipped') is True:
                return True
            if isinstance(cached, dict) and cached.get('skipped') is True:
                return True
            metadata = (cached.get('result') or {}).get('metadata') if isinstance(cached, dict) else None
            return isinstance(metadata, dict) and metadata.get('execution_state') == 'skipped'

        def _node_display_name(node, node_id):
            return node.data.get('label', node_id) if hasattr(node, 'data') else node_id

        def _skip_reason_from_cache(cached):
            if not isinstance(cached, dict):
                return 'gate_failed'
            metadata = (cached.get('result') or {}).get('metadata') or {}
            if isinstance(metadata, dict) and metadata.get('reason_code'):
                return metadata['reason_code']
            return 'gate_failed'

        # 构建节点结果列表
        results = []
        seen = set()
        for node_id in executed_nodes_snapshot:
            node = self.nodes.get(node_id)
            if not node:
                continue

            # 获取执行状态
            exec_status = execution_results_snapshot.get(node_id, {})

            # 构建测试结果格式
            node_result = {
                'node_id': node_id,
                'node_name': _node_display_name(node, node_id),
                'node_type': node.node_type,
                'success': exec_status.get('success', True),
                'execution_time': exec_status.get('execution_time', 0),
                'data': self._build_node_result_data(node_id, node, context)
            }

            # 添加错误信息（如果有）
            if 'error' in exec_status:
                node_result['error'] = exec_status['error']

            results.append(node_result)
            seen.add(node_id)

        skipped_order = []
        for source in (execution_results_snapshot, cache_snapshot):
            for node_id in source:
                if node_id in seen or node_id in skipped_order:
                    continue
                exec_status = execution_results_snapshot.get(node_id, {})
                cached = cache_snapshot.get(node_id)
                if _is_skipped_node(node_id, exec_status, cached):
                    skipped_order.append(node_id)
        for node_id in skipped_nodes_snapshot:
            if node_id not in seen and node_id not in skipped_order:
                skipped_order.append(node_id)

        for node_id in skipped_order:
            node = self.nodes.get(node_id)
            if not node:
                continue
            exec_status = execution_results_snapshot.get(node_id, {})
            cached = cache_snapshot.get(node_id) or {}
            reason_code = _skip_reason_from_cache(cached)
            results.append({
                'node_id': node_id,
                'node_name': _node_display_name(node, node_id),
                'node_type': node.node_type,
                'success': True,
                'skipped': True,
                'execution_time': exec_status.get('execution_time', 0),
                'data': {
                    'execution_state': 'skipped',
                    'reason_code': reason_code,
                    'message': _GATE_SKIP_REASON_MESSAGES.get(reason_code, f'已跳过：{reason_code}'),
                    'detection_count': 0,
                },
            })
            seen.add(node_id)

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
            with self._state_lock:
                cached = self.node_results_cache.get(node_id)
            if cached:
                detections = cached.get('result', {}).get('detections', [])

                upstream_roi = self._find_upstream_roi(node_id)
                effective_roi = upstream_roi if upstream_roi is not None else self.algorithm_roi_configs.get(node_id, [])
                roi_applied = len(effective_roi) > 0

                result_data = {
                    'message': f'检测完成，检测到 {len(detections)} 个目标',
                    'detections': detections,
                    'detection_count': len(detections),
                    'roi_regions': effective_roi,
                    'roi_applied': roi_applied,
                    'detections_detail': detections[:10],
                    'result_image': cached.get('result_image'),
                }

                # 添加 ROI 调试信息
                algo_metadata = cached.get('result', {}).get('metadata', {})
                if algo_metadata.get('detections_before_roi') is not None:
                    result_data['debug_info'] = {
                        'roi_configured': roi_applied,
                        'roi_regions_count': len(effective_roi),
                        'detections_before_roi': algo_metadata.get('detections_before_roi', len(detections)),
                        'roi_filtered_count': algo_metadata.get('roi_filtered_count', 0),
                        'detection_count': len(detections),
                        'roi_filter_enabled': roi_applied
                    }

                return result_data
            else:
                return {'message': '算法未产生结果'}

        # 外部 API 节点
        elif node_type == 'external_api':
            with self._state_lock:
                cached = self.node_results_cache.get(node_id)
            if cached:
                result_payload = cached.get('result', {})
                detections = result_payload.get('detections', [])
                metadata = result_payload.get('metadata', {})
                execution_mode = metadata.get('execution_mode', 'sync')
                submitted = metadata.get('submitted', False)

                if execution_mode == 'async_submit':
                    return {
                        'message': '异步提交成功，当前帧不等待外部 API 返回',
                        'detection_count': 0,
                        'execution_mode': execution_mode,
                        'submitted': submitted,
                        'debug_info': metadata,
                    }

                return {
                    'message': f"外部 API 调用完成，检测到 {len(detections)} 个目标",
                    'detections': detections,
                    'detection_count': len(detections),
                    'execution_mode': execution_mode,
                    'debug_info': metadata,
                    'result_image': cached.get('result_image'),
                }
            return {'message': '外部 API 未产生结果'}

        # 函数节点
        elif node_type == 'function':
            with self._state_lock:
                cached = self.node_results_cache.get(node_id)
            if cached:
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
            if getattr(node, 'condition_kind', 'count') == 'count_change':
                with self._state_lock:
                    metadata = dict(self.condition_diagnostics_cache.get(node_id) or {})
                if metadata.get('waiting_for_sample'):
                    message = '数量骤变条件等待上游产生新样本'
                elif not metadata.get('warmed_up'):
                    message = (
                        f"数量骤变条件预热中 "
                        f"{metadata.get('warmup_count', 0)}/{metadata.get('window_size', node.window_size)}"
                    )
                else:
                    relative_percent = float(metadata.get('relative_change') or 0) * 100
                    message = (
                        f"数量骤变条件: 当前 {metadata.get('current_count', 0):g}，"
                        f"基线 {metadata.get('baseline', 0):g}，"
                        f"变化 {metadata.get('delta', 0):+g} ({relative_percent:.1f}%) - "
                        f"{'✓ 触发' if metadata.get('triggered') else '✗ 未触发'}"
                    )
                return {
                    'message': message,
                    'detection_count': metadata.get('current_count'),
                    'condition_passed': bool(metadata.get('triggered')),
                    'debug_info': metadata,
                }

            if getattr(node, 'condition_kind', 'count') == 'ocr_text':
                upstream_results = self._get_upstream_results(node_id)
                condition_passed, metadata, error = self._evaluate_ocr_text_condition(node, upstream_results)
                if error:
                    return {
                        'message': f'OCR 文字条件失败: {error}',
                        'condition_passed': False,
                        'error': error,
                        'debug_info': metadata,
                    }
                return {
                    'message': f"OCR 文字条件 - {'✓ 通过' if condition_passed else '✗ 未通过'}",
                    'condition_passed': condition_passed,
                    'full_text': metadata.get('full_text', ''),
                    'matched_terms': metadata.get('matched_terms', []),
                    'debug_info': metadata,
                }

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

        elif node_type == 'time_schedule':
            with self._state_lock:
                cached = dict(self.node_results_cache.get(node_id, {}))
            enabled = bool(cached.get('enabled'))
            matched_period = cached.get('matched_period')
            return {
                'message': f"时间启用区间 - {'✓ 通过' if enabled else '✗ 未通过'}",
                'schedule_enabled': enabled,
                'current_time': cached.get('current_time'),
                'weekday': cached.get('weekday'),
                'matched_period': matched_period,
                'debug_info': cached,
            }

        # Alert 节点
        elif node_type in ('alert', 'output'):
            log_collector = context.get('log_collector')
            with self._state_lock:
                cached_output_result = self.node_results_cache.get(node_id, {})

            # 从缓存获取检测数量
            detection_count = cached_output_result.get('detection_count')
            if detection_count is None:
                detection_count = 0
                for conn in self.connections:
                    if conn['to'] == node_id:
                        upstream_id = conn['from']
                        with self._state_lock:
                            cached = self.node_results_cache.get(upstream_id)
                        if cached:
                            detection_count = len(cached.get('result', {}).get('detections', []))
                            break

            has_detection = cached_output_result.get('alert_triggered')
            if has_detection is None:
                has_detection = context.get('has_detection', False)

            trigger_reason = cached_output_result.get('trigger_reason')
            if not trigger_reason:
                trigger_reason = '满足触发条件' if has_detection else '不满足触发条件'

            return {
                'message': f"告警输出: {detection_count} 个目标 → {'✓ 触发告警' if has_detection else '✗ 未触发'}",
                'detection_count': detection_count,
                'alert_triggered': has_detection,
                'result_image': cached_output_result.get('result_image'),
                'debug_info': {
                    'has_detection': has_detection,
                    'detection_count': detection_count,
                    'alert_triggered': has_detection,
                    'trigger_reason': trigger_reason,
                    'log_count': len(log_collector.logs) if log_collector else 0,
                    'upstream_node_id': cached_output_result.get('upstream_node_id')
                }
            }

        elif node_type == 'webhook':
            with self._state_lock:
                cached_webhook_result = dict(self.node_results_cache.get(node_id) or {})
            status = cached_webhook_result.get('delivery_status', 'skipped')
            status_messages = {
                'preview': 'Webhook 测试预览已生成（未发送网络请求）',
                'queued': 'Webhook 已加入异步推送队列',
                'dropped': 'Webhook 推送队列已满，事件已丢弃',
                'skipped': '上游告警未触发，Webhook 已跳过',
            }
            return {
                'message': status_messages.get(status, f'Webhook 状态: {status}'),
                'delivery_status': status,
                'provider': cached_webhook_result.get('provider', 'generic'),
                'event': cached_webhook_result.get('event'),
                'request_preview': cached_webhook_result.get('request_preview'),
                'debug_info': {
                    'delivery_status': status,
                    'trigger_reason': cached_webhook_result.get('trigger_reason'),
                    'network_request_sent': not self.test_mode and status == 'queued',
                },
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
        - 不发布到消息队列
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

            cached_data = None
            if upstream_node_id:
                with self._state_lock:
                    if upstream_node_id in self.node_results_cache:
                        cached_data = self.node_results_cache[upstream_node_id]
            if cached_data:
                context.update({
                    'result': cached_data.get('result'),
                    'has_detection': cached_data.get('has_detection'),
                    'roi_mask': cached_data.get('roi_mask'),
                    'label_color': cached_data.get('label_color', '#FF0000'),
                    'upstream_node_id': cached_data.get('upstream_node_id', upstream_node_id)
                })

        # 检查必需数据
        if 'frame' not in context or 'result' not in context:
            self._cache_output_result(
                node_id=node_id,
                alert_triggered=False,
                detection_count=0,
                trigger_reason='缺少必需数据：上游节点未产生结果'
            )
            if log_collector:
                log_collector.add_warning(node_id, "缺少必需数据：上游节点未产生结果")
            logger.warning(f"[Workflow-{self.workflow_id}] 输出节点 {node_id} 缺少必需数据")
            return

        # 获取 Alert 节点配置
        alert_node = self.nodes.get(node_id)
        if not isinstance(alert_node, AlertNodeData):
            self._cache_output_result(
                node_id=node_id,
                alert_triggered=False,
                detection_count=0,
                trigger_reason='节点类型错误：不是 Alert 节点'
            )
            if log_collector:
                log_collector.add_warning(node_id, "不是 Alert 节点")
            return

        alert_log_scope = self._get_alert_log_scope(node_id)

        # 从 context 获取检测数据
        has_detection = context.get('has_detection', False)
        result = context['result']
        detection_count = len(result.get('detections', []))

        upstream_node_id = context.get('upstream_node_id')
        roi_regions = []
        if upstream_node_id:
            upstream_roi = self._find_upstream_roi(upstream_node_id)
            if upstream_roi is not None:
                roi_regions = upstream_roi
            else:
                roi_regions = self.algorithm_roi_configs.get(upstream_node_id, [])

        result_image = self._save_test_result_image(
            node_id=node_id,
            frame_rgb=context.get('frame'),
            detections=result.get('detections', []),
            label_color=context.get('label_color', '#FF0000'),
            roi_mask=context.get('roi_mask'),
            roi_regions=roi_regions,
            upstream_node_id=upstream_node_id
        )

        self._cache_output_result(
            node_id=node_id,
            alert_triggered=has_detection,
            detection_count=detection_count,
            trigger_reason='测试模式：按上游条件判断触发',
            result_image=result_image,
            upstream_node_id=upstream_node_id
        )

        logger.info(f"[Workflow-{self.workflow_id}] Alert 节点 {node_id} 测试结果: has_detection={has_detection}, 检测数={detection_count}")

        vl_validation = alert_node.vl_validation or {}
        if log_collector and vl_validation.get('enable'):
            prompt_configured = bool((vl_validation.get('prompt_template') or '').strip())
            if prompt_configured:
                log_collector.add_info(
                    node_id,
                    "VL核验已启用，测试模式未实际调用外部模型",
                    metadata={
                        'vl_checked': False,
                        'test_mode': True,
                        'vl_prompt_configured': True,
                    }
                )
            else:
                log_collector.add_warning(
                    node_id,
                    "VL核验已启用，但未配置核验提示词模板",
                    metadata={
                        'vl_checked': False,
                        'test_mode': True,
                        'vl_prompt_configured': False,
                    }
                )

        alert_type = alert_node.alert_type or "detection"
        alert_level = alert_node.alert_level or "info"
        alert_message = self._compose_alert_message(alert_node, log_collector, alert_log_scope)

        # 记录测试日志
        if log_collector:
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

            logger.info(f"[Workflow-{self.workflow_id}] Alert 节点 {node_id} 测试消息: {alert_message[:200] if alert_message else 'None'}...")

        if has_detection:
            source_node_data = next(
                (item for item in self.workflow_data.get('nodes', []) if item.get('type') == 'source'),
                {},
            )
            image_path = result_image
            marker = '/api/image/frames/'
            if image_path and marker in image_path:
                image_path = image_path.split(marker, 1)[1].split('?', 1)[0]
            alert_event = {
                'schema_version': '1.0',
                'event_id': f'test-alert:{self.workflow_id}:{node_id}',
                'event_type': 'alert.created',
                'occurred_at': datetime.now().isoformat(),
                'source': {
                    'id': source_node_data.get('dataId'),
                    'name': source_node_data.get('videoSourceName') or '',
                    'code': source_node_data.get('videoSourceCode') or '',
                },
                'workflow': {
                    'id': self.workflow_id,
                    'name': getattr(self.workflow, 'name', '') or '',
                },
                'alert': {
                    'id': None,
                    'type': alert_type,
                    'level': alert_level,
                    'message': alert_message,
                    'detection_count': detection_count,
                },
                'detection': {
                    'has_detection': True,
                    'detections': self._to_jsonable(result.get('detections', [])),
                    'metadata': self._to_jsonable(result.get('metadata', {})),
                },
                'media': {
                    'image_path': image_path,
                    'image_url': None,
                    'original_image_path': None,
                    'original_image_url': None,
                    'video_path': None,
                    'video_url': None,
                },
            }
            self._cache_output_result(
                node_id=node_id,
                alert_triggered=True,
                detection_count=detection_count,
                trigger_reason='测试模式：按上游条件判断触发',
                result_image=result_image,
                upstream_node_id=upstream_node_id,
                alert_event=alert_event,
            )
