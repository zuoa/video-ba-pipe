"""
Hook管理器 - 管理算法前后的钩子函数
"""
import json
import os
import time
from typing import List, Dict, Any, Callable, Optional
from collections import defaultdict

from app import logger
from app.core.script_loader import get_script_loader, ScriptLoadError


class Hook:
    """Hook对象"""

    def __init__(
        self,
        hook_id: int,
        name: str,
        hook_point: str,
        script_path: str,
        entry_function: str = 'execute',
        priority: int = 100,
        condition: dict = None,
        enabled: bool = True
    ):
        self.hook_id = hook_id
        self.name = name
        self.hook_point = hook_point  # 'pre_detect', 'post_detect', 'pre_alert', 'pre_record', 'post_record'
        self.script_path = script_path
        self.entry_function = entry_function
        self.priority = priority  # 越小越先执行
        self.condition = condition or {}
        self.enabled = enabled

        # 缓存加载的模块和函数
        self._module = None
        self._function = None
        self._loaded = False

    def load(self):
        """加载Hook脚本"""
        if self._loaded:
            return

        try:
            loader = get_script_loader()
            self._module, metadata = loader.load(self.script_path)
            self._function = getattr(self._module, self.entry_function, None)

            if self._function is None:
                raise ScriptLoadError(f"Hook脚本缺少函数: {self.entry_function}")

            self._loaded = True
            logger.info(f"[HookManager] Hook '{self.name}' 加载成功")

        except Exception as e:
            logger.error(f"[HookManager] Hook '{self.name}' 加载失败: {e}")
            raise

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行Hook

        Args:
            context: 上下文数据，包含 frame, task_id, algorithm_id 等

        Returns:
            Hook执行结果
        """
        if not self.enabled:
            return {'skip': False, 'metadata': {}}

        if not self._loaded:
            self.load()

        # 检查条件
        if not self._check_condition(context):
            logger.debug(f"[HookManager] Hook '{self.name}' 条件不满足，跳过执行")
            return {'skip': False, 'metadata': {}}

        try:
            logger.debug(f"[HookManager] 执行Hook '{self.name}'")
            result = self._function(context)

            # 确保返回结果格式
            if not isinstance(result, dict):
                result = {}

            return {
                'skip': result.get('skip', False),
                'metadata': result.get('metadata', {}),
                'data': result
            }

        except Exception as e:
            logger.error(f"[HookManager] Hook '{self.name}' 执行失败: {e}")
            return {'skip': False, 'metadata': {}}

    def _check_condition(self, context: Dict[str, Any]) -> bool:
        """检查Hook执行条件"""
        if not self.condition:
            return True

        # 时间范围条件
        if 'time_range' in self.condition:
            time_range = self.condition['time_range']
            if len(time_range) == 2:
                current_hour = time.localtime().tm_hour
                if not (time_range[0] <= current_hour <= time_range[1]):
                    return False

        # 最小检测数量
        if 'min_detection_count' in self.condition:
            min_count = self.condition['min_detection_count']
            if context.get('detection_count', 0) < min_count:
                return False

        # 算法ID条件
        if 'algorithm_ids' in self.condition:
            algo_ids = self.condition['algorithm_ids']
            if context.get('algorithm_id') not in algo_ids:
                return False

        # 自定义条件（Lambda表达式）
        if 'custom_condition' in self.condition:
            # 注意：生产环境应该谨慎使用eval
            try:
                condition_func = eval(self.condition['custom_condition'])
                if not condition_func(context):
                    return False
            except Exception as e:
                logger.warning(f"[HookManager] 自定义条件检查失败: {e}")
                return False

        return True

    def reload(self):
        """重新加载Hook"""
        self._loaded = False
        self._module = None
        self._function = None


class HookManager:
    """Hook管理器"""

    # Hook点定义
    HOOK_POINTS = [
        'pre_detect',    # 检测前（可以修改frame）
        'post_detect',   # 检测后（可以过滤/增强detections）
        'pre_alert',     # 告警前
        'pre_record',    # 录制前
        'post_record',   # 录制后
    ]

    def __init__(self):
        # 按Hook点组织的Hook列表
        self._hooks: Dict[str, List[Hook]] = defaultdict(list)

        # 算法关联的Hook: {algorithm_id: [hook_id, ...]}
        self._algorithm_hooks: Dict[int, List[int]] = defaultdict(list)

    def load_from_database(self):
        """从数据库加载Hook配置"""
        try:
            from app.core.database_models import Hook, AlgorithmHook

            # 加载所有Hook
            hooks_query = Hook.select()
            for hook_db in hooks_query:
                hook = Hook(
                    hook_id=hook_db.id,
                    name=hook_db.name,
                    hook_point=hook_db.hook_point,
                    script_path=hook_db.script_path,
                    entry_function=hook_db.entry_function or 'execute',
                    priority=hook_db.priority or 100,
                    condition=json.loads(hook_db.condition_json) if hook_db.condition_json else None,
                    enabled=hook_db.enabled
                )

                self._hooks[hook.hook_point].append(hook)

            # 按优先级排序
            for hook_point in self._hooks:
                self._hooks[hook_point].sort(key=lambda h: h.priority)

            # 加载算法关联
            algo_hooks_query = AlgorithmHook.select()
            for algo_hook in algo_hooks_query:
                if algo_hook.enabled:
                    self._algorithm_hooks[algo_hook.algorithm_id].append(algo_hook.hook_id)

            logger.info(f"[HookManager] 已加载 {len(hooks_query)} 个Hook")

        except Exception as e:
            logger.error(f"[HookManager] 从数据库加载Hook失败: {e}")

    def get_hooks_for_algorithm(self, algorithm_id: int, hook_point: str) -> List[Hook]:
        """
        获取算法指定Hook点的所有Hook

        Args:
            algorithm_id: 算法ID
            hook_point: Hook点名称

        Returns:
            Hook列表（按优先级排序）
        """
        if algorithm_id not in self._algorithm_hooks:
            return []

        hook_ids = self._algorithm_hooks[algorithm_id]
        hooks = []

        for hook in self._hooks.get(hook_point, []):
            if hook.hook_id in hook_ids and hook.enabled:
                hooks.append(hook)

        return hooks

    def execute_hooks(self, algorithm_id: int, hook_point: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行算法指定Hook点的所有Hook

        Args:
            algorithm_id: 算法ID
            hook_point: Hook点名称
            context: 上下文数据

        Returns:
            Hook执行结果汇总
        """
        hooks = self.get_hooks_for_algorithm(algorithm_id, hook_point)

        if not hooks:
            return {'skip': False, 'metadata': {}}

        logger.debug(f"[HookManager] 执行 {len(hooks)} 个Hook (算法ID={algorithm_id}, 点={hook_point})")

        results = {
            'skip': False,
            'metadata': {},
            'hooks_executed': 0
        }

        for hook in hooks:
            try:
                result = hook.execute(context)

                # 合并结果
                results['metadata'].update(result.get('metadata', {}))

                # 如果Hook要求跳过
                if result.get('skip'):
                    results['skip'] = True
                    logger.info(f"[HookManager] Hook '{hook.name}' 要求跳过后续处理")
                    break

                results['hooks_executed'] += 1

            except Exception as e:
                logger.error(f"[HookManager] Hook '{hook.name}' 执行异常: {e}")
                continue

        return results

    def execute_pre_detect_hooks(self, algorithm_id: int, frame, task_id: int) -> tuple:
        """
        执行pre_detect Hook（特殊处理，可以修改frame）

        Args:
            algorithm_id: 算法ID
            frame: 输入帧
            task_id: 任务ID

        Returns:
            (modified_frame, should_skip)
        """
        context = {
            'frame': frame,
            'task_id': task_id,
            'algorithm_id': algorithm_id,
            'hook_point': 'pre_detect'
        }

        result = self.execute_hooks(algorithm_id, 'pre_detect', context)

        # 获取修改后的frame
        modified_frame = result.get('metadata', {}).get('frame', frame)
        should_skip = result.get('skip', False)

        return modified_frame, should_skip

    def execute_post_detect_hooks(
        self,
        algorithm_id: int,
        detections: list,
        frame,
        task_id: int
    ) -> tuple:
        """
        执行post_detect Hook（可以过滤/增强detections）

        Args:
            algorithm_id: 算法ID
            detections: 检测结果列表
            frame: 原始帧
            task_id: 任务ID

        Returns:
            (filtered_detections, should_skip)
        """
        context = {
            'detections': detections,
            'frame': frame,
            'task_id': task_id,
            'algorithm_id': algorithm_id,
            'hook_point': 'post_detect',
            'detection_count': len(detections)
        }

        result = self.execute_hooks(algorithm_id, 'post_detect', context)

        # 获取过滤后的detections
        filtered_detections = result.get('metadata', {}).get('detections', detections)
        should_skip = result.get('skip', False)

        return filtered_detections, should_skip

    def reload_hooks(self):
        """重新加载所有Hook"""
        for hook_point in self._hooks:
            for hook in self._hooks[hook_point]:
                hook.reload()

        logger.info("[HookManager] 所有Hook已重新加载")


# 全局单例
_global_hook_manager: Optional[HookManager] = None


def get_hook_manager() -> HookManager:
    """获取全局Hook管理器实例"""
    global _global_hook_manager

    if _global_hook_manager is None:
        _global_hook_manager = HookManager()
        _global_hook_manager.load_from_database()

    return _global_hook_manager
