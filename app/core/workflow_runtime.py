from copy import deepcopy
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


def validate_single_source_node(workflow_data: dict) -> tuple[bool, str]:
    """校验工作流中必须且只能有一个合法的 source 节点。"""
    if not isinstance(workflow_data, dict):
        return False, "workflow_data 必须是对象"

    nodes = workflow_data.get('nodes', [])
    if not isinstance(nodes, list):
        return False, "workflow_data.nodes 必须是数组"

    source_nodes = [node for node in nodes if node.get('type') == 'source']
    if not source_nodes:
        return False, "工作流必须包含一个视频源节点"
    if len(source_nodes) > 1:
        return False, "工作流只允许包含一个视频源节点"

    source_id = source_nodes[0].get('dataId')
    if source_id in (None, ''):
        return False, "视频源节点缺少 dataId"

    try:
        int(source_id)
    except (TypeError, ValueError):
        return False, "视频源节点 dataId 非法"

    return True, ""


def normalize_source_node_fields(workflow_data: dict, source) -> dict:
    """统一 source 节点字段，确保 dataId/videoSourceId/名称/编码保持一致。"""
    normalized = deepcopy(workflow_data) if isinstance(workflow_data, dict) else {}
    nodes = normalized.get('nodes')
    if not isinstance(nodes, list):
        normalized['nodes'] = []
        return normalized

    source_id = int(getattr(source, 'id'))
    source_name = getattr(source, 'name', None)
    source_code = getattr(source, 'source_code', None)

    for node in nodes:
        if node.get('type') != 'source':
            continue

        node['dataId'] = source_id
        node['videoSourceId'] = source_id
        node['videoSourceName'] = source_name
        node['videoSourceCode'] = source_code

        data = node.get('data')
        if isinstance(data, dict):
            data['dataId'] = source_id
            data['videoSourceId'] = source_id
            data['videoSourceName'] = source_name
            data['videoSourceCode'] = source_code

    return normalized


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
