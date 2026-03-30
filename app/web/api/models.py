"""
模型管理API
"""
import os
import sys
import json
import re
import shutil
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file, current_app
from werkzeug.utils import secure_filename
from urllib.parse import urlparse, unquote, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.database_models import MLModel, Algorithm, db
from app.config import MODEL_SAVE_PATH
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
ALLOWED_EXTENSIONS = {'.pt', '.pth', '.onnx', '.engine', '.bin', '.tflite', '.xml', '.param', '.json', '.rknn'}


EXTENSION_MODEL_HINTS = {
    '.pt': ('YOLO', 'ultralytics'),
    '.pth': ('PyTorch', 'pytorch'),
    '.onnx': ('ONNX', 'onnx'),
    '.engine': ('TensorRT', 'tensorrt'),
    '.tflite': ('TFLite', 'tflite'),
    '.rknn': ('RKNN', 'rknn'),
}


def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS


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
        if os.path.exists(existing.file_path):
            os.remove(existing.file_path)
        # 更新记录
        existing.filename = filename
        existing.file_path = file_path
        existing.file_size = file_size
        existing.model_type = model_type
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


def serialize_model(model):
    """序列化模型对象"""
    return {
        'id': model.id,
        'name': model.name,
        'filename': model.filename,
        'file_path': model.file_path,
        'file_size': model.file_size,
        'file_size_mb': round(model.file_size / (1024 * 1024), 2),
        'model_type': model.model_type,
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
        'usage_count': model.usage_count,
        'enabled': model.enabled
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

        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': f'不支持的文件类型，允许的类型: {", ".join(ALLOWED_EXTENSIONS)}'}), 400

        # 获取表单数据
        name = request.form.get('name', '').strip()
        if not name:
            return jsonify({'success': False, 'error': '模型名称不能为空'}), 400

        description = request.form.get('description', '').strip()
        model_type = request.form.get('model_type', '').strip()
        framework = request.form.get('framework', '').strip()
        input_shape = request.form.get('input_shape', '').strip()
        classes = request.form.get('classes', '{}').strip()
        model_postprocess = request.form.get('model_postprocess', '').strip()
        tags = request.form.get('tags', '[]').strip()
        version = request.form.get('version', 'v1.0').strip()

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

        # 获取文件大小
        file_size = os.path.getsize(file_path)

        model = _upsert_model_record(
            name=name,
            version=version,
            filename=final_filename,
            file_path=file_path,
            file_size=file_size,
            model_type=model_type,
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
    """
    file_path = None
    try:
        data = request.get_json(silent=True) or {}
        source_type = (data.get('source_type') or 'url').strip().lower()

        model_type = (data.get('model_type') or '').strip()
        framework = (data.get('framework') or '').strip()
        input_shape = (data.get('input_shape') or '').strip()
        description = (data.get('description') or '').strip()
        version = (data.get('version') or 'v1.0').strip()

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

            source_url = (
                f"https://huggingface.co/{quote(repo_id, safe='/-')}"
                f"/resolve/{quote(revision, safe='')}/{quote(repo_filename, safe='/-_.')}?download=1"
            )
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

        source_filename_valid = bool(source_filename and allowed_file(source_filename))

        if source_type == 'huggingface' and not source_filename_valid:
            return jsonify({'success': False, 'error': f'不支持的文件类型，允许的类型: {", ".join(ALLOWED_EXTENSIONS)}'}), 400

        model_name = (data.get('name') or '').strip() or (os.path.splitext(source_filename)[0] if source_filename else '')
        if not model_name:
            return jsonify({'success': False, 'error': '模型名称不能为空'}), 400

        request_obj = Request(source_url, headers=headers)
        try:
            with urlopen(request_obj, timeout=120) as response:
                response_filename = _extract_filename_from_content_disposition(response.headers.get('Content-Disposition'))
                download_filename = source_filename if source_filename_valid else ''
                if response_filename:
                    response_filename = secure_filename(response_filename)
                    if response_filename and allowed_file(response_filename):
                        download_filename = response_filename

                if not download_filename:
                    return jsonify({
                        'success': False,
                        'error': f'无法识别模型文件名或文件类型，允许的类型: {", ".join(ALLOWED_EXTENSIONS)}'
                    }), 400

                model_type, framework = _infer_model_meta(download_filename, model_type, framework)

                try:
                    file_path, final_filename = _get_unique_file_path(model_type, download_filename)
                except RuntimeError as e:
                    return jsonify({'success': False, 'error': str(e)}), 500

                with open(file_path, 'wb') as output_file:
                    shutil.copyfileobj(response, output_file, length=1024 * 1024)
        except HTTPError as e:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            return jsonify({'success': False, 'error': f'下载失败，HTTP状态码: {e.code}'}), 400
        except URLError as e:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            return jsonify({'success': False, 'error': f'下载失败: {e.reason}'}), 400
        except Exception as e:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            return jsonify({'success': False, 'error': f'下载模型失败: {e}'}), 500

        if not os.path.exists(file_path):
            return jsonify({'success': False, 'error': '模型下载失败，文件未生成'}), 500

        file_size = os.path.getsize(file_path)

        model = _upsert_model_record(
            name=model_name,
            version=version,
            filename=final_filename,
            file_path=file_path,
            file_size=file_size,
            model_type=model_type,
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
        if model.usage_count > 0:
            return jsonify({
                'success': False,
                'error': f'该模型正在被 {model.usage_count} 个算法使用，无法删除'
            }), 400

        # 删除文件
        if os.path.exists(model.file_path):
            os.remove(model.file_path)
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

        return send_file(
            model.file_path,
            as_attachment=True,
            download_name=model.filename
        )

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

        # 查询使用该模型的算法
        # 通过 ext_config_json 中包含模型路径的算法
        algorithms = Algorithm.select().where(
            Algorithm.ext_config_json.contains(model.file_path)
        )

        result = []
        for algo in algorithms:
            result.append({
                'id': algo.id,
                'name': algo.name,
                'plugin_module': algo.plugin_module,
                'label_name': algo.label_name
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
