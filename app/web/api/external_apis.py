"""
外部 API 管理 API
"""
import json
from datetime import datetime

from flask import jsonify, request

from app.core.database_models import ExternalApi
from app.web.api.auth import (
    require_auth,
    apply_owner_scope,
    require_resource_owner,
    current_username,
)


def _parse_json_value(value, field_name, default_value):
    if value is None or value == '':
        return default_value

    if isinstance(value, (dict, list)):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f'{field_name} JSON 格式错误: {exc}')

    raise ValueError(f'{field_name} 字段类型错误')


def _ensure_json_object(value, field_name):
    parsed = _parse_json_value(value, field_name, {})
    if not isinstance(parsed, dict):
        raise ValueError(f'{field_name} 必须是 JSON 对象')
    return parsed


def _serialize_external_api(item: ExternalApi):
    return {
        'id': item.id,
        'name': item.name,
        'description': item.description,
        'endpoint_url': item.endpoint_url,
        'method': item.method,
        'headers': item.headers,
        'request_template': item.request_template,
        'input_schema': item.input_schema,
        'output_schema': item.output_schema,
        'output_mapping': item.output_mapping,
        'timeout_seconds': item.timeout_seconds,
        'enabled': item.enabled,
        'created_at': item.created_at.isoformat() if item.created_at else None,
        'updated_at': item.updated_at.isoformat() if item.updated_at else None,
        'created_by': item.created_by,
    }


def register_external_apis_api(app):
    @app.route('/api/external-apis', methods=['GET'])
    @require_auth
    def get_external_apis():
        try:
            items = apply_owner_scope(
                ExternalApi.select().order_by(ExternalApi.updated_at.desc(), ExternalApi.id.desc()),
                ExternalApi,
            )
            return jsonify([_serialize_external_api(item) for item in items])
        except Exception as exc:
            app.logger.error(f"获取外部 API 列表失败: {exc}")
            return jsonify({'error': str(exc)}), 500

    @app.route('/api/external-apis/<int:item_id>', methods=['GET'])
    @require_auth
    def get_external_api(item_id):
        try:
            item = ExternalApi.get_by_id(item_id)
            owner_response = require_resource_owner(item)
            if owner_response:
                return owner_response
            return jsonify(_serialize_external_api(item))
        except ExternalApi.DoesNotExist:
            return jsonify({'error': '外部 API 不存在'}), 404
        except Exception as exc:
            app.logger.error(f"获取外部 API 失败: {exc}")
            return jsonify({'error': str(exc)}), 500

    @app.route('/api/external-apis', methods=['POST'])
    @require_auth
    def create_external_api():
        try:
            data = request.json or {}
            if not data.get('name'):
                return jsonify({'error': '缺少必填字段: name'}), 400
            if not data.get('endpoint_url'):
                return jsonify({'error': '缺少必填字段: endpoint_url'}), 400

            now = datetime.now()
            item = ExternalApi.create(
                name=data['name'],
                description=data.get('description'),
                endpoint_url=data['endpoint_url'],
                method=str(data.get('method') or 'POST').upper(),
                headers_json=json.dumps(_ensure_json_object(data.get('headers'), 'headers'), ensure_ascii=False),
                request_template_json=json.dumps(
                    _ensure_json_object(data.get('request_template'), 'request_template'),
                    ensure_ascii=False,
                ),
                input_schema_json=json.dumps(
                    _parse_json_value(data.get('input_schema'), 'input_schema', []),
                    ensure_ascii=False,
                ),
                output_schema_json=json.dumps(
                    _parse_json_value(data.get('output_schema'), 'output_schema', []),
                    ensure_ascii=False,
                ),
                output_mapping_json=json.dumps(
                    _ensure_json_object(data.get('output_mapping'), 'output_mapping'),
                    ensure_ascii=False,
                ),
                timeout_seconds=int(data.get('timeout_seconds') or 30),
                enabled=bool(data.get('enabled', True)),
                created_at=now,
                updated_at=now,
                created_by=current_username('admin'),
            )
            return jsonify({'id': item.id, 'message': '外部 API 创建成功'}), 201
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:
            app.logger.error(f"创建外部 API 失败: {exc}")
            return jsonify({'error': str(exc)}), 500

    @app.route('/api/external-apis/<int:item_id>', methods=['PUT'])
    @require_auth
    def update_external_api(item_id):
        try:
            item = ExternalApi.get_by_id(item_id)
            owner_response = require_resource_owner(item)
            if owner_response:
                return owner_response

            data = request.json or {}
            if 'name' in data:
                item.name = data['name']
            if 'description' in data:
                item.description = data['description']
            if 'endpoint_url' in data:
                item.endpoint_url = data['endpoint_url']
            if 'method' in data:
                item.method = str(data.get('method') or 'POST').upper()
            if 'headers' in data:
                item.headers_json = json.dumps(_ensure_json_object(data.get('headers'), 'headers'), ensure_ascii=False)
            if 'request_template' in data:
                item.request_template_json = json.dumps(
                    _ensure_json_object(data.get('request_template'), 'request_template'),
                    ensure_ascii=False,
                )
            if 'input_schema' in data:
                item.input_schema_json = json.dumps(
                    _parse_json_value(data.get('input_schema'), 'input_schema', []),
                    ensure_ascii=False,
                )
            if 'output_schema' in data:
                item.output_schema_json = json.dumps(
                    _parse_json_value(data.get('output_schema'), 'output_schema', []),
                    ensure_ascii=False,
                )
            if 'output_mapping' in data:
                item.output_mapping_json = json.dumps(
                    _ensure_json_object(data.get('output_mapping'), 'output_mapping'),
                    ensure_ascii=False,
                )
            if 'timeout_seconds' in data:
                item.timeout_seconds = int(data.get('timeout_seconds') or 30)
            if 'enabled' in data:
                item.enabled = bool(data['enabled'])

            item.updated_at = datetime.now()
            item.save()
            return jsonify({'message': '外部 API 更新成功'})
        except ExternalApi.DoesNotExist:
            return jsonify({'error': '外部 API 不存在'}), 404
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:
            app.logger.error(f"更新外部 API 失败: {exc}")
            return jsonify({'error': str(exc)}), 500

    @app.route('/api/external-apis/<int:item_id>', methods=['DELETE'])
    @require_auth
    def delete_external_api(item_id):
        try:
            item = ExternalApi.get_by_id(item_id)
            owner_response = require_resource_owner(item)
            if owner_response:
                return owner_response
            item.delete_instance()
            return jsonify({'message': '外部 API 删除成功'})
        except ExternalApi.DoesNotExist:
            return jsonify({'error': '外部 API 不存在'}), 404
        except Exception as exc:
            app.logger.error(f"删除外部 API 失败: {exc}")
            return jsonify({'error': str(exc)}), 500
