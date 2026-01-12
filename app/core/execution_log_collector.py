"""
工作流执行日志收集器

用于收集工作流执行过程中各节点的消息，为 Alert 节点生成格式化的 alert_message
"""

from typing import List, Dict, Optional
import time


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
                - 'detailed': 详细格式（包含节点ID和级别）
                - 'simple': 简单格式（仅消息内容）
                - 'summary': 汇总格式（按级别分组）
            include_metadata: 是否包含元数据

        Returns:
            格式化的消息字符串
        """
        if not self.logs:
            return "无执行日志"

        if format_type == 'detailed':
            lines = []
            for log in self.logs:
                line = f"[{log['node_id']}] {log['content']}"
                if include_metadata and log['metadata']:
                    line += f" ({log['metadata']})"
                lines.append(line)
            return "\n".join(lines)

        elif format_type == 'simple':
            return "\n".join([log['content'] for log in self.logs])

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
