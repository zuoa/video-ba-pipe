import numpy as np
from typing import Any, Dict, List

SCRIPT_METADATA = {
    "name": "函数计算器",
    "version": "v1.0",
    "description": "多目标关系计算函数，支持面积比、高度比、距离等",
    "author": "system",
    "category": "function",
    "tags": ["function", "multi-input", "calculator"],
    
    "config_schema": {
        "function_name": {
            "type": "select",
            "label": "计算函数",
            "required": True,
            "options": [
                {"value": "area_ratio", "label": "面积比"},
                {"value": "height_ratio", "label": "高度比"},
                {"value": "width_ratio", "label": "宽度比"},
                {"value": "iou_check", "label": "IOU检查"},
                {"value": "distance_check", "label": "距离检查"}
            ],
            "default": "area_ratio"
        },
        "input_a": {
            "type": "object",
            "label": "输入A配置",
            "properties": {
                "node_id": {"type": "string", "label": "节点ID"},
                "class_filter": {"type": "int_list", "label": "类别过滤", "default": []}
            }
        },
        "input_b": {
            "type": "object",
            "label": "输入B配置",
            "properties": {
                "node_id": {"type": "string", "label": "节点ID"},
                "class_filter": {"type": "int_list", "label": "类别过滤", "default": []}
            }
        },
        "threshold": {
            "type": "float",
            "label": "阈值",
            "default": 0.7,
            "min": 0.0,
            "max": 1000.0,
            "step": 0.1
        },
        "operator": {
            "type": "select",
            "label": "比较运算符",
            "options": [
                {"value": "less_than", "label": "小于"},
                {"value": "greater_than", "label": "大于"},
                {"value": "equal", "label": "等于"}
            ],
            "default": "less_than"
        }
    },
    
    "performance": {
        "timeout": 5,
        "memory_limit_mb": 128,
        "gpu_required": False,
        "estimated_time_ms": 5
    },
    
    "dependencies": [
        "numpy>=1.19.0"
    ]
}


def init(config: dict) -> Dict[str, Any]:
    from app.core.builtin_functions import BUILTIN_FUNCTIONS
    
    function_name = config.get('function_name', 'area_ratio')
    if function_name not in BUILTIN_FUNCTIONS:
        raise ValueError(f"未知的函数: {function_name}")
    
    return {
        'function_name': function_name,
        'function': BUILTIN_FUNCTIONS[function_name]
    }


def process(frame: np.ndarray, config: dict, roi_regions: list = None, 
            state: dict = None, upstream_results: dict = None) -> dict:
    from app import logger
    
    if not state or 'function' not in state:
        return {'detections': [], 'function_results': []}
    
    if not upstream_results:
        return {'detections': [], 'function_results': []}
    
    input_a_config = config.get('input_a', {})
    input_b_config = config.get('input_b', {})
    
    node_a_id = input_a_config.get('node_id')
    node_b_id = input_b_config.get('node_id')
    
    if not node_a_id or not node_b_id:
        logger.warning(f"[函数计算器] 缺少输入节点配置")
        return {'detections': [], 'function_results': []}
    
    result_a = upstream_results.get(node_a_id, {})
    result_b = upstream_results.get(node_b_id, {})
    
    detections_a = result_a.get('detections', [])
    detections_b = result_b.get('detections', [])
    
    class_filter_a = input_a_config.get('class_filter', [])
    class_filter_b = input_b_config.get('class_filter', [])
    
    if class_filter_a:
        detections_a = [d for d in detections_a if d.get('class') in class_filter_a]
    if class_filter_b:
        detections_b = [d for d in detections_b if d.get('class') in class_filter_b]
    
    if not detections_a or not detections_b:
        return {'detections': [], 'function_results': []}
    
    function_config = {
        'threshold': config.get('threshold', 0.7),
        'operator': config.get('operator', 'less_than')
    }
    
    func = state['function']
    results = func(detections_a, detections_b, function_config)
    
    all_detections = []
    for r in results:
        all_detections.append(r['object_a'])
        all_detections.append(r['object_b'])
    
    return {
        'detections': all_detections,
        'function_results': results,
        'metadata': {
            'function_name': state['function_name'],
            'matched_count': len(results)
        }
    }


def cleanup(state: dict) -> None:
    pass

