"""
模型管理API
"""
import ast
import os
import sys
import json
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from urllib.parse import urlparse, unquote, quote

import requests
from flask import Blueprint, request, jsonify, send_file, current_app, after_this_request
from peewee import IntegrityError
from werkzeug.utils import secure_filename

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.database_models import MLModel, Algorithm, Workflow, db
from app.core.script_loader import get_script_loader
from app.core.workflow_runtime import (
    build_template_workflow_data,
    validate_template_source_node,
)
from app.config import (
    HF_DOWNLOAD_TIMEOUT_SECONDS,
    HF_MIRROR_ENDPOINT,
    HF_USE_MIRROR,
    MODEL_SAVE_PATH,
)
from app import logger
from app.web.api.auth import require_auth, require_admin, current_username

# 创建蓝图
models_bp = Blueprint('models', __name__, url_prefix='/api/models')


@models_bp.before_request
def enforce_model_permissions():
    auth_response = require_auth(lambda: None)()
    if auth_response is not None:
        return auth_response
    admin_response = require_admin(lambda: None)()
    if admin_response is not None:
        return admin_response

# 模型存储根目录（由配置项控制）
MODELS_ROOT = MODEL_SAVE_PATH

# 允许的模型文件扩展名
ALLOWED_EXTENSIONS = {
    '.pt',
    '.pth',
    '.safetensors',
    '.onnx',
    '.engine',
    '.bin',
    '.tflite',
    '.xml',
    '.param',
    '.json',
    '.rknn',
}
OCR_ARCHIVE_EXTENSIONS = ('.zip', '.tar', '.tar.gz', '.tgz')
OCR_MODEL_ROLES = {'detection', 'recognition'}
OCR_MAX_ARCHIVE_ENTRIES = 10_000
OCR_MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024


EXTENSION_MODEL_HINTS = {
    '.pt': ('YOLO', 'ultralytics'),
    '.pth': ('PyTorch', 'pytorch'),
    '.safetensors': ('PyTorch', 'pytorch'),
    '.onnx': ('ONNX', 'onnx'),
    '.engine': ('TensorRT', 'tensorrt'),
    '.tflite': ('TFLite', 'tflite'),
    '.rknn': ('RKNN', 'rknn'),
}

HF_OFFICIAL_ENDPOINT = 'https://huggingface.co'
QUICK_SETUP_SCRIPT_PATH = 'templates/adaptive_yolo_detector.py'
QUICK_SETUP_MARKER_KEY = 'quick_create'
QUICK_SETUP_LABEL_COLOR = '#52c41a'


def _parse_boolean(value, default=False):
    """解析 JSON/环境变量中的布尔开关。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ('true', '1', 'yes', 'on'):
            return True
        if normalized in ('false', '0', 'no', 'off', ''):
            return False
    raise ValueError('use_hf_mirror 必须是布尔值')


def _normalize_hf_endpoint(endpoint):
    """校验并标准化 Hugging Face 下载端点。"""
    normalized = str(endpoint or '').strip().rstrip('/')
    parsed = urlparse(normalized)
    if (
        parsed.scheme not in ('http', 'https')
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError('Hugging Face 下载端点必须是合法的 http/https 地址')
    return normalized


def _build_huggingface_download_url(
    repo_id,
    repo_filename,
    revision='main',
    use_mirror=False,
    mirror_endpoint=HF_MIRROR_ENDPOINT,
):
    """构建 Hugging Face 单文件下载地址，并拒绝歧义路径。"""
    repo_id = str(repo_id or '').strip()
    repo_filename = str(repo_filename or '').strip()
    revision = str(revision or 'main').strip()

    repo_parts = repo_id.split('/')
    if (
        len(repo_parts) not in (1, 2)
        or any(part in ('', '.', '..') for part in repo_parts)
        or '\\' in repo_id
        or any(ord(char) < 32 for char in repo_id)
    ):
        raise ValueError('仓库 ID 格式错误，应为 repo 或 owner/repo')

    filename_parts = repo_filename.split('/')
    if (
        not repo_filename
        or repo_filename.startswith('/')
        or '\\' in repo_filename
        or any(part in ('', '.', '..') for part in filename_parts)
    ):
        raise ValueError('模型文件路径格式错误')

    if not revision or any(ord(char) < 32 for char in revision):
        raise ValueError('Revision 格式错误')

    endpoint = _normalize_hf_endpoint(
        mirror_endpoint if use_mirror else HF_OFFICIAL_ENDPOINT
    )
    encoded_repo_id = '/'.join(quote(part, safe='') for part in repo_parts)
    encoded_revision = quote(revision, safe='')
    encoded_filename = '/'.join(quote(part, safe='') for part in filename_parts)
    return (
        f'{endpoint}/{encoded_repo_id}/resolve/'
        f'{encoded_revision}/{encoded_filename}?download=true'
    )


def _is_ocr_archive(filename):
    return str(filename or '').lower().endswith(OCR_ARCHIVE_EXTENSIONS)


def allowed_file(filename, model_type=None):
    """检查文件扩展名是否允许"""
    if str(model_type or '').strip().upper() == 'OCR' and _is_ocr_archive(filename):
        return True
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS


def _remove_model_artifact(path):
    if not path or not os.path.exists(path):
        return
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def _safe_archive_target(root, member_name):
    normalized = str(member_name or '').replace('\\', '/')
    if not normalized or normalized.startswith('/') or re.match(r'^[A-Za-z]:', normalized):
        raise ValueError(f'OCR 模型包包含非法路径: {member_name}')
    target = os.path.realpath(os.path.join(root, normalized))
    root_real = os.path.realpath(root)
    if target != root_real and not target.startswith(root_real + os.sep):
        raise ValueError(f'OCR 模型包包含目录穿越路径: {member_name}')
    return target


def _extract_ocr_archive(archive_path):
    """安全解压 OCR 模型包并返回可直接交给 PaddleOCR 的模型目录。"""
    extract_root = tempfile.mkdtemp(prefix='.ocr-extract-', dir=os.path.dirname(archive_path))
    final_dir = None
    entry_count = 0
    extracted_bytes = 0
    try:
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                for member in members:
                    entry_count += 1
                    extracted_bytes += int(member.file_size or 0)
                    _safe_archive_target(extract_root, member.filename)
                    file_mode = (member.external_attr >> 16) & 0o170000
                    if file_mode == stat.S_IFLNK:
                        raise ValueError('OCR 模型包不允许包含符号链接')
                if entry_count > OCR_MAX_ARCHIVE_ENTRIES or extracted_bytes > OCR_MAX_EXTRACTED_BYTES:
                    raise ValueError('OCR 模型包解压后的条目数或总大小超出限制')
                archive.extractall(extract_root)
        elif tarfile.is_tarfile(archive_path):
            with tarfile.open(archive_path) as archive:
                members = archive.getmembers()
                for member in members:
                    entry_count += 1
                    extracted_bytes += int(member.size or 0)
                    _safe_archive_target(extract_root, member.name)
                    if member.issym() or member.islnk() or member.isdev():
                        raise ValueError('OCR 模型包不允许包含链接或设备文件')
                if entry_count > OCR_MAX_ARCHIVE_ENTRIES or extracted_bytes > OCR_MAX_EXTRACTED_BYTES:
                    raise ValueError('OCR 模型包解压后的条目数或总大小超出限制')
                archive.extractall(extract_root)
        else:
            raise ValueError('OCR 模型必须是 ZIP、TAR、TAR.GZ 或 TGZ 压缩包')

        config_markers = {'inference.json', 'inference.yml', 'model.yml', 'inference.pdmodel'}
        model_directories = []
        for directory, subdirectories, filenames in os.walk(extract_root):
            subdirectories[:] = [
                name for name in subdirectories
                if name != '__MACOSX' and not name.startswith('.')
            ]
            direct_files = set(filenames)
            has_config = bool(direct_files.intersection(config_markers))
            has_parameters = any(name.endswith(('.pdiparams', '.pdparams')) for name in direct_files)
            if has_config and has_parameters:
                model_directories.append(directory)

        if not model_directories:
            raise ValueError('压缩包不是有效的 PaddleOCR 推理模型目录')
        if len(model_directories) > 1:
            raise ValueError('OCR 模型包包含多个推理模型目录，请每个压缩包只放一个模型')
        source_dir = model_directories[0]

        archive_name = os.path.basename(archive_path)
        base_name = re.sub(r'(?i)(\.tar\.gz|\.tgz|\.tar|\.zip)$', '', archive_name)
        final_dir = os.path.join(os.path.dirname(archive_path), base_name)
        counter = 0
        while os.path.exists(final_dir):
            counter += 1
            final_dir = os.path.join(os.path.dirname(archive_path), f'{base_name}_{counter}')
        shutil.move(source_dir, final_dir)
        if os.path.exists(extract_root):
            shutil.rmtree(extract_root)
        os.remove(archive_path)
        return final_dir, extracted_bytes
    except Exception:
        shutil.rmtree(extract_root, ignore_errors=True)
        if final_dir and os.path.exists(final_dir):
            shutil.rmtree(final_dir, ignore_errors=True)
        raise


def _infer_model_meta(filename, model_type=None, framework=None):
    """根据扩展名推断模型类型与框架，补齐默认值。"""
    ext = os.path.splitext(filename or '')[1].lower()
    inferred_type, inferred_framework = EXTENSION_MODEL_HINTS.get(ext, ('Custom', 'custom'))

    normalized_type = (model_type or '').strip()
    normalized_framework = (framework or '').strip()

    if not normalized_type or normalized_type in ('YOLO', 'Custom'):
        normalized_type = inferred_type

    if not normalized_framework or normalized_framework in ('ultralytics', 'custom'):
        normalized_framework = inferred_framework

    return normalized_type, normalized_framework


def _extract_filename_from_content_disposition(content_disposition):
    """从 Content-Disposition 中提取文件名"""
    if not content_disposition:
        return None

    filename_star_match = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition, flags=re.IGNORECASE)
    if filename_star_match:
        return unquote(filename_star_match.group(1))

    filename_match = re.search(r'filename="?([^";]+)"?', content_disposition, flags=re.IGNORECASE)
    if filename_match:
        return filename_match.group(1)

    return None


def _get_unique_file_path(model_type, filename):
    """获取唯一可写入的模型文件路径"""
    type_dir = model_type.lower()
    save_dir = os.path.join(MODELS_ROOT, type_dir)

    try:
        os.makedirs(save_dir, exist_ok=True)
    except Exception as e:
        raise RuntimeError(f'创建存储目录失败: {e}')

    base_name, ext = os.path.splitext(filename)
    counter = 0
    final_filename = filename
    while os.path.exists(os.path.join(save_dir, final_filename)):
        counter += 1
        final_filename = f"{base_name}_{counter}{ext}"

    return os.path.join(save_dir, final_filename), final_filename


def _upsert_model_record(
    name,
    version,
    filename,
    file_path,
    file_size,
    model_type,
    model_role,
    framework,
    input_shape,
    classes,
    model_postprocess,
    description,
    tags,
    uploaded_by='admin',
):
    """创建或更新模型记录"""
    existing = MLModel.select().where((MLModel.name == name) & (MLModel.version == version)).first()
    if existing:
        # 删除旧文件
        if existing.file_path != file_path:
            _remove_model_artifact(existing.file_path)
        # 更新记录
        existing.filename = filename
        existing.file_path = file_path
        existing.file_size = file_size
        existing.model_type = model_type
        existing.model_role = model_role or None
        existing.framework = framework
        existing.input_shape = input_shape or None
        existing.classes = classes or None
        existing.model_postprocess = model_postprocess or None
        existing.description = description or None
        existing.tags = tags or None
        existing.uploaded_by = uploaded_by or existing.uploaded_by
        existing.updated_at = datetime.now()
        existing.save()
        return existing

    now = datetime.now()
    return MLModel.create(
        name=name,
        filename=filename,
        file_path=file_path,
        file_size=file_size,
        model_type=model_type,
        model_role=model_role or None,
        framework=framework,
        input_shape=input_shape or None,
        classes=classes or None,
        model_postprocess=model_postprocess or None,
        description=description or None,
        version=version,
        tags=tags or None,
        created_at=now,
        updated_at=now,
        uploaded_by=uploaded_by or 'admin',
    )


def _parse_json_field(value, field_name, default_value):
    """兼容字符串或原生对象的 JSON 字段"""
    if value is None or value == '':
        return default_value

    if isinstance(value, (dict, list)):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(f'{field_name} JSON格式错误: {e}')

    raise ValueError(f'{field_name} 字段类型错误')


def _parse_json_object_field(value, field_name):
    if value is None or value == '':
        return None

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(f'{field_name} JSON格式错误: {e}')

    if not isinstance(value, dict):
        raise ValueError(f'{field_name} 必须是 JSON 对象')

    return json.dumps(value, ensure_ascii=False)


def _model_reference_matches(reference, target_model):
    if isinstance(reference, str):
        return reference == target_model.name
    if not isinstance(reference, dict):
        return False
    try:
        if int(reference.get('model_id')) == target_model.id:
            return True
    except (TypeError, ValueError):
        pass
    return str(reference.get('name') or '') == target_model.name


def _algorithm_references_model(algorithm, model_id, target_model=None):
    target_id = int(model_id)
    if target_model is None:
        try:
            target_model = MLModel.get_by_id(target_id)
        except MLModel.DoesNotExist:
            return False
    ext_config = algorithm.ext_config
    ocr_config = ext_config.get('ocr_config') or {}
    for key in ('detection_model_id', 'recognition_model_id'):
        try:
            if int(ocr_config.get(key)) == target_id:
                return True
        except (TypeError, ValueError):
            pass

    cascade_config = ext_config.get('cascade_config') or {}
    for stage in cascade_config.get('stages') or []:
        if not isinstance(stage, dict):
            continue
        try:
            if int(stage.get('model_id')) == target_id:
                return True
        except (TypeError, ValueError):
            continue

    config = algorithm.config_dict
    models = config.get('models') or []
    if isinstance(models, dict):
        if any(_model_reference_matches(reference, target_model) for reference in models.values()):
            return True
    elif isinstance(models, list):
        if any(_model_reference_matches(reference, target_model) for reference in models):
            return True

    raw_model_ids = ext_config.get('model_ids')
    if isinstance(raw_model_ids, str):
        try:
            raw_model_ids = json.loads(raw_model_ids)
        except json.JSONDecodeError:
            raw_model_ids = []
    if isinstance(raw_model_ids, list):
        for reference_id in raw_model_ids:
            try:
                if int(reference_id) == target_id:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _algorithms_referencing_model(model_id):
    try:
        target_model = MLModel.get_by_id(int(model_id))
    except (MLModel.DoesNotExist, TypeError, ValueError):
        return []
    return [
        algorithm
        for algorithm in Algorithm.select()
        if _algorithm_references_model(algorithm, target_model.id, target_model=target_model)
    ]


def _normalized_filter_values(values):
    if not isinstance(values, list):
        return set()
    return {str(value).strip().lower() for value in values if str(value).strip()}


@lru_cache(maxsize=1)
def _load_quick_setup_metadata():
    """Read the built-in script metadata literal without importing the script."""
    file_path = get_script_loader().resolve_path(QUICK_SETUP_SCRIPT_PATH)
    with open(file_path, 'r', encoding='utf-8') as script_file:
        tree = ast.parse(script_file.read(), filename=file_path)
    for node in tree.body:
        value_node = None
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == 'SCRIPT_METADATA' for target in node.targets):
                value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == 'SCRIPT_METADATA':
                value_node = node.value
        if value_node is not None:
            metadata = ast.literal_eval(value_node)
            return metadata if isinstance(metadata, dict) else {}
    return {}


def _quick_setup_definition(model):
    """Resolve quick-setup defaults from the generic script without executing it."""
    try:
        metadata = _load_quick_setup_metadata()
    except Exception as exc:
        logger.error(f'读取快速创建脚本元数据失败: {exc}')
        return {
            'eligible': False,
            'reason': '通用检测脚本当前不可用',
            'script': None,
            'script_config': {},
            'performance': {},
        }

    schema = metadata.get('config_schema') if isinstance(metadata, dict) else None
    schema = schema if isinstance(schema, dict) else {}
    model_field = schema.get('model_id')
    if not isinstance(model_field, dict) or model_field.get('type') != 'model_select':
        return {
            'eligible': False,
            'reason': '通用检测脚本缺少单模型配置',
            'script': None,
            'script_config': {},
            'performance': {},
        }

    filters = model_field.get('filters') if isinstance(model_field.get('filters'), dict) else {}
    allowed_types = _normalized_filter_values(filters.get('model_type'))
    allowed_frameworks = _normalized_filter_values(filters.get('framework'))
    model_type = str(model.model_type or '').strip().lower()
    framework = str(model.framework or '').strip().lower()

    reason = None
    if not model.enabled:
        reason = '模型已禁用，请先启用模型'
    elif allowed_types and model_type not in allowed_types:
        reason = f'模型类型 {model.model_type} 不受通用检测脚本支持'
    elif allowed_frameworks and framework not in allowed_frameworks:
        reason = f'模型框架 {model.framework} 不受通用检测脚本支持'

    script_config = {'model_id': model.id}
    missing_required = []
    for field_name, field_schema in schema.items():
        if field_name == 'model_id' or not isinstance(field_schema, dict):
            continue
        if 'default' in field_schema:
            script_config[field_name] = deepcopy(field_schema['default'])
        elif field_schema.get('required'):
            missing_required.append(field_schema.get('label') or field_name)

    if not reason and missing_required:
        reason = f"通用脚本仍需配置: {', '.join(missing_required)}"

    performance = metadata.get('performance') if isinstance(metadata.get('performance'), dict) else {}
    return {
        'eligible': reason is None,
        'reason': reason,
        'script': {
            'name': metadata.get('name') or '自适应 YOLO 检测',
            'path': QUICK_SETUP_SCRIPT_PATH,
            'version': metadata.get('version') or '',
        },
        'script_config': script_config,
        'performance': performance,
    }


def _quick_marker(algorithm):
    marker = algorithm.ext_config.get(QUICK_SETUP_MARKER_KEY)
    return marker if isinstance(marker, dict) else {}


def _find_quick_algorithm(model, username):
    for algorithm in Algorithm.select().where(Algorithm.created_by == username):
        marker = _quick_marker(algorithm)
        try:
            marker_model_id = int(marker.get('model_id'))
        except (TypeError, ValueError):
            continue
        if marker_model_id != model.id:
            continue
        if algorithm.script_path != QUICK_SETUP_SCRIPT_PATH:
            continue
        if _algorithm_references_model(algorithm, model.id, target_model=model):
            return algorithm
    return None


def _workflow_references_algorithm(workflow, algorithm_id, exact_quick_graph=False):
    graph = workflow.data_dict
    nodes = graph.get('nodes') if isinstance(graph, dict) else None
    if not isinstance(nodes, list):
        return False
    if exact_quick_graph:
        node_types = sorted(str(node.get('type') or '') for node in nodes if isinstance(node, dict))
        if node_types != ['alert', 'algorithm', 'source']:
            return False
    for node in nodes:
        if not isinstance(node, dict) or node.get('type') != 'algorithm':
            continue
        raw_id = node.get('dataId') or node.get('algorithmId')
        try:
            if int(raw_id) == int(algorithm_id):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _find_quick_template(algorithm, username):
    marker = _quick_marker(algorithm)
    try:
        template_id = int(marker.get('workflow_template_id'))
    except (TypeError, ValueError):
        template_id = None

    if template_id is not None:
        template = Workflow.get_or_none(
            (Workflow.id == template_id)
            & (Workflow.created_by == username)
            & (Workflow.is_template == True)
        )
        if template and _workflow_references_algorithm(template, algorithm.id):
            return template

    candidates = Workflow.select().where(
        (Workflow.created_by == username) & (Workflow.is_template == True)
    )
    for template in candidates:
        if _workflow_references_algorithm(template, algorithm.id, exact_quick_graph=True):
            return template
    return None


def _serialize_quick_resource(resource, created):
    return {
        'id': resource.id,
        'name': resource.name,
        'created': created,
    }


def _quick_setup_preview(model, username):
    definition = _quick_setup_definition(model)
    algorithm = _find_quick_algorithm(model, username)
    template = _find_quick_template(algorithm, username) if algorithm else None
    return {
        'eligible': definition['eligible'],
        'reason': definition['reason'],
        'model': {
            'id': model.id,
            'name': model.name,
            'model_type': model.model_type,
            'framework': model.framework,
        },
        'script': definition['script'],
        'defaults': {
            'algorithm_name': f'{model.name}算法',
            'template_name': f'{model.name}告警模板',
        },
        'existing': {
            'algorithm': _serialize_quick_resource(algorithm, False) if algorithm else None,
            'workflow_template': _serialize_quick_resource(template, False) if template else None,
        },
    }


def _build_quick_template_graph(model, algorithm, runtime_timeout, memory_limit_mb):
    graph = build_template_workflow_data()
    source_node = graph['nodes'][0]
    source_node.update({'x': 80, 'y': 160})
    algorithm_node_id = f'quick-algorithm-{algorithm.id}'
    alert_node_id = f'quick-alert-{algorithm.id}'
    graph['nodes'].extend([
        {
            'id': algorithm_node_id,
            'type': 'algorithm',
            'name': algorithm.name,
            'x': 360,
            'y': 160,
            'description': algorithm.description,
            'dataId': algorithm.id,
            'algorithmId': algorithm.id,
            'config': {
                'interval_seconds': 1,
                'runtime_timeout': runtime_timeout,
                'memory_limit_mb': memory_limit_mb,
                'label_name': model.name,
                'label_color': QUICK_SETUP_LABEL_COLOR,
                'window_detection': {
                    'enable': False,
                    'window_size': 30,
                    'window_mode': 'ratio',
                    'window_threshold': 0.3,
                },
            },
        },
        {
            'id': alert_node_id,
            'type': 'alert',
            'name': '告警输出',
            'x': 660,
            'y': 160,
            'description': '发送检测告警',
            'config': None,
            'data': {
                'alertLevel': 'warning',
                'alertType': 'detection',
                'alertMessage': f'模型 {model.name} 检测到目标',
                'messageFormat': 'detailed',
                'triggerCondition': None,
                'suppression': None,
                'vlValidation': {'enable': False, 'promptTemplate': ''},
            },
        },
    ])
    graph['connections'] = [
        {
            'id': f'{source_node["id"]}-{algorithm_node_id}',
            'from': source_node['id'],
            'to': algorithm_node_id,
            'from_node_id': source_node['id'],
            'to_node_id': algorithm_node_id,
            'from_port': 'output',
            'to_port': 'input',
            'condition': None,
            'label': '',
        },
        {
            'id': f'{algorithm_node_id}-{alert_node_id}',
            'from': algorithm_node_id,
            'to': alert_node_id,
            'from_node_id': algorithm_node_id,
            'to_node_id': alert_node_id,
            'from_port': 'output',
            'to_port': 'input',
            'condition': None,
            'label': '',
        },
    ]
    return graph


def serialize_model(model):
    """序列化模型对象"""
    quick_setup = _quick_setup_definition(model)
    return {
        'id': model.id,
        'name': model.name,
        'filename': model.filename,
        'file_path': model.file_path,
        'file_size': model.file_size,
        'file_size_mb': round(model.file_size / (1024 * 1024), 2),
        'model_type': model.model_type,
        'model_role': model.model_role or None,
        'framework': model.framework,
        'input_shape': model.input_shape,
        'classes': model.classes_dict,
        'model_postprocess': model.model_postprocess_dict,
        'description': model.description,
        'version': model.version,
        'tags': model.tags_list,
        'created_at': model.created_at.isoformat() if model.created_at else None,
        'updated_at': model.updated_at.isoformat() if model.updated_at else None,
        'uploaded_by': model.uploaded_by,
        'download_count': model.download_count,
        'usage_count': len(_algorithms_referencing_model(model.id)),
        'enabled': model.enabled,
        'quick_setup': {
            'eligible': quick_setup['eligible'],
            'reason': quick_setup['reason'],
        },
    }


@models_bp.route('/', methods=['GET'])
def list_models():
    """
    获取模型列表

    Query参数:
        - type: 模型类型筛选 (YOLO, ONNX, TensorRT等)
        - framework: 框架筛选 (ultralytics, onnx等)
        - enabled: 是否只显示启用的模型 (true/false)
        - search: 搜索关键词（名称或描述）
    """
    try:
        query = MLModel.select()

        # 筛选条件
        model_type = request.args.get('type')
        if model_type:
            query = query.where(MLModel.model_type == model_type)

        framework = request.args.get('framework')
        if framework:
            query = query.where(MLModel.framework == framework)

        enabled_only = request.args.get('enabled', 'false').lower() == 'true'
        if enabled_only:
            query = query.where(MLModel.enabled == True)

        search = request.args.get('search', '').strip()
        if search:
            query = query.where((MLModel.name.contains(search)) | (MLModel.description.contains(search)))

        # 排序：最新创建的在前
        query = query.order_by(MLModel.created_at.desc())

        models = list(query)
        return jsonify({
            'success': True,
            'models': [serialize_model(m) for m in models],
            'total': len(models)
        })

    except Exception as e:
        logger.error(f"获取模型列表失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@models_bp.route('/', methods=['POST'])
def upload_model():
    """
    上传模型文件

    Form参数:
        - name: 模型名称
        - description: 描述
        - model_type: 模型类型 (YOLO, ONNX, TensorRT等)
        - framework: 框架 (ultralytics, pytorch, onnx等)
        - input_shape: 输入尺寸 (如 "640x640")
        - classes: 支持的类别 (JSON字符串, 如 '{"0": "person", "1": "car"}')
        - tags: 标签 (JSON数组, 如 '["person", "detection"]')
        - version: 版本号 (默认 v1.0)
        - file: 模型文件
    """
    try:
        # 验证必填字段
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '缺少文件字段'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': '未选择文件'}), 400

        # 获取表单数据
        name = request.form.get('name', '').strip()
        if not name:
            return jsonify({'success': False, 'error': '模型名称不能为空'}), 400

        description = request.form.get('description', '').strip()
        model_type = request.form.get('model_type', '').strip()
        model_role = request.form.get('model_role', '').strip().lower()
        framework = request.form.get('framework', '').strip()
        input_shape = request.form.get('input_shape', '').strip()
        classes = request.form.get('classes', '{}').strip()
        model_postprocess = request.form.get('model_postprocess', '').strip()
        tags = request.form.get('tags', '[]').strip()
        version = request.form.get('version', 'v1.0').strip()

        if not allowed_file(file.filename, model_type):
            return jsonify({'success': False, 'error': '不支持的模型文件类型'}), 400
        if model_type.upper() == 'OCR':
            if model_role not in OCR_MODEL_ROLES:
                return jsonify({'success': False, 'error': 'OCR 模型角色仅支持 detection 或 recognition'}), 400
            if not _is_ocr_archive(file.filename):
                return jsonify({'success': False, 'error': 'OCR 模型必须上传 ZIP/TAR/TAR.GZ/TGZ 压缩包'}), 400
            model_type = 'OCR'
            framework = 'paddleocr'
        else:
            model_role = ''

        # 验证JSON格式
        try:
            if classes:
                json.loads(classes)
            if model_postprocess:
                parsed_model_postprocess = json.loads(model_postprocess)
                if not isinstance(parsed_model_postprocess, dict):
                    return jsonify({'success': False, 'error': 'model_postprocess 必须是 JSON 对象'}), 400
            if tags:
                json.loads(tags)
        except json.JSONDecodeError as e:
            return jsonify({'success': False, 'error': f'JSON格式错误: {e}'}), 400

        # 安全文件名
        filename = secure_filename(file.filename)
        if model_type != 'OCR':
            model_type, framework = _infer_model_meta(filename, model_type, framework)

        # 处理目录和重名
        try:
            file_path, final_filename = _get_unique_file_path(model_type, filename)
        except RuntimeError as e:
            logger.error(f"创建目录失败，错误: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

        # 保存文件
        try:
            file.save(file_path)
        except Exception as e:
            logger.error(f"保存文件失败: {file_path}, 错误: {e}")
            return jsonify({'success': False, 'error': f'保存文件失败: {e}'}), 500

        # OCR 模型以目录形式保存；普通模型保持单文件。
        if model_type == 'OCR':
            try:
                file_path, file_size = _extract_ocr_archive(file_path)
                final_filename = f'{os.path.basename(file_path)}.zip'
            except ValueError as e:
                _remove_model_artifact(file_path)
                return jsonify({'success': False, 'error': str(e)}), 400
        else:
            file_size = os.path.getsize(file_path)

        model = _upsert_model_record(
            name=name,
            version=version,
            filename=final_filename,
            file_path=file_path,
            file_size=file_size,
            model_type=model_type,
            model_role=model_role,
            framework=framework,
            input_shape=input_shape,
            classes=classes,
            model_postprocess=model_postprocess,
            description=description,
            tags=tags,
            uploaded_by=current_username('admin'),
        )

        logger.info(f"模型上传成功: {name} ({version}), 路径: {file_path}")

        return jsonify({
            'success': True,
            'model': serialize_model(model),
            'message': '模型上传成功'
        })

    except Exception as e:
        logger.error(f"模型上传失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@models_bp.route('/import', methods=['POST'])
def import_model_from_url():
    """
    通过 URL 或 Hugging Face 仓库拉取模型

    JSON Body:
        - source_type: url | huggingface
        - name: 模型名称（可选，默认使用文件名）
        - description: 描述
        - model_type: 模型类型
        - framework: 框架
        - input_shape: 输入尺寸
        - classes: 类别（JSON对象或JSON字符串）
        - tags: 标签（JSON数组或JSON字符串）
        - version: 版本号
        - source_url: 直链URL（source_type=url时必填）
        - repo_id: 仓库ID（source_type=huggingface时必填）
        - filename: 仓库内模型文件路径（source_type=huggingface时必填）
        - revision: 分支/Tag/Commit（默认main）
        - hf_token: 私有仓库访问Token（可选）
        - use_hf_mirror: 是否使用国内镜像（可选，默认读取 HF_USE_MIRROR）
    """
    file_path = None
    try:
        data = request.get_json(silent=True) or {}
        source_type = (data.get('source_type') or 'url').strip().lower()

        model_type = (data.get('model_type') or '').strip()
        model_role = (data.get('model_role') or '').strip().lower()
        framework = (data.get('framework') or '').strip()
        input_shape = (data.get('input_shape') or '').strip()
        description = (data.get('description') or '').strip()
        version = (data.get('version') or 'v1.0').strip()
        if model_type.upper() == 'OCR':
            if model_role not in OCR_MODEL_ROLES:
                return jsonify({'success': False, 'error': 'OCR 模型角色仅支持 detection 或 recognition'}), 400
            model_type = 'OCR'
            framework = 'paddleocr'
        else:
            model_role = ''

        try:
            classes_data = _parse_json_field(data.get('classes'), 'classes', {})
            model_postprocess = _parse_json_object_field(data.get('model_postprocess'), 'model_postprocess')
            tags_data = _parse_json_field(data.get('tags'), 'tags', [])
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400

        classes = json.dumps(classes_data, ensure_ascii=False) if classes_data is not None else None
        tags = json.dumps(tags_data, ensure_ascii=False) if tags_data is not None else None

        headers = {
            'User-Agent': 'video-ba-pipe-model-import/1.0'
        }

        if source_type == 'huggingface':
            repo_id = (data.get('repo_id') or '').strip()
            repo_filename = (data.get('filename') or '').strip()
            revision = (data.get('revision') or 'main').strip()
            hf_token = (data.get('hf_token') or os.getenv('HF_TOKEN') or '').strip()

            if not repo_id or not repo_filename:
                return jsonify({'success': False, 'error': 'huggingface 模式下 repo_id 和 filename 必填'}), 400

            try:
                use_hf_mirror = _parse_boolean(
                    data.get('use_hf_mirror'),
                    default=HF_USE_MIRROR,
                )
                source_url = _build_huggingface_download_url(
                    repo_id,
                    repo_filename,
                    revision=revision,
                    use_mirror=use_hf_mirror,
                )
            except ValueError as e:
                return jsonify({'success': False, 'error': str(e)}), 400
            source_filename = secure_filename(os.path.basename(repo_filename))
            if hf_token:
                headers['Authorization'] = f'Bearer {hf_token}'
        elif source_type == 'url':
            source_url = (data.get('source_url') or '').strip()
            if not source_url:
                return jsonify({'success': False, 'error': 'source_url 不能为空'}), 400

            parsed_url = urlparse(source_url)
            if parsed_url.scheme not in ('http', 'https'):
                return jsonify({'success': False, 'error': '仅支持 http/https URL'}), 400

            source_filename = secure_filename(unquote(os.path.basename(parsed_url.path)))
            if not source_filename:
                source_filename = secure_filename((data.get('filename') or '').strip())
        else:
            return jsonify({'success': False, 'error': 'source_type 仅支持 url 或 huggingface'}), 400

        source_filename_valid = bool(source_filename and allowed_file(source_filename, model_type))

        if source_type == 'huggingface' and not source_filename_valid:
            return jsonify({'success': False, 'error': f'不支持的文件类型，允许的类型: {", ".join(ALLOWED_EXTENSIONS)}'}), 400

        model_name = (data.get('name') or '').strip() or (os.path.splitext(source_filename)[0] if source_filename else '')
        if not model_name:
            return jsonify({'success': False, 'error': '模型名称不能为空'}), 400

        try:
            request_timeout = (
                min(30, HF_DOWNLOAD_TIMEOUT_SECONDS),
                HF_DOWNLOAD_TIMEOUT_SECONDS,
            )
            with requests.get(
                source_url,
                headers=headers,
                stream=True,
                timeout=request_timeout,
                allow_redirects=True,
            ) as response:
                response.raise_for_status()
                response_filename = _extract_filename_from_content_disposition(
                    response.headers.get('Content-Disposition')
                )
                download_filename = source_filename if source_filename_valid else ''
                if response_filename:
                    response_filename = secure_filename(response_filename)
                    if response_filename and allowed_file(response_filename, model_type):
                        download_filename = response_filename

                if not download_filename:
                    return jsonify({
                        'success': False,
                        'error': f'无法识别模型文件名或文件类型，允许的类型: {", ".join(ALLOWED_EXTENSIONS)}'
                    }), 400

                if model_type != 'OCR':
                    model_type, framework = _infer_model_meta(download_filename, model_type, framework)

                try:
                    file_path, final_filename = _get_unique_file_path(model_type, download_filename)
                except RuntimeError as e:
                    return jsonify({'success': False, 'error': str(e)}), 500

                with open(file_path, 'wb') as output_file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output_file.write(chunk)
        except requests.HTTPError as e:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            status_code = e.response.status_code if e.response is not None else '未知'
            return jsonify({'success': False, 'error': f'下载失败，HTTP状态码: {status_code}'}), 400
        except requests.RequestException as e:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            return jsonify({'success': False, 'error': f'下载失败: {e}'}), 400
        except Exception as e:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            return jsonify({'success': False, 'error': f'下载模型失败: {e}'}), 500

        if not os.path.exists(file_path):
            return jsonify({'success': False, 'error': '模型下载失败，文件未生成'}), 500

        if model_type == 'OCR':
            if not _is_ocr_archive(final_filename):
                _remove_model_artifact(file_path)
                return jsonify({'success': False, 'error': 'OCR 模型必须是 ZIP/TAR/TAR.GZ/TGZ 压缩包'}), 400
            try:
                file_path, file_size = _extract_ocr_archive(file_path)
                final_filename = f'{os.path.basename(file_path)}.zip'
            except ValueError as e:
                _remove_model_artifact(file_path)
                return jsonify({'success': False, 'error': str(e)}), 400
        else:
            file_size = os.path.getsize(file_path)

        model = _upsert_model_record(
            name=model_name,
            version=version,
            filename=final_filename,
            file_path=file_path,
            file_size=file_size,
            model_type=model_type,
            model_role=model_role,
            framework=framework,
            input_shape=input_shape,
            classes=classes,
            model_postprocess=model_postprocess,
            description=description,
            tags=tags,
            uploaded_by=current_username('admin'),
        )

        logger.info(f"模型导入成功: {model_name} ({version}), 来源: {source_url}, 路径: {file_path}")
        return jsonify({
            'success': True,
            'model': serialize_model(model),
            'message': '模型拉取成功'
        })

    except Exception as e:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        logger.error(f"模型导入失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@models_bp.route('/<int:model_id>/quick-setup', methods=['GET'])
def get_model_quick_setup(model_id):
    """Preview the default algorithm and workflow template for a model."""
    try:
        model = MLModel.get_by_id(model_id)
        return jsonify({
            'success': True,
            **_quick_setup_preview(model, current_username('admin')),
        })
    except MLModel.DoesNotExist:
        return jsonify({'success': False, 'error': '模型不存在'}), 404
    except Exception as exc:
        logger.error(f'获取模型快速创建预览失败 (model_id={model_id}): {exc}')
        return jsonify({'success': False, 'error': str(exc)}), 500


@models_bp.route('/<int:model_id>/quick-setup', methods=['POST'])
def create_model_quick_setup(model_id):
    """Create or reuse a default algorithm and its alert workflow template."""
    try:
        model = MLModel.get_by_id(model_id)
    except MLModel.DoesNotExist:
        return jsonify({'success': False, 'error': '模型不存在'}), 404

    username = current_username('admin')
    definition = _quick_setup_definition(model)
    if not definition['eligible']:
        return jsonify({
            'success': False,
            'code': 'quick_setup_ineligible',
            'error': definition['reason'] or '该模型不支持快速创建',
        }), 400

    data = request.get_json(silent=True) or {}
    default_algorithm_name = f'{model.name}算法'
    default_template_name = f'{model.name}告警模板'
    algorithm_name = str(data.get('algorithm_name') or default_algorithm_name).strip()
    template_name = str(data.get('template_name') or default_template_name).strip()
    if not algorithm_name:
        return jsonify({'success': False, 'error': '算法名称不能为空'}), 400
    if not template_name:
        return jsonify({'success': False, 'error': '模板名称不能为空'}), 400

    existing_algorithm = _find_quick_algorithm(model, username)
    if existing_algorithm is None and Algorithm.get_or_none(Algorithm.name == algorithm_name):
        return jsonify({
            'success': False,
            'code': 'algorithm_name_conflict',
            'error': '算法名称已存在，请修改后重试',
        }), 409

    now = datetime.now()
    algorithm_created = False
    template_created = False
    try:
        with db.atomic():
            algorithm = existing_algorithm
            if algorithm is None:
                performance = definition['performance']
                runtime_timeout = int(performance.get('timeout') or 15)
                memory_limit_mb = int(performance.get('memory_limit_mb') or 256)
                script = definition['script'] or {}
                ext_config = {
                    'algorithm_type': 'script',
                    'plugin_module': 'script_algorithm',
                    'script_type': 'script',
                    'script_version': script.get('version') or '',
                    'interval_seconds': 1,
                    'runtime_timeout': runtime_timeout,
                    'memory_limit_mb': memory_limit_mb,
                    'enable_window_check': False,
                    'window_size': 30,
                    'window_mode': 'ratio',
                    'window_threshold': 0.3,
                    'label_name': model.name,
                    'label_color': QUICK_SETUP_LABEL_COLOR,
                    'model_json': json.dumps({'model_id': model.id}, ensure_ascii=False),
                    'model_ids': json.dumps([model.id]),
                    QUICK_SETUP_MARKER_KEY: {
                        'model_id': model.id,
                        'script_path': QUICK_SETUP_SCRIPT_PATH,
                        'script_version': script.get('version') or '',
                    },
                }
                algorithm = Algorithm.create(
                    name=algorithm_name,
                    description=f'由模型「{model.name}」快速创建的通用检测算法',
                    script_path=QUICK_SETUP_SCRIPT_PATH,
                    script_config=json.dumps(definition['script_config'], ensure_ascii=False),
                    ext_config_json=json.dumps(ext_config, ensure_ascii=False),
                    enabled_hooks=None,
                    created_at=now,
                    updated_at=now,
                    created_by=username,
                )
                algorithm_created = True

            template = _find_quick_template(algorithm, username)
            if template is None:
                ext_config = dict(algorithm.ext_config)
                runtime_timeout = int(ext_config.get('runtime_timeout') or 15)
                memory_limit_mb = int(ext_config.get('memory_limit_mb') or 256)
                graph = _build_quick_template_graph(
                    model,
                    algorithm,
                    runtime_timeout,
                    memory_limit_mb,
                )
                valid, error_message = validate_template_source_node(graph)
                if not valid:
                    raise ValueError(error_message)
                template = Workflow.create(
                    name=template_name,
                    description=f'由模型「{model.name}」快速创建的告警编排模板',
                    workflow_data=json.dumps(graph, ensure_ascii=False),
                    is_active=False,
                    is_template=True,
                    source_template=None,
                    video_source=None,
                    created_at=now,
                    updated_at=now,
                    created_by=username,
                )
                template_created = True

            ext_config = dict(algorithm.ext_config)
            marker = dict(ext_config.get(QUICK_SETUP_MARKER_KEY) or {})
            if marker.get('workflow_template_id') != template.id:
                marker['workflow_template_id'] = template.id
                ext_config[QUICK_SETUP_MARKER_KEY] = marker
                algorithm.ext_config_json = json.dumps(ext_config, ensure_ascii=False)
                algorithm.updated_at = now
                algorithm.save()

        status_code = 201 if algorithm_created or template_created else 200
        return jsonify({
            'success': True,
            'message': '算法与编排模板创建完成' if status_code == 201 else '已复用现有算法与编排模板',
            'algorithm': _serialize_quick_resource(algorithm, algorithm_created),
            'workflow_template': _serialize_quick_resource(template, template_created),
        }), status_code
    except IntegrityError as exc:
        logger.warning(f'模型快速创建发生名称冲突 (model_id={model_id}): {exc}')
        return jsonify({
            'success': False,
            'code': 'algorithm_name_conflict',
            'error': '算法名称已存在，请修改后重试',
        }), 409
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        logger.error(f'模型快速创建失败 (model_id={model_id}): {exc}')
        return jsonify({'success': False, 'error': str(exc)}), 500


@models_bp.route('/<int:model_id>', methods=['GET'])
def get_model(model_id):
    """获取单个模型详情"""
    try:
        model = MLModel.get_by_id(model_id)
        return jsonify({
            'success': True,
            'model': serialize_model(model)
        })
    except MLModel.DoesNotExist:
        return jsonify({'success': False, 'error': '模型不存在'}), 404
    except Exception as e:
        logger.error(f"获取模型详情失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@models_bp.route('/<int:model_id>', methods=['PUT'])
def update_model(model_id):
    """
    更新模型信息

    JSON Body:
        - name: 模型名称
        - description: 描述
        - input_shape: 输入尺寸
        - classes: 类别JSON
        - tags: 标签JSON
        - enabled: 是否启用
    """
    try:
        model = MLModel.get_by_id(model_id)
        data = request.get_json()

        # 更新字段
        if 'name' in data:
            model.name = data['name'].strip()
        if 'description' in data:
            model.description = data['description'].strip()
        if 'model_role' in data:
            model_role = str(data['model_role'] or '').strip().lower()
            if model.model_type.upper() == 'OCR' and model_role not in OCR_MODEL_ROLES:
                return jsonify({'success': False, 'error': 'OCR 模型角色仅支持 detection 或 recognition'}), 400
            model.model_role = model_role or None
        if 'input_shape' in data:
            model.input_shape = data['input_shape'].strip() or None
        if 'classes' in data:
            model.classes = json.dumps(data['classes']) if isinstance(data['classes'], dict) else data['classes']
        try:
            if 'model_postprocess' in data:
                model.model_postprocess = _parse_json_object_field(data['model_postprocess'], 'model_postprocess')
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400
        if 'tags' in data:
            model.tags = json.dumps(data['tags']) if isinstance(data['tags'], list) else data['tags']
        if 'enabled' in data:
            model.enabled = data['enabled']

        model.updated_at = datetime.now()
        model.save()

        logger.info(f"模型信息更新: {model.name} (ID: {model_id})")

        return jsonify({
            'success': True,
            'model': serialize_model(model)
        })

    except MLModel.DoesNotExist:
        return jsonify({'success': False, 'error': '模型不存在'}), 404
    except Exception as e:
        logger.error(f"更新模型失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@models_bp.route('/<int:model_id>', methods=['DELETE'])
def delete_model(model_id):
    """删除模型"""
    try:
        model = MLModel.get_by_id(model_id)

        # 检查是否被算法使用
        referencing_algorithms = _algorithms_referencing_model(model_id)
        if referencing_algorithms:
            return jsonify({
                'success': False,
                'error': f'该模型正在被 {len(referencing_algorithms)} 个算法使用，无法删除'
            }), 400

        _remove_model_artifact(model.file_path)
        logger.info(f"已删除模型文件: {model.file_path}")

        # 删除数据库记录
        model_name = model.name
        model.delete_instance()

        logger.info(f"模型删除成功: {model_name} (ID: {model_id})")

        return jsonify({
            'success': True,
            'message': '模型删除成功'
        })

    except MLModel.DoesNotExist:
        return jsonify({'success': False, 'error': '模型不存在'}), 404
    except Exception as e:
        logger.error(f"删除模型失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@models_bp.route('/<int:model_id>/download', methods=['GET'])
def download_model(model_id):
    """下载模型文件"""
    try:
        model = MLModel.get_by_id(model_id)

        if not os.path.exists(model.file_path):
            return jsonify({'success': False, 'error': '模型文件不存在'}), 404

        # 增加下载计数
        model.download_count += 1
        model.save()

        if os.path.isdir(model.file_path):
            archive_root = tempfile.mkdtemp(prefix='ocr-model-download-')
            archive_path = shutil.make_archive(
                os.path.join(archive_root, os.path.basename(model.file_path)),
                'zip',
                root_dir=os.path.dirname(model.file_path),
                base_dir=os.path.basename(model.file_path),
            )

            @after_this_request
            def cleanup_archive(response):
                shutil.rmtree(archive_root, ignore_errors=True)
                return response

            return send_file(
                archive_path,
                as_attachment=True,
                download_name=model.filename if model.filename.endswith('.zip') else f'{model.filename}.zip',
            )

        return send_file(model.file_path, as_attachment=True, download_name=model.filename)

    except MLModel.DoesNotExist:
        return jsonify({'success': False, 'error': '模型不存在'}), 404
    except Exception as e:
        logger.error(f"下载模型失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@models_bp.route('/<int:model_id>/algorithms', methods=['GET'])
def get_model_algorithms(model_id):
    """获取使用该模型的算法列表"""
    try:
        model = MLModel.get_by_id(model_id)

        algorithms = _algorithms_referencing_model(model_id)

        result = []
        for algo in algorithms:
            ext_config = algo.ext_config if hasattr(algo, 'ext_config') else {}
            script_config = algo.config_dict if hasattr(algo, 'config_dict') else {}
            result.append({
                'id': algo.id,
                'name': algo.name,
                'plugin_module': ext_config.get('plugin_module') or 'script_algorithm',
                'label_name': (
                    ext_config.get('label_name')
                    or script_config.get('label_name')
                    or algo.name
                )
            })

        return jsonify({
            'success': True,
            'algorithms': result,
            'total': len(result)
        })

    except MLModel.DoesNotExist:
        return jsonify({'success': False, 'error': '模型不存在'}), 404
    except Exception as e:
        logger.error(f"获取模型算法列表失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@models_bp.route('/types', methods=['GET'])
def get_model_types():
    """获取所有模型类型（用于筛选器）"""
    try:
        types = MLModel.select(MLModel.model_type).distinct()
        type_list = [t.model_type for t in types]
        return jsonify({
            'success': True,
            'types': type_list
        })
    except Exception as e:
        logger.error(f"获取模型类型失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@models_bp.route('/frameworks', methods=['GET'])
def get_model_frameworks():
    """获取所有框架（用于筛选器）"""
    try:
        frameworks = MLModel.select(MLModel.framework).distinct()
        framework_list = [f.framework for f in frameworks]
        return jsonify({
            'success': True,
            'frameworks': framework_list
        })
    except Exception as e:
        logger.error(f"获取框架列表失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def register_models_api(app):
    """注册模型管理API到Flask应用"""
    app.register_blueprint(models_bp)
    logger.info("模型管理API已注册")
