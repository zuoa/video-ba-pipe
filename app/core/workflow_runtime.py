from typing import Iterable, Optional, Tuple


def extract_source_id_from_workflow_data(workflow_data: dict) -> Optional[int]:
    """从工作流 JSON 中提取 source 节点绑定的视频源 ID。"""
    if not isinstance(workflow_data, dict):
        return None

    for node in workflow_data.get('nodes', []):
        if node.get('type') != 'source':
            continue

        source_id = node.get('dataId')
        if source_id in (None, ''):
            return None

        try:
            return int(source_id)
        except (TypeError, ValueError):
            return None

    return None


def build_workflow_signature(workflows: Iterable) -> Tuple[Tuple[int, int], ...]:
    """构建用于判断 source host 是否需要重启的签名。"""
    signature = []
    for workflow in workflows:
        workflow_id = getattr(workflow, 'id', None)
        config_version = getattr(workflow, 'config_version', 0)
        if workflow_id is None:
            continue
        signature.append((int(workflow_id), int(config_version)))

    return tuple(sorted(signature))
