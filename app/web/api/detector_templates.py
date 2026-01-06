"""
检测器模板API
"""
import json
import os
from datetime import datetime
from flask import Blueprint, request, jsonify

from app.core.database_models import DetectorTemplate, db
from app.core.script_loader import get_script_loader
from app import logger

# 创建蓝图
detector_templates_bp = Blueprint('detector_templates', __name__, url_prefix='/api/detector-templates')


def serialize_template(template):
    """序列化模板对象"""
    return {
        'id': template.id,
        'name': template.name,
        'description': template.description or '',
        'script_path': template.script_path,
        'config_preset': template.config_dict,
        'category': template.category,
        'tags_list': template.tags_list,  # 注意：前端使用 tags_list
        'is_system': template.is_system,
        'is_enabled': template.is_enabled if hasattr(template, 'is_enabled') else True,
        'icon': template.icon if hasattr(template, 'icon') else None,
        'created_at': template.created_at.isoformat() if template.created_at else None,
        'created_by': template.created_by,
        'usage_count': template.usage_count if hasattr(template, 'usage_count') else 0
    }


@detector_templates_bp.route('/', methods=['GET'])
def list_templates():
    """
    列出所有检测器模板
    
    Query参数:
        - is_system: 是否只列出系统模板 (true/false)
        - category: 类别过滤
    """
    try:
        is_system = request.args.get('is_system', '').lower() == 'true'
        category = request.args.get('category')
        
        query = DetectorTemplate.select()
        
        if is_system:
            query = query.where(DetectorTemplate.is_system == True)
        
        if category:
            query = query.where(DetectorTemplate.category == category)
        
        query = query.order_by(DetectorTemplate.is_system.desc(), DetectorTemplate.created_at.desc())
        
        templates = [serialize_template(t) for t in query]
        
        return jsonify({
            'success': True,
            'templates': templates
        })
        
    except Exception as e:
        logger.error(f"列出模板失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@detector_templates_bp.route('/<int:template_id>', methods=['GET'])
def get_template(template_id):
    """获取单个模板详情"""
    try:
        template = DetectorTemplate.get_by_id(template_id)
        
        # 同时获取脚本的SCRIPT_METADATA
        loader = get_script_loader()
        try:
            module, metadata = loader.load(template.script_path)
            script_metadata = metadata
        except Exception as e:
            logger.warning(f"加载脚本元数据失败: {e}")
            script_metadata = {}
        
        result = serialize_template(template)
        result['script_metadata'] = script_metadata
        
        return jsonify({
            'success': True,
            'template': result
        })
        
    except DetectorTemplate.DoesNotExist:
        return jsonify({
            'success': False,
            'error': '模板不存在'
        }), 404
    except Exception as e:
        logger.error(f"获取模板失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@detector_templates_bp.route('/', methods=['POST'])
def create_template():
    """
    创建检测器模板
    
    Request body:
        {
            "name": "自定义检测器",
            "description": "描述",
            "script_path": "detectors/my_detector.py",
            "config_preset": {...},
            "category": "detection",
            "tags": ["custom"],
            "is_system": false
        }
    """
    try:
        data = request.get_json()
        
        # 验证必填字段
        required_fields = ['name', 'script_path']
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    'success': False,
                    'error': f'缺少必填字段: {field}'
                }), 400
        
        # 验证脚本是否存在
        loader = get_script_loader()
        script_path = data['script_path']
        abs_path = loader.resolve_path(script_path)
        
        if not os.path.exists(abs_path):
            return jsonify({
                'success': False,
                'error': f'脚本不存在: {script_path}'
            }), 400
        
        # 创建模板
        template = DetectorTemplate.create(
            name=data['name'],
            description=data.get('description', ''),
            script_path=script_path,
            config_preset=json.dumps(data.get('config_preset', {})),
            category=data.get('category', 'detection'),
            tags=json.dumps(data.get('tags', [])),
            is_system=data.get('is_system', False),
            created_at=datetime.now(),
            created_by=data.get('created_by', 'user')
        )
        
        logger.info(f"创建检测器模板: {template.name}")
        
        return jsonify({
            'success': True,
            'template': serialize_template(template)
        })
        
    except Exception as e:
        logger.error(f"创建模板失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@detector_templates_bp.route('/<int:template_id>', methods=['PUT'])
def update_template(template_id):
    """更新检测器模板"""
    try:
        template = DetectorTemplate.get_by_id(template_id)
        data = request.get_json()
        
        # 更新字段
        if 'name' in data:
            template.name = data['name']
        if 'description' in data:
            template.description = data['description']
        if 'script_path' in data:
            template.script_path = data['script_path']
        if 'config_preset' in data:
            template.config_preset = json.dumps(data['config_preset'])
        if 'category' in data:
            template.category = data['category']
        if 'tags' in data:
            template.tags = json.dumps(data['tags'])
        
        template.save()
        
        logger.info(f"更新检测器模板: {template.name}")
        
        return jsonify({
            'success': True,
            'template': serialize_template(template)
        })
        
    except DetectorTemplate.DoesNotExist:
        return jsonify({
            'success': False,
            'error': '模板不存在'
        }), 404
    except Exception as e:
        logger.error(f"更新模板失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@detector_templates_bp.route('/<int:template_id>', methods=['DELETE'])
def delete_template(template_id):
    """删除检测器模板"""
    try:
        template = DetectorTemplate.get_by_id(template_id)
        
        # 检查是否为系统模板
        if template.is_system:
            return jsonify({
                'success': False,
                'error': '系统模板不能删除'
            }), 403
        
        template_name = template.name
        template.delete_instance()
        
        logger.info(f"删除检测器模板: {template_name}")
        
        return jsonify({
            'success': True,
            'message': '模板已删除'
        })
        
    except DetectorTemplate.DoesNotExist:
        return jsonify({
            'success': False,
            'error': '模板不存在'
        }), 404
    except Exception as e:
        logger.error(f"删除模板失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@detector_templates_bp.route('/<int:template_id>/instantiate', methods=['POST'])
def instantiate_template(template_id):
    """
    根据模板实例化算法配置
    
    返回一个完整的算法配置对象，可以直接用于创建算法
    """
    try:
        template = DetectorTemplate.get_by_id(template_id)
        data = request.get_json()
        
        # 获取用户自定义配置
        user_config = data.get('config', {})
        
        # 合并预设配置和用户配置
        final_config = {**template.config_dict, **user_config}
        
        # 构建算法配置
        algorithm_config = {
            'name': data.get('name', f"{template.name}-实例"),
            'script_path': template.script_path,
            'script_config': final_config,
            'detector_template_id': template.id,
            # 从请求或使用默认值
            'interval_seconds': data.get('interval_seconds', 1.0),
            'runtime_timeout': data.get('runtime_timeout', 30),
            'memory_limit_mb': data.get('memory_limit_mb', 512),
            'label_name': data.get('label_name', 'Object'),
            'label_color': data.get('label_color', '#FF0000'),
            # 时间窗口配置
            'enable_window_check': data.get('enable_window_check', False),
            'window_size': data.get('window_size', 30),
            'window_mode': data.get('window_mode', 'ratio'),
            'window_threshold': data.get('window_threshold', 0.3)
        }
        
        return jsonify({
            'success': True,
            'algorithm_config': algorithm_config
        })
        
    except DetectorTemplate.DoesNotExist:
        return jsonify({
            'success': False,
            'error': '模板不存在'
        }), 404
    except Exception as e:
        logger.error(f"实例化模板失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@detector_templates_bp.route('/init-system-templates', methods=['POST'])
def init_system_templates():
    """
    初始化系统检测器模板
    
    创建默认的系统检测器模板（如果不存在）
    """
    try:
        # 系统模板列表
        system_templates = [
            {
                'name': 'YOLO通用检测器',
                'description': '基于YOLOv8的通用目标检测，支持80类COCO数据集对象。适用于人员、车辆、物品等常见目标检测。',
                'script_path': 'templates/yolo_detector.py',
                'config_preset': {
                    'model_ids': [],  # 需要用户在模型库中选择模型
                    'confidence': 0.6,
                    'iou_threshold': 0.5,
                    'class_filter': None,  # None表示检测所有类别
                    'enable_multimodel': False,  # 是否启用多模型级联
                    'roi_enabled': True  # 支持ROI
                },
                'category': 'detection',
                'tags': ['yolo', 'detection', '通用', '推荐'],
                'icon': '🎯',
            },
            {
                'name': 'YOLO人员检测器',
                'description': '专门用于人员检测（COCO class 0），置信度较高，减少误报。适用于人流统计、区域入侵检测等场景。',
                'script_path': 'templates/yolo_detector.py',
                'config_preset': {
                    'model_ids': [],
                    'confidence': 0.7,
                    'iou_threshold': 0.5,
                    'class_filter': 0,  # person
                    'enable_multimodel': False,
                    'roi_enabled': True
                },
                'category': 'detection',
                'tags': ['yolo', 'person', '人员检测', '推荐'],
                'icon': '👤',
            },
            {
                'name': 'YOLO车辆检测器',
                'description': '检测常见车辆类型（汽车、摩托车、公交车、卡车），适用于停车场、道路监控等场景。',
                'script_path': 'templates/yolo_detector.py',
                'config_preset': {
                    'model_ids': [],
                    'confidence': 0.6,
                    'iou_threshold': 0.5,
                    'class_filter': [2, 3, 5, 7],  # car, motorcycle, bus, truck
                    'enable_multimodel': False,
                    'roi_enabled': True
                },
                'category': 'detection',
                'tags': ['yolo', 'vehicle', '车辆检测'],
                'icon': '🚗',
            },
            {
                'name': 'YOLO多级联检测器',
                'description': '支持多模型级联检测，例如先检测头部再检测手机。适用于复杂的多阶段检测任务。',
                'script_path': 'templates/yolo_detector.py',
                'config_preset': {
                    'model_ids': [],  # 需要配置多个模型
                    'confidence': 0.6,
                    'iou_threshold': 0.5,
                    'enable_multimodel': True,  # 启用多模型级联
                    'multimodel_iou_threshold': 0.5,
                    'roi_enabled': True
                },
                'category': 'detection',
                'tags': ['yolo', 'multimodel', '多级联', '高级'],
                'icon': '🔗',
            },
            {
                'name': '简单YOLO检测器',
                'description': '简化版的YOLO检测器，适合初学者和快速原型开发。功能简单，易于理解和修改。',
                'script_path': 'templates/simple_yolo_detector.py',
                'config_preset': {
                    'model_ids': [],
                    'confidence': 0.6,
                    'class_filter': None
                },
                'category': 'detection',
                'tags': ['yolo', 'simple', '简单', '入门'],
                'icon': '📦',
            },
            {
                'name': '占位检测器',
                'description': '演示用的占位检测器，展示脚本的基本结构和接口。可作为开发自定义检测器的起点。',
                'script_path': 'templates/placeholder_detector.py',
                'config_preset': {
                    'model_ids': [],
                    'confidence': 0.6
                },
                'category': 'custom',
                'tags': ['placeholder', '示例', '模板'],
                'icon': '📝',
            }
        ]
        
        created_count = 0
        updated_count = 0
        errors = []
        
        for template_data in system_templates:
            try:
                # 使用 get_or_create 避免重复
                template, created = DetectorTemplate.get_or_create(
                    name=template_data['name'],
                    defaults={
                        'description': template_data['description'],
                        'script_path': template_data['script_path'],
                        'config_preset': json.dumps(template_data['config_preset']),
                        'category': template_data['category'],
                        'tags': json.dumps(template_data['tags']),
                        'is_system': True,
                        'is_enabled': True,
                        'icon': template_data.get('icon'),
                        'created_at': datetime.now(),
                        'updated_at': datetime.now(),
                        'created_by': 'system',
                        'usage_count': 0
                    }
                )
                
                if created:
                    created_count += 1
                    logger.info(f"创建系统模板: {template.name}")
                else:
                    # 更新已存在的模板
                    template.description = template_data['description']
                    template.script_path = template_data['script_path']
                    template.config_preset = json.dumps(template_data['config_preset'])
                    template.category = template_data['category']
                    template.tags = json.dumps(template_data['tags'])
                    template.icon = template_data.get('icon')
                    template.updated_at = datetime.now()
                    template.save()
                    updated_count += 1
                    logger.info(f"更新系统模板: {template.name}")
                    
            except Exception as e:
                error_msg = f"处理模板 '{template_data['name']}' 失败: {e}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        return jsonify({
            'success': True,
            'message': '系统模板初始化完成',
            'created': created_count,
            'updated': updated_count,
            'errors': errors,
            'total': len(system_templates)
        })
        
    except Exception as e:
        logger.error(f"初始化系统模板失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@detector_templates_bp.route('/script-config/<path:script_path>', methods=['GET'])
def get_script_config_schema(script_path):
    """
    获取脚本的配置模式
    
    返回脚本的 SCRIPT_METADATA.config_schema
    """
    try:
        loader = get_script_loader()
        
        # 加载脚本模块
        try:
            module, metadata = loader.load(script_path)
        except Exception as e:
            logger.error(f"加载脚本失败: {e}")
            return jsonify({
                'success': False,
                'error': f'加载脚本失败: {str(e)}'
            }), 400
        
        # 获取配置模式
        config_schema = metadata.get('config_schema', {})
        
        # 返回完整的元数据
        return jsonify({
            'success': True,
            'config_schema': config_schema,
            'metadata': {
                'name': metadata.get('name', ''),
                'version': metadata.get('version', ''),
                'description': metadata.get('description', ''),
                'author': metadata.get('author', ''),
                'performance': metadata.get('performance', {}),
                'output_format': metadata.get('output_format', {})
            }
        })
        
    except Exception as e:
        logger.error(f"获取脚本配置模式失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def register_detector_templates_api(app):
    """注册检测器模板API到Flask应用"""
    app.register_blueprint(detector_templates_bp)

