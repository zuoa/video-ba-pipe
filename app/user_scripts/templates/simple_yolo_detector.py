"""
简单YOLO检测器脚本模板

单模型检测，适合快速开始和简单场景。
如果需要多模型并行检测，请使用 yolo_detector.py

作者: system
版本: v1.0
更新: 2025-01-06
"""

import cv2
import numpy as np
from ultralytics import YOLO
from typing import Any, Dict

# ==================== 脚本元数据 ====================

SCRIPT_METADATA = {
    "name": "简单YOLO检测",
    "version": "v1.0",
    "description": "单模型YOLO检测，快速简单",
    "author": "system",
    "category": "detection",
    "tags": ["yolo", "simple", "single-model"],
    
    "config_schema": {
        "model_id": {
            "type": "model_select",
            "label": "检测模型",
            "required": True,
            "filters": {
                "model_type": ["YOLO", "ONNX"],
                "framework": ["ultralytics"]
            }
        },
        "class_filter": {
            "type": "int_list",
            "label": "类别过滤（留空=全部类别）",
            "default": [],
            "description": "例如: [0, 1, 2] 只检测person, bicycle, car"
        },
        "confidence": {
            "type": "float",
            "label": "置信度阈值",
            "default": 0.6,
            "min": 0.0,
            "max": 1.0,
            "step": 0.05
        }
    },
    
    "performance": {
        "timeout": 10,
        "memory_limit_mb": 256,
        "gpu_required": False,
        "estimated_time_ms": 30
    }
}


# ==================== 生命周期函数 ====================

def init(config: dict) -> Dict[str, Any]:
    """初始化 - 加载模型"""
    from app import logger
    
    model_id = config.get('model_id')
    if not model_id:
        raise ValueError("缺少 model_id 配置")
    
    model_path = resolve_model(model_id)
    logger.info(f"[Simple YOLO] 加载模型: {model_path}")
    
    model = YOLO(model_path)
    
    return {'model': model, 'model_path': model_path}


def process(frame: np.ndarray, config: dict, roi_regions: list = None, state: dict = None) -> dict:
    """执行检测"""
    from app import logger
    
    if not state or 'model' not in state:
        return {'detections': []}
    
    model = state['model']
    confidence = config.get('confidence', 0.6)
    class_filter = config.get('class_filter', [])
    
    # 转换为BGR
    frame_bgr = cv2.cvtColor(frame.copy(), cv2.COLOR_RGB2BGR)
    
    # 执行检测
    kwargs = {'save': False, 'conf': confidence, 'verbose': False}
    if class_filter:
        kwargs['classes'] = class_filter
    
    results = model.predict(frame_bgr, **kwargs)
    
    # 转换结果
    detections = []
    if results and len(results) > 0:
        for det in results[0].boxes.data.tolist():
            x1, y1, x2, y2, conf, cls = det
            detections.append({
                'box': (x1, y1, x2, y2),
                'label': results[0].names[int(cls)],
                'label_name': config.get('label_name', results[0].names[int(cls)]),
                'class': int(cls),
                'confidence': float(conf)
            })
    
    return {
        'detections': detections,
        'metadata': {
            'model_path': state['model_path'],
            'total_detections': len(detections)
        }
    }


def cleanup(state: dict) -> None:
    """清理资源"""
    if state and 'model' in state:
        model = state['model']
        if hasattr(model, 'close'):
            model.close()

