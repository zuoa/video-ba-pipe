from copy import deepcopy
from typing import Iterable, Optional, Tuple


SOURCE_NODE_TYPES = {'source', 'videosource', 'video_source'}
SOURCE_ID_KEYS = ('dataId', 'data_id', 'videoSourceId', 'video_source_id')


def get_node_type(node: dict) -> str:
    if not isinstance(node, dict):
        return ''

    data = node.get('data')
    nested_type = data.get('type') if isinstance(data, dict) else None
    return str(node.get('type') or nested_type or '').strip().lower()


def is_source_node(node: dict) -> bool:
    return get_node_type(node) in SOURCE_NODE_TYPES


def iter_source_node_id_values(node: dict):
    if not isinstance(node, dict):
        return

    data = node.get('data')
    for key in SOURCE_ID_KEYS:
        yield node.get(key)

    if isinstance(data, dict):
        for key in SOURCE_ID_KEYS:
            yield data.get(key)


def extract_source_id_from_node(node: dict) -> Optional[int]:
    for source_id in iter_source_node_id_values(node):
        if source_id in (None, ''):
            continue

        try:
            return int(source_id)
        except (TypeError, ValueError):
            continue

    return None


def _source_node_has_any_id_value(node: dict) -> bool:
    return any(source_id not in (None, '') for source_id in iter_source_node_id_values(node))


def _build_runtime_comparable_workflow_data(workflow_data: dict) -> dict:
    comparable = deepcopy(workflow_data) if isinstance(workflow_data, dict) else {}
    nodes = comparable.get('nodes')
    if not isinstance(nodes, list):
        return comparable

    for node in nodes:
        if get_node_type(node) == 'external_api':
            node.pop('externalApiName', None)
            data = node.get('data')
            if isinstance(data, dict):
                data.pop('externalApiName', None)

        if not is_source_node(node):
            continue

        node['type'] = 'source'
        node.pop('videoSourceName', None)
        node.pop('videoSourceCode', None)

        data = node.get('data')
        if isinstance(data, dict):
            data['type'] = 'source'
            data.pop('videoSourceName', None)
            data.pop('videoSourceCode', None)

        source_id = extract_source_id_from_node(node)
        if source_id is None:
            continue

        node['dataId'] = source_id
        node['videoSourceId'] = source_id
        if isinstance(data, dict):
            data['dataId'] = source_id
            data['videoSourceId'] = source_id

    return comparable


def workflow_configs_equivalent(left: dict, right: dict) -> bool:
    """比较两个工作流是否在运行时语义上等价。"""
    return _build_runtime_comparable_workflow_data(left) == _build_runtime_comparable_workflow_data(right)


def extract_source_id_from_workflow_data(workflow_data: dict) -> Optional[int]:
    """从工作流 JSON 中提取 source 节点绑定的视频源 ID。"""
    if not isinstance(workflow_data, dict):
        return None

    for node in workflow_data.get('nodes', []):
        if not is_source_node(node):
            continue

        return extract_source_id_from_node(node)

    return None


def validate_single_source_node(workflow_data: dict) -> tuple[bool, str]:
    """校验工作流中必须且只能有一个合法的 source 节点。"""
    if not isinstance(workflow_data, dict):
        return False, "workflow_data 必须是对象"

    nodes = workflow_data.get('nodes', [])
    if not isinstance(nodes, list):
        return False, "workflow_data.nodes 必须是数组"

    source_nodes = [node for node in nodes if is_source_node(node)]
    if not source_nodes:
        return False, "工作流必须包含一个视频源节点"
    if len(source_nodes) > 1:
        return False, "工作流只允许包含一个视频源节点"

    source_id = extract_source_id_from_node(source_nodes[0])
    if source_id is None and not _source_node_has_any_id_value(source_nodes[0]):
        return False, "视频源节点缺少 dataId"
    if source_id is None:
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
        if not is_source_node(node):
            continue

        node['type'] = 'source'
        node['dataId'] = source_id
        node['videoSourceId'] = source_id
        node['videoSourceName'] = source_name
        node['videoSourceCode'] = source_code

        data = node.get('data')
        if isinstance(data, dict):
            data['type'] = 'source'
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
