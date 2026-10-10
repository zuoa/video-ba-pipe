"""Entry configuration discovery and safe graph edits, independent of HTTP."""

from copy import deepcopy
import math
from uuid import uuid4

from app.core.time_schedule import validate_weekly_schedule
from app.core.workflow_batch_config import SUPPORTED_CHANGES
from app.core.workflow_runtime import get_node_type, is_source_node, validate_single_source_node


class ConfigurationError(ValueError):
    def __init__(self, code, message, status=400, details=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or []


ENTRY_TYPES = ('time_schedule', 'roi_draw')
ROI_MODES = {'pre_mask', 'crop_infer', 'post_filter'}
ROI_ANCHORS = {
    'top_left', 'top_center', 'top_right', 'center_left', 'center',
    'center_right', 'bottom_left', 'bottom_center', 'bottom_right',
}


def _entry_type(node):
    node_type = get_node_type(node)
    return 'roi_draw' if node_type == 'roi' else node_type


def positive_integer(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError('invalid_field', f'{field} 必须是正整数')
    return value


def validate_fields(value, allowed, required=()):
    if not isinstance(value, dict):
        raise ConfigurationError('invalid_request', '请求必须是 JSON 对象')
    unknown = set(value) - set(allowed)
    if unknown:
        raise ConfigurationError('unknown_field', f'包含未定义的字段: {", ".join(sorted(unknown))}')
    missing = set(required) - set(value)
    if missing:
        raise ConfigurationError('missing_required_field', f'缺少字段: {", ".join(sorted(missing))}')


def dry_run_value(data):
    value = data.get('dry_run', True)
    if not isinstance(value, bool):
        raise ConfigurationError('invalid_field', 'dry_run 必须是布尔值')
    return value


def normalize_roi_regions(regions):
    if not isinstance(regions, list):
        raise ConfigurationError('invalid_field', 'roi_regions 必须是数组')
    result = []
    for index, region in enumerate(regions):
        label = f'roi_regions[{index}]'
        try:
            validate_fields(region, {'name', 'mode', 'polygon', 'anchor'}, {'name', 'mode', 'polygon'})
            if not isinstance(region['name'], str) or not region['name'].strip():
                raise ConfigurationError('invalid_field', 'name 必须是非空字符串')
            if not isinstance(region['mode'], str) or region['mode'] not in ROI_MODES:
                raise ConfigurationError('invalid_field', 'mode 必须是 pre_mask、crop_infer 或 post_filter')
            if 'anchor' in region and (
                not isinstance(region['anchor'], str) or region['anchor'] not in ROI_ANCHORS
            ):
                raise ConfigurationError('invalid_field', 'anchor 无效')
            polygon = region['polygon']
            if not isinstance(polygon, list) or len(polygon) < 3:
                raise ConfigurationError('invalid_field', 'polygon 至少需要三个顶点')
            points = []
            for point_index, point in enumerate(polygon):
                validate_fields(point, {'x', 'y'}, {'x', 'y'})
                for axis in ('x', 'y'):
                    value = point[axis]
                    if (isinstance(value, bool) or not isinstance(value, (int, float))
                            or not 0 <= value <= 1 or not math.isfinite(value)):
                        raise ConfigurationError('invalid_field', f'polygon[{point_index}].{axis} 必须是 0-1 的有限数值')
                points.append((point['x'], point['y']))
            area = sum(
                x * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * y
                for i, (x, y) in enumerate(points)
            )
            if len(set(points)) < 3 or area == 0:
                raise ConfigurationError('invalid_field', 'polygon 需要三个不同顶点且面积不能为零')
        except ConfigurationError as exc:
            raise ConfigurationError(exc.code, f'{label}: {exc}', details=[{'field': label}]) from exc
        result.append(deepcopy(region))
    return result


def _endpoint(connection, primary, alias):
    if primary in connection and alias in connection and connection[primary] != connection[alias]:
        raise ConfigurationError('invalid_workflow_structure', '连线端点字段不一致')
    return connection.get(primary) or connection.get(alias)


def inspect_entry(workflow_data):
    """Recognize a single, unconditional prefix before any branching."""
    valid, error = validate_single_source_node(workflow_data)
    if not valid:
        raise ConfigurationError('invalid_workflow_structure', error)
    nodes = workflow_data['nodes']
    ids = [node.get('id') if isinstance(node, dict) else None for node in nodes]
    if any(not isinstance(node_id, str) or not node_id for node_id in ids) or len(set(ids)) != len(ids):
        raise ConfigurationError('invalid_workflow_structure', '节点 ID 必须是非空且唯一的字符串')
    for node in nodes:
        for key in ('data', 'config'):
            if node.get(key) is not None and not isinstance(node[key], dict):
                raise ConfigurationError('invalid_workflow_structure', f'节点 {node["id"]} 的 {key} 必须是对象')
    connections = workflow_data.get('connections', [])
    if not isinstance(connections, list):
        raise ConfigurationError('invalid_workflow_structure', 'connections 必须是数组')
    outgoing = {node_id: [] for node_id in ids}
    incoming = {node_id: [] for node_id in ids}
    for conn in connections:
        if not isinstance(conn, dict):
            raise ConfigurationError('invalid_workflow_structure', '连线必须是对象')
        start = _endpoint(conn, 'from', 'from_node_id')
        end = _endpoint(conn, 'to', 'to_node_id')
        if not isinstance(start, str) or not isinstance(end, str) or start not in outgoing or end not in incoming:
            raise ConfigurationError('invalid_workflow_structure', '连线引用不存在的节点')
        outgoing[start].append(conn)
        incoming[end].append(conn)
    # Reject cycles: an entry prefix cannot safely gate a feedback path.
    degrees = {key: len(value) for key, value in incoming.items()}
    pending = [key for key, degree in degrees.items() if degree == 0]
    visited = 0
    while pending:
        current = pending.pop()
        visited += 1
        for conn in outgoing[current]:
            end = _endpoint(conn, 'to', 'to_node_id')
            degrees[end] -= 1
            if degrees[end] == 0:
                pending.append(end)
    if visited != len(nodes):
        raise ConfigurationError('invalid_workflow_structure', '编排存在循环连线')
    source = next(node for node in nodes if is_source_node(node))
    if incoming[source['id']]:
        raise ConfigurationError('invalid_workflow_structure', '视频源不能有入边')
    current = source
    entries = {}
    conflict = False
    for node_type in ENTRY_TYPES:
        matches = [node for node in nodes if _entry_type(node) == node_type]
        if not matches:
            continue
        node = matches[0]
        links = outgoing[current['id']]
        if (len(matches) != 1 or len(links) != 1
                or _endpoint(links[0], 'to', 'to_node_id') != node['id']
                or links[0].get('condition') not in (None, '', 'always')
                or len(incoming[node['id']]) != 1):
            conflict = True
            continue
        entries[node_type] = node
        current = node
    return source, entries, outgoing, conflict


def configuration_view(workflow):
    graph = workflow.data_dict
    _source, entries, _outgoing, conflict = inspect_entry(graph)
    capabilities = {}
    for node_type, key, stored in (
        ('time_schedule', 'weekly_schedule', 'weeklySchedule'),
        ('roi_draw', 'roi_regions', 'roiRegions'),
    ):
        node = entries.get(node_type)
        capabilities[node_type] = {
            'status': 'conflict' if conflict else ('configured' if node else 'missing'),
            'node_id': node['id'] if node else None,
            key: deepcopy((node.get('data') or {}).get(stored, (node.get('data') or {}).get(key))) if node else None,
        }
    common = []
    for node in graph['nodes']:
        node_type = get_node_type(node)
        if node_type not in SUPPORTED_CHANGES:
            continue
        data = (node.get('config') or {}) if node_type == 'algorithm' else (node.get('data') or {})
        fields = SUPPORTED_CHANGES[node_type]
        stored_keys = {'trigger_condition': 'triggerCondition', 'weekly_schedule': 'weeklySchedule'}
        common.append({
            'node_id': node['id'], 'node_type': node_type,
            'name': node.get('name') or node['id'],
            'allowed_fields': sorted(fields),
            'config': {key: deepcopy(data.get(stored_keys.get(key, key), data.get(key))) for key in sorted(fields)},
        })
    return {
        'workflow_id': workflow.id, 'config_version': workflow.config_version,
        'is_active': workflow.is_active, **capabilities, 'common_parameters': common,
    }


def apply_entry_configuration(graph, node_type, value):
    """Return a new graph and reviewable changes; never mutate the input."""
    if node_type == 'time_schedule':
        valid, error = validate_weekly_schedule(value)
        if not valid:
            raise ConfigurationError('invalid_field', error, details=[{'field': 'weekly_schedule'}])
        field = 'weeklySchedule'
    elif node_type == 'roi_draw':
        value = normalize_roi_regions(value)
        field = 'roiRegions'
    else:
        raise ConfigurationError('invalid_field', '不支持的入口组件')
    updated = deepcopy(graph)
    source, entries, outgoing, conflict = inspect_entry(updated)
    if conflict:
        raise ConfigurationError(
            'entry_component_conflict', '入口组件重复、位于分支或顺序异常，请在编辑器中整理', 409,
            [{'node_id': node['id'], 'node_type': _entry_type(node)}
             for node in updated['nodes'] if _entry_type(node) in ENTRY_TYPES],
        )
    node = entries.get(node_type)
    if node is None and node_type == 'roi_draw' and not value:
        return updated, {'action': 'unchanged', 'node_type': node_type, 'node_id': None, 'connections': []}
    rewired = []
    action = 'updated'
    if node is None:
        predecessor = entries.get('time_schedule', source) if node_type == 'roi_draw' else source
        links = outgoing[predecessor['id']]
        if not links:
            raise ConfigurationError('invalid_workflow_structure', '插入点没有下游连接，无法补建入口组件')
        action = 'created'
        try:
            x = float(predecessor.get('x', predecessor.get('position_x')) or 0)
            y = float(predecessor.get('y', predecessor.get('position_y')) or 0)
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError('non-finite position')
        except (TypeError, ValueError, OverflowError) as exc:
            raise ConfigurationError('invalid_workflow_structure', '插入点坐标无效') from exc
        node = {
            'id': f'{node_type}-{uuid4().hex}',
            # The editor persists ROI as "roi"; the executor accepts both aliases.
            'type': 'roi' if node_type == 'roi_draw' else node_type,
            'name': '时间启动区间' if node_type == 'time_schedule' else '热区绘制',
            'x': x + 180, 'y': y,
            'data': {},
        }
        updated['nodes'].append(node)
        for conn in links:
            before = deepcopy(conn)
            conn['from'] = conn['from_node_id'] = node['id']
            conn['to'] = conn['to_node_id'] = _endpoint(conn, 'to', 'to_node_id')
            conn['from_port'] = 'output'
            conn['to_port'] = conn.get('to_port') or conn.get('toPort') or 'input'
            if 'fromPort' in conn:
                conn['fromPort'] = 'output'
            rewired.append({'before': before, 'after': deepcopy(conn)})
        new_connection = {
            'id': f'edge-{uuid4().hex}',
            'from': predecessor['id'], 'from_node_id': predecessor['id'],
            'to': node['id'], 'to_node_id': node['id'],
            'from_port': 'output', 'to_port': 'input', 'condition': None, 'label': '',
        }
        updated['connections'].append(new_connection)
        rewired.append({'before': None, 'after': deepcopy(new_connection)})
    data = node.get('data') or {}
    node['data'] = data
    data[field] = deepcopy(value)
    data.pop('weekly_schedule' if node_type == 'time_schedule' else 'roi_regions', None)
    # Keep legacy editor fallback data in sync when present.
    if 'edges' in updated:
        updated['edges'] = deepcopy(updated['connections'])
    return updated, {
        'action': action, 'node_type': node_type, 'node_id': node['id'],
        'fields': ['weekly_schedule' if node_type == 'time_schedule' else 'roi_regions'],
        'connections': rewired,
    }
