"""Shared workflow validation for internal and public configuration APIs."""

import logging
import re

from app.core.database_models import Algorithm
from app.core.ocr_algorithm_config import validate_ocr_crop_node_config
from app.core.workflow_runtime import validate_single_source_node
from app.core.webhook_workflow_config import validate_workflow_webhook_nodes
from app.core.http_request_workflow import validate_http_value_conditions
from app.core.detection_filter import validate_workflow_detection_filter_nodes
from app.core.time_schedule import validate_workflow_time_schedule_nodes

_logger = logging.getLogger(__name__)


def _validate_ocr_text_conditions(workflow_data):
    if not isinstance(workflow_data, dict):
        return True, None
    nodes = {node.get('id'): node for node in workflow_data.get('nodes', []) if isinstance(node, dict)}
    connections = workflow_data.get('connections', []) or []
    connected_pairs = {
        (connection.get('from') or connection.get('from_node_id'), connection.get('to') or connection.get('to_node_id'))
        for connection in connections
        if isinstance(connection, dict)
    }

    for node in nodes.values():
        if node.get('type') != 'condition':
            continue
        data = node.get('data') or {}
        condition_kind = data.get('conditionKind') or data.get('condition_kind') or 'count'
        if condition_kind != 'ocr_text':
            continue

        source_node_id = data.get('sourceNodeId') or data.get('source_node_id')
        source_node = nodes.get(source_node_id)
        if not source_node or (source_node_id, node.get('id')) not in connected_pairs:
            return False, f"文字条件 {node.get('name') or node.get('id')} 必须选择已连接的 OCR 节点"
        if source_node.get('type') != 'algorithm':
            return False, '文字条件来源必须是算法节点'
        try:
            algorithm = Algorithm.get_by_id(source_node.get('dataId') or source_node.get('algorithmId'))
        except (Algorithm.DoesNotExist, TypeError, ValueError):
            return False, '文字条件来源算法不存在'
        if (algorithm.ext_config.get('algorithm_type') or 'script') != 'ocr':
            return False, '文字条件来源必须是 OCR 算法'

        operator = data.get('textOperator') or data.get('text_operator') or 'contains'
        pattern_type = data.get('patternType') or data.get('pattern_type') or 'keywords'
        if operator not in ('contains', 'not_contains'):
            return False, 'OCR 文字条件操作符无效'
        if pattern_type == 'regex':
            pattern = data.get('regexPattern') or data.get('regex_pattern') or ''
            if not pattern:
                return False, 'OCR 文字条件正则不能为空'
            try:
                re.compile(pattern)
            except re.error as exc:
                return False, f'OCR 文字条件正则无效: {exc}'
        elif pattern_type == 'keywords':
            keywords = data.get('keywords')
            if not isinstance(keywords, list) or not any(str(item).strip() for item in keywords):
                return False, 'OCR 文字条件至少需要一个关键词'
            logic = data.get('keywordLogic') or data.get('keyword_logic') or 'any'
            if logic not in ('any', 'all'):
                return False, 'OCR 文字条件关键词逻辑无效'
        else:
            return False, 'OCR 文字条件匹配方式无效'
    return True, None


def _validate_count_change_conditions(workflow_data):
    """校验数量骤变条件的来源连接与数值配置。"""
    if not isinstance(workflow_data, dict):
        return True, None
    nodes = {
        node.get('id'): node
        for node in workflow_data.get('nodes', [])
        if isinstance(node, dict) and node.get('id')
    }
    connections = [
        connection
        for connection in (workflow_data.get('connections', []) or [])
        if isinstance(connection, dict)
    ]
    connected_pairs = {
        (
            connection.get('from') or connection.get('from_node_id'),
            connection.get('to') or connection.get('to_node_id'),
        )
        for connection in connections
    }
    valid_source_types = {
        'algorithm', 'function', 'external_api', 'externalApi',
        'detection_filter', 'detectionFilter',
    }

    for node in nodes.values():
        if node.get('type') != 'condition':
            continue
        data = node.get('data') or {}
        condition_kind = data.get('conditionKind') or data.get('condition_kind') or 'count'
        if condition_kind != 'count_change':
            continue

        name = node.get('name') or node.get('id')
        source_node_id = data.get('sourceNodeId') or data.get('source_node_id')
        source_node = nodes.get(source_node_id)
        incoming_connections = [
            connection for connection in connections
            if (connection.get('to') or connection.get('to_node_id')) == node.get('id')
        ]
        if len(incoming_connections) != 1:
            return False, f'数量骤变条件 {name} 必须且只能连接一个上游结果节点'
        if not source_node or (source_node_id, node.get('id')) not in connected_pairs:
            return False, f'数量骤变条件 {name} 选择的来源必须与唯一入边一致'
        if source_node.get('type') not in valid_source_types:
            return False, f'数量骤变条件 {name} 的来源必须是算法、函数或外部 API 节点'

        direction = data.get('direction', 'both')
        if direction not in ('increase', 'decrease', 'both'):
            return False, f'数量骤变条件 {name} 的变化方向无效'

        numeric_fields = (
            ('windowSize', 'window_size', 10, int, 2, 300, '历史窗口'),
            ('relativeThreshold', 'relative_threshold', 0.5, float, 0.000001, 100, '相对阈值'),
            ('absoluteThreshold', 'absolute_threshold', 3, int, 1, 100000, '绝对阈值'),
            ('confirmationCount', 'confirmation_count', 1, int, 1, 20, '确认次数'),
        )
        for camel_key, snake_key, default, converter, minimum, maximum, label in numeric_fields:
            raw_value = data.get(camel_key, data.get(snake_key, default))
            if isinstance(raw_value, bool):
                return False, f'数量骤变条件 {name} 的{label}无效'
            try:
                numeric_value = float(raw_value)
            except (TypeError, ValueError):
                return False, f'数量骤变条件 {name} 的{label}必须是数字'
            if converter is int and not numeric_value.is_integer():
                return False, f'数量骤变条件 {name} 的{label}必须是整数'
            value = converter(numeric_value)
            if value < minimum or value > maximum:
                return False, f'数量骤变条件 {name} 的{label}必须在 {minimum}-{maximum} 之间'

        labels = data.get('labels', [])
        if not isinstance(labels, list) or any(not isinstance(item, str) for item in labels):
            return False, f'数量骤变条件 {name} 的类别筛选必须是字符串数组'

    return True, None


_CROP_SOURCE_TYPES = {
    'algorithm', 'function', 'external_api', 'externalApi',
    'detection_filter', 'detectionFilter',
}
_GATE_EDGE_CONDITIONS = {'detected', 'not_detected', 'true', 'false', 'yes', 'no'}
_PERSISTED_ALGO_EDGE_CONDITIONS = {'detected', 'not_detected'}


def _connection_source(connection):
    return connection.get('from') or connection.get('from_node_id')


def _connection_target(connection):
    return connection.get('to') or connection.get('to_node_id')


def _node_overlay_config(node):
    config = node.get('config')
    if isinstance(config, dict):
        return config
    data = node.get('data') if isinstance(node.get('data'), dict) else {}
    nested = data.get('config')
    return nested if isinstance(nested, dict) else {}


def _normalize_algorithm_edge_condition(condition):
    """UI 'always' and unknown values persist as JSON null, never the string 'always'."""
    if condition in (None, '', 'always'):
        return None
    if condition in _PERSISTED_ALGO_EDGE_CONDITIONS:
        return condition
    return None


def _sanitize_workflow_edge_conditions(workflow_data):
    if not isinstance(workflow_data, dict):
        return workflow_data
    nodes = {
        node.get('id'): node
        for node in workflow_data.get('nodes', [])
        if isinstance(node, dict) and node.get('id')
    }
    for connection in workflow_data.get('connections', []) or []:
        if not isinstance(connection, dict):
            continue
        source_node = nodes.get(_connection_source(connection)) or {}
        if source_node.get('type') == 'condition':
            continue
        connection['condition'] = _normalize_algorithm_edge_condition(connection.get('condition'))
    return workflow_data


def _is_ocr_algorithm_node(node):
    if not isinstance(node, dict) or node.get('type') != 'algorithm':
        return False
    try:
        algorithm = Algorithm.get_by_id(node.get('dataId') or node.get('algorithmId'))
    except (Algorithm.DoesNotExist, TypeError, ValueError):
        return False
    ext_config = algorithm.ext_config if getattr(algorithm, 'ext_config', None) else {}
    if not isinstance(ext_config, dict):
        return False
    return (ext_config.get('algorithm_type') or 'script') == 'ocr'


def _mixed_incoming_or_warning(name, incoming):
    has_empty = False
    has_gated = False
    for connection in incoming:
        condition = connection.get('condition')
        if condition in (None, '', 'always'):
            has_empty = True
        elif condition in _GATE_EDGE_CONDITIONS:
            has_gated = True
    if has_empty and has_gated:
        return (
            f'节点 {name} 同时存在空条件和门控入边，执行语义是 OR：'
            '任意一条空条件入边触发即会执行。'
        )
    return None


def _validate_ocr_crop_nodes(workflow_data):
    """校验 OCR 节点裁剪 overlay 与 upstream_crops 入边合同。

    只调用 validate_ocr_crop_node_config，禁止 normalize_ocr_algorithm_config。
    返回 (is_valid, error_message, warnings)。
    """
    if not isinstance(workflow_data, dict):
        return True, None, []

    nodes = {
        node.get('id'): node
        for node in workflow_data.get('nodes', [])
        if isinstance(node, dict) and node.get('id')
    }
    connections = [
        connection
        for connection in (workflow_data.get('connections', []) or [])
        if isinstance(connection, dict)
    ]
    warnings = []

    for node in nodes.values():
        name = node.get('name') or node.get('id')
        incoming = [
            connection for connection in connections
            if _connection_target(connection) == node.get('id')
        ]
        is_ocr = _is_ocr_algorithm_node(node)

        if is_ocr:
            config = _node_overlay_config(node)
            try:
                overlay = validate_ocr_crop_node_config(config)
            except ValueError as exc:
                return False, str(exc), warnings

            input_mode = overlay.get('input_mode') or config.get('input_mode') or 'frame'
            if input_mode == 'upstream_crops':
                if len(incoming) != 1:
                    return False, (
                        f'OCR 节点 {name} 的上游裁剪模式必须恰好连接一条入边'
                    ), warnings
                source_node = nodes.get(_connection_source(incoming[0]))
                if not source_node or source_node.get('type') not in _CROP_SOURCE_TYPES:
                    return False, (
                        f'OCR 节点 {name} 的上游裁剪入边必须来自算法、函数或外部 API'
                    ), warnings
                if incoming[0].get('condition') != 'detected':
                    return False, (
                        f'OCR 节点 {name} 的上游裁剪入边条件必须为 detected'
                    ), warnings
            else:
                if any(connection.get('condition') == 'detected' for connection in incoming):
                    warning = (
                        f'OCR 节点 {name} 入边已设为「检测到」，但仍使用整帧识别；'
                        '如只需识别检测框内文字，请将输入模式改为「上游裁剪」。'
                    )
                    warnings.append(warning)
                    _logger.warning(warning)
                mixed_warning = _mixed_incoming_or_warning(name, incoming)
                if mixed_warning:
                    warnings.append(mixed_warning)
                    _logger.warning(mixed_warning)
        else:
            mixed_warning = _mixed_incoming_or_warning(name, incoming)
            if mixed_warning:
                warnings.append(mixed_warning)
                _logger.warning(mixed_warning)

    return True, None, warnings


def validate_workflow_configuration(workflow_data):
    """Run the same complete validation for all field-scoped updates."""
    validators = (
        validate_single_source_node,
        _validate_ocr_text_conditions,
        _validate_count_change_conditions,
        _validate_ocr_crop_nodes,
        validate_workflow_webhook_nodes,
        validate_http_value_conditions,
        validate_workflow_detection_filter_nodes,
        validate_workflow_time_schedule_nodes,
    )
    for validator in validators:
        result = validator(workflow_data)
        if not result[0]:
            return False, result[1]
    return True, None
