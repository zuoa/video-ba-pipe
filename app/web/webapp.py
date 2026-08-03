import os
import tempfile
import base64
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np
from werkzeug.utils import secure_filename
import subprocess
import json
import threading
import time
import math

from flask import Flask, jsonify, request, render_template, send_file, abort, Response
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

from app.core.database_models import Algorithm, VideoSource, Alert, MLModel, SourceHealthLog, Workflow
from app.core.database_models import db
from app.config import (
    ANALYSIS_BUFFER_SECONDS,
    ANALYSIS_TARGET_FPS,
    FRAME_SAVE_PATH,
    VIDEO_FRAME_PIXEL_FORMAT,
    SNAPSHOT_SAVE_PATH,
    VIDEO_SAVE_PATH,
    MODEL_SAVE_PATH,
    VIDEO_SOURCE_PATH,
)
from app.core.rabbitmq_publisher import publish_alert_to_rabbitmq, format_alert_message
from app.core.vl_validator import get_vl_service_config, save_vl_service_config
from app.core.source_rotation import (
    get_source_rotation_config,
    save_source_rotation_config,
)
from app.core.alert_media_cleaner import directory_usage_bytes
from app.core.recording_storage_config import (
    get_recording_storage_config,
    save_recording_storage_config,
)
from app.core.storage_pressure import measure_storage_pressure
from app.core.ops_notification_config import (
    get_ops_notification_config,
    normalize_ops_notification_config,
    save_ops_notification_config,
)
from app.core.inference_resource_config import (
    detect_inference_capabilities,
    effective_inference_resource_config,
    get_inference_resource_status,
    load_inference_resource_config,
    save_inference_resource_config,
)
from app.core.dingtalk_notifier import (
    send_dingtalk_text,
    validate_dingtalk_webhook_url,
)
from app.core.vl_algorithm_config import normalize_vl_algorithm_config
from app.core.ocr_algorithm_config import normalize_ocr_algorithm_config
from app.core.ocr_runtime import get_ocr_runtime_status, is_ocr_runtime_available
from app.core.workflow_runtime import (
    extract_source_id_from_workflow_data,
    workflow_references_algorithm,
)
from app.core.algorithm import BaseAlgorithm
from app.core.window_detector import get_window_detector
from app.core.video_probe import normalize_video_codec
from app.core.system_metrics import collect_system_metrics
from app.setup_database import setup_database
from app.version import get_app_version
from app.branding import get_company_name

app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app, resources={r"/api/*": {"origins": "*"}})
app.config['JSON_AS_ASCII'] = False
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1GB for large model files


@app.before_request
def open_db_connection():
    if db.is_closed():
        db.connect(reuse_if_open=True)


@app.teardown_appcontext
def close_db_connection(_exception):
    if not db.is_closed():
        db.close()

# 初始化数据库（如果不存在则创建所有表）
try:
    setup_database()
    app.logger.info("数据库初始化完成")
except Exception as e:
    app.logger.error(f"数据库初始化失败: {e}")
    # 不阻止应用启动,让应用继续运行并在后续操作中报告错误

# ========== 注册认证API ==========
from app.web.api.auth import (
    auth_bp,
    require_auth,
    require_admin,
    apply_owner_scope,
    require_resource_owner,
    current_username,
    is_admin_user,
)
app.register_blueprint(auth_bp)


def serialize_algorithm(algorithm):
    ext_config = dict(algorithm.ext_config if hasattr(algorithm, 'ext_config') else {})
    algorithm_type = ext_config.get('algorithm_type') or 'script'
    if algorithm_type == 'vl':
        vl_config = dict(ext_config.get('vl_config') or {})
        api_key = vl_config.pop('api_key', '')
        vl_config['api_key_configured'] = bool(api_key)
        ext_config['vl_config'] = vl_config
    return {
        'id': algorithm.id,
        'name': algorithm.name,
        'description': algorithm.description,
        'script_path': algorithm.script_path,
        'script_config': algorithm.script_config,
        'enabled_hooks': algorithm.enabled_hooks,
        'created_at': algorithm.created_at.isoformat() if algorithm.created_at else None,
        'updated_at': algorithm.updated_at.isoformat() if algorithm.updated_at else None,
        'created_by': getattr(algorithm, 'created_by', 'admin'),
        'algorithm_type': algorithm_type,
        'runtime_available': algorithm_type != 'ocr' or is_ocr_runtime_available(),
        **ext_config,
    }


def _bump_active_workflows_for_algorithm(algorithm_id):
    """让 orchestrator 发现算法依赖变化并重启对应的活动 source host。"""
    affected_ids = []
    now = datetime.now()
    query = Workflow.select().where(Workflow.is_active == True)
    for workflow in query:
        if not workflow_references_algorithm(workflow.data_dict, algorithm_id):
            continue
        workflow.config_version = int(workflow.config_version or 0) + 1
        workflow.updated_at = now
        workflow.save(only=[Workflow.config_version, Workflow.updated_at])
        affected_ids.append(workflow.id)
    return affected_ids


def serialize_video_source(source):
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
        'buffer_name': source.buffer_name,
        'status': source.status,
        'decoder_pid': source.decoder_pid,
        'created_by': getattr(source, 'created_by', 'admin'),
    }

# ========== API 端点 ==========

# Plugin API
@app.route('/api/plugins/modules', methods=['GET'])
@require_auth
def list_plugin_modules():
    """
    返回可用的插件模块列表
    返回当前内置算法插件。
    """
    ocr_available, ocr_error = get_ocr_runtime_status()
    modules = ['script_algorithm', 'vl_algorithm']
    if ocr_available:
        modules.append('ocr_algorithm')
    return jsonify({
        'modules': modules,
        'capabilities': {
            'ocr': {
                'available': ocr_available,
                'error': ocr_error,
            }
        },
    })


@app.route('/api/system/info', methods=['GET'])
@require_auth
def get_system_info():
    return jsonify({
        'success': True,
        'version': get_app_version(),
        'company_name': get_company_name(),
    })


@app.route('/api/system/metrics', methods=['GET'])
@require_auth
def get_system_metrics():
    try:
        return jsonify({
            'success': True,
            'data': collect_system_metrics(),
        })
    except Exception as e:
        app.logger.error(f"采集系统状态失败: {e}")
        return jsonify({
            'success': False,
            'error': '无法读取当前机器的系统状态',
        }), 500


@app.route('/api/system/vl-config', methods=['GET'])
@require_auth
@require_admin
def get_system_vl_config():
    return jsonify({
        'success': True,
        'config': get_vl_service_config(),
    })


@app.route('/api/system/vl-config', methods=['PUT'])
@require_auth
@require_admin
def update_system_vl_config():
    data = request.json or {}
    try:
        config = save_vl_service_config(data, updated_by=current_username('admin'))
        return jsonify({
            'success': True,
            'config': config,
            'message': 'VL 配置已更新',
        })
    except Exception as e:
        app.logger.error(f"更新 VL 配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/system/source-rotation-config', methods=['GET'])
@require_auth
@require_admin
def get_system_source_rotation_config():
    config = get_source_rotation_config()
    active_source_ids = {
        extract_source_id_from_workflow_data(workflow.data_dict)
        for workflow in Workflow.select().where(Workflow.is_active == True)
    }
    active_source_ids.discard(None)
    eligible_source_count = VideoSource.select().where(
        (VideoSource.enabled == True)
        & (VideoSource.id.in_(active_source_ids))
    ).count() if active_source_ids else 0
    batch_count = (
        math.ceil(eligible_source_count / config['batch_size'])
        if eligible_source_count
        else 0
    )
    return jsonify({
        'success': True,
        'config': config,
        'eligible_source_count': eligible_source_count,
        'estimated_cycle_seconds': batch_count * config['dwell_seconds'],
    })


@app.route('/api/system/source-rotation-config', methods=['PUT'])
@require_auth
@require_admin
def update_system_source_rotation_config():
    data = request.json or {}
    try:
        config = save_source_rotation_config(
            data,
            updated_by=current_username('admin'),
        )
        return jsonify({
            'success': True,
            'config': config,
            'message': '视频轮转配置已更新',
        })
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        app.logger.error(f"更新视频轮转配置失败: {exc}")
        return jsonify({'success': False, 'error': str(exc)}), 500


def _inference_resource_response(config, source, status):
    capabilities = (
        status.get('capabilities')
        if status.get('worker_online') and isinstance(status.get('capabilities'), dict)
        else detect_inference_capabilities()
    )
    effective = effective_inference_resource_config(config, capabilities)
    config_pending = (
        status.get('applied_config_revision') != config.revision
    )
    restart_required = bool(
        not status.get('worker_online') or status.get('reconcile_error')
    )
    return {
        'success': True,
        'config': config.to_dict(),
        'configured_revision': config.revision,
        'config_source': source,
        'effective_config': effective.to_dict(),
        'capabilities': capabilities,
        'status': status,
        'config_pending': config_pending,
        'restart_required': restart_required,
        'apply_mode': 'worker_auto_reconcile',
    }


@app.route('/api/system/inference-resource-config', methods=['GET'])
@require_auth
@require_admin
def get_system_inference_resource_config():
    config, source, _database_available = load_inference_resource_config(
        initialize=False
    )
    return jsonify(_inference_resource_response(
        config,
        source,
        get_inference_resource_status(),
    ))


@app.route('/api/system/inference-resource-config', methods=['PUT'])
@require_auth
@require_admin
def update_system_inference_resource_config():
    try:
        config = save_inference_resource_config(
            request.json or {},
            updated_by=current_username('admin'),
        )
        response = _inference_resource_response(
            config,
            'database',
            get_inference_resource_status(),
        )
        response['message'] = (
            '推理资源配置已保存，worker 将在 5 秒内自动应用；'
            '共享服务参数变化会短暂重建 source host'
        )
        return jsonify(response)
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        app.logger.error(f"更新推理资源配置失败: {exc}")
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/system/recording-storage-config', methods=['GET'])
@require_auth
@require_admin
def get_system_recording_storage_config():
    config = get_recording_storage_config()
    pressure = measure_storage_pressure(config)
    return jsonify({
        'success': True,
        'config': config.to_dict(),
        'usage': {
            'video_bytes': directory_usage_bytes(VIDEO_SAVE_PATH),
            'image_bytes': directory_usage_bytes(FRAME_SAVE_PATH),
            'disk_total_bytes': pressure.total_bytes,
            'disk_used_bytes': pressure.used_bytes,
            'disk_free_bytes': pressure.free_bytes,
            'disk_used_percent': pressure.used_percent,
            'pressure_level': pressure.level.value,
        },
    })


@app.route('/api/system/recording-storage-config', methods=['PUT'])
@require_auth
@require_admin
def update_system_recording_storage_config():
    try:
        config = save_recording_storage_config(
            request.json or {},
            updated_by=current_username('admin'),
        )
        return jsonify({
            'success': True,
            'config': config.to_dict(),
            'message': '录像与存储配置已更新，将在 5 秒内热生效',
        })
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        app.logger.error(f"更新录像与存储配置失败: {exc}")
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/system/ops-notification-config', methods=['GET'])
@require_auth
@require_admin
def get_system_ops_notification_config():
    config = get_ops_notification_config()
    return jsonify({
        'success': True,
        'config': config.to_dict(include_secret=False),
    })


@app.route('/api/system/ops-notification-config', methods=['PUT'])
@require_auth
@require_admin
def update_system_ops_notification_config():
    try:
        config = save_ops_notification_config(
            request.json or {},
            updated_by=current_username('admin'),
        )
        return jsonify({
            'success': True,
            'config': config.to_dict(include_secret=False),
            'message': '钉钉运维通知配置已更新',
        })
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        app.logger.error(f"更新运维通知配置失败: {exc}")
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/system/ops-notification-config/test', methods=['POST'])
@require_auth
@require_admin
def test_system_ops_notification_config():
    try:
        existing = get_ops_notification_config()
        config = normalize_ops_notification_config(
            request.json or {},
            existing_secret=existing.secret,
        )
        validate_dingtalk_webhook_url(config.webhook_url)
        send_dingtalk_text(
            config,
            '[VideoBA运维] 测试通知\n如果你收到这条消息，说明钉钉 Webhook 配置有效。',
        )
        return jsonify({'success': True, 'message': '测试通知已发送'})
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        app.logger.error(f"发送钉钉测试通知失败: {exc}")
        return jsonify({'success': False, 'error': str(exc)}), 502

# Algorithm API
@app.route('/api/algorithms', methods=['GET'])
@require_auth
def get_algorithms():
    algorithms = apply_owner_scope(
        Algorithm.select().order_by(Algorithm.created_at.desc()),
        Algorithm,
    )
    return jsonify([serialize_algorithm(a) for a in algorithms])

@app.route('/api/algorithms/<int:id>', methods=['GET'])
@require_auth
def get_algorithm(id):
    try:
        algorithm = Algorithm.get_by_id(id)
        owner_response = require_resource_owner(algorithm)
        if owner_response:
            return owner_response
        return jsonify(serialize_algorithm(algorithm))
    except Algorithm.DoesNotExist:
        return jsonify({'error': 'Algorithm not found'}), 404

@app.route('/api/algorithms', methods=['POST'])
@require_auth
def create_algorithm():
    data = request.json or {}
    try:
        from datetime import datetime

        # 验证必填字段
        if not data.get('name'):
            return jsonify({'error': '缺少必填字段: name'}), 400
        algorithm_type = str(data.get('algorithm_type') or 'script').strip().lower()
        if algorithm_type not in ('script', 'vl', 'ocr'):
            return jsonify({'error': 'algorithm_type 仅支持 script、vl 或 ocr'}), 400
        if algorithm_type == 'ocr' and not is_ocr_runtime_available():
            _, runtime_error = get_ocr_runtime_status()
            return jsonify({'error': runtime_error or '当前环境不支持 OCR'}), 503
        if algorithm_type == 'script' and not data.get('script_path'):
            return jsonify({'error': '缺少必填字段: script_path'}), 400

        # 构建扩展配置（执行配置）
        ext_config = {}
        exec_config_fields = [
            'label_name', 'label_color',
            'interval_seconds', 'runtime_timeout', 'memory_limit_mb',
            'enable_window_check', 'window_size', 'window_mode', 'window_threshold',
            'plugin_module', 'script_type', 'entry_function', 'script_version',
            'model_json', 'model_ids'
        ]
        for field in exec_config_fields:
            if field in data:
                ext_config[field] = data[field]
        ext_config['algorithm_type'] = algorithm_type
        if algorithm_type == 'vl':
            ext_config['plugin_module'] = 'vl_algorithm'
            ext_config['vl_config'] = normalize_vl_algorithm_config(data.get('vl_config'))
            ext_config.setdefault('runtime_timeout', ext_config['vl_config']['timeout_seconds'])
        elif algorithm_type == 'ocr':
            ext_config['plugin_module'] = 'ocr_algorithm'
            ext_config['ocr_config'] = normalize_ocr_algorithm_config(data.get('ocr_config'))

        # 创建算法
        algorithm = Algorithm.create(
            name=data['name'],
            description=data.get('description'),
            script_path=data.get('script_path') or '',
            script_config=data.get('script_config', '{}'),
            ext_config_json=json.dumps(ext_config),
            enabled_hooks=data.get('enabled_hooks'),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            created_by=current_username('admin'),
        )

        return jsonify({'id': algorithm.id, 'message': 'Algorithm created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/algorithms/<int:id>', methods=['PUT'])
@require_auth
def update_algorithm(id):
    try:
        from datetime import datetime

        algorithm = Algorithm.get_by_id(id)
        owner_response = require_resource_owner(algorithm)
        if owner_response:
            return owner_response
        data = request.json or {}

        try:
            ext_config = json.loads(algorithm.ext_config_json) if algorithm.ext_config_json else {}
        except Exception:
            ext_config = {}
        current_type = ext_config.get('algorithm_type') or 'script'
        algorithm_type = str(data.get('algorithm_type') or current_type).strip().lower()
        if algorithm_type not in ('script', 'vl', 'ocr'):
            return jsonify({'error': 'algorithm_type 仅支持 script、vl 或 ocr'}), 400
        if algorithm_type == 'ocr' and not is_ocr_runtime_available():
            _, runtime_error = get_ocr_runtime_status()
            return jsonify({'error': runtime_error or '当前环境不支持 OCR'}), 503

        # 更新基本字段
        if 'name' in data:
            algorithm.name = data['name']
        if 'description' in data:
            algorithm.description = data['description']
        if 'script_path' in data:
            algorithm.script_path = data['script_path']
        if 'script_config' in data:
            algorithm.script_config = data['script_config']
        if 'enabled_hooks' in data:
            algorithm.enabled_hooks = data['enabled_hooks']

        # 更新扩展配置（执行配置）
        exec_config_fields = [
            'label_name', 'label_color',
            'interval_seconds', 'runtime_timeout', 'memory_limit_mb',
            'enable_window_check', 'window_size', 'window_mode', 'window_threshold',
            'plugin_module', 'script_type', 'entry_function', 'script_version',
            'model_json', 'model_ids'
        ]

        # 更新执行配置字段
        for field in exec_config_fields:
            if field in data:
                ext_config[field] = data[field]

        ext_config['algorithm_type'] = algorithm_type
        if algorithm_type == 'script':
            if not str(algorithm.script_path or '').strip():
                return jsonify({'error': '脚本算法缺少必填字段: script_path'}), 400
            ext_config['plugin_module'] = data.get('plugin_module') or 'script_algorithm'
            ext_config.pop('vl_config', None)
            ext_config.pop('ocr_config', None)
        elif algorithm_type == 'vl':
            current_vl_config = ext_config.get('vl_config') if current_type == 'vl' else None
            ext_config['plugin_module'] = 'vl_algorithm'
            ext_config['vl_config'] = normalize_vl_algorithm_config(
                data.get('vl_config'),
                current=current_vl_config,
            )
            algorithm.script_path = ''
            ext_config.pop('ocr_config', None)
        else:
            current_ocr_config = ext_config.get('ocr_config') if current_type == 'ocr' else None
            ext_config['plugin_module'] = 'ocr_algorithm'
            ext_config['ocr_config'] = normalize_ocr_algorithm_config(
                data.get('ocr_config'),
                current=current_ocr_config,
            )
            ext_config.pop('vl_config', None)
            algorithm.script_path = ''

        algorithm.ext_config_json = json.dumps(ext_config)
        algorithm.updated_at = datetime.now()
        with db.atomic():
            algorithm.save()
            reloaded_workflow_ids = _bump_active_workflows_for_algorithm(algorithm.id)

        return jsonify({
            'message': 'Algorithm updated',
            'reloaded_workflow_ids': reloaded_workflow_ids,
        })
    except Algorithm.DoesNotExist:
        return jsonify({'error': 'Algorithm not found'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        app.logger.error(f"更新算法失败 (ID={id}): {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/algorithms/<int:id>', methods=['DELETE'])
@require_auth
def delete_algorithm(id):
    try:
        algorithm = Algorithm.get_by_id(id)
        owner_response = require_resource_owner(algorithm)
        if owner_response:
            return owner_response
        algorithm.delete_instance(recursive=True)
        return jsonify({'message': 'Algorithm deleted'})
    except Algorithm.DoesNotExist:
        return jsonify({'error': 'Algorithm not found'}), 404

@app.route('/api/algorithms/test', methods=['POST'])
@require_auth
def test_algorithm():
    """算法测试API端点"""
    try:
        # 检查是否有上传的图片
        if 'image' not in request.files:
            return jsonify({'success': False, 'error': '没有上传图片'}), 400
        
        file = request.files['image']
        if file.filename == '':
            return jsonify({'success': False, 'error': '没有选择文件'}), 400
        
        # 检查算法ID
        algorithm_id = request.form.get('algorithm_id')
        if not algorithm_id:
            return jsonify({'success': False, 'error': '缺少算法ID'}), 400
        
        try:
            algorithm_id = int(algorithm_id)
        except ValueError:
            return jsonify({'success': False, 'error': '无效的算法ID'}), 400
        
        # 获取算法配置
        try:
            algorithm = Algorithm.get_by_id(algorithm_id)
        except Algorithm.DoesNotExist:
            return jsonify({'success': False, 'error': '算法不存在'}), 404

        owner_response = require_resource_owner(algorithm)
        if owner_response:
            return owner_response
        
        # 验证文件类型
        if not file.content_type.startswith('image/'):
            return jsonify({'success': False, 'error': '只支持图片文件'}), 400
        
        # 保存上传的图片到临时文件
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            file.save(temp_file.name)
            temp_image_path = temp_file.name
        
        try:
            # 读取图片
            image = cv2.imread(temp_image_path)
            if image is None:
                return jsonify({'success': False, 'error': '无法读取图片文件'}), 400

            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

            # 调整图片大小，宽高至少640
            h, w = image.shape[:2]
            if w < 640 or h < 640:
                scale = max(640 / w, 640 / h)
                new_w = int(w * scale)
                new_h = int(h * scale)
                image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
                app.logger.info(f"已调整图片大小为: {new_w}x{new_h}")


            # 准备算法配置
            script_config = algorithm.config_dict  # 从 script_config 字段解析
            ext_config = algorithm.ext_config
            algorithm_type = ext_config.get('algorithm_type') or 'script'

            full_config = {
                "id": algorithm.id,
                "name": algorithm.name,
                "label_name": getattr(algorithm, 'label_name', None) or script_config.get('label_name', 'Object'),
                "label_color": getattr(algorithm, 'label_color', None) or script_config.get('label_color', '#FF0000'),
                "interval_seconds": getattr(algorithm, 'interval_seconds', None) or script_config.get('interval_seconds', 1),
                "source_id": 0,  # 测试模式，使用虚拟视频源ID
                "pixel_format": "rgb24",  # 上传图片直调入口保持 RGB 输入

                # 脚本执行相关配置
                "script_path": algorithm.script_path,
                "entry_function": 'process',
                "runtime_timeout": getattr(algorithm, 'runtime_timeout', None) or script_config.get('runtime_timeout', 30),
                "memory_limit_mb": getattr(algorithm, 'memory_limit_mb', None) or script_config.get('memory_limit_mb', 512),
                "algorithm_type": algorithm_type,
            }

            # 合并脚本配置
            full_config.update(script_config)
            full_config.update(ext_config)

            if algorithm_type == 'vl':
                from app.plugins.vl_algorithm import VLAlgorithm
                algo_instance = VLAlgorithm(full_config)
            elif algorithm_type == 'ocr':
                from app.plugins.ocr_algorithm import OCRAlgorithm
                algo_instance = OCRAlgorithm(full_config)
            else:
                from app.plugins.script_algorithm import ScriptAlgorithm
                algo_instance = ScriptAlgorithm(full_config)
            
            # 创建算法实例并处理图片
            results = algo_instance.process(image)

            # 处理结果
            detections = BaseAlgorithm.normalize_detection_results(results.get('detections', []))
            metadata = results.get('metadata', {})
            algorithm_error = metadata.get('error') if isinstance(metadata, dict) else None
            
            # 生成可视化结果（无论是否有检测结果都生成）
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_result:
                result_image_path = temp_result.name
            
            # 生成可视化结果
            algo_instance.visualize(
                image,
                detections, 
                save_path=result_image_path,
                label_color=full_config.get('label_color', '#FF0000')
            )
            
            # 准备返回结果
            response_data = {
                'success': not bool(algorithm_error),
                'detections': detections,
                'detection_count': len(detections),
                'metadata': metadata  # 包含调试信息
            }
            if algorithm_error:
                response_data['error'] = algorithm_error
            
            # 转换结果图片为base64
            if result_image_path and os.path.exists(result_image_path):
                with open(result_image_path, 'rb') as f:
                    result_image_data = base64.b64encode(f.read()).decode('utf-8')
                    response_data['result_image'] = f'data:image/jpeg;base64,{result_image_data}'
                
                # 清理临时结果文件
                os.unlink(result_image_path)
            
            return jsonify(response_data)
            
        finally:
            # 清理临时上传文件
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
                
    except Exception as e:
        app.logger.error(f"算法测试失败: {str(e)}")
        return jsonify({'success': False, 'error': f'测试失败: {str(e)}'}), 500

# VideoSource API
@app.route('/api/video-sources', methods=['GET'])
@require_auth
def get_video_sources():
    sources = apply_owner_scope(
        VideoSource.select().order_by(VideoSource.id.desc()),
        VideoSource,
    )
    return jsonify([serialize_video_source(s) for s in sources])

@app.route('/api/video-sources/<int:id>', methods=['GET'])
@require_auth
def get_video_source(id):
    try:
        source = VideoSource.get_by_id(id)
        owner_response = require_resource_owner(source)
        if owner_response:
            return owner_response
        return jsonify(serialize_video_source(source))
    except VideoSource.DoesNotExist:
        return jsonify({'error': '视频源不存在'}), 404

@app.route('/api/video-sources', methods=['POST'])
@require_auth
def create_video_source():
    data = request.json
    try:
        source = VideoSource.create(
            name=data['name'],
            enabled=data.get('enabled', True),
            source_code=data['source_code'],
            source_url=data['source_url'],
            source_decode_width=data.get('source_decode_width', 960),
            source_decode_height=data.get('source_decode_height', 540),
            source_fps=data.get('source_fps', 10),
            source_codec=normalize_video_codec(
                data.get('source_codec'),
                allow_unknown=True,
            ),
            status='STOPPED',
            decoder_pid=None,
            created_by=current_username('admin'),
        )
        return jsonify({'id': source.id, 'message': '视频源创建成功'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/video-sources/<int:id>', methods=['PUT'])
@require_auth
def update_video_source(id):
    try:
        source = VideoSource.get_by_id(id)
        owner_response = require_resource_owner(source)
        if owner_response:
            return owner_response
        data = request.json
        source.name = data.get('name', source.name)
        source.enabled = data.get('enabled', source.enabled)
        source.source_code = data.get('source_code', source.source_code)
        previous_source_url = source.source_url
        source.source_url = data.get('source_url', source.source_url)
        source.source_decode_width = data.get('source_decode_width', source.source_decode_width)
        source.source_decode_height = data.get('source_decode_height', source.source_decode_height)
        source.source_fps = data.get('source_fps', source.source_fps)
        if 'source_codec' in data:
            source.source_codec = normalize_video_codec(
                data.get('source_codec'),
                allow_unknown=True,
            )
        elif source.source_url != previous_source_url:
            source.source_codec = 'unknown'
        source.save()
        
        return jsonify({'message': '视频源更新成功'})
    except VideoSource.DoesNotExist:
        return jsonify({'error': '视频源不存在'}), 404

@app.route('/api/video-sources/<int:id>', methods=['DELETE'])
@require_auth
def delete_video_source(id):
    try:
        source = VideoSource.get_by_id(id)
        owner_response = require_resource_owner(source)
        if owner_response:
            return owner_response
        source.delete_instance(recursive=True)
        return jsonify({'message': '视频源删除成功'})
    except VideoSource.DoesNotExist:
        return jsonify({'error': '视频源不存在'}), 404
    except Exception as e:
        app.logger.error(f"删除视频源失败 (ID={id}): {e}")
        return jsonify({'error': str(e)}), 500

# ========== 健康监控API ==========

@app.route('/api/video-sources/<int:source_id>/health', methods=['GET'])
@require_auth
def get_source_health(source_id):
    """获取视频源的健康状态"""
    try:
        source = VideoSource.get_by_id(source_id)
        owner_response = require_resource_owner(source)
        if owner_response:
            return owner_response

        # 尝试获取 ring buffer 的健康状态
        from app.core.ringbuffer import VideoRingBuffer

        buffer_name = source.analysis_buffer_name
        analysis_fps = max(1, min(int(source.source_fps), int(ANALYSIS_TARGET_FPS)))
        if not buffer_name:
            return jsonify({
                'source_id': source_id,
                'status': source.status,
                'error': 'No buffer configured'
            }), 404

        try:
            # 连接到现有的 buffer
            buffer = VideoRingBuffer(
                name=buffer_name,
                create=False,
                width=source.source_decode_width,
                height=source.source_decode_height,
                pixel_format=VIDEO_FRAME_PIXEL_FORMAT,
                fps=analysis_fps,
                duration_seconds=ANALYSIS_BUFFER_SECONDS
            )
            health_status = buffer.get_health_status()
            buffer.close()

            return jsonify({
                'source_id': source_id,
                'name': source.name,
                'status': source.status,
                'enabled': source.enabled,
                'last_write_time': health_status['last_write_time'],
                'time_since_last_frame': health_status['time_since_last_frame'],
                'consecutive_errors': health_status['consecutive_errors'],
                'frame_count': health_status['frame_count'],
                'is_healthy': health_status['is_healthy']
            })
        except FileNotFoundError:
            # buffer 不存在
            return jsonify({
                'source_id': source_id,
                'name': source.name,
                'status': source.status,
                'enabled': source.enabled,
                'error': 'Buffer not found',
                'is_healthy': False
            })
        except Exception as e:
            app.logger.error(f"获取 buffer 健康状态失败: {e}")
            return jsonify({
                'source_id': source_id,
                'name': source.name,
                'status': source.status,
                'enabled': source.enabled,
                'error': str(e),
                'is_healthy': False
            })

    except VideoSource.DoesNotExist:
        return jsonify({'error': '视频源不存在'}), 404

@app.route('/api/video-sources/<int:source_id>/health-logs', methods=['GET'])
@require_auth
def get_source_health_logs(source_id):
    """获取视频源的健康事件日志"""
    try:
        source = VideoSource.get_by_id(source_id)
        owner_response = require_resource_owner(source)
        if owner_response:
            return owner_response

        # 获取查询参数
        event_type = request.args.get('event_type')
        severity = request.args.get('severity')
        limit = int(request.args.get('limit', 100))
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 30))

        # 构建查询
        query = SourceHealthLog.select().where(SourceHealthLog.source == source_id)

        if event_type:
            query = query.where(SourceHealthLog.event_type == event_type)
        if severity:
            query = query.where(SourceHealthLog.severity == severity)

        # 获取总数
        total = query.count()

        # 计算分页
        total_pages = (total + per_page - 1) // per_page
        offset = (page - 1) * per_page

        # 获取分页数据
        logs = query.order_by(SourceHealthLog.created_at.desc()).limit(per_page).offset(offset)

        return jsonify({
            'data': [{
                'id': log.id,
                'event_type': log.event_type,
                'details': log.details_dict,
                'severity': log.severity,
                'created_at': log.created_at.isoformat()
            } for log in logs],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'total_pages': total_pages
            }
        })

    except VideoSource.DoesNotExist:
        return jsonify({'error': '视频源不存在'}), 404
    except Exception as e:
        app.logger.error(f"获取健康日志失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/health/event-types', methods=['GET'])
@require_auth
def get_health_event_types():
    """获取所有健康事件类型"""
    event_types = [
        {'value': 'no_frame_warning', 'label': '无帧警告'},
        {'value': 'no_frame_critical', 'label': '无帧严重'},
        {'value': 'process_exit', 'label': '进程退出'},
        {'value': 'rotation_drain_timeout', 'label': '轮转排空超时'},
        {'value': 'low_fps', 'label': '低帧率'},
        {'value': 'high_error_rate', 'label': '高错误率'},
        {'value': 'restart_backoff', 'label': '重启退避'},
        {'value': 'resource_wait', 'label': '硬解资源等待'},
        {'value': 'sw_fallback', 'label': '软解兜底'},
        {'value': 'hw_upgrade', 'label': '硬解升级'}
    ]
    return jsonify(event_types)

@app.route('/api/health/stats', methods=['GET'])
@require_auth
def get_health_stats():
    """获取整体健康统计信息"""
    try:
        import time
        from datetime import datetime, timedelta

        # 统计各类状态的视频源数量
        source_query = apply_owner_scope(VideoSource.select(), VideoSource)
        total_sources = source_query.count()
        running_count = source_query.where(VideoSource.status == 'RUNNING').count()
        starting_count = source_query.where(VideoSource.status == 'STARTING').count()
        draining_count = source_query.where(VideoSource.status == 'DRAINING').count()
        stopped_count = source_query.where(VideoSource.status == 'STOPPED').count()
        error_count = source_query.where(VideoSource.status == 'ERROR').count()

        # 统计最近24小时的健康事件
        start_time = datetime.now() - timedelta(hours=24)
        accessible_source_ids = apply_owner_scope(VideoSource.select(VideoSource.id), VideoSource)
        recent_logs = SourceHealthLog.select().where(
            (SourceHealthLog.created_at >= start_time) &
            (SourceHealthLog.source.in_(accessible_source_ids))
        )

        # 按严重级别统计
        severity_stats = {}
        for log in recent_logs:
            severity = log.severity
            severity_stats[severity] = severity_stats.get(severity, 0) + 1

        # 按事件类型统计
        event_type_stats = {}
        for log in recent_logs:
            event_type = log.event_type
            event_type_stats[event_type] = event_type_stats.get(event_type, 0) + 1

        return jsonify({
            'sources': {
                'total': total_sources,
                'running': running_count,
                'starting': starting_count,
                'draining': draining_count,
                'stopped': stopped_count,
                'error': error_count,
                'enabled': source_query.where(VideoSource.enabled == True).count()
            },
            'last_24h_events': {
                'by_severity': severity_stats,
                'by_event_type': event_type_stats,
                'total': recent_logs.count()
            }
        })

    except Exception as e:
        app.logger.error(f"获取健康统计失败: {e}")
        return jsonify({'error': str(e)}), 500

# Video file management API
ALLOWED_VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.m4v', '.webm', '.wmv'}

@app.route('/api/video-sources/upload', methods=['POST'])
@require_auth
def upload_video_file():
    """上传视频文件到服务器"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '没有上传文件'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': '文件名为空'}), 400

        # 验证文件类型
        filename = secure_filename(file.filename)
        if not filename:
            return jsonify({'success': False, 'error': '无效的文件名'}), 400

        file_ext = os.path.splitext(filename)[1].lower()
        if file_ext not in ALLOWED_VIDEO_EXTENSIONS:
            return jsonify({'success': False, 'error': f'不支持的文件格式，允许的格式: {", ".join(ALLOWED_VIDEO_EXTENSIONS)}'}), 400

        # 避免文件名重复
        base, ext = os.path.splitext(filename)
        counter = 1
        final_filename = filename
        while os.path.exists(os.path.join(VIDEO_SOURCE_PATH, final_filename)):
            final_filename = f"{base}_{counter}{ext}"
            counter += 1

        # 保存文件
        filepath = os.path.join(VIDEO_SOURCE_PATH, final_filename)
        file.save(filepath)

        # 获取文件信息
        file_size = os.path.getsize(filepath)

        # 可选：使用 ffprobe 获取视频信息
        video_info = {}
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                '-select_streams', 'v:0',
                filepath
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
            if result.returncode == 0:
                probe_data = json.loads(result.stdout)
                if probe_data.get('streams'):
                    stream = probe_data['streams'][0]
                    video_info = {
                        'width': stream.get('width', 0),
                        'height': stream.get('height', 0),
                        'fps': stream.get('r_frame_rate', '0/1'),
                        'duration': stream.get('duration', 0),
                        'codec': stream.get('codec_name', 'unknown')
                    }
        except Exception as e:
            app.logger.warning(f"获取视频信息失败: {e}")

        return jsonify({
            'success': True,
            'data': {
                'filename': final_filename,
                'path': filepath,
                'size': file_size,
                'info': video_info
            }
        })

    except Exception as e:
        app.logger.error(f"视频上传失败: {str(e)}")
        return jsonify({'success': False, 'error': f'上传失败: {str(e)}'}), 500

@app.route('/api/video-sources/files', methods=['GET'])
@require_auth
def list_video_files():
    """列出可用的视频文件"""
    try:
        files = []
        if not os.path.exists(VIDEO_SOURCE_PATH):
            return jsonify({'success': True, 'data': []})

        for filename in os.listdir(VIDEO_SOURCE_PATH):
            filepath = os.path.join(VIDEO_SOURCE_PATH, filename)
            if os.path.isfile(filepath):
                file_ext = os.path.splitext(filename)[1].lower()
                if file_ext in ALLOWED_VIDEO_EXTENSIONS:
                    file_size = os.path.getsize(filepath)
                    file_stat = os.stat(filepath)

                    files.append({
                        'filename': filename,
                        'path': filepath,
                        'size': file_size,
                        'created_at': file_stat.st_ctime,
                        'modified_at': file_stat.st_mtime
                    })

        # 按修改时间倒序排列
        files.sort(key=lambda x: x['modified_at'], reverse=True)

        return jsonify({'success': True, 'data': files})

    except Exception as e:
        app.logger.error(f"列出视频文件失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/video-sources/files/<path:filename>', methods=['DELETE'])
@require_auth
def delete_video_file(filename):
    """删除视频文件"""
    try:
        # 安全处理文件名
        filename = secure_filename(filename)
        if not filename:
            return jsonify({'success': False, 'error': '无效的文件名'}), 400

        filepath = os.path.join(VIDEO_SOURCE_PATH, filename)

        # 验证文件在允许的目录内
        filepath = os.path.abspath(filepath)
        source_path = os.path.abspath(VIDEO_SOURCE_PATH)
        if not filepath.startswith(source_path + os.sep) and filepath != source_path:
            return jsonify({'success': False, 'error': '路径遍历检测'}), 403

        if not os.path.exists(filepath):
            return jsonify({'success': False, 'error': '文件不存在'}), 404

        if not os.path.isfile(filepath):
            return jsonify({'success': False, 'error': '不是文件'}), 400

        # 检查是否有视频源正在使用此文件
        # 这里简单检查 source_url 是否包含此文件名
        sources_using = VideoSource.select().where(VideoSource.source_url.contains(filename))
        if sources_using.count() > 0:
            source_names = [s.name for s in sources_using]
            return jsonify({
                'success': False,
                'error': f'文件正在被以下视频源使用: {", ".join(source_names)}，请先删除这些视频源'
            }), 400

        # 删除文件
        os.remove(filepath)

        return jsonify({'success': True, 'message': f'文件 {filename} 已删除'})

    except Exception as e:
        app.logger.error(f"删除视频文件失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Alert API
@app.route('/api/alerts', methods=['GET'])
@require_auth
def get_alerts():
    source_id = request.args.get('source_id') or request.args.get('task_id')  # 兼容旧参数
    workflow_id = request.args.get('workflow_id')  # 流程编排筛选
    alert_type = request.args.get('alert_type')
    start_time = request.args.get('start_time')  # 开始时间筛选
    end_time = request.args.get('end_time')  # 结束时间筛选
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 30))

    # 构建查询
    query = apply_owner_scope(Alert.select(), Alert)
    if source_id:
        query = query.where(Alert.video_source == source_id)
    if workflow_id:
        query = query.where(Alert.workflow == workflow_id)
    if alert_type:
        query = query.where(Alert.alert_type == alert_type)
    if start_time:
        try:
            from datetime import datetime
            start_dt = datetime.fromisoformat(start_time)
            query = query.where(Alert.alert_time >= start_dt)
        except ValueError:
            app.logger.warning(f"无效的 start_time 格式: {start_time}")
    if end_time:
        try:
            from datetime import datetime
            end_dt = datetime.fromisoformat(end_time)
            query = query.where(Alert.alert_time <= end_dt)
        except ValueError:
            app.logger.warning(f"无效的 end_time 格式: {end_time}")
    
    # 获取总数
    total = query.count()
    
    # 计算分页
    total_pages = (total + per_page - 1) // per_page
    offset = (page - 1) * per_page
    
    # 获取分页数据
    alerts = query.order_by(Alert.alert_time.desc()).limit(per_page).offset(offset)
    
    return jsonify({
        'data': [{
            'id': a.id,
            'source_id': a.video_source.id,
            'task_id': a.video_source.id,  # 兼容旧字段
            'workflow_id': a.workflow.id if a.workflow else None,
            'workflow_name': a.workflow.name if a.workflow else None,
            'alert_time': a.alert_time.isoformat(),
            'alert_type': a.alert_type,
            'alert_message': a.alert_message,
            'alert_image': a.alert_image,
            'alert_image_ori': a.alert_image_ori,
            'alert_video': a.alert_video,
            'detection_count': a.detection_count,
            'window_stats': a.window_stats,
            'detection_images': a.detection_images,
            'created_by': a.created_by,
        } for a in alerts],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total,
            'total_pages': total_pages
        }
    })

@app.route('/api/alerts', methods=['POST'])
@require_auth
def create_alert():
    data = request.json
    try:
        source_id = data.get('source_id') or data.get('task_id')  # 兼容旧参数
        workflow_id = data.get('workflow_id')  # 可选的 workflow_id
        source = VideoSource.get_by_id(source_id)
        owner_response = require_resource_owner(source)
        if owner_response:
            return owner_response

        # 准备 Alert 创建参数
        alert_params = {
            'video_source': source,
            'alert_time': datetime.fromisoformat(data.get('alert_time', datetime.now().isoformat())),
            'alert_type': data['alert_type'],
            'alert_message': data['alert_message'],
            'alert_image': data.get('alert_image'),
            'alert_video': data.get('alert_video'),
            'created_by': getattr(source, 'created_by', current_username('admin')),
        }

        # 如果提供了 workflow_id，添加到参数中
        if workflow_id is not None:
            from app.core.database_models import Workflow
            try:
                workflow = Workflow.get_by_id(workflow_id)
                workflow_owner_response = require_resource_owner(workflow)
                if workflow_owner_response:
                    return workflow_owner_response
                alert_params['workflow'] = workflow
            except Workflow.DoesNotExist:
                return jsonify({'error': f'Workflow {workflow_id} does not exist'}), 400

        alert = Alert.create(**alert_params)
        
        # 发布预警消息到RabbitMQ
        try:
            alert_message = format_alert_message(alert)
            if publish_alert_to_rabbitmq(alert_message):
                print(f"预警消息已成功发布到RabbitMQ: {alert.id}")
            else:
                print(f"预警消息发布到RabbitMQ失败: {alert.id}")
        except Exception as e:
            print(f"发布预警消息到RabbitMQ时发生错误: {e}")
        
        return jsonify({'id': alert.id, 'message': 'Alert created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/alert-types', methods=['GET'])
@require_auth
def get_alert_types():
    """获取所有可用的告警类型"""
    alert_types = apply_owner_scope(
        Alert.select(Alert.alert_type).distinct().order_by(Alert.alert_type),
        Alert,
    )
    return jsonify([at.alert_type for at in alert_types])

@app.route('/api/alerts/today-count', methods=['GET'])
@require_auth
def get_today_alerts_count():
    """获取今日告警数量"""
    from datetime import datetime, date

    # 获取今天的开始和结束时间
    today = date.today()
    start_of_day = datetime.combine(today, datetime.min.time())
    end_of_day = datetime.combine(today, datetime.max.time())

    # 查询今日告警数量
    count = apply_owner_scope(Alert.select(), Alert).where(
        (Alert.alert_time >= start_of_day) &
        (Alert.alert_time <= end_of_day)
    ).count()

    return jsonify({'count': count})

@app.route('/api/alerts/trend', methods=['GET'])
@require_auth
def get_alert_trend():
    """获取告警趋势数据"""
    from datetime import datetime, timedelta

    # 获取查询参数（天数）
    days = request.args.get('days', 7, type=int)
    days = min(days, 30)  # 最多30天

    # 计算日期范围
    end_date = datetime.now().replace(hour=23, minute=59, second=59)
    start_date = (end_date - timedelta(days=days-1)).replace(hour=0, minute=0, second=0)

    # 查询所有告警
    alerts = apply_owner_scope(Alert.select(), Alert).where(
        (Alert.alert_time >= start_date) &
        (Alert.alert_time <= end_date)
    )

    # 按日期统计
    result_dict = {}
    for alert in alerts:
        alert_date = alert.alert_time.date()
        result_dict[alert_date] = result_dict.get(alert_date, 0) + 1

    # 填充缺失的日期
    trend = []
    current_date = start_date.date()
    while current_date <= end_date.date():
        trend.append({
            'date': current_date.strftime('%Y-%m-%d'),
            'count': result_dict.get(current_date, 0)
        })
        current_date += timedelta(days=1)

    return jsonify({'trend': trend})

@app.route('/api/alerts/channel-stats', methods=['GET'])
@require_auth
def get_alert_channel_stats():
    """按通道统计告警数量，支持 日/周/月/年 时间维度切换"""
    from datetime import timedelta

    period = request.args.get('period', 'day')
    now = datetime.now()

    if period == 'week':
        # 本周（周一起）
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'month':
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == 'year':
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        # 默认今日
        period = 'day'
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 统计时间范围内各通道的告警数量
    counts = {}
    alerts = apply_owner_scope(
        Alert.select(Alert.video_source).where(Alert.alert_time >= start),
        Alert,
    )
    for alert in alerts:
        counts[alert.video_source_id] = counts.get(alert.video_source_id, 0) + 1

    # 返回所有视频源（无告警的通道 count 为 0），按告警数降序
    channels = []
    for source in apply_owner_scope(VideoSource.select(), VideoSource):
        channels.append({
            'id': source.id,
            'name': source.name,
            'count': counts.get(source.id, 0),
        })
    channels.sort(key=lambda c: (-c['count'], c['id']))

    return jsonify({
        'period': period,
        'start': start.strftime('%Y-%m-%d %H:%M:%S'),
        'channels': channels,
    })

# ========== 时间窗口检测API ==========

@app.route('/api/window/stats/<int:source_id>/<algorithm_id>', methods=['GET'])
@require_auth
def get_window_stats(source_id, algorithm_id):
    """获取指定视频源和算法的窗口统计信息"""
    try:
        source = VideoSource.get_by_id(source_id)
        owner_response = require_resource_owner(source)
        if owner_response:
            return owner_response
        window_detector = get_window_detector()
        stats = window_detector.get_stats(source_id, algorithm_id)
        
        # 判断当前状态
        if stats['config']['enable']:
            mode = stats['config']['mode']
            threshold = stats['config']['threshold']
            
            if mode == 'count':
                passed = stats['detection_count'] >= threshold
            elif mode == 'ratio':
                passed = stats['detection_ratio'] >= threshold
            elif mode == 'consecutive':
                passed = stats['max_consecutive'] >= threshold
            else:
                passed = False
            
            stats['status'] = 'above_threshold' if passed else 'below_threshold'
        else:
            stats['status'] = 'disabled'
        
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/window/stats/<int:source_id>', methods=['GET'])
@require_auth
def get_source_window_stats(source_id):
    """获取指定视频源的所有算法窗口统计信息"""
    try:
        source = VideoSource.get_by_id(source_id)
        owner_response = require_resource_owner(source)
        if owner_response:
            return owner_response
        # 注意：此API需要工作流系统来管理视频源和算法的关联
        # 目前返回空列表
        return jsonify([])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/window/memory', methods=['GET'])
@require_auth
def get_window_memory_usage():
    """获取窗口检测器的内存使用情况"""
    try:
        window_detector = get_window_detector()
        memory_info = window_detector.get_memory_usage()
        return jsonify(memory_info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== 图片服务接口 ==========

@app.route('/api/image/<path:file_path>', methods=['GET'])
def get_image(file_path):
    """
    安全的图片返回接口
    支持的图片路径：
    - frames/ 下的帧图片
    - snapshots/ 下的快照图片
    """
    try:
        # 定义允许访问的基础路径
        allowed_bases = {
            'frames': FRAME_SAVE_PATH,
            'snapshots': SNAPSHOT_SAVE_PATH
        }
        
        # 解析路径的第一部分作为基础目录类型
        path_parts = file_path.split('/', 1)
        if len(path_parts) < 2:
            abort(400, description="Invalid path format")
            
        base_type, relative_path = path_parts
        
        # 检查基础路径是否被允许
        if base_type not in allowed_bases:
            abort(403, description="Access to this directory is not allowed")
            
        base_path = allowed_bases[base_type]
        
        # 构建完整的文件路径
        full_path = os.path.join(base_path, relative_path)
        
        # 规范化路径，防止路径遍历攻击
        full_path = os.path.abspath(full_path)
        base_path = os.path.abspath(base_path)
        
        # 确保请求的文件在允许的基础路径内
        if not full_path.startswith(base_path + os.sep) and full_path != base_path:
            abort(403, description="Path traversal detected")
            
        # 检查文件是否存在
        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            abort(404, description="Image not found")
            
        # 验证文件扩展名（只允许常见的图片格式）
        allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff'}
        file_ext = Path(full_path).suffix.lower()
        if file_ext not in allowed_extensions:
            abort(400, description="File type not supported")
            
        # 返回图片文件
        return send_file(full_path, as_attachment=False)
        
    except HTTPException:
        raise
    except Exception as e:
        app.logger.error(f"Error serving image {file_path}: {str(e)}")
        abort(500, description="Internal server error")

@app.route('/api/video/<path:file_path>', methods=['GET'])
def get_video(file_path):
    """
    安全的视频返回接口
    支持的视频路径：
    - videos/<相对路径>
    - <相对路径>（兼容历史数据）
    支持 Range 请求以便视频播放器可以 seek
    """
    try:
        normalized_path = (file_path or '').replace('\\', '/').strip()
        normalized_path = normalized_path.lstrip('/')

        if normalized_path.startswith('api/video/'):
            normalized_path = normalized_path[len('api/video/'):]

        while normalized_path.startswith('videos/videos/'):
            normalized_path = normalized_path[len('videos/'):]

        videos_marker = '/videos/'
        videos_index = normalized_path.rfind(videos_marker)
        if videos_index != -1:
            normalized_path = normalized_path[videos_index + len(videos_marker):]

        if normalized_path.startswith('videos/'):
            relative_path = normalized_path[len('videos/'):]
        else:
            relative_path = normalized_path

        if not relative_path:
            abort(400, description="Invalid path format")

        base_path = os.path.abspath(VIDEO_SAVE_PATH)

        # 构建完整的文件路径
        full_path = os.path.join(base_path, relative_path)
        
        # 规范化路径，防止路径遍历攻击
        full_path = os.path.abspath(full_path)
        
        # 确保请求的文件在允许的基础路径内
        if not full_path.startswith(base_path + os.sep) and full_path != base_path:
            abort(403, description="Path traversal detected")
            
        # 检查文件是否存在
        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            abort(404, description="Video not found")
            
        # 验证文件扩展名（只允许常见的视频格式）
        allowed_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}
        file_ext = Path(full_path).suffix.lower()
        if file_ext not in allowed_extensions:
            abort(400, description="File type not supported")
        
        # 获取文件大小
        file_size = os.path.getsize(full_path)
        
        # 设置 MIME 类型
        mime_type = 'video/mp4' if file_ext == '.mp4' else f'video/{file_ext[1:]}'
        
        # 处理 Range 请求
        range_header = request.headers.get('Range', None)
        
        if not range_header:
            # 没有 Range 请求，返回整个文件
            with open(full_path, 'rb') as f:
                data = f.read()
            
            response = Response(data, 200, mimetype=mime_type)
            response.headers.add('Content-Length', str(file_size))
            response.headers.add('Accept-Ranges', 'bytes')
            return response
        
        # 解析 Range 请求头
        # Range: bytes=start-end
        byte_range = range_header.replace('bytes=', '').strip()
        byte_range_parts = byte_range.split('-')
        
        start = int(byte_range_parts[0]) if byte_range_parts[0] else 0
        end = int(byte_range_parts[1]) if byte_range_parts[1] else file_size - 1
        
        # 确保 end 不超过文件大小
        if end >= file_size:
            end = file_size - 1
        
        # 计算内容长度
        content_length = end - start + 1
        
        # 读取指定范围的数据
        with open(full_path, 'rb') as f:
            f.seek(start)
            data = f.read(content_length)
        
        # 创建 206 Partial Content 响应
        response = Response(data, 206, mimetype=mime_type)
        response.headers.add('Content-Range', f'bytes {start}-{end}/{file_size}')
        response.headers.add('Accept-Ranges', 'bytes')
        response.headers.add('Content-Length', str(content_length))
        response.headers.add('Cache-Control', 'no-cache')
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        app.logger.error(f"Error serving video {file_path}: {str(e)}")
        abort(500, description=f"Error serving video {file_path}: {str(e)}")

# ========== 流检测API ==========

@app.route('/api/stream/detect', methods=['POST'])
@require_auth
def detect_stream_info():
    """检测视频流的分辨率和帧率"""
    try:
        data = request.json
        url = data.get('url')
        
        if not url:
            return jsonify({'success': False, 'error': '缺少URL参数'}), 400
        
        # 使用ffprobe检测流信息
        try:
            # 根据URL类型构建不同的ffprobe命令
            if url.startswith('rtsp://'):
                # RTSP流需要特殊参数
                cmd = [
                    'ffprobe',
                    '-print_format', 'json',
                    '-show_streams',
                    '-select_streams', 'v:0',
                    '-rtsp_transport', 'tcp',  # 使用TCP传输
                    '-timeout', '5000000',   # 5秒超时（微秒）
                    '-analyzeduration', '2000000',  # 分析时长2秒
                    '-probesize', '2000000',  # 探测大小2MB
                    url
                ]
            elif url.startswith('rtmp://'):
                # RTMP流
                cmd = [
                    'ffprobe',
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_streams',
                    '-select_streams', 'v:0',
                    '-analyzeduration', '1000000',
                    '-probesize', '1000000',
                    url
                ]
            else:
                # HTTP/HTTPS/文件等其他类型
                cmd = [
                    'ffprobe',
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_streams',
                    '-select_streams', 'v:0',
                    '-analyzeduration', '2000000',  # 分析时长2秒
                    '-probesize', '2000000',  # 探测大小2MB
                    url
                ]
            
            # 设置超时时间（15秒，给RTSP流更多时间）
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                timeout=15,
                check=False  # 不自动抛出异常，我们手动处理
            )
            
            # 检查stderr是否有错误信息
            if result.returncode != 0:
                error_output = result.stderr.strip() if result.stderr else '未知错误'
                app.logger.error(f"ffprobe失败 (返回码 {result.returncode}): {error_output}")
                
                # 解析常见错误并提供更友好的提示
                if '503 ServerUnavailable' in error_output:
                    return jsonify({'success': False, 'error': 'RTSP服务器不可用（503），请检查摄像头是否在线或是否被其他客户端占用'}), 400
                elif '401 Unauthorized' in error_output or 'Authentication' in error_output:
                    return jsonify({'success': False, 'error': '认证失败，请检查用户名和密码是否正确'}), 400
                elif 'Connection refused' in error_output:
                    return jsonify({'success': False, 'error': '连接被拒绝，请检查IP地址和端口是否正确'}), 400
                elif 'Connection timed out' in error_output or 'timeout' in error_output.lower():
                    return jsonify({'success': False, 'error': '连接超时，请检查网络连接或摄像头是否可达'}), 400
                elif 'No route to host' in error_output:
                    return jsonify({'success': False, 'error': '无法到达主机，请检查网络配置'}), 400
                else:
                    return jsonify({'success': False, 'error': f'ffprobe执行失败: {error_output}'}), 400
            
            # 解析JSON输出
            if not result.stdout.strip():
                return jsonify({'success': False, 'error': 'ffprobe未返回任何数据，请检查URL是否正确'}), 400
                
            probe_data = json.loads(result.stdout)
            
            # 调试信息
            app.logger.info(f"ffprobe成功，流数量: {len(probe_data.get('streams', []))}")
            if result.stderr:
                app.logger.debug(f"ffprobe stderr: {result.stderr}")
            
            if not probe_data.get('streams'):
                # 如果没有找到视频流，尝试获取所有流信息
                if probe_data.get('format'):
                    format_info = probe_data['format']
                    return jsonify({
                        'success': False, 
                        'error': f'未找到视频流。格式信息: {format_info.get("format_name", "unknown")}, 时长: {format_info.get("duration", "unknown")}秒'
                    }), 400
                else:
                    return jsonify({'success': False, 'error': '无法解析流信息，请检查URL是否可访问'}), 400
            
            stream = probe_data['streams'][0]
            
            # 提取分辨率和帧率信息
            width = stream.get('width', 0)
            height = stream.get('height', 0)
            
            # 尝试获取帧率
            fps = 0
            if 'r_frame_rate' in stream:
                # 解析分数形式的帧率，如 "30/1"
                fps_str = stream['r_frame_rate']
                if '/' in fps_str:
                    num, den = fps_str.split('/')
                    fps = float(num) / float(den) if float(den) != 0 else 0
                else:
                    fps = float(fps_str)
            
            # 如果无法获取帧率，尝试使用avg_frame_rate
            if fps == 0 and 'avg_frame_rate' in stream:
                fps_str = stream['avg_frame_rate']
                if '/' in fps_str:
                    num, den = fps_str.split('/')
                    fps = float(num) / float(den) if float(den) != 0 else 0
                else:
                    fps = float(fps_str)
            
            # 如果仍然无法获取帧率，尝试使用fps
            if fps == 0 and 'fps' in stream:
                fps = float(stream['fps'])
            
            if width == 0 or height == 0:
                return jsonify({'success': False, 'error': '无法获取视频分辨率'}), 400
            
            return jsonify({
                'success': True,
                'width': int(width),
                'height': int(height),
                'fps': round(fps, 2) if fps > 0 else 0,
                'codec': stream.get('codec_name', 'unknown'),
                'duration': stream.get('duration', 0)
            })
            
        except subprocess.TimeoutExpired:
            return jsonify({'success': False, 'error': '检测超时（15秒），请检查URL是否可访问或网络连接是否正常'}), 400
        except FileNotFoundError:
            return jsonify({'success': False, 'error': 'ffprobe未安装，请安装FFmpeg: brew install ffmpeg'}), 400
        except json.JSONDecodeError as e:
            return jsonify({'success': False, 'error': f'解析ffprobe输出失败: {str(e)}'}), 400
        except Exception as e:
            return jsonify({'success': False, 'error': f'检测过程中发生错误: {str(e)}'}), 400
            
    except Exception as e:
        app.logger.error(f"流检测失败: {str(e)}")
        return jsonify({'success': False, 'error': f'检测失败: {str(e)}'}), 500

# ========== 管理后台页面路由 ==========

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/admin/algorithms')
def admin_algorithms():
    return render_template('algorithms.html')

@app.route('/admin/tasks')
@app.route('/admin/video-sources')
def admin_video_sources():
    return render_template('video_sources.html')

@app.route('/admin/alerts')
def admin_alerts():
    return render_template('alerts.html')

@app.route('/admin/gpu-calculator')
def admin_gpu_calculator():
    return render_template('gpu_benchmark.html')

@app.route('/admin/roi-config')
def admin_roi_config():
    return render_template('roi_config.html')

# 添加简短的路由别名
@app.route('/tasks')
@app.route('/video-sources')
def video_sources():
    return render_template('video_sources.html')

@app.route('/algorithms')
def algorithms():
    return render_template('algorithms.html')

@app.route('/algorithm-wizard')
def algorithm_wizard():
    """算法配置向导"""
    return render_template('algorithm_wizard.html')

@app.route('/test-templates')
def test_templates():
    """测试检测器模板API"""
    return render_template('test_templates.html')

@app.route('/alerts')
def alerts():
    return render_template('alerts.html')

@app.route('/roi-config')
def roi_config():
    return render_template('roi_config.html')

@app.route('/alert-wall')
def alert_wall():
    """告警大屏页面"""
    return render_template('alert_wall.html')

# ========== 脚本管理页面路由 ==========

@app.route('/scripts')
def scripts():
    """脚本管理页面"""
    return render_template('scripts.html')

@app.route('/scripts/templates')
def script_templates():
    """脚本模板页面"""
    return render_template('script_templates.html')

@app.route('/admin/scripts')
def admin_scripts():
    """脚本管理页面（管理后台路径）"""
    return render_template('scripts.html')

# ========== 模型管理页面路由 ==========

@app.route('/models')
def models():
    """模型管理页面"""
    return render_template('models.html')

@app.route('/admin/models')
def admin_models():
    """模型管理页面（管理后台路径）"""
    return render_template('models.html')

@app.route('/workflows')
def workflows():
    """工作流管理页面"""
    return render_template('workflows.html')

@app.route('/admin/workflows')
def admin_workflows():
    """工作流管理页面（管理后台路径）"""
    return render_template('workflows.html')

@app.route('/api/upload/model', methods=['POST'])
@require_auth
@require_admin
def upload_model_file():
    try:
        if 'model_file' not in request.files:
            return jsonify({'success': False, 'error': '缺少文件字段 model_file'}), 400
        file = request.files['model_file']
        if file.filename == '':
            return jsonify({'success': False, 'error': '未选择文件'}), 400

        # 安全的文件名
        filename = secure_filename(file.filename)
        if not filename:
            return jsonify({'success': False, 'error': '无效的文件名'}), 400

        # 允许的扩展名
        allowed_exts = {'.pt', '.onnx', '.engine', '.bin', '.tflite', '.xml', '.param', '.json', '.rknn'}
        ext = os.path.splitext(filename)[1].lower()
        if ext not in allowed_exts:
            return jsonify({'success': False, 'error': '不支持的文件类型'}), 400

        # 创建存储路径（按日期分目录）
        date_dir = datetime.now().strftime('%Y%m%d')
        save_dir = os.path.join(MODEL_SAVE_PATH, date_dir)
        os.makedirs(save_dir, exist_ok=True)

        # 防止重名覆盖
        base, extname = os.path.splitext(filename)
        counter = 0
        final_name = filename
        while os.path.exists(os.path.join(save_dir, final_name)):
            counter += 1
            final_name = f"{base}_{counter}{extname}"

        save_path = os.path.join(save_dir, final_name)
        file.save(save_path)

        return jsonify({'success': True, 'saved_path': save_path})
    except Exception as e:
        app.logger.error(f"模型上传失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ========== 注册脚本管理API ==========
try:
    from app.web.api.scripts import register_scripts_api
    register_scripts_api(app)
    app.logger.info("脚本管理API已注册")
except ImportError as e:
    app.logger.warning(f"脚本管理API注册失败: {e}")

# ========== 注册模型管理API ==========
try:
    from app.web.api.models import register_models_api
    register_models_api(app)
    app.logger.info("模型管理API已注册")
except ImportError as e:
    app.logger.warning(f"模型管理API注册失败: {e}")

# ========== 注册检测器模板API ==========
try:
    app.logger.info("检测器模板API已注册")
except ImportError as e:
    app.logger.warning(f"检测器模板API注册失败: {e}")

# ========== 注册外部 API 管理API ==========
try:
    from app.web.api.external_apis import register_external_apis_api
    register_external_apis_api(app)
    app.logger.info("外部 API 管理API已注册")
except ImportError as e:
    app.logger.warning(f"外部 API 管理API注册失败: {e}")

# ========== 注册工作流管理API ==========
try:
    from app.web.api.workflows import register_workflows_api
    register_workflows_api(app)
    app.logger.info("工作流管理API已注册")

except ImportError as e:
    app.logger.warning(f"检测器模板API注册失败: {e}")

# ========== 注册工作流测试API ==========
try:
    from app.web.api.workflow_test import register_workflow_test_api
    register_workflow_test_api(app)
    app.logger.info("工作流测试API已注册")
except ImportError as e:
    app.logger.warning(f"工作流测试API注册失败: {e}")

# ========== 注册视频源导入API ==========
try:
    from app.web.api.source_import import register_source_import_api
    register_source_import_api(app)
    app.logger.info("视频源导入API已注册")
except ImportError as e:
    app.logger.warning(f"视频源导入API注册失败: {e}")

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)
