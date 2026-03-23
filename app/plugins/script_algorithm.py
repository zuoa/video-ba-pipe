"""
脚本算法插件 - 执行用户自定义Python脚本
"""
import inspect
import traceback
import time
import numpy as np

from app import logger
from app.core.algorithm import BaseAlgorithm
from app.core.script_loader import get_script_loader, ScriptLoadError
from app.core.resource_limiter import get_script_executor
from app.core.hook_manager import get_hook_manager
from app.core.model_resolver import get_model_resolver


class ScriptAlgorithm(BaseAlgorithm):
    """
    脚本算法插件

    通过执行用户自定义的Python脚本来实现检测逻辑
    """

    # 类属性，避免实例化时就需要配置
    name = "script_executor"

    @staticmethod
    def _summarize_models(config) -> str:
        """压缩输出模型配置，避免日志过长。"""
        models = config.get('models')
        if isinstance(models, list):
            summary = []
            for idx, item in enumerate(models):
                if not isinstance(item, dict):
                    summary.append(f"#{idx}:type={type(item).__name__}")
                    continue
                summary.append(
                    f"#{idx}:id={item.get('model_id')},"
                    f"type={item.get('_model_type') or item.get('model_type')},"
                    f"framework={item.get('_model_framework') or item.get('framework')},"
                    f"path={item.get('_model_path') or item.get('path')}"
                )
            return "; ".join(summary) if summary else "[]"

        if isinstance(models, dict):
            summary = []
            for role, item in models.items():
                if isinstance(item, dict):
                    summary.append(
                        f"{role}:name={item.get('name')},"
                        f"type={item.get('model_type')},"
                        f"path={item.get('path')}"
                    )
                else:
                    summary.append(f"{role}:{item}")
            return "; ".join(summary) if summary else "{}"

        model_id = config.get('model_id')
        if model_id is not None:
            return f"model_id={model_id}"
        return "none"

    @staticmethod
    def _metadata_summary(metadata) -> str:
        """压缩 metadata，优先输出与推理诊断相关的信息。"""
        if not isinstance(metadata, dict):
            return str(metadata)

        keys = [
            'error',
            'model_path',
            'model_type',
            'framework',
            'total_detections',
            'detections_before_roi',
            'roi_filtered_count',
            'inference_time_ms',
            'confidence_threshold'
        ]
        parts = []
        for key in keys:
            if key in metadata:
                parts.append(f"{key}={metadata.get(key)}")

        if not parts:
            parts.append(f"keys={sorted(metadata.keys())}")
        return ", ".join(parts)

    def load_model(self):
        """
        加载脚本（不是模型）
        """
        self.script_path = self.config.get('script_path')
        self.entry_function = self.config.get('entry_function', 'process')
        self.timeout = self.config.get('runtime_timeout', 30)
        self.memory_limit = self.config.get('memory_limit_mb', 512)
        self._empty_detection_count = 0
        self._last_empty_detection_log_at = 0.0

        # 如果没有 script_path，跳过加载（插件管理器扫描时可能没有完整配置）
        if not self.script_path:
            logger.debug(f"[{self.name}] 未指定 script_path，跳过脚本加载")
            return

        logger.info(
            f"[{self.name}] 加载脚本: path={self.script_path}, "
            f"source_id={self.config.get('source_id')}, "
            f"timeout={self.timeout}s, memory_limit={self.memory_limit}MB, "
            f"models={self._summarize_models(self.config)}"
        )

        # 加载脚本模块
        try:
            loader = get_script_loader()
            self.script_module, metadata = loader.load(self.script_path)
            self.script_metadata = metadata

            logger.info(f"[{self.name}] 脚本加载成功: {metadata.get('name', 'unknown')} v{metadata.get('version', '1.0')}")

            # 解析模型引用（将 name 转换为 path）
            resolver = get_model_resolver()
            resolved_config = resolver.resolve_models(self.config)
            self.resolved_config = resolved_config
            logger.info(
                f"[{self.name}] 模型解析完成: source_id={resolved_config.get('source_id')}, "
                f"models={self._summarize_models(resolved_config)}"
            )

            # 如果脚本有init函数，调用它（传递解析后的config）
            if hasattr(self.script_module, 'init'):
                logger.info(f"[{self.name}] 调用脚本的 init() 函数...")
                self.script_state = self.script_module.init(resolved_config)
                logger.info(
                    f"[{self.name}] 脚本 init() 完成: "
                    f"state_keys={sorted(self.script_state.keys()) if isinstance(self.script_state, dict) else type(self.script_state).__name__}"
                )
            else:
                self.script_state = None

        except ScriptLoadError as e:
            logger.error(f"[{self.name}] 脚本加载失败: {e}")
            raise
        except Exception as e:
            logger.error(
                f"[{self.name}] 脚本初始化异常: script_path={self.script_path}, "
                f"source_id={self.config.get('source_id')}, "
                f"models={self._summarize_models(self.config)}, error={e}"
            )
            logger.error(traceback.format_exc())
            raise ScriptLoadError(f"脚本初始化异常: {e}") from e

        # 获取处理函数
        if not hasattr(self.script_module, self.entry_function):
            raise ScriptLoadError(f"脚本缺少必需的函数: {self.entry_function}")

        self.process_func = getattr(self.script_module, self.entry_function)

        # 获取执行器
        self.executor = get_script_executor(timeout=self.timeout, memory_limit_mb=self.memory_limit)

        # 获取Hook管理器
        self.hook_manager = get_hook_manager()

        # 算法ID（从config获取，用于Hook）
        self.algorithm_id = self.config.get('id')

    def process(self, frame: np.ndarray, roi_regions: list = None, upstream_results: dict = None) -> dict:
        """
        处理帧（执行脚本）

        Args:
            frame: RGB格式的视频帧
            roi_regions: ROI热区配置
            upstream_results: 上游节点的执行结果

        Returns:
            检测结果字典
        """
        # 检查脚本是否已加载
        if not hasattr(self, 'process_func') or self.process_func is None:
            logger.error(f"[{self.name}] 脚本未正确加载，请检查 script_path 配置")
            return {'detections': []}

        # 1. 执行pre_detect Hooks
        if self.algorithm_id:
            hook_frame = frame
            if self.hook_manager.has_hooks_for_algorithm(self.algorithm_id, 'pre_detect'):
                # pre_detect hook 允许修改输入帧，单独复制，避免为每个算法都做防御式整帧 copy。
                hook_frame = frame.copy()

            modified_frame, should_skip = self.hook_manager.execute_pre_detect_hooks(
                self.algorithm_id, hook_frame, self.config.get('source_id', 0)
            )

            if should_skip:
                logger.info(f"[{self.name}] pre_detect Hook要求跳过处理")
                return {'detections': []}

            frame = modified_frame

        # 2. 执行脚本
        try:
            all_args = {
                'frame': frame,
                'config': self.config,
                'roi_regions': roi_regions,
                'state': self.script_state,
                'upstream_results': upstream_results
            }

            sig = inspect.signature(self.process_func)
            script_args = {}
            for param_name in sig.parameters:
                if param_name in all_args:
                    script_args[param_name] = all_args[param_name]

            logger.debug(f"[{self.name}] 调用脚本函数参数: {list(script_args.keys())}")

            # 执行（带资源限制）
            result, exec_time_ms, success, error = self.executor.execute(
                self.process_func,
                **script_args
            )

            if not success:
                logger.error(
                    f"[{self.name}] 脚本执行失败: script_path={self.script_path}, "
                    f"source_id={self.config.get('source_id')}, frame_shape={getattr(frame, 'shape', None)}, "
                    f"roi_count={len(roi_regions or [])}, upstream={list((upstream_results or {}).keys())}, "
                    f"models={self._summarize_models(getattr(self, 'resolved_config', self.config))}, error={error}"
                )
                return {'detections': []}

            logger.debug(f"[{self.name}] 脚本执行成功，耗时 {exec_time_ms:.2f}ms")

            # 3. 验证返回结果格式
            if not isinstance(result, dict):
                logger.warning(
                    f"[{self.name}] 脚本返回格式错误，应为dict: "
                    f"script_path={self.script_path}, source_id={self.config.get('source_id')}, "
                    f"result_type={type(result).__name__}, result={repr(result)[:400]}"
                )
                return {'detections': []}

            if 'detections' not in result:
                logger.warning(
                    f"[{self.name}] 脚本返回结果缺少 'detections' 字段: "
                    f"script_path={self.script_path}, source_id={self.config.get('source_id')}, "
                    f"keys={sorted(result.keys())}"
                )
                return {'detections': []}

            detections = result.get('detections', [])
            metadata = result.get('metadata', {})

            # 4. 执行post_detect Hooks
            if self.algorithm_id:
                filtered_detections, should_skip = self.hook_manager.execute_post_detect_hooks(
                    self.algorithm_id, detections, frame, self.config.get('source_id', 0)
                )

                if should_skip:
                    logger.info(f"[{self.name}] post_detect Hook要求跳过结果")
                    return {'detections': []}

                detections = filtered_detections

            if detections:
                logger.info(
                    f"[{self.name}] 推理完成: detections={len(detections)}, exec_time_ms={exec_time_ms:.2f}, "
                    f"script_path={self.script_path}, source_id={self.config.get('source_id')}, "
                    f"metadata={self._metadata_summary(metadata)}"
                )
            else:
                self._empty_detection_count += 1
                now = time.time()
                should_log_empty = (
                    self._empty_detection_count <= 3 or
                    now - self._last_empty_detection_log_at >= 30
                )
                if should_log_empty:
                    self._last_empty_detection_log_at = now
                    logger.warning(
                        f"[{self.name}] 推理完成但未检测到目标: empty_count={self._empty_detection_count}, "
                        f"script_path={self.script_path}, source_id={self.config.get('source_id')}, "
                        f"frame_shape={getattr(frame, 'shape', None)}, roi_count={len(roi_regions or [])}, "
                        f"models={self._summarize_models(getattr(self, 'resolved_config', self.config))}, "
                        f"metadata={self._metadata_summary(metadata)}"
                    )

            # 5. 返回结果
            return {
                'detections': detections,
                'metadata': metadata,
                'exec_time_ms': exec_time_ms
            }

        except Exception as e:
            logger.error(
                f"[{self.name}] 处理过程异常: script_path={self.script_path}, "
                f"source_id={self.config.get('source_id')}, frame_shape={getattr(frame, 'shape', None)}, "
                f"roi_count={len(roi_regions or [])}, upstream={list((upstream_results or {}).keys())}, "
                f"models={self._summarize_models(getattr(self, 'resolved_config', self.config))}, error={e}"
            )
            logger.error(traceback.format_exc())
            return {'detections': []}

    def cleanup(self):
        """清理资源"""
        if hasattr(self, 'script_module') and hasattr(self.script_module, 'cleanup'):
            try:
                self.script_module.cleanup(self.script_state)
                logger.info(f"[{self.name}] 脚本cleanup函数已调用")
            except Exception as e:
                logger.error(f"[{self.name}] 脚本cleanup失败: {e}")

        # 卸载脚本
        if hasattr(self, 'script_path'):
            loader = get_script_loader()
            loader.unload(self.script_path)
