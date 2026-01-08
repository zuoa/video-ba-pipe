"""
模型解析器 - 将模型名称解析为完整路径
"""
import json
from typing import Dict, Any, Union

from app import logger
from app.core.database_models import MLModel


class ModelResolver:
    """模型名称解析器"""
    
    def __init__(self):
        self._cache = {}
    
    def resolve_models(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析配置中的模型引用，将 name 或 id 转换为完整路径

        支持两种格式：
        1. 列表格式（新格式，用于 yolo_detector.py）:
           {"models": [{"model_id": 1, ...}, {"model_id": 2, ...}]}
        2. 字典格式（旧格式）:
           {"models": {"person": "YOLOv8n-Person", "helmet": "YOLOv8n-Helmet"}}

        Args:
            config: 算法配置字典，可能包含 models 字段

        Returns:
            解析后的配置字典

        Example:
            输入（列表）: {"models": [{"model_id": 1, "class": 0}]}
            输出（列表）: {"models": [{"model_id": 1, "class": 0, "_model_path": "/path/to/model.pt"}]}

            输入（字典）: {"models": {"person": "YOLOv8n-Person"}, "iou": 0.45}
            输出（字典）: {"models": {"person": {"path": "/models/yolov8n.pt", "name": "YOLOv8n-Person"}}, "iou": 0.45}
        """
        if 'models' not in config:
            return config

        models_config = config['models']

        # 处理列表格式（新格式，用于模板脚本）
        if isinstance(models_config, list):
            return self._resolve_models_list(config, models_config)

        # 处理字典格式（旧格式）
        if isinstance(models_config, dict):
            return self._resolve_models_dict(config, models_config)

        logger.warning(f"[ModelResolver] models 字段格式错误，应为 list 或 dict: {type(models_config)}")
        return config

    def _resolve_models_list(self, config: Dict[str, Any], models_config: list) -> Dict[str, Any]:
        """
        解析列表格式的模型配置

        为每个模型项添加 _model_path 字段（包含模型文件路径）
        """
        resolved_models = []

        for idx, model_item in enumerate(models_config):
            if not isinstance(model_item, dict):
                logger.warning(f"[ModelResolver] 模型项 {idx} 格式错误，应为 dict: {type(model_item)}")
                resolved_models.append(model_item)
                continue

            # 获取 model_id（支持数字和字符串）
            model_id = model_item.get('model_id')
            if model_id is None:
                logger.warning(f"[ModelResolver] 模型项 {idx} 缺少 model_id")
                resolved_models.append(model_item)
                continue

            # 查询模型路径
            model_info = self._get_model_info(model_id)
            if not model_info:
                logger.error(f"[ModelResolver] 模型项 {idx} 的模型不存在: model_id={model_id}")
                resolved_models.append(model_item)
                continue

            # 复制原配置并添加路径信息
            resolved_item = model_item.copy()
            resolved_item['_model_path'] = model_info['path']
            resolved_item['_model_type'] = model_info.get('model_type', '')
            resolved_item['_model_framework'] = model_info.get('framework', '')
            resolved_models.append(resolved_item)

        # 替换配置中的 models 字段
        resolved_config = config.copy()
        resolved_config['models'] = resolved_models

        logger.info(f"[ModelResolver] 解析了 {len(resolved_models)} 个模型引用（列表格式）")

        return resolved_config

    def _resolve_models_dict(self, config: Dict[str, Any], models_config: dict) -> Dict[str, Any]:
        """
        解析字典格式的模型配置（旧格式）
        """
        resolved_models = {}

        for role, model_ref in models_config.items():
            # 支持两种格式：
            # 1. 字符串（模型名称）："person": "YOLOv8n-Person"
            # 2. 字典（已解析或带额外配置）："person": {"name": "...", "conf": 0.5}
            
            if isinstance(model_ref, str):
                model_name = model_ref
                extra_config = {}
            elif isinstance(model_ref, dict):
                model_name = model_ref.get('name')
                extra_config = {k: v for k, v in model_ref.items() if k != 'name'}
            else:
                logger.warning(f"[ModelResolver] 模型引用格式错误 (role={role}): {model_ref}")
                continue
            
            if not model_name:
                logger.warning(f"[ModelResolver] 模型名称为空 (role={role})")
                continue
            
            # 查询模型路径
            model_info = self._get_model_info(model_name)
            if not model_info:
                logger.error(f"[ModelResolver] 模型不存在: {model_name} (role={role})")
                continue
            
            # 合并配置
            resolved_models[role] = {
                'name': model_name,
                'path': model_info['path'],
                'model_type': model_info['model_type'],
                **extra_config
            }
        
        # 替换配置中的 models 字段
        resolved_config = config.copy()
        resolved_config['models'] = resolved_models
        
        logger.info(f"[ModelResolver] 解析了 {len(resolved_models)} 个模型引用")
        
        return resolved_config
    
    def _get_model_info(self, model_ref: Union[int, str]) -> Dict[str, Any]:
        """从数据库查询模型信息，支持按ID或名称查询"""
        
        cache_key = str(model_ref)
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        try:
            if isinstance(model_ref, int) or (isinstance(model_ref, str) and model_ref.isdigit()):
                model = MLModel.get_by_id(int(model_ref))
            else:
                model = MLModel.get(MLModel.name == model_ref)
            
            if not model.enabled:
                logger.warning(f"[ModelResolver] 模型已禁用: {model_ref}")
                return None
            
            model_info = {
                'path': model.file_path,
                'model_type': model.model_type,
                'framework': model.framework,
                'input_shape': model.input_shape,
                'classes': model.classes_dict
            }
            
            self._cache[cache_key] = model_info
            model.increment_usage()
            
            return model_info
            
        except MLModel.DoesNotExist:
            logger.error(f"[ModelResolver] 模型不存在: {model_ref}")
            return None
        except Exception as e:
            logger.error(f"[ModelResolver] 查询模型失败: {e}")
            return None
    
    def clear_cache(self):
        """清空缓存"""
        self._cache.clear()


# 全局单例
_model_resolver = None


def get_model_resolver() -> ModelResolver:
    """获取模型解析器单例"""
    global _model_resolver
    if _model_resolver is None:
        _model_resolver = ModelResolver()
    return _model_resolver


def resolve_model(model_ref: Union[int, str]) -> str:
    """
    辅助函数：解析模型ID或名称为路径
    供用户脚本直接调用
    """
    resolver = get_model_resolver()
    model_info = resolver._get_model_info(model_ref)
    if not model_info:
        raise ValueError(f"模型不存在或已禁用: {model_ref}")
    return model_info['path']


def inject_model_helpers(namespace: dict):
    """
    向脚本命名空间注入模型解析辅助函数
    """
    namespace['resolve_model'] = resolve_model
    namespace['get_model_resolver'] = get_model_resolver
