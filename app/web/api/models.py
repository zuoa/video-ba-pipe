"""
模型管理API
"""
import os
import sys
import json
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file, current_app
from werkzeug.utils import secure_filename

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.database_models import MLModel, Algorithm, db
from app import logger

# 创建蓝图
models_bp = Blueprint('models', __name__, url_prefix='/api/models')

# 模型存储根目录（项目根目录下的 models 文件夹）
MODELS_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'models')

# 允许的模型文件扩展名
ALLOWED_EXTENSIONS = {'.pt', '.pth', '.onnx', '.engine', '.bin', '.tflite', '.xml', '.param', '.json'}


def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS


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
        model_type = request.form.get('model_type', 'YOLO').strip()
        framework = request.form.get('framework', 'ultralytics').strip()
        input_shape = request.form.get('input_shape', '').strip()
        classes = request.form.get('classes', '{}').strip()
        tags = request.form.get('tags', '[]').strip()
        version = request.form.get('version', 'v1.0').strip()

        # 验证JSON格式
        try:
            if classes:
                json.loads(classes)
            if tags:
                json.loads(tags)
        except json.JSONDecodeError as e:
            return jsonify({'success': False, 'error': f'JSON格式错误: {e}'}), 400

        # 安全文件名
        filename = secure_filename(file.filename)

        # 确定存储目录
        type_dir = model_type.lower()
        save_dir = os.path.join(MODELS_ROOT, type_dir)
        os.makedirs(save_dir, exist_ok=True)

        # 保存文件
        file_path = os.path.join(save_dir, filename)
        file.save(file_path)

        # 获取文件大小
        file_size = os.path.getsize(file_path)

        # 检查是否已存在同名同版本的模型
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
            existing.description = description or None
            existing.tags = tags or None
            existing.updated_at = datetime.now()
            existing.save()
            model = existing
        else:
            # 创建新记录
            now = datetime.now()
            model = MLModel.create(
                name=name,
                filename=filename,
                file_path=file_path,
                file_size=file_size,
                model_type=model_type,
                framework=framework,
                input_shape=input_shape or None,
                classes=classes or None,
                description=description or None,
                version=version,
                tags=tags or None,
                created_at=now,
                updated_at=now
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
