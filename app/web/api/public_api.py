"""System API-key management and versioned public integration APIs."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from copy import deepcopy
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import jsonify, request, send_file
from peewee import IntegrityError

from app.core.database_models import ApiKey, VideoSource, Workflow, db
from app.core.mediamtx_client import mediamtx_client
from app.core.time_schedule import validate_workflow_time_schedule_nodes
from app.core.video_probe import normalize_video_codec
from app.core.webhook_workflow_config import mask_workflow_webhook_secrets
from app.core.workflow_runtime import (
    normalize_source_node_fields,
    validate_template_source_node,
)
from app.web.api.auth import current_username, require_admin, require_auth


API_KEY_PREFIX = 'vbp_'
API_INTEGRATION_OWNER = 'api-integration'
_SAFE_SOURCE_CODE_PATTERN = re.compile(r'^[A-Za-z0-9._~-]+$')
_INTEGER_STRING_PATTERN = re.compile(r'^[+-]?\d+$')

# docs/ 目录位于仓库根目录(app/web/api/public_api.py 上三级)
_DOCS_DIR = Path(__file__).resolve().parents[3] / 'docs'


def _success(data=None, status=200):
    return jsonify({'success': True, 'data': data}), status


def _error(code, message, status):
    return jsonify({'success': False, 'code': code, 'message': message}), status


def _hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode('utf-8')).hexdigest()


def _generate_api_key() -> str:
    return f'{API_KEY_PREFIX}{secrets.token_urlsafe(32)}'


def require_api_key(func):
    """Authenticate a public API call using the X-API-Key header."""

    @wraps(func)
    def decorated(*args, **kwargs):
        raw_key = (request.headers.get('X-API-Key') or '').strip()
        if not raw_key:
            return _error('api_key_required', '缺少 X-API-Key 请求头', 401)

        key_hash = _hash_api_key(raw_key)
        api_key = (
            ApiKey.select()
            .where((ApiKey.key_hash == key_hash) & (ApiKey.enabled == True))
            .first()
        )
        if api_key is None:
            return _error('invalid_api_key', 'API Key 无效或已禁用', 401)

        now = datetime.now()
        ApiKey.update(last_used_at=now).where(ApiKey.id == api_key.id).execute()
        request.api_key = {
            'id': api_key.id,
            'name': api_key.name,
        }
        return func(*args, **kwargs)

    return decorated


def _serialize_api_key(item: ApiKey):
    return {
        'id': item.id,
        'name': item.name,
        'key_prefix': item.key_prefix,
        'enabled': item.enabled,
        'created_at': item.created_at.isoformat() if item.created_at else None,
        'last_used_at': item.last_used_at.isoformat() if item.last_used_at else None,
        'created_by': item.created_by,
    }


def _serialize_source(source: VideoSource):
    return {
        'id': source.id,
        'name': source.name,
        'enabled': source.enabled,
        'source_code': source.source_code,
        'source_url': source.source_url,
        'source_decode_width': source.source_decode_width,
        'source_decode_height': source.source_decode_height,
        'source_fps': source.source_fps,
        'source_codec': getattr(source, 'source_codec', 'unknown'),
        'status': source.status,
    }


def _serialize_workflow(workflow: Workflow):
    source = workflow.video_source if workflow.video_source_id is not None else None
    template = workflow.source_template if workflow.source_template_id is not None else None
    return {
        'id': workflow.id,
        'name': workflow.name,
        'description': workflow.description,
        'workflow_data': mask_workflow_webhook_secrets(workflow.data_dict),
        'is_active': workflow.is_active,
        'is_template': workflow.is_template,
        'source_template_id': workflow.source_template_id,
        'source_template_name': template.name if template is not None else None,
        'video_source_id': workflow.video_source_id,
        'source_code': source.source_code if source is not None else None,
        'config_version': workflow.config_version,
        'created_at': workflow.created_at.isoformat() if workflow.created_at else None,
        'updated_at': workflow.updated_at.isoformat() if workflow.updated_at else None,
    }


def _get_source_by_code(source_code: str):
    return VideoSource.get_or_none(VideoSource.source_code == source_code)


def _sync_mediamtx_path(app, source: VideoSource):
    try:
        mediamtx_client.register_path(source.source_code, source.source_url)
    except Exception as exc:
        app.logger.warning(
            'MediaMTX 路径同步失败（忽略）source=%s: %s',
            source.source_code,
            exc,
        )


def _parse_positive_integer(value, field_name):
    if isinstance(value, bool):
        raise ValueError(f'{field_name} 必须是正整数')
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and _INTEGER_STRING_PATTERN.fullmatch(value.strip()):
        parsed = int(value.strip())
    else:
        raise ValueError(f'{field_name} 必须是正整数')
    if parsed <= 0:
        raise ValueError(f'{field_name} 必须是正整数')
    return parsed


def _validate_positive_integer(data, field_name, default=None):
    if field_name not in data:
        return default
    return _parse_positive_integer(data[field_name], field_name)


def _parse_template_id(value):
    try:
        return _parse_positive_integer(value, 'template_workflow_id')
    except ValueError as exc:
        raise ValueError('template_workflow_id 必须是正整数') from exc


def _validate_source_code(source_code: str):
    if len(source_code) > 255:
        raise ValueError('source_code 不能超过 255 个字符')
    if not _SAFE_SOURCE_CODE_PATTERN.fullmatch(source_code):
        raise ValueError(
            'source_code 只能包含字母、数字、点、下划线、波浪号和连字符'
        )


def _generate_workflow_name(source: VideoSource, template: Workflow) -> str:
    name = f'{source.name}-{template.name}'
    return name if len(name) <= 50 else f'{name[:47]}...'


def register_public_api(app):
    """Register admin API-key management and /openapi/v1 endpoints."""

    @app.route('/api/system/api-keys', methods=['GET'])
    @require_auth
    @require_admin
    def list_api_keys():
        keys = ApiKey.select().order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        return jsonify({
            'success': True,
            'keys': [_serialize_api_key(item) for item in keys],
        })

    @app.route('/api/system/api-keys', methods=['POST'])
    @require_auth
    @require_admin
    def create_api_key():
        data = request.get_json(silent=True) or {}
        name = str(data.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Key 名称不能为空'}), 400
        if len(name) > 100:
            return jsonify({'success': False, 'error': 'Key 名称不能超过 100 个字符'}), 400

        raw_key = _generate_api_key()
        try:
            item = ApiKey.create(
                name=name,
                key_prefix=raw_key[:12],
                key_hash=_hash_api_key(raw_key),
                enabled=True,
                created_at=datetime.now(),
                created_by=current_username('admin'),
            )
        except IntegrityError:
            return jsonify({'success': False, 'error': 'Key 名称已存在'}), 409

        payload = _serialize_api_key(item)
        payload['key'] = raw_key
        return jsonify({
            'success': True,
            'key': payload,
            'message': 'API Key 已生成，请立即复制保存，之后无法再次查看完整 Key',
        }), 201

    @app.route('/api/system/api-keys/<int:key_id>', methods=['PATCH'])
    @require_auth
    @require_admin
    def update_api_key(key_id):
        item = ApiKey.get_or_none(ApiKey.id == key_id)
        if item is None:
            return jsonify({'success': False, 'error': 'API Key 不存在'}), 404
        data = request.get_json(silent=True) or {}
        if 'enabled' not in data or not isinstance(data['enabled'], bool):
            return jsonify({'success': False, 'error': 'enabled 必须是布尔值'}), 400
        item.enabled = data['enabled']
        item.save(only=[ApiKey.enabled])
        return jsonify({'success': True, 'key': _serialize_api_key(item)})

    def _serve_doc_file(filename, download_name, mimetype):
        file_path = (_DOCS_DIR / filename).resolve()
        if not file_path.is_file() or file_path.parent != _DOCS_DIR:
            return jsonify({'success': False, 'error': '文档文件不存在'}), 404
        return send_file(
            file_path,
            as_attachment=True,
            download_name=download_name,
            mimetype=mimetype,
        )

    @app.route('/api/system/openapi/spec', methods=['GET'])
    @require_auth
    def download_openapi_spec():
        return _serve_doc_file(
            'openapi.yaml',
            'video-ba-pipe-openapi.yaml',
            'application/yaml',
        )

    @app.route('/api/system/openapi/guide', methods=['GET'])
    @require_auth
    def download_openapi_guide():
        return _serve_doc_file(
            'openapi_usage.md',
            'video-ba-pipe-api-usage.md',
            'text/markdown',
        )

    @app.route('/openapi/v1/video-sources', methods=['POST'])
    @require_api_key
    def public_create_video_source():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error('invalid_request', '请求体必须是 JSON 对象', 400)

        source_code = str(data.get('source_code') or '').strip()
        name = str(data.get('name') or '').strip()
        source_url = str(data.get('source_url') or '').strip()
        if not source_code or not name or not source_url:
            return _error(
                'missing_required_field',
                'source_code、name 和 source_url 为必填字段',
                400,
            )
        try:
            _validate_source_code(source_code)
        except ValueError as exc:
            return _error('invalid_field', str(exc), 400)
        if 'enabled' in data and not isinstance(data['enabled'], bool):
            return _error('invalid_field', 'enabled 必须是布尔值', 400)

        try:
            source = VideoSource.create(
                name=name,
                enabled=data.get('enabled', True),
                source_code=source_code,
                source_url=source_url,
                source_decode_width=_validate_positive_integer(
                    data, 'source_decode_width', 960
                ),
                source_decode_height=_validate_positive_integer(
                    data, 'source_decode_height', 540
                ),
                source_fps=_validate_positive_integer(data, 'source_fps', 10),
                source_codec=normalize_video_codec(
                    data.get('source_codec'), allow_unknown=True
                ),
                status='STOPPED',
                decoder_pid=None,
                created_by=API_INTEGRATION_OWNER,
            )
        except IntegrityError:
            return _error('source_code_exists', '视频源编码已存在', 409)
        except ValueError as exc:
            return _error('invalid_field', str(exc), 400)

        _sync_mediamtx_path(app, source)
        return _success(_serialize_source(source), 201)

    @app.route('/openapi/v1/video-sources/<string:source_code>', methods=['PATCH'])
    @require_api_key
    def public_update_video_source(source_code):
        source = _get_source_by_code(source_code)
        if source is None:
            return _error('video_source_not_found', '视频源不存在', 404)
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not data:
            return _error('invalid_request', '请求体必须是非空 JSON 对象', 400)

        forbidden_fields = {'source_code', 'source_url', 'status', 'decoder_pid'}
        forbidden = sorted(forbidden_fields.intersection(data))
        if forbidden:
            return _error(
                'field_not_allowed',
                f"不允许通过此接口修改字段: {', '.join(forbidden)}",
                400,
            )
        allowed_fields = {
            'name',
            'enabled',
            'source_decode_width',
            'source_decode_height',
            'source_fps',
            'source_codec',
        }
        unknown = sorted(set(data) - allowed_fields)
        if unknown:
            return _error(
                'unknown_field',
                f"未知字段: {', '.join(unknown)}",
                400,
            )

        try:
            if 'name' in data:
                name = str(data['name'] or '').strip()
                if not name:
                    raise ValueError('name 不能为空')
                source.name = name
            if 'enabled' in data:
                if not isinstance(data['enabled'], bool):
                    raise ValueError('enabled 必须是布尔值')
                source.enabled = data['enabled']
            for field_name in (
                'source_decode_width',
                'source_decode_height',
                'source_fps',
            ):
                if field_name in data:
                    setattr(
                        source,
                        field_name,
                        _validate_positive_integer(data, field_name),
                    )
            if 'source_codec' in data:
                source.source_codec = normalize_video_codec(
                    data.get('source_codec'), allow_unknown=True
                )
            source.save()
        except ValueError as exc:
            return _error('invalid_field', str(exc), 400)

        return _success(_serialize_source(source))

    @app.route(
        '/openapi/v1/video-sources/<string:source_code>/source-url',
        methods=['PUT'],
    )
    @require_api_key
    def public_update_video_source_url(source_code):
        source = _get_source_by_code(source_code)
        if source is None:
            return _error('video_source_not_found', '视频源不存在', 404)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error('invalid_request', '请求体必须是 JSON 对象', 400)
        source_url = str(data.get('source_url') or '').strip()
        if not source_url:
            return _error('invalid_field', 'source_url 不能为空', 400)
        unknown = sorted(set(data) - {'source_url'})
        if unknown:
            return _error(
                'unknown_field',
                f"未知字段: {', '.join(unknown)}",
                400,
            )

        changed = source.source_url != source_url
        running = source.status in {'STARTING', 'RUNNING', 'DRAINING'}
        if changed:
            with db.atomic():
                source.source_url = source_url
                source.source_codec = 'unknown'
                source.save(only=[VideoSource.source_url, VideoSource.source_codec])
            _sync_mediamtx_path(app, source)

        payload = {
            'source_code': source.source_code,
            'source_url': source.source_url,
            'changed': changed,
            'reload_scheduled': bool(changed and running),
        }
        return _success(payload, 202 if changed and running else 200)

    @app.route('/openapi/v1/workflow-templates', methods=['GET'])
    @require_api_key
    def public_list_workflow_templates():
        query = (
            Workflow.select()
            .where(Workflow.is_template == True)
            .order_by(Workflow.updated_at.desc(), Workflow.id.desc())
        )
        items = [_serialize_workflow(item) for item in query]
        return _success({'items': items, 'total': len(items)})

    @app.route('/openapi/v1/workflow-activations', methods=['POST'])
    @require_api_key
    def public_activate_workflow():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error('invalid_request', '请求体必须是 JSON 对象', 400)
        source_code = str(data.get('source_code') or '').strip()
        try:
            template_id = _parse_template_id(data.get('template_workflow_id'))
        except ValueError:
            return _error(
                'invalid_field', 'template_workflow_id 必须是正整数', 400
            )
        if not source_code:
            return _error('missing_required_field', 'source_code 为必填字段', 400)

        source = _get_source_by_code(source_code)
        if source is None:
            return _error('video_source_not_found', '视频源不存在', 404)
        template = Workflow.get_or_none(Workflow.id == template_id)
        if template is None or not template.is_template:
            return _error('workflow_template_not_found', '编排模板不存在', 404)

        template_data = template.data_dict
        valid, message = validate_template_source_node(template_data)
        if not valid:
            return _error('invalid_workflow_template', message, 400)
        valid, message = validate_workflow_time_schedule_nodes(template_data)
        if not valid:
            return _error('invalid_workflow_template', message, 400)

        existing = (
            Workflow.select()
            .where(
                (Workflow.source_template == template.id)
                & (Workflow.video_source == source.id)
            )
            .first()
        )
        created = False
        workflow = existing
        if workflow is None:
            workflow_data = normalize_source_node_fields(
                deepcopy(template_data), source
            )
            now = datetime.now()
            try:
                with db.atomic():
                    workflow = Workflow.create(
                        name=_generate_workflow_name(source, template),
                        description=f"从模板 '{template.name}' 复制",
                        workflow_data=json.dumps(workflow_data),
                        is_active=True,
                        is_template=False,
                        source_template=template,
                        video_source=source,
                        created_at=now,
                        updated_at=now,
                        created_by=API_INTEGRATION_OWNER,
                    )
                created = True
            except IntegrityError:
                workflow = (
                    Workflow.select()
                    .where(
                        (Workflow.source_template == template.id)
                        & (Workflow.video_source == source.id)
                    )
                    .first()
                )
                if workflow is None:
                    raise

        if not workflow.is_active:
            Workflow.update(
                is_active=True,
                updated_at=datetime.now(),
            ).where(Workflow.id == workflow.id).execute()
            workflow.is_active = True

        payload = {
            'workflow_id': workflow.id,
            'template_workflow_id': template.id,
            'source_code': source.source_code,
            'created': created,
            'is_active': True,
        }
        return _success(payload, 201 if created else 200)

    @app.route(
        '/openapi/v1/workflows/<int:workflow_id>/deactivate', methods=['POST']
    )
    @require_api_key
    def public_deactivate_workflow(workflow_id):
        workflow = Workflow.get_or_none(Workflow.id == workflow_id)
        if workflow is None:
            return _error('workflow_not_found', '编排不存在', 404)
        if workflow.is_template:
            return _error(
                'workflow_template_not_deactivatable', '编排模板不能去激活', 400
            )
        if workflow.is_active:
            Workflow.update(
                is_active=False,
                updated_at=datetime.now(),
            ).where(Workflow.id == workflow.id).execute()
            workflow.is_active = False
        return _success({'workflow_id': workflow.id, 'is_active': False})

    @app.route('/openapi/v1/workflows', methods=['GET'])
    @require_api_key
    def public_list_workflows():
        source_code = (request.args.get('source_code') or '').strip()
        query = Workflow.select().where(Workflow.is_template == False)
        if source_code:
            source = _get_source_by_code(source_code)
            if source is None:
                return _error('video_source_not_found', '视频源不存在', 404)
            query = query.where(Workflow.video_source == source.id)
        query = query.order_by(Workflow.updated_at.desc(), Workflow.id.desc())
        items = [_serialize_workflow(item) for item in query]
        return _success({'items': items, 'total': len(items)})
