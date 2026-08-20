"""
工作流执行日志收集器

用于收集工作流执行过程中各节点的消息，为 Alert 节点生成格式化的 alert_message
"""

from collections import Counter
from typing import Any, Collection, List, Dict, Optional
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

    @staticmethod
    def _detection_track_id(detection: Dict[str, Any]) -> Optional[int]:
        raw = detection.get('track_id')
        if raw is None and isinstance(detection.get('attributes'), dict):
            raw = detection['attributes'].get('track_id')
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _detection_dwell_seconds(detection: Dict[str, Any]) -> Optional[float]:
        attrs = detection.get('attributes') if isinstance(detection.get('attributes'), dict) else {}
        raw = detection.get('dwell_seconds')
        if raw is None:
            raw = attrs.get('dwell_seconds')
        if raw is None:
            first = attrs.get('first_seen_ts')
            last = attrs.get('last_seen_ts')
            if first is not None and last is not None:
                try:
                    return max(0.0, float(last) - float(first))
                except (TypeError, ValueError):
                    return None
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    @classmethod
    def _format_detection_target(cls, detection: Dict[str, Any], label: str) -> str:
        track_id = cls._detection_track_id(detection)
        text = f"{label}#{track_id}" if track_id is not None else label
        dwell = cls._detection_dwell_seconds(detection)
        if dwell is not None and dwell >= 1:
            text = f"{text} 停留 {int(round(dwell))}s"
        return text

    def add_detection_result(
        self,
        node_id: str,
        detections: Optional[List[Dict[str, Any]]],
        node_name: Optional[str] = None,
        has_detection: Optional[bool] = None,
        metadata: Optional[Dict] = None,
    ):
        """记录可直接用于告警正文的检测摘要。"""
        detections = [item for item in (detections or []) if isinstance(item, dict)]
        label_counts = Counter()
        confidences = []
        target_texts = []
        has_track_id = False

        for detection in detections:
            label = (
                detection.get('label_name')
                or detection.get('label')
                or detection.get('class_name')
                or detection.get('text')
                or '未知类别'
            )
            label = str(label).replace('\n', ' ').strip() or '未知类别'
            label_counts[label[:40]] += 1
            target_texts.append(self._format_detection_target(detection, label))
            if self._detection_track_id(detection) is not None:
                has_track_id = True

            confidence = detection.get('confidence')
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                confidences.append(float(confidence))

        display_name = (node_name or '目标检测').strip()
        hit = bool(detections) if has_detection is None else bool(has_detection)
        if detections:
            if hit:
                content_parts = [f"{display_name}：命中 {len(detections)} 个目标"]
            else:
                content_parts = [f"{display_name}：未命中，返回 {len(detections)} 个候选目标"]
            if has_track_id:
                shown = target_texts[:8]
                target_text = '、'.join(shown)
                if len(target_texts) > 8:
                    target_text += f" 等 {len(target_texts)} 个"
                content_parts.append(f"目标：{target_text}")
            elif label_counts:
                labels = list(label_counts.items())
                label_text = '、'.join(f"{label} × {count}" for label, count in labels[:5])
                if len(labels) > 5:
                    label_text += f"等 {len(labels)} 类"
                content_parts.append(f"类别：{label_text}")
            if confidences:
                min_confidence = min(confidences)
                max_confidence = max(confidences)
                if abs(max_confidence - min_confidence) < 0.0005:
                    content_parts.append(f"置信度：{max_confidence:.1%}")
                else:
                    content_parts.append(f"置信度：{min_confidence:.1%}–{max_confidence:.1%}")
            content = "；".join(content_parts)
        elif hit:
            content = f"{display_name}：命中，但未返回目标明细"
        else:
            content = f"{display_name}：未命中目标"

        event_metadata = {
            'event_type': 'detection',
            'node_name': display_name,
            'has_detection': hit,
            'detection_count': len(detections),
            'label_counts': dict(label_counts),
        }
        if confidences:
            event_metadata.update({
                'confidence_min': min(confidences),
                'confidence_max': max(confidences),
            })
        if metadata:
            event_metadata.update(metadata)
        self.add_info(node_id, content, event_metadata)

    def has_event(
        self,
        event_type: str,
        node_id: Optional[str] = None,
        node_ids: Optional[Collection[str]] = None,
    ) -> bool:
        """判断是否已记录指定类型的过程事件。"""
        allowed_node_ids = set(node_ids) if node_ids is not None else None
        return any(
            log.get('metadata', {}).get('event_type') == event_type
            and (node_id is None or log.get('node_id') == node_id)
            and (allowed_node_ids is None or log.get('node_id') in allowed_node_ids)
            for log in self.logs
        )

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
        include_metadata: bool = False,
        node_ids: Optional[Collection[str]] = None,
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
        allowed_node_ids = set(node_ids) if node_ids is not None else None
        scoped_logs = [
            log for log in self.logs
            if allowed_node_ids is None or log.get('node_id') in allowed_node_ids
        ]

        if not scoped_logs:
            return "无执行日志"

        if format_type == 'detailed':
            # 自动使用智能分组格式（带节点ID）
            return self._build_grouped_message(include_node_id=True, logs=scoped_logs)

        elif format_type == 'simple':
            # 自动使用智能分组格式（不带节点ID）
            return self._build_grouped_message(include_node_id=False, logs=scoped_logs)

        elif format_type == 'summary':
            summary = []

            # 按级别分组统计
            error_logs = [log for log in scoped_logs if log['level'] == 'error']
            warning_logs = [log for log in scoped_logs if log['level'] == 'warning']
            info_logs = [log for log in scoped_logs if log['level'] == 'info']

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

    def _build_grouped_message(
        self,
        include_node_id: bool = True,
        logs: Optional[List[Dict]] = None,
    ) -> str:
        """
        构建分组格式的告警消息

        展示完整的分支判断链路：
        1. 将条件日志和检测日志按分支分组
        2. 每个分支显示：算法检测 -> 条件判断 -> 是否触发
        3. 突出显示最终触发的分支

        Args:
            include_node_id: 是否包含节点ID
        """
        selected_logs = self.logs if logs is None else logs

        # 分类日志
        condition_logs = []  # 条件判断日志
        detection_logs = []  # 算法检测日志
        other_logs = []      # 其他日志

        for log in selected_logs:
            content = log['content']
            event_type = log.get('metadata', {}).get('event_type')
            if event_type == 'condition' or content.startswith('条件判断:'):
                condition_logs.append(log)
            elif event_type == 'detection' or (content.startswith('检测到 ') and ' 个目标' in content):
                detection_logs.append(log)
            else:
                other_logs.append(log)

        if not condition_logs and not detection_logs:
            # 如果没有检测或条件日志，返回简单格式
            lines = []
            for log in selected_logs:
                if include_node_id:
                    lines.append(f"[{log['node_id']}] {log['content']}")
                else:
                    lines.append(log['content'])
            return "\n".join(lines) if lines else "无执行日志"

        lines = []

        # 按条件日志分组，每个条件日志前可能有对应的检测日志
        # 按时间顺序分组（假设日志按时间顺序记录）
        all_logs = sorted(selected_logs, key=lambda x: x.get('timestamp', 0))

        # 分组：每个分支包含检测日志+条件日志
        branches = []
        current_branch = {'detection': None, 'condition': None, 'logs': []}

        for log in all_logs:
            content = log['content']

            event_type = log.get('metadata', {}).get('event_type')

            if event_type == 'detection' or (content.startswith('检测到 ') and ' 个目标' in content):
                # 新的检测日志，可能开始新分支
                if current_branch['detection'] or current_branch['condition']:
                    branches.append(current_branch)
                    current_branch = {'detection': None, 'condition': None, 'logs': []}
                current_branch['detection'] = log
            elif event_type == 'condition' or content.startswith('条件判断:'):
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

            # 算法可以直接连到告警节点，此时没有条件日志。
            # 旧实现会整个跳过这种分支，使告警只剩下用户填写的固定文案。
            if cond_log:
                metadata = cond_log.get('metadata', {})
                condition_passed = metadata.get('condition_passed', False)
                branch_title = f"分支 {idx + 1}: {'✓ 触发预警' if condition_passed else '未触发'}"
            else:
                metadata = det_log.get('metadata', {}) if det_log else {}
                condition_passed = metadata.get(
                    'has_detection',
                    metadata.get('detection_count', 0) > 0,
                )
                branch_title = f"检测步骤 {idx + 1}: {'✓ 命中' if condition_passed else '未命中'}"

            if condition_passed:
                has_passed_branch = True
            lines.append(branch_title)

            # 检测日志
            if det_log:
                if include_node_id:
                    lines.append(f"  └─ [{det_log['node_id']}] {det_log['content']}")
                else:
                    lines.append(f"  └─ {det_log['content']}")

            # 条件日志
            if cond_log:
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
