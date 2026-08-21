"""
工作流管理 API
"""
import json
import logging
import re
from datetime import datetime
from copy import deepcopy
from flask import jsonify, request
from peewee import IntegrityError

from app.core.database_models import db, Workflow, VideoSource, Algorithm
from app.core.workflow_runtime import (
    build_template_workflow_data,
    extract_source_id_from_workflow_data,
    normalize_source_node_fields,
    validate_single_source_node,
    validate_template_source_node,
    workflow_configs_equivalent,
)
from app.core.webhook_workflow_config import (
    mask_workflow_webhook_secrets,
    merge_workflow_webhook_secrets,
    validate_workflow_webhook_nodes,
)
from app.core.time_schedule import validate_workflow_time_schedule_nodes
from app.core.detection_filter import validate_workflow_detection_filter_nodes
from app.core.workflow_batch_config import (
    BatchConfigValidationError,
    apply_batch_node_changes,
)
from app.config import SNAPSHOT_SAVE_PATH
from app.core.ocr_algorithm_config import validate_ocr_crop_node_config
from app.core.ocr_algorithm_config import is_ocr_algorithm_runtime_available
from app.web.api.auth import (
    require_auth,
    apply_owner_scope,
    require_resource_owner,
    current_username,
)

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


def register_workflows_api(app):
    """注册工作流管理 API 路由"""

    def serialize_workflow(workflow):
        source_template_name = None
        if workflow.source_template_id is not None:
            try:
                source_template_name = workflow.source_template.name
            except Workflow.DoesNotExist:
                source_template_name = None
        return {
            'id': workflow.id,
            'name': workflow.name,
            'description': workflow.description,
            'workflow_data': mask_workflow_webhook_secrets(workflow.data_dict),
            'is_active': workflow.is_active,
            'is_template': workflow.is_template,
            'source_template_id': workflow.source_template_id,
            'source_template_name': source_template_name,
            'video_source_id': workflow.video_source_id,
            'config_version': workflow.config_version,
            'created_at': workflow.created_at.isoformat() if workflow.created_at else None,
            'updated_at': workflow.updated_at.isoformat() if workflow.updated_at else None,
            'created_by': workflow.created_by,
        }

    def find_template_source_duplicate(template_id, source_id, exclude_workflow_id=None):
        query = Workflow.select().where(
            (Workflow.source_template == template_id)
            & (Workflow.video_source == source_id)
        )
        if exclude_workflow_id is not None:
            query = query.where(Workflow.id != exclude_workflow_id)
        return query.first()

    def duplicate_template_source_response(existing_workflow):
        return {
            'code': 'duplicate_template_source',
            'error': '该模板已为此视频源创建过编排',
            'existing_workflow_id': existing_workflow.id if existing_workflow else None,
            'existing_workflow_name': existing_workflow.name if existing_workflow else None,
        }

    def get_algorithm_label_name(algorithm):
        ext_config = algorithm.ext_config if hasattr(algorithm, 'ext_config') else {}
        script_config = algorithm.config_dict if hasattr(algorithm, 'config_dict') else {}
        return (
            ext_config.get('label_name')
            or script_config.get('label_name')
            or algorithm.name
        )
    
    @app.route('/api/workflows', methods=['GET'])
    @require_auth
    def get_workflows():
        """获取所有工作流"""
        try:
            workflows = apply_owner_scope(
                Workflow.select().order_by(Workflow.updated_at.desc()),
                Workflow,
            )
            return jsonify([serialize_workflow(w) for w in workflows])
        except Exception as e:
            app.logger.error(f"获取工作流列表失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/<int:id>', methods=['GET'])
    @require_auth
    def get_workflow(id):
        """获取单个工作流"""
        try:
            workflow = Workflow.get_by_id(id)
            owner_response = require_resource_owner(workflow)
            if owner_response:
                return owner_response
            data_dict = mask_workflow_webhook_secrets(workflow.data_dict)
            
            # 确保 workflow_data 包含必需的字段
            if 'nodes' not in data_dict:
                data_dict['nodes'] = []
            if 'connections' not in data_dict:
                data_dict['connections'] = []
            
            app.logger.info(f"加载工作流 {id} 数据: nodes={len(data_dict.get('nodes', []))}, connections={len(data_dict.get('connections', []))}")
            return jsonify({
                **serialize_workflow(workflow),
                'workflow_data': data_dict,
            })
        except Workflow.DoesNotExist:
            return jsonify({'error': '工作流不存在'}), 404
        except Exception as e:
            app.logger.error(f"获取工作流失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows', methods=['POST'])
    @require_auth
    def create_workflow():
        """创建工作流"""
        try:
            data = request.json
            
            if not data.get('name'):
                return jsonify({'error': '缺少必填字段: name'}), 400
            
            is_template = bool(data.get('is_template', False))
            if data.get('source_template_id') is not None:
                return jsonify({'error': '来源模板只能通过模板复制接口设置'}), 400

            raw_workflow_data = data.get('workflow_data')
            workflow_data = (
                build_template_workflow_data()
                if is_template and not raw_workflow_data
                else deepcopy(raw_workflow_data or {})
            )
            if is_template:
                is_valid, error_message = validate_template_source_node(workflow_data)
                if not is_valid:
                    return jsonify({'error': error_message}), 400
            source_id = extract_source_id_from_workflow_data(workflow_data) if workflow_data else None
            source = None
            if not is_template and source_id is not None:
                try:
                    source = VideoSource.get_by_id(source_id)
                except VideoSource.DoesNotExist:
                    return jsonify({'error': f'视频源不存在: {source_id}'}), 400
                owner_response = require_resource_owner(source)
                if owner_response:
                    return owner_response
                workflow_data = normalize_source_node_fields(workflow_data, source)
            _sanitize_workflow_edge_conditions(workflow_data)
            is_valid, error_message = _validate_ocr_text_conditions(workflow_data)
            if not is_valid:
                return jsonify({'error': error_message}), 400
            is_valid, error_message = _validate_count_change_conditions(workflow_data)
            if not is_valid:
                return jsonify({'error': error_message}), 400
            is_valid, error_message, crop_warnings = _validate_ocr_crop_nodes(workflow_data)
            if not is_valid:
                return jsonify({'error': error_message}), 400
            is_valid, error_message = validate_workflow_webhook_nodes(workflow_data)
            if not is_valid:
                return jsonify({'error': error_message}), 400
            is_valid, error_message = validate_workflow_detection_filter_nodes(workflow_data)
            if not is_valid:
                return jsonify({'error': error_message}), 400
            is_valid, error_message = validate_workflow_time_schedule_nodes(workflow_data)
            if not is_valid:
                return jsonify({'error': error_message}), 400

            workflow = Workflow.create(
                name=data['name'],
                description=data.get('description', ''),
                workflow_data=json.dumps(workflow_data),
                is_active=False if is_template else data.get('is_active', False),
                is_template=is_template,
                source_template=None,
                video_source=None if is_template else source,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                created_by=current_username('admin')
            )
            
            payload = {
                'id': workflow.id,
                'message': '工作流创建成功'
            }
            if crop_warnings:
                payload['warnings'] = crop_warnings
            return jsonify(payload), 201
        except Exception as e:
            app.logger.error(f"创建工作流失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/<int:id>', methods=['PUT'])
    @require_auth
    def update_workflow(id):
        """更新工作流"""
        try:
            workflow = Workflow.get_by_id(id)
            owner_response = require_resource_owner(workflow)
            if owner_response:
                return owner_response
            data = request.json

            if 'is_template' in data and bool(data['is_template']) != workflow.is_template:
                return jsonify({'error': '编排类型创建后不可修改'}), 400
            if 'source_template_id' in data:
                return jsonify({'error': '来源模板不可修改'}), 400

            need_version_bump = False
            crop_warnings = []

            if 'name' in data:
                workflow.name = data['name']
            if 'description' in data:
                workflow.description = data['description']
            if 'workflow_data' in data:
                existing_workflow_data = workflow.data_dict
                workflow_data = deepcopy(data['workflow_data']) if isinstance(data['workflow_data'], dict) else data['workflow_data']
                workflow_data = merge_workflow_webhook_secrets(existing_workflow_data, workflow_data)
                validator = validate_template_source_node if workflow.is_template else validate_single_source_node
                is_valid, error_message = validator(workflow_data)
                if not is_valid:
                    return jsonify({'error': error_message}), 400

                source_id = extract_source_id_from_workflow_data(workflow_data)
                source = None
                if not workflow.is_template:
                    if source_id is None:
                        return jsonify({'error': '视频源节点 dataId 非法'}), 400
                    try:
                        source = VideoSource.get_by_id(source_id)
                    except VideoSource.DoesNotExist:
                        return jsonify({'error': f'视频源不存在: {source_id}'}), 400

                    owner_response = require_resource_owner(source)
                    if owner_response:
                        return owner_response

                    duplicate = (
                        find_template_source_duplicate(
                            workflow.source_template_id,
                            source.id,
                            exclude_workflow_id=workflow.id,
                        )
                        if workflow.source_template_id is not None
                        else None
                    )
                    if duplicate:
                        return jsonify(duplicate_template_source_response(duplicate)), 409
                    workflow_data = normalize_source_node_fields(workflow_data, source)
                _sanitize_workflow_edge_conditions(workflow_data)
                is_valid, error_message = _validate_ocr_text_conditions(workflow_data)
                if not is_valid:
                    return jsonify({'error': error_message}), 400
                is_valid, error_message = _validate_count_change_conditions(workflow_data)
                if not is_valid:
                    return jsonify({'error': error_message}), 400
                is_valid, error_message, crop_warnings = _validate_ocr_crop_nodes(workflow_data)
                if not is_valid:
                    return jsonify({'error': error_message}), 400
                is_valid, error_message = validate_workflow_webhook_nodes(workflow_data)
                if not is_valid:
                    return jsonify({'error': error_message}), 400
                is_valid, error_message = validate_workflow_detection_filter_nodes(workflow_data)
                if not is_valid:
                    return jsonify({'error': error_message}), 400
                is_valid, error_message = validate_workflow_time_schedule_nodes(workflow_data)
                if not is_valid:
                    return jsonify({'error': error_message}), 400
                workflow_data_str = json.dumps(workflow_data)
                app.logger.info(f"保存工作流 {id} 数据: nodes={len(workflow_data.get('nodes', []))}, connections={len(workflow_data.get('connections', []))}")

                # 只有运行时有效配置变化才递增版本号；展示字段刷新不触发重启
                if not workflow_configs_equivalent(existing_workflow_data, workflow_data):
                    need_version_bump = True
                    app.logger.info(f"工作流 {id} 配置已变更，将递增版本号")

                if workflow.workflow_data != workflow_data_str:
                    workflow.workflow_data = workflow_data_str
                workflow.video_source = source

            if 'is_active' in data:
                if workflow.is_template and data['is_active']:
                    return jsonify({'error': '编排模板不可激活'}), 400
                workflow.is_active = data['is_active']

            # 如果工作流配置变更，递增版本号
            if need_version_bump:
                old_version = workflow.config_version
                workflow.config_version = old_version + 1
                app.logger.info(f"工作流 {id} 配置版本号: {old_version} -> {workflow.config_version}")

            workflow.updated_at = datetime.now()
            try:
                workflow.save()
            except IntegrityError:
                duplicate = (
                    find_template_source_duplicate(
                        workflow.source_template_id,
                        workflow.video_source_id,
                        exclude_workflow_id=workflow.id,
                    )
                    if workflow.source_template_id and workflow.video_source_id
                    else None
                )
                return jsonify(duplicate_template_source_response(duplicate)), 409

            payload = {
                'message': '工作流更新成功',
                'config_version': workflow.config_version
            }
            if crop_warnings:
                payload['warnings'] = crop_warnings
            return jsonify(payload)
        except Workflow.DoesNotExist:
            return jsonify({'error': '工作流不存在'}), 404
        except Exception as e:
            app.logger.error(f"更新工作流失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/<int:id>', methods=['DELETE'])
    @require_auth
    def delete_workflow(id):
        """删除工作流"""
        try:
            workflow = Workflow.get_by_id(id)
            owner_response = require_resource_owner(workflow)
            if owner_response:
                return owner_response
            if workflow.is_template:
                derived = Workflow.select().where(Workflow.source_template == workflow.id).first()
                if derived:
                    return jsonify({
                        'error': '该模板已有派生编排，不能删除',
                        'code': 'template_in_use',
                        'existing_workflow_id': derived.id,
                        'existing_workflow_name': derived.name,
                    }), 409
            try:
                workflow.delete_instance(recursive=True)
            except IntegrityError:
                if not workflow.is_template:
                    raise
                return jsonify({
                    'error': '该模板已有派生编排，不能删除',
                    'code': 'template_in_use',
                }), 409
            return jsonify({'message': '工作流删除成功'})
        except Workflow.DoesNotExist:
            return jsonify({'error': '工作流不存在'}), 404
        except Exception as e:
            app.logger.error(f"删除工作流失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/<int:id>/activate', methods=['POST'])
    @require_auth
    def activate_workflow(id):
        """激活工作流（将工作流配置应用到实际任务）"""
        try:
            workflow = Workflow.get_by_id(id)
            owner_response = require_resource_owner(workflow)
            if owner_response:
                return owner_response
            if workflow.is_template:
                return jsonify({'error': '编排模板不可激活'}), 400
            workflow_data = workflow.data_dict

            is_valid, error_message = validate_workflow_detection_filter_nodes(workflow_data)
            if not is_valid:
                return jsonify({'error': error_message}), 400
            is_valid, error_message = validate_workflow_time_schedule_nodes(workflow_data)
            if not is_valid:
                return jsonify({'error': error_message}), 400
            
            # 这里可以添加逻辑：根据工作流配置创建实际的Task和Algorithm关联
            # 暂时只是标记为激活
            workflow.is_active = True
            workflow.save()
            
            return jsonify({'message': '工作流激活成功'})
        except Workflow.DoesNotExist:
            return jsonify({'error': '工作流不存在'}), 404
        except Exception as e:
            app.logger.error(f"激活工作流失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/<int:id>/deactivate', methods=['POST'])
    @require_auth
    def deactivate_workflow(id):
        """停用工作流"""
        try:
            workflow = Workflow.get_by_id(id)
            owner_response = require_resource_owner(workflow)
            if owner_response:
                return owner_response
            workflow.is_active = False
            workflow.save()
            
            return jsonify({'message': '工作流已停用'})
        except Workflow.DoesNotExist:
            return jsonify({'error': '工作流不存在'}), 404
        except Exception as e:
            app.logger.error(f"停用工作流失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/resources', methods=['GET'])
    @require_auth
    def get_workflow_resources():
        """获取可用于工作流的资源（视频源、算法等）"""
        try:
            sources = apply_owner_scope(VideoSource.select(), VideoSource)
            algorithms = apply_owner_scope(Algorithm.select(), Algorithm)
            
            return jsonify({
                'sources': [{
                    'id': s.id,
                    'name': s.name,
                    'source_code': s.source_code,
                    'source_url': s.source_url,
                    'status': s.status,
                    'created_by': s.created_by,
                } for s in sources],
                'algorithms': [{
                    'id': a.id,
                    'name': a.name,
                    'algorithm_type': a.ext_config.get('algorithm_type') or 'script',
                    'runtime_available': is_ocr_algorithm_runtime_available(a.ext_config),
                    'label_name': get_algorithm_label_name(a),
                    'script_path': a.script_path,
                    'created_by': a.created_by,
                } for a in algorithms]
            })
        except Exception as e:
            app.logger.error(f"获取工作流资源失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/capture_frame/<int:source_id>', methods=['GET'])
    @require_auth
    def capture_frame_from_source(source_id):
        """从指定视频源捕获当前帧"""
        import os
        import base64
        import cv2
        
        def capture_with_timeout(source_url, timeout=10):
            """带超时的视频帧捕获"""
            cap = cv2.VideoCapture(source_url)
            
            # 设置超时参数（毫秒）
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout * 1000)
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout * 1000)
            
            if not cap.isOpened():
                return None, None, "无法打开视频源"
            
            ret, frame = cap.read()
            cap.release()
            
            if not ret or frame is None:
                return None, None, "无法读取视频帧"
            
            return ret, frame, None
        
        try:
            source = VideoSource.get_by_id(source_id)
            owner_response = require_resource_owner(source)
            if owner_response:
                return owner_response
            
            # 优先级1: 如果视频源正在运行，优先读取快照（最快且不占用连接）
            if source.status == 'RUNNING':
                snapshot_path = os.path.join(SNAPSHOT_SAVE_PATH, f'{source.source_code}.jpg')
                if os.path.exists(snapshot_path):
                    try:
                        with open(snapshot_path, 'rb') as f:
                            image_data = f.read()
                            image_base64 = base64.b64encode(image_data).decode('utf-8')
                            
                        app.logger.info(f"成功读取快照: {source.name}")
                        return jsonify({
                            'success': True,
                            'image': f'data:image/jpeg;base64,{image_base64}',
                            'source': 'snapshot',
                            'source_name': source.name,
                            'message': '从运行中的视频源快照读取'
                        })
                    except Exception as e:
                        app.logger.warning(f"读取快照失败: {e}，尝试直接连接")
            
            # 优先级2: 视频源未运行或快照不存在，尝试直接连接（带超时和重试）
            app.logger.info(f"尝试直接连接视频源: {source.name} ({source.source_url})")
            
            # 检查是否为RTSP流
            is_rtsp = source.source_url.startswith('rtsp://')
            timeout = 5 if is_rtsp else 10
            
            ret, frame, error = capture_with_timeout(source.source_url, timeout)
            
            if error:
                error_msg = error
                if is_rtsp:
                    error_msg = (
                        f"RTSP连接失败: {error}\n"
                        f"建议: \n"
                        f"1. 如果该视频源配置正确，请启动它\n"
                        f"2. 启动后可从快照读取（更快且不占用连接数）\n"
                        f"3. 或使用'上传图片'方式进行测试"
                    )
                
                app.logger.error(f"捕获帧失败 [{source.name}]: {error}")
                return jsonify({
                    'error': error_msg,
                    'source_status': source.status,
                    'is_rtsp': is_rtsp
                }), 400
            
            # 成功捕获，编码并返回
            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            image_base64 = base64.b64encode(buffer).decode('utf-8')
            
            app.logger.info(f"成功捕获帧: {source.name} [{frame.shape[1]}x{frame.shape[0]}]")
            return jsonify({
                'success': True,
                'image': f'data:image/jpeg;base64,{image_base64}',
                'source': 'direct',
                'source_name': source.name,
                'resolution': f'{frame.shape[1]}x{frame.shape[0]}',
                'message': '通过临时连接获取'
            })
            
        except VideoSource.DoesNotExist:
            return jsonify({'error': '视频源不存在'}), 404
        except cv2.error as e:
            error_detail = str(e)
            app.logger.error(f"OpenCV错误: {error_detail}")
            
            # 识别常见的RTSP错误
            suggestion = ""
            if "503" in error_detail or "ServerUnavailable" in error_detail:
                suggestion = (
                    "RTSP服务器不可用，可能原因:\n"
                    "1. 连接数已满（请先启动任务，从快照读取）\n"
                    "2. 服务器正在重启或维护\n"
                    "3. 认证失败（检查URL中的用户名密码）\n"
                    "4. 网络问题（检查网络连接）"
                )
            elif "timeout" in error_detail.lower():
                suggestion = "连接超时，请检查网络和RTSP服务器状态"
            
            return jsonify({
                'error': f'捕获失败: {error_detail}',
                'suggestion': suggestion
            }), 500
        except Exception as e:
            app.logger.error(f"捕获帧失败: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'捕获帧失败: {str(e)}'}), 500

    # ==================== 批量复制相关 API ====================

    def generate_workflow_name(source: VideoSource, workflow_data: dict, template_name: str = '') -> str:
        """
        智能生成工作流名称

        规则：
        1. 提取算法节点信息
        2. 组合：{视频源名称}-{算法描述}
        3. 如果名称过长，截断算法部分
        """
        # 提取所有 algorithm 节点
        algo_nodes = [n for n in workflow_data.get('nodes', []) if n.get('type') == 'algorithm']

        if not algo_nodes:
            return f"{source.name}-检测流程"

        # 提取算法名称
        algo_names = []
        for node in algo_nodes:
            # 优先级：label > name > data.name > data.algorithmName
            label = (node.get('label', '') or node.get('name', '') or '').strip()
            if not label:
                data = node.get('data', {})
                label = data.get('name', data.get('algorithmName', '算法'))
            algo_names.append(label)

        # 组合算法名称
        if len(algo_names) == 1:
            algo_desc = algo_names[0]
        elif len(algo_names) == 2:
            algo_desc = "+".join(algo_names)
        else:
            algo_desc = f"{algo_names[0]}等{len(algo_names)}个算法"

        # 生成最终名称
        full_name = f"{source.name}-{algo_desc}"

        # 长度限制
        if len(full_name) > 50:
            max_algo_len = 50 - len(source.name) - 1
            algo_desc = algo_desc[:max_algo_len] + "..."
            full_name = f"{source.name}-{algo_desc}"

        return full_name

    @app.route('/api/workflows/<int:workflow_id>/batch-copy', methods=['POST'])
    @require_auth
    def batch_copy_workflow(workflow_id):
        """批量复制工作流到多个视频源"""
        try:
            data = request.json or {}
            source_ids = data.get('source_ids', [])
            is_active = data.get('is_active', False)

            if not source_ids:
                return jsonify({'error': '请选择要应用的视频源'}), 400
            if not isinstance(is_active, bool):
                return jsonify({'error': 'is_active 必须是布尔值'}), 400

            # 读取模板工作流
            template = Workflow.get_by_id(workflow_id)
            owner_response = require_resource_owner(template)
            if owner_response:
                return owner_response
            if not template.is_template:
                return jsonify({'error': '只有编排模板可以复制'}), 400
            template_data = template.data_dict
            is_valid, error_message = validate_template_source_node(template_data)
            if not is_valid:
                return jsonify({'error': error_message}), 400

            results = []
            errors = []

            for source_id in source_ids:
                try:
                    source = VideoSource.get_by_id(source_id)
                    source_owner_response = require_resource_owner(source)
                    if source_owner_response:
                        raise PermissionError('Forbidden')

                    duplicate = find_template_source_duplicate(template.id, source.id)
                    if duplicate:
                        errors.append({
                            'source_id': source.id,
                            **duplicate_template_source_response(duplicate),
                        })
                        continue

                    # 深拷贝 workflow_data
                    new_data = deepcopy(template_data)
                    new_data = normalize_source_node_fields(new_data, source)

                    # 生成名称
                    name = generate_workflow_name(source, new_data, template.name)

                    # 根据应用选项创建并按需立即激活工作流
                    try:
                        with db.atomic():
                            new_workflow = Workflow.create(
                                name=name,
                                description=f"从模板 '{template.name}' 复制",
                                workflow_data=json.dumps(new_data),
                                is_active=is_active,
                                is_template=False,
                                source_template=template,
                                video_source=source,
                                created_at=datetime.now(),
                                updated_at=datetime.now(),
                                created_by=current_username('admin')
                            )
                    except IntegrityError:
                        duplicate = find_template_source_duplicate(template.id, source.id)
                        errors.append({
                            'source_id': source.id,
                            **duplicate_template_source_response(duplicate),
                        })
                        continue

                    results.append({
                        'workflow_id': new_workflow.id,
                        'source_id': source.id,
                        'name': name,
                        'source_name': source.name,
                        'is_active': is_active,
                        'source_template_id': template.id,
                    })

                except Exception as e:
                    errors.append({
                        'source_id': source_id,
                        'error': str(e)
                    })

            response = {
                'success': True,
                'template': {
                    'id': template.id,
                    'name': template.name
                },
                'created': results,
                'errors': errors,
                'summary': {
                    'total': len(source_ids),
                    'success': len(results),
                    'failed': len(errors)
                }
            }
            if not results and errors and all(
                item.get('code') == 'duplicate_template_source' for item in errors
            ):
                response['success'] = False
                response['error'] = '所选视频源均已从该模板创建过编排'
                return jsonify(response), 409
            return jsonify(response)

        except Workflow.DoesNotExist:
            return jsonify({'error': '工作流不存在'}), 404
        except Exception as e:
            app.logger.error(f"批量复制工作流失败: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workflows/batch-activate', methods=['POST'])
    @require_auth
    def batch_activate_workflows():
        """批量激活工作流"""
        try:
            data = request.json
            workflow_ids = data.get('workflow_ids', [])

            if not workflow_ids:
                return jsonify({'error': '请选择要激活的工作流'}), 400

            success_count = 0
            failed = []

            for workflow_id in workflow_ids:
                try:
                    workflow = Workflow.get_by_id(workflow_id)
                    owner_response = require_resource_owner(workflow)
                    if owner_response:
                        raise PermissionError('Forbidden')
                    if workflow.is_template:
                        raise ValueError('编排模板不可激活')
                    is_valid, error_message = validate_workflow_detection_filter_nodes(workflow.data_dict)
                    if not is_valid:
                        raise ValueError(error_message)
                    is_valid, error_message = validate_workflow_time_schedule_nodes(workflow.data_dict)
                    if not is_valid:
                        raise ValueError(error_message)
                    workflow.is_active = True
                    workflow.updated_at = datetime.now()
                    workflow.save()
                    success_count += 1
                except Exception as e:
                    failed.append({
                        'workflow_id': workflow_id,
                        'error': str(e)
                    })

            return jsonify({
                'success': True,
                'activated': success_count,
                'failed': failed,
                'message': f'成功激活 {success_count} 个工作流'
            })

        except Exception as e:
            app.logger.error(f"批量激活工作流失败: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workflows/batch-config', methods=['POST'])
    @require_auth
    def batch_config_workflows():
        """Preview or atomically apply field-scoped configuration changes."""
        try:
            data = request.json or {}
            workflow_ids = data.get('workflow_ids') or []
            targets = data.get('targets') or []
            expected_versions = data.get('expected_versions') or {}
            dry_run = data.get('dry_run') is not False

            if not isinstance(workflow_ids, list) or not workflow_ids:
                return jsonify({'error': '请选择要配置的编排'}), 400
            if not isinstance(targets, list) or not targets:
                return jsonify({'error': '至少选择一个要应用的参数'}), 400
            if not isinstance(expected_versions, dict):
                return jsonify({'error': 'expected_versions 格式无效'}), 400
            try:
                normalized_ids = [int(workflow_id) for workflow_id in workflow_ids]
            except (TypeError, ValueError):
                return jsonify({'error': 'workflow_ids 格式无效'}), 400
            if len(normalized_ids) != len(set(normalized_ids)):
                return jsonify({'error': 'workflow_ids 包含重复项'}), 400
            selected_ids = set(normalized_ids)

            workflows_by_id = {}
            pending_data = {}
            change_details = []
            failures = []

            for workflow_id in normalized_ids:
                try:
                    workflow = Workflow.get_by_id(workflow_id)
                except Workflow.DoesNotExist:
                    failures.append({'workflow_id': workflow_id, 'error': '编排不存在'})
                    continue
                owner_response = require_resource_owner(workflow)
                if owner_response:
                    return owner_response
                if workflow.is_template:
                    failures.append({'workflow_id': workflow_id, 'error': '编排模板不可批量配置'})
                    continue
                expected = expected_versions.get(str(workflow_id), expected_versions.get(workflow_id))
                if expected is None or int(expected) != workflow.config_version:
                    failures.append({
                        'workflow_id': workflow_id,
                        'error': '配置已发生变化，请刷新后重试',
                        'code': 'version_conflict',
                    })
                    continue
                workflows_by_id[workflow_id] = workflow
                pending_data[workflow_id] = workflow.data_dict

            seen_targets = set()
            for target in targets:
                if not isinstance(target, dict):
                    failures.append({'error': '目标节点配置格式无效'})
                    continue
                target_ids = target.get('workflow_ids') or []
                node_id = str(target.get('node_id') or '')
                node_type = target.get('node_type')
                changes = target.get('changes')
                if not target_ids or not node_id:
                    failures.append({'error': '目标节点缺少 workflow_ids 或 node_id'})
                    continue
                for raw_id in target_ids:
                    try:
                        workflow_id = int(raw_id)
                    except (TypeError, ValueError):
                        failures.append({'error': f'目标编排 ID 无效: {raw_id}'})
                        continue
                    if workflow_id not in selected_ids:
                        failures.append({'workflow_id': workflow_id, 'error': '目标不在已选编排中'})
                        continue
                    target_key = (workflow_id, node_id)
                    if target_key in seen_targets:
                        failures.append({'workflow_id': workflow_id, 'error': f'节点 {node_id} 被重复配置'})
                        continue
                    seen_targets.add(target_key)
                    if workflow_id not in pending_data:
                        continue
                    try:
                        patched, changed_fields, node_name = apply_batch_node_changes(
                            pending_data[workflow_id],
                            node_id=node_id,
                            node_type=node_type,
                            changes=changes,
                        )
                        pending_data[workflow_id] = patched
                        change_details.append({
                            'workflow_id': workflow_id,
                            'workflow_name': workflows_by_id[workflow_id].name,
                            'node_id': node_id,
                            'node_name': node_name,
                            'node_type': node_type,
                            'fields': changed_fields,
                            'is_active': workflows_by_id[workflow_id].is_active,
                        })
                    except BatchConfigValidationError as exc:
                        failures.append({'workflow_id': workflow_id, 'node_id': node_id, 'error': str(exc)})

            targeted_workflow_ids = {item['workflow_id'] for item in change_details}
            missing_targets = selected_ids - targeted_workflow_ids
            for workflow_id in sorted(missing_targets):
                if workflow_id in workflows_by_id:
                    failures.append({'workflow_id': workflow_id, 'error': '没有匹配到可配置节点'})

            for workflow_id in sorted(targeted_workflow_ids):
                workflow_data = pending_data[workflow_id]
                is_valid, error_message = validate_single_source_node(workflow_data)
                if not is_valid:
                    failures.append({'workflow_id': workflow_id, 'error': error_message})
                    continue
                is_valid, error_message = _validate_ocr_text_conditions(workflow_data)
                if not is_valid:
                    failures.append({'workflow_id': workflow_id, 'error': error_message})
                    continue
                is_valid, error_message = _validate_count_change_conditions(workflow_data)
                if not is_valid:
                    failures.append({'workflow_id': workflow_id, 'error': error_message})
                    continue
                is_valid, error_message, _crop_warnings = _validate_ocr_crop_nodes(workflow_data)
                if not is_valid:
                    failures.append({'workflow_id': workflow_id, 'error': error_message})
                    continue
                is_valid, error_message = validate_workflow_webhook_nodes(workflow_data)
                if not is_valid:
                    failures.append({'workflow_id': workflow_id, 'error': error_message})
                    continue
                is_valid, error_message = validate_workflow_detection_filter_nodes(workflow_data)
                if not is_valid:
                    failures.append({'workflow_id': workflow_id, 'error': error_message})
                    continue
                is_valid, error_message = validate_workflow_time_schedule_nodes(workflow_data)
                if not is_valid:
                    failures.append({'workflow_id': workflow_id, 'error': error_message})

            if failures:
                status = 409 if any(item.get('code') == 'version_conflict' for item in failures) else 400
                return jsonify({'error': '批量配置预检失败', 'failures': failures}), status

            summary = {
                'workflow_count': len(targeted_workflow_ids),
                'active_count': sum(
                    1 for workflow_id in targeted_workflow_ids
                    if workflows_by_id[workflow_id].is_active
                ),
                'node_change_count': len(change_details),
            }
            if dry_run:
                return jsonify({
                    'success': True,
                    'dry_run': True,
                    'summary': summary,
                    'changes': change_details,
                })

            with db.atomic():
                for workflow_id in sorted(targeted_workflow_ids):
                    expected = expected_versions.get(str(workflow_id), expected_versions.get(workflow_id))
                    updated_count = (
                        Workflow.update(
                            workflow_data=json.dumps(pending_data[workflow_id]),
                            config_version=Workflow.config_version + 1,
                            updated_at=datetime.now(),
                        )
                        .where(
                            (Workflow.id == workflow_id)
                            & (Workflow.config_version == int(expected))
                        )
                        .execute()
                    )
                    if updated_count != 1:
                        raise BatchConfigValidationError(
                            f'编排 {workflows_by_id[workflow_id].name} 配置已发生变化，请刷新后重试'
                        )

            return jsonify({
                'success': True,
                'dry_run': False,
                'summary': summary,
                'changes': change_details,
                'message': f"已更新 {summary['workflow_count']} 个编排",
            })
        except BatchConfigValidationError as exc:
            return jsonify({'error': str(exc)}), 409
        except (TypeError, ValueError) as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as e:
            app.logger.error(f"批量配置工作流失败: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workflows/batch-deactivate', methods=['POST'])
    @require_auth
    def batch_deactivate_workflows():
        """批量停用工作流"""
        try:
            data = request.json
            workflow_ids = data.get('workflow_ids', [])

            if not workflow_ids:
                return jsonify({'error': '请选择要停用的工作流'}), 400

            success_count = 0
            failed = []

            for workflow_id in workflow_ids:
                try:
                    workflow = Workflow.get_by_id(workflow_id)
                    owner_response = require_resource_owner(workflow)
                    if owner_response:
                        raise PermissionError('Forbidden')
                    workflow.is_active = False
                    workflow.updated_at = datetime.now()
                    workflow.save()
                    success_count += 1
                except Exception as e:
                    failed.append({
                        'workflow_id': workflow_id,
                        'error': str(e)
                    })

            return jsonify({
                'success': True,
                'deactivated': success_count,
                'failed': failed,
                'message': f'成功停用 {success_count} 个工作流'
            })

        except Exception as e:
            app.logger.error(f"批量停用工作流失败: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workflows/batch-delete', methods=['POST'])
    @require_auth
    def batch_delete_workflows():
        """批量删除工作流"""
        try:
            data = request.json
            workflow_ids = data.get('workflow_ids', [])

            if not workflow_ids:
                return jsonify({'error': '请选择要删除的工作流'}), 400

            success_count = 0
            failed = []

            for workflow_id in workflow_ids:
                try:
                    workflow = Workflow.get_by_id(workflow_id)
                    owner_response = require_resource_owner(workflow)
                    if owner_response:
                        raise PermissionError('Forbidden')
                    if workflow.is_template:
                        derived = Workflow.select().where(
                            Workflow.source_template == workflow.id
                        ).first()
                        if derived:
                            raise ValueError(
                                f"模板已有派生编排: {derived.name} (ID={derived.id})"
                            )
                    workflow.delete_instance(recursive=True)
                    success_count += 1
                except Exception as e:
                    failed.append({
                        'workflow_id': workflow_id,
                        'error': str(e)
                    })

            return jsonify({
                'success': True,
                'deleted': success_count,
                'failed': failed,
                'message': f'成功删除 {success_count} 个工作流'
            })

        except Exception as e:
            app.logger.error(f"批量删除工作流失败: {e}")
            return jsonify({'error': str(e)}), 500
