"""
YOLO目标检测器脚本模板

这是一个系统级脚本模板，替代原有的 target_detection 插件。
支持单模型和多模型检测，支持IOU合并和多阶段检测。

作者: system
版本: v2.0
更新: 2025-01-06
"""

import cv2
import numpy as np
from ultralytics import YOLO
from typing import Any, Dict, List

# ==================== 脚本元数据 ====================

SCRIPT_METADATA = {
    # 基础信息
    "name": "YOLO目标检测",
    "version": "v2.0",
    "description": "基于YOLO的通用目标检测，支持多模型并行和IOU合并",
    "author": "system",
    "category": "detection",
    "tags": ["yolo", "object-detection", "multi-model", "realtime"],
    
    # 配置模式定义
    "config_schema": {
        "models": {
            "type": "model_list",
            "label": "检测模型",
            "required": True,
            "multiple": True,
            "description": "选择一个或多个YOLO模型进行检测",
            "filters": {
                "model_type": ["YOLO", "ONNX"],
                "framework": ["ultralytics"]
            },
            "item_schema": {
                "model_id": {"type": "model_select", "label": "模型"},
                "class": {"type": "int", "label": "类别索引", "default": 0, "min": 0},
                "confidence": {"type": "float", "label": "置信度", "default": 0.6, "min": 0.0, "max": 1.0, "step": 0.05},
                "label_name": {"type": "string", "label": "标签名称", "default": "Object"},
                "label_color": {"type": "color", "label": "标签颜色", "default": "#FF0000"},
                "expand_width": {"type": "float", "label": "宽度扩展", "default": 0.1, "min": 0.0, "max": 1.0, "step": 0.05},
                "expand_height": {"type": "float", "label": "高度扩展", "default": 0.1, "min": 0.0, "max": 1.0, "step": 0.05}
            }
        },
        "iou_threshold": {
            "type": "float",
            "label": "IOU阈值（多模型合并）",
            "default": 0.5,
            "min": 0.0,
            "max": 1.0,
            "step": 0.05,
            "description": "多模型检测结果合并的IOU阈值，仅在使用2个以上模型时生效",
            "condition": "len(models) > 1"
        },
        "roi_mode": {
            "type": "select",
            "label": "ROI应用模式",
            "default": "post_filter",
            "options": [
                {"value": "post_filter", "label": "后过滤（推荐）"},
                {"value": "pre_mask", "label": "前掩码（更快）"}
            ],
            "description": "post_filter: 全帧检测后过滤，精度高；pre_mask: 检测前应用掩码，速度快"
        }
    },
    
    # 性能配置
    "performance": {
        "timeout": 30,
        "memory_limit_mb": 512,
        "gpu_required": False,
        "estimated_time_ms": 50
    },
    
    # 输出配置
    "output_format": {
        "detections": True,
        "metadata": True,
        "visualization": True,
        "stages": True  # 支持多阶段检测结果
    }
}


# ==================== 辅助函数 ====================

def create_roi_mask(frame_shape: tuple, roi_regions: list) -> np.ndarray:
    """创建ROI掩码"""
    if not roi_regions:
        return np.ones((frame_shape[0], frame_shape[1]), dtype=np.uint8) * 255
    
    mask = np.zeros((frame_shape[0], frame_shape[1]), dtype=np.uint8)
    for region in roi_regions:
        points = region.get('points', [])
        if len(points) >= 3:
            pts = np.array(points, dtype=np.int32)
            cv2.fillPoly(mask, [pts], 255)
    return mask


def apply_roi_mask(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """应用ROI掩码到图像"""
    return cv2.bitwise_and(frame, frame, mask=mask)


def filter_detections_by_roi(detections: list, mask: np.ndarray) -> list:
    """根据ROI掩码过滤检测结果"""
    if mask is None:
        return detections
    
    filtered = []
    for det in detections:
        box = det.get('box', [])
        if len(box) >= 4:
            center_x = int((box[0] + box[2]) / 2)
            center_y = int((box[1] + box[3]) / 2)
            if (0 <= center_y < mask.shape[0] and 
                0 <= center_x < mask.shape[1] and 
                mask[center_y, center_x] > 0):
                filtered.append(det)
    return filtered


def calculate_iou(box1, box2):
    """计算两个边界框的IOU"""
    x1_min, y1_min, x1_max, y1_max = box1
    x2_min, y2_min, x2_max, y2_max = box2
    
    # 计算交集
    inter_x_min = max(x1_min, x2_min)
    inter_y_min = max(y1_min, y2_min)
    inter_x_max = min(x1_max, x2_max)
    inter_y_max = min(y1_max, y2_max)
    
    if inter_x_max < inter_x_min or inter_y_max < inter_y_min:
        return 0.0
    
    inter_area = (inter_x_max - inter_x_min) * (inter_y_max - inter_y_min)
    box1_area = (x1_max - x1_min) * (y1_max - y1_min)
    box2_area = (x2_max - x2_min) * (y2_max - y2_min)
    union_area = box1_area + box2_area - inter_area
    
    return inter_area / union_area if union_area > 0 else 0.0


def find_multimodel_groups(stages_results: dict, iou_threshold: float = 0.5) -> list:
    """
    查找被多个模型共同检测到的目标组
    
    Args:
        stages_results: {model_name: {'result': YOLO_result, 'model_config': config}}
        iou_threshold: IOU阈值
        
    Returns:
        检测组列表，每组包含多个模型的检测结果
    """
    if len(stages_results) < 2:
        return []
    
    # 提取所有检测
    all_detections = []
    for model_name, data in stages_results.items():
        result = data['result']
        model_config = data['model_config']
        
        for box in result.boxes:
            xyxy = box.xyxy[0].tolist()
            # 应用扩展
            expand_w = model_config.get('expand_width', 0.1)
            expand_h = model_config.get('expand_height', 0.1)
            
            w = xyxy[2] - xyxy[0]
            h = xyxy[3] - xyxy[1]
            
            expanded_box = [
                xyxy[0] - w * expand_w,
                xyxy[1] - h * expand_h,
                xyxy[2] + w * expand_w,
                xyxy[3] + h * expand_h
            ]
            
            all_detections.append({
                'model_name': model_name,
                'bbox': expanded_box,
                'original_bbox': xyxy,
                'class': int(box.cls[0]),
                'class_name': result.names[int(box.cls[0])],
                'confidence': float(box.conf[0])
            })
    
    # 分组逻辑：找出IOU>阈值且来自不同模型的检测
    groups = []
    used_indices = set()
    
    for i, det1 in enumerate(all_detections):
        if i in used_indices:
            continue
        
        group = [det1]
        models_in_group = {det1['model_name']}
        group_indices = {i}
        
        for j, det2 in enumerate(all_detections):
            if j <= i or j in used_indices:
                continue
            
            # 必须来自不同模型
            if det2['model_name'] in models_in_group:
                continue
            
            # 计算与组内所有检测的IOU
            ious = [calculate_iou(det1['bbox'], det2['bbox']) for det1 in group]
            if any(iou >= iou_threshold for iou in ious):
                group.append(det2)
                models_in_group.add(det2['model_name'])
                group_indices.add(j)
        
        # 只有被多个模型检测到才算有效组
        if len(group) >= 2:
            groups.append(group)
            used_indices.update(group_indices)
    
    return groups


# ==================== 生命周期函数 ====================

def init(config: dict) -> Dict[str, Any]:
    """
    初始化函数 - 加载YOLO模型
    
    Args:
        config: 完整配置字典
        
    Returns:
        state: 包含加载的模型和配置
    """
    import time
    from app import logger
    
    logger.info(f"[YOLO Detector] 开始初始化...")
    
    models_config = config.get('models', [])
    if not models_config:
        logger.warning("[YOLO Detector] 没有配置模型")
        return {'models': [], 'initialized_at': time.time()}
    
    loaded_models = []
    for model_cfg in models_config:
        try:
            # 使用模型解析器获取模型路径
            model_id = model_cfg.get('model_id')
            if model_id:
                model_path = resolve_model(model_id)
            else:
                model_path = model_cfg.get('path')
            
            if not model_path:
                logger.error(f"[YOLO Detector] 模型配置缺少 model_id 或 path")
                continue
            
            logger.info(f"[YOLO Detector] 加载模型: {model_path}")
            model = YOLO(model_path)
            
            loaded_models.append({
                'model': model,
                'config': model_cfg,
                'path': model_path
            })
            
            logger.info(f"[YOLO Detector] 模型加载成功: {model_cfg.get('label_name', 'Object')}")
            
        except Exception as e:
            logger.error(f"[YOLO Detector] 模型加载失败: {e}")
            import traceback
            traceback.print_exc()
    
    logger.info(f"[YOLO Detector] 初始化完成，共加载 {len(loaded_models)} 个模型")
    
    return {
        'models': loaded_models,
        'initialized_at': time.time()
    }


def process(frame: np.ndarray, 
            config: dict, 
            roi_regions: list = None,
            state: dict = None) -> dict:
    """
    核心处理函数 - 执行YOLO检测
    
    Args:
        frame: RGB格式的视频帧
        config: 配置字典
        roi_regions: ROI区域配置
        state: init()返回的状态
        
    Returns:
        检测结果字典
    """
    from app import logger
    import time
    
    start_time = time.time()
    
    if state is None or not state.get('models'):
        logger.warning("[YOLO Detector] 模型未初始化")
        return {'detections': [], 'metadata': {'error': 'Models not initialized'}}
    
    # 转换颜色空间（YOLO需要BGR）
    frame_bgr = cv2.cvtColor(frame.copy(), cv2.COLOR_RGB2BGR)
    
    # 创建ROI掩码
    roi_mask = create_roi_mask(frame_bgr.shape, roi_regions) if roi_regions else None
    
    # ROI应用模式
    roi_mode = config.get('roi_mode', 'post_filter')
    
    if roi_mode == 'pre_mask' and roi_mask is not None:
        frame_to_detect = apply_roi_mask(frame_bgr, roi_mask)
        logger.debug("[YOLO Detector] 使用pre_mask模式")
    else:
        frame_to_detect = frame_bgr
    
    # 执行多模型检测
    stages_results = {}
    for model_info in state['models']:
        model = model_info['model']
        model_cfg = model_info['config']
        
        try:
            conf_thresh = model_cfg.get('confidence', 0.6)
            class_filter = [model_cfg.get('class', 0)]
            
            results = model.predict(
                frame_to_detect, 
                save=False, 
                classes=class_filter, 
                conf=conf_thresh,
                verbose=False
            )
            
            if results and len(results) > 0:
                model_name = model_cfg.get('label_name', f"Model{len(stages_results)}")
                stages_results[model_name] = {
                    'result': results[0],
                    'model_config': model_cfg
                }
                logger.debug(f"[YOLO Detector] {model_name} 检测到 {len(results[0].boxes)} 个目标")
                
        except Exception as e:
            logger.error(f"[YOLO Detector] 模型推理失败: {e}")
    
    # 处理检测结果
    detections = []
    
    if len(stages_results) < 2:
        # 单模型：直接返回结果
        if stages_results:
            first_model_name = list(stages_results.keys())[0]
            first_model_data = stages_results[first_model_name]
            first_result = first_model_data['result']
            first_config = first_model_data['model_config']
            
            for det in first_result.boxes.data.tolist():
                x1, y1, x2, y2, conf, cls = det
                detections.append({
                    'box': (x1, y1, x2, y2),
                    'label_name': first_config.get('label_name', 'Object'),
                    'label_color': first_config.get('label_color', '#FF0000'),
                    'class': int(cls),
                    'confidence': float(conf)
                })
    else:
        # 多模型：IOU合并
        iou_threshold = config.get('iou_threshold', 0.5)
        detection_groups = find_multimodel_groups(stages_results, iou_threshold)
        
        logger.info(f"[YOLO Detector] 发现 {len(detection_groups)} 个目标组")
        
        for group in detection_groups:
            # 计算外接矩形
            x_min = min(d['bbox'][0] for d in group)
            y_min = min(d['bbox'][1] for d in group)
            x_max = max(d['bbox'][2] for d in group)
            y_max = max(d['bbox'][3] for d in group)
            
            # 构建stages信息
            stages_info = []
            for d in group:
                model_config = stages_results[d['model_name']]['model_config']
                stages_info.append({
                    'model_name': d['model_name'],
                    'box': tuple(d['original_bbox']),
                    'label_name': model_config.get('label_name', 'Object'),
                    'label_color': model_config.get('label_color', '#00FF00'),
                    'class': d['class'],
                    'confidence': d['confidence']
                })
            
            detections.append({
                'box': (x_min, y_min, x_max, y_max),
                'label_name': config.get('label_name', 'Object'),
                'label_color': config.get('label_color', '#FF0000'),
                'class': 0,
                'confidence': np.mean([d['confidence'] for d in group]),
                'stages': stages_info
            })
    
    # ROI后过滤
    if roi_mode == 'post_filter' and roi_mask is not None and len(detections) > 0:
        original_count = len(detections)
        detections = filter_detections_by_roi(detections, roi_mask)
        filtered_count = original_count - len(detections)
        if filtered_count > 0:
            logger.debug(f"[YOLO Detector] ROI过滤移除 {filtered_count} 个检测")
    
    # 计算处理时间
    processing_time = (time.time() - start_time) * 1000
    
    return {
        'detections': detections,
        'metadata': {
            'model_count': len(state['models']),
            'inference_time_ms': processing_time,
            'total_detections': len(detections),
            'roi_mode': roi_mode
        }
    }


def cleanup(state: dict) -> None:
    """
    清理函数 - 释放模型资源
    
    Args:
        state: init()返回的状态
    """
    from app import logger
    
    if state and 'models' in state:
        for model_info in state['models']:
            model = model_info.get('model')
            if model and hasattr(model, 'close'):
                try:
                    model.close()
                except:
                    pass
        
        logger.info(f"[YOLO Detector] 清理完成，释放 {len(state['models'])} 个模型")
