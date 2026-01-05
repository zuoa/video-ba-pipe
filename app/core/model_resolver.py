"""
模型解析器 - 供脚本直接调用的辅助函数

脚本可以直接使用这些函数来引用模型管理中的模型，无需通过配置传递。

使用示例：
    from app.core.model_resolver import resolve_model, get_model_info
    
    # 通过ID获取模型路径
    model_path = resolve_model(1)
    
    # 通过名称获取模型路径
    model_path = resolve_model('yolov8n-person')
    
    # 获取完整的模型信息
    model_info = get_model_info(1)
    print(model_info['path'])
    print(model_info['classes'])
"""
from typing import Union, Optional, Dict, List
from app import logger
from app.core.database_models import MLModel


def resolve_model(identifier: Union[int, str]) -> str:
    """
    解析模型引用，返回模型文件路径
    
    Args:
        identifier: 模型ID（整数）或模型名称（字符串）
        
    Returns:
        模型文件的完整路径
        
    Raises:
        ValueError: 如果模型不存在
        
    Examples:
        >>> path = resolve_model(1)
        '/path/to/models/yolo/yolov8n.pt'
        
        >>> path = resolve_model('yolov8n-person')
        '/path/to/models/yolo/yolov8n.pt'
    """
    try:
        if isinstance(identifier, int):
            # 通过ID查询
            model = MLModel.get_by_id(identifier)
        else:
            # 通过名称查询
            model = MLModel.get(MLModel.name == identifier)
        
        logger.debug(f"解析模型 {identifier} -> {model.file_path}")
        return model.file_path
        
    except MLModel.DoesNotExist:
        error_msg = f"模型不存在: {identifier}"
        logger.error(error_msg)
        raise ValueError(error_msg)


def get_model_info(identifier: Union[int, str]) -> Dict:
    """
    获取模型的完整信息
    
    Args:
        identifier: 模型ID或名称
        
    Returns:
        模型信息字典，包含：
        - id: 模型ID
        - name: 模型名称
        - path: 文件路径
        - type: 模型类型（YOLO, ONNX等）
        - framework: 框架（ultralytics等）
        - classes: 类别字典
        - input_shape: 输入尺寸
        - description: 描述
        
    Examples:
        >>> info = get_model_info(1)
        >>> print(info['path'])
        >>> print(info['classes'])
    """
    try:
        if isinstance(identifier, int):
            model = MLModel.get_by_id(identifier)
        else:
            model = MLModel.get(MLModel.name == identifier)
        
        return {
            'id': model.id,
            'name': model.name,
            'path': model.file_path,
            'filename': model.filename,
            'type': model.model_type,
            'framework': model.framework,
            'classes': model.classes_dict,
            'input_shape': model.input_shape,
            'description': model.description,
            'version': model.version,
            'tags': model.tags_list
        }
        
    except MLModel.DoesNotExist:
        error_msg = f"模型不存在: {identifier}"
        logger.error(error_msg)
        raise ValueError(error_msg)


def list_available_models(model_type: Optional[str] = None, 
                         framework: Optional[str] = None,
                         enabled_only: bool = True) -> List[Dict]:
    """
    列出可用的模型
    
    Args:
        model_type: 模型类型筛选（YOLO, ONNX等），None表示所有类型
        framework: 框架筛选（ultralytics等），None表示所有框架
        enabled_only: 是否只返回启用的模型
        
    Returns:
        模型信息列表
        
    Examples:
        >>> models = list_available_models(model_type='YOLO')
        >>> for m in models:
        >>>     print(f"{m['id']}: {m['name']}")
    """
    query = MLModel.select()
    
    if model_type:
        query = query.where(MLModel.model_type == model_type)
    
    if framework:
        query = query.where(MLModel.framework == framework)
    
    if enabled_only:
        query = query.where(MLModel.enabled == True)
    
    query = query.order_by(MLModel.created_at.desc())
    
    return [
        {
            'id': m.id,
            'name': m.name,
            'path': m.file_path,
            'type': m.model_type,
            'framework': m.framework,
            'description': m.description
        }
        for m in query
    ]


def resolve_models(identifiers: List[Union[int, str]]) -> List[str]:
    """
    批量解析多个模型
    
    Args:
        identifiers: 模型ID或名称列表
        
    Returns:
        模型路径列表
        
    Examples:
        >>> paths = resolve_models([1, 2, 'yolov8n-person'])
        ['/path/to/model1.pt', '/path/to/model2.pt', '/path/to/model3.pt']
    """
    paths = []
    for identifier in identifiers:
        try:
            path = resolve_model(identifier)
            paths.append(path)
        except ValueError as e:
            logger.warning(f"跳过无效模型引用: {identifier} - {e}")
    
    return paths


# ==================== 便捷别名 ====================

# 提供简短的别名
model = resolve_model  # model(1) 或 model('name')
models = resolve_models  # models([1, 2, 3])
model_info = get_model_info  # model_info(1)
list_models = list_available_models  # list_models()


# ==================== 脚本全局变量注入 ====================

def inject_model_helpers(script_globals: dict):
    """
    将模型解析函数注入到脚本的全局命名空间
    
    这样脚本中可以直接使用 resolve_model() 等函数，无需导入
    
    Args:
        script_globals: 脚本的 globals() 字典
    """
    script_globals['resolve_model'] = resolve_model
    script_globals['get_model_info'] = get_model_info
    script_globals['list_available_models'] = list_available_models
    script_globals['resolve_models'] = resolve_models
    
    # 简短别名
    script_globals['model'] = model
    script_globals['models'] = models
    script_globals['model_info'] = model_info
    script_globals['list_models'] = list_models

