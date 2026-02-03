"""
工作流执行日志收集器

用于收集工作流执行过程中各节点的消息，为 Alert 节点生成格式化的 alert_message
"""

from typing import List, Dict, Optional
import time
import threading


class ExecutionLogCollector:
    """
    工作流执行日志收集器

    职责：
    - 收集工作流执行过程中各节点的消息
    - 为 Alert 节点生成格式化的 alert_message
    - 支持不同级别的日志（info, warning, error）

    注意：
    - 仅在内存中收集，不持久化到数据库
    - 每次处理帧时创建新实例
    - 生命周期：单次工作流执行
    """

    def __init__(self):
        """初始化日志收集器"""
        self.logs: List[Dict] = []  # 日志记录列表
        self.frame_timestamp: Optional[float] = None  # 当前帧时间戳
        self._lock = threading.Lock()

    def add_log(
        self,
        node_id: str,
        level: str,
        content: str,
        metadata: Optional[Dict] = None
    ):
        """
        添加一条节点日志

        Args:
            node_id: 节点ID
            level: 日志级别 (info, warning, error)
            content: 日志内容
            metadata: 额外的元数据（可选）
        """
        log_entry = {
            'node_id': node_id,
            'level': level,
            'content': content,
            'timestamp': time.time(),
            'metadata': metadata or {}
        }
        with self._lock:
            self.logs.append(log_entry)

    def add_info(self, node_id: str, content: str, metadata: Optional[Dict] = None):
        """添加 info 级别日志（便捷方法）"""
        self.add_log(node_id, 'info', content, metadata)

    def add_warning(self, node_id: str, content: str, metadata: Optional[Dict] = None):
        """添加 warning 级别日志（便捷方法）"""
        self.add_log(node_id, 'warning', content, metadata)

    def add_error(self, node_id: str, content: str, metadata: Optional[Dict] = None):
        """添加 error 级别日志（便捷方法）"""
        self.add_log(node_id, 'error', content, metadata)

    def get_logs_by_node(self, node_id: str) -> List[Dict]:
        """获取指定节点的所有日志"""
        return [log for log in self.logs if log['node_id'] == node_id]

    def get_logs_by_level(self, level: str) -> List[Dict]:
        """获取指定级别的所有日志"""
        return [log for log in self.logs if log['level'] == level]

    def get_error_count(self) -> int:
        """获取错误日志数量"""
        return len(self.get_logs_by_level('error'))

    def get_warning_count(self) -> int:
        """获取警告日志数量"""
        return len(self.get_logs_by_level('warning'))

    def build_alert_message(
        self,
        format_type: str = 'detailed',
        include_metadata: bool = False
    ) -> str:
        """
        构建告警消息

        Args:
            format_type: 消息格式类型
                - 'detailed': 详细格式（智能分组，突出触发的分支）
                - 'simple': 简单格式（仅消息内容，不分组）
                - 'summary': 汇总格式（按级别分组）
            include_metadata: 是否包含元数据

        Returns:
            格式化的消息字符串
        """
        if not self.logs:
            return "无执行日志"

        if format_type == 'detailed':
            # 自动使用智能分组格式（带节点ID）
            return self._build_grouped_message(include_node_id=True)

        elif format_type == 'simple':
            # 自动使用智能分组格式（不带节点ID）
            return self._build_grouped_message(include_node_id=False)

        elif format_type == 'summary':
            summary = []

            # 按级别分组统计
            error_logs = self.get_logs_by_level('error')
            warning_logs = self.get_logs_by_level('warning')
            info_logs = self.get_logs_by_level('info')

            if error_logs:
                summary.append(f"❌ 错误 ({len(error_logs)}):")
                for log in error_logs:
                    summary.append(f"  [{log['node_id']}] {log['content']}")

            if warning_logs:
                summary.append(f"⚠️  警告 ({len(warning_logs)}):")
                for log in warning_logs:
                    summary.append(f"  [{log['node_id']}] {log['content']}")

            if info_logs:
                summary.append(f"ℹ️  信息 ({len(info_logs)}):")
                for log in info_logs:
                    summary.append(f"  [{log['node_id']}] {log['content']}")

            return "\n".join(summary) if summary else "无执行日志"

        else:
            return f"不支持的格式类型: {format_type}"

    def _build_grouped_message(self, include_node_id: bool = True) -> str:
        """
        构建分组格式的告警消息

        展示完整的分支判断链路：
        1. 将条件日志和检测日志按分支分组
        2. 每个分支显示：算法检测 -> 条件判断 -> 是否触发
        3. 突出显示最终触发的分支

        Args:
            include_node_id: 是否包含节点ID
        """
        # 分类日志
        condition_logs = []  # 条件判断日志
        detection_logs = []  # 算法检测日志
        other_logs = []      # 其他日志

        for log in self.logs:
            content = log['content']
            if content.startswith('条件判断:'):
                condition_logs.append(log)
            elif content.startswith('检测到 ') and ' 个目标' in content:
                detection_logs.append(log)
            else:
                other_logs.append(log)

        if not condition_logs and not detection_logs:
            # 如果没有检测或条件日志，返回简单格式
            lines = []
            for log in self.logs:
                if include_node_id:
                    lines.append(f"[{log['node_id']}] {log['content']}")
                else:
                    lines.append(log['content'])
            return "\n".join(lines) if lines else "无执行日志"

        lines = []

        # 按条件日志分组，每个条件日志前可能有对应的检测日志
        # 按时间顺序分组（假设日志按时间顺序记录）
        all_logs = sorted(self.logs, key=lambda x: x.get('timestamp', 0))

        # 分组：每个分支包含检测日志+条件日志
        branches = []
        current_branch = {'detection': None, 'condition': None, 'logs': []}

        for log in all_logs:
            content = log['content']

            if content.startswith('检测到 ') and ' 个目标' in content:
                # 新的检测日志，可能开始新分支
                if current_branch['detection'] or current_branch['condition']:
                    branches.append(current_branch)
                    current_branch = {'detection': None, 'condition': None, 'logs': []}
                current_branch['detection'] = log
            elif content.startswith('条件判断:'):
                current_branch['condition'] = log
            else:
                current_branch['logs'].append(log)

        if current_branch['detection'] or current_branch['condition']:
            branches.append(current_branch)

        # 构建分支消息
        has_passed_branch = False
        for idx, branch in enumerate(branches):
            cond_log = branch['condition']
            det_log = branch['detection']

            if not cond_log:
                continue

            metadata = cond_log.get('metadata', {})
            condition_passed = metadata.get('condition_passed', False)

            if condition_passed:
                has_passed_branch = True
                lines.append(f"分支 {idx + 1}: ✓ 触发预警")
            else:
                lines.append(f"分支 {idx + 1}: 未触发")

            # 检测日志
            if det_log:
                if include_node_id:
                    lines.append(f"  └─ [{det_log['node_id']}] {det_log['content']}")
                else:
                    lines.append(f"  └─ {det_log['content']}")

            # 条件日志
            content = cond_log['content']
            condition_text = content.replace('条件判断: ', '')

            if include_node_id:
                lines.append(f"  └─ [{cond_log['node_id']}] {condition_text}")
            else:
                lines.append(f"  └─ {condition_text}")

            lines.append("")  # 分支间空行

        # 添加其他日志（如果有）
        if other_logs:
            lines.append("其他信息:")
            for log in other_logs:
                if include_node_id:
                    lines.append(f"  [{log['node_id']}] {log['content']}")
                else:
                    lines.append(f"  {log['content']}")

        return "\n".join(lines).strip() if lines else "无执行日志"

    def clear(self):
        """清空所有日志（用于复用实例）"""
        self.logs.clear()

    def to_dict(self) -> Dict:
        """转换为字典格式（用于调试或序列化）"""
        return {
            'frame_timestamp': self.frame_timestamp,
            'log_count': len(self.logs),
            'error_count': self.get_error_count(),
            'warning_count': self.get_warning_count(),
            'logs': self.logs
        }
