"""
YOLO多模型检测器 - 高级功能

支持多个YOLO模型并行检测，通过IOU合并确保多模型共同确认目标。
适合需要高精度检测或多阶段检测的场景。

使用场景：
- 需要多个模型共同确认（减少误报）
- 多阶段检测（先检测人，再检测安全帽）
- 复杂场景下的精确检测

作者: system
版本: v2.1
更新: 2025-01-08
"""

import cv2
import numpy as np
from ultralytics import YOLO
from typing import Any, Dict, List

from app.user_scripts.common.result import build_result
from app.user_scripts.common.roi import apply_roi

# ==================== 脚本元数据（必需） ====================

SCRIPT_METADATA = {
    # === 基础信息 ===
    "name": "YOLO多模型检测",
    "version": "v2.1",
    "description": "支持多模型并行检测和IOU合并，适合高精度场景",
    "author": "system",
    "category": "detection",
    "tags": ["yolo", "multi-model", "iou-merge", "advanced"],
    
    # === 配置模式定义 ===
    "config_schema": {
        # 模型列表配置（支持添加多个模型）
        "models": {
            "type": "model_list",             # 模型列表类型
            "label": "检测模型列表",
            "required": True,
            "multiple": True,                 # 允许添加多个
            "description": "添加一个或多个YOLO模型，每个模型可以独立配置参数",
            "filters": {
                "model_type": ["YOLO", "ONNX"],
                "framework": ["ultralytics"]
            },
            # 每个模型项的配置结构
            "item_schema": {
                "model_id": {
                    "type": "model_select",
                    "label": "选择模型",
                    "required": True
                },
                "class": {
                    "type": "int",
                    "label": "目标类别ID",
                    "default": 0,
                    "min": 0,
                    "description": "YOLO类别索引，0=person, 1=bicycle, 2=car..."
                },
                "confidence": {
                    "type": "float",
                    "label": "置信度阈值",
                    "default": 0.6,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.05
                },
                "label_name": {
                    "type": "string",
                    "label": "显示标签",
                    "default": "Object",
                    "description": "检测框上显示的文字"
                },
                "label_color": {
                    "type": "color",
                    "label": "标签颜色",
                    "default": "#FF0000"
                },
                "expand_width": {
                    "type": "float",
                    "label": "宽度扩展比例",
                    "default": 0.1,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.05,
                    "description": "检测框宽度扩展，用于IOU匹配，0.1表示左右各扩展10%"
                },
                "expand_height": {
                    "type": "float",
                    "label": "高度扩展比例",
                    "default": 0.1,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.05,
                    "description": "检测框高度扩展，用于IOU匹配"
                }
            }
        },
        
        # IOU合并配置（仅在多模型时生效）
        "iou_threshold": {
            "type": "float",
            "label": "IOU合并阈值",
            "default": 0.5,
            "min": 0.0,
            "max": 1.0,
            "step": 0.05,
            "description": "多模型检测结果合并的IOU阈值，仅在使用2个以上模型时生效。值越大要求重叠度越高"
        },
        
        # ROI处理模式
        "roi_mode": {
            "type": "select",
            "label": "ROI应用模式",
            "default": "post_filter",
            "options": [
                {"value": "post_filter", "label": "后过滤（推荐）- 全帧检测后过滤"},
                {"value": "pre_mask", "label": "前掩码（更快）- 检测前应用掩码"}
            ],
            "description": "post_filter精度高但稍慢，pre_mask速度快但边缘可能有误检"
        }
    },
    
    # === 性能配置 ===
    "performance": {
        "timeout": 30,
        "memory_limit_mb": 512,
        "gpu_required": False,
        "estimated_time_ms": 80                # 多模型会更慢
    },
    
    # === 依赖列表 ===
    "dependencies": [
        "opencv-python>=4.5.0",
        "numpy>=1.19.0",
        "ultralytics>=8.0.0"
    ]
}


# ==================== 辅助函数 ====================

def create_roi_mask(frame_shape: tuple, roi_regions: list) -> np.ndarray:
    """
    创建ROI掩码

    Args:
        frame_shape: 图像尺寸 (height, width, channels)
        roi_regions: ROI区域列表

    Returns:
        np.ndarray: 掩码图像，ROI内为255，外部为0
    """
    if not roi_regions:
        # 没有ROI配置，返回全白掩码（全部区域有效）
        return np.ones((frame_shape[0], frame_shape[1]), dtype=np.uint8) * 255

    # 创建黑色掩码
    mask = np.zeros((frame_shape[0], frame_shape[1]), dtype=np.uint8)
    height, width = frame_shape[0], frame_shape[1]

    # 在ROI区域填充白色
    for region in roi_regions:
        # 支持两种字段名：'polygon'（新格式）和 'points'（旧格式）
        points = region.get('polygon', region.get('points', []))

        if not points or len(points) < 3:
            continue

        # 检查坐标格式
        # 如果是相对坐标格式 [{"x": 0.1, "y": 0.2}, ...]，转换为绝对坐标
        if isinstance(points[0], dict):
            # 相对坐标格式，需要转换为绝对坐标
            pts = [[int(p['x'] * width), int(p['y'] * height)] for p in points]
        else:
            # 已经是绝对坐标格式 [[x1, y1], [x2, y2], ...]
            pts = points

        pts_array = np.array(pts, dtype=np.int32)
        cv2.fillPoly(mask, [pts_array], 255)

    return mask


def apply_roi_mask(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """
    应用ROI掩码到图像
    
    将图像ROI外的区域置黑，用于前掩码模式。
    
    Args:
        frame: 原始图像
        mask: ROI掩码
        
    Returns:
        np.ndarray: 掩码后的图像
    """
    return cv2.bitwise_and(frame, frame, mask=mask)


def filter_detections_by_roi(detections: list, mask: np.ndarray) -> list:
    """
    根据ROI掩码过滤检测结果
    
    只保留中心点在ROI内的检测框，用于后过滤模式。
    
    Args:
        detections: 检测结果列表
        mask: ROI掩码
        
    Returns:
        list: 过滤后的检测结果
    """
    if mask is None:
        return detections
    
    filtered = []
    for det in detections:
        box = det.get('box', [])
        if len(box) >= 4:
            # 计算检测框中心点
            center_x = int((box[0] + box[2]) / 2)
            center_y = int((box[1] + box[3]) / 2)
            
            # 检查中心点是否在ROI内
            if (0 <= center_y < mask.shape[0] and 
                0 <= center_x < mask.shape[1] and 
                mask[center_y, center_x] > 0):
                filtered.append(det)
    
    return filtered


def calculate_iou(box1, box2):
    """
    计算两个边界框的IOU（交并比）
    
    Args:
        box1: [x1, y1, x2, y2]
        box2: [x1, y1, x2, y2]
        
    Returns:
        float: IOU值 (0-1)
    """
    x1_min, y1_min, x1_max, y1_max = box1
    x2_min, y2_min, x2_max, y2_max = box2
    
    # 计算交集区域
    inter_x_min = max(x1_min, x2_min)
    inter_y_min = max(y1_min, y2_min)
    inter_x_max = min(x1_max, x2_max)
    inter_y_max = min(y1_max, y2_max)
    
    # 检查是否有交集
    if inter_x_max < inter_x_min or inter_y_max < inter_y_min:
        return 0.0
    
    # 计算面积
    inter_area = (inter_x_max - inter_x_min) * (inter_y_max - inter_y_min)
    box1_area = (x1_max - x1_min) * (y1_max - y1_min)
    box2_area = (x2_max - x2_min) * (y2_max - y2_min)
    union_area = box1_area + box2_area - inter_area
    
    return inter_area / union_area if union_area > 0 else 0.0


def find_multimodel_groups(stages_results: dict, iou_threshold: float = 0.5) -> list:
    """
    查找被多个模型共同检测到的目标组
    
    通过IOU匹配，将不同模型检测到的同一目标合并为一组。
    
    Args:
        stages_results: {model_name: {'result': YOLO_result, 'model_config': config}}
        iou_threshold: IOU阈值，超过此值认为是同一目标
        
    Returns:
        list: 检测组列表，每组包含多个模型的检测结果
    """
    if len(stages_results) < 2:
        return []
    
    # 1. 提取所有模型的检测结果
    all_detections = []
    for model_name, data in stages_results.items():
        result = data['result']
        model_config = data['model_config']
        
        for box in result.boxes:
            xyxy = box.xyxy[0].tolist()
            
            # 应用扩展（用于更宽松的IOU匹配）
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
                'bbox': expanded_box,           # 扩展后的框（用于匹配）
                'original_bbox': xyxy,          # 原始框（用于显示）
                'class': int(box.cls[0]),
                'class_name': result.names[int(box.cls[0])],
                'confidence': float(box.conf[0])
            })
    
    # 2. 分组：找出IOU>阈值且来自不同模型的检测
    groups = []
    used_indices = set()
    
    for i, det1 in enumerate(all_detections):
        if i in used_indices:
            continue
        
        # 创建新组
        group = [det1]
        models_in_group = {det1['model_name']}
        group_indices = {i}
        
        # 查找与此检测匹配的其他模型检测
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
    初始化函数 - 加载多个YOLO模型
    
    Args:
        config: 配置字典，包含：
            - models: list, 模型配置列表，每项包含：
                * model_id: int, 模型ID
                * class: int, 类别ID
                * confidence: float, 置信度
                * label_name: str, 显示标签
                * 等
                
    Returns:
        dict: 状态对象，包含：
            - models: list, 加载的模型列表
            - initialized_at: float, 初始化时间戳
    """
    import time
    from app import logger
    
    logger.info(f"[YOLO多模型] 开始初始化...")
    
    models_config = config.get('models', [])
    if not models_config:
        logger.warning("[YOLO多模型] 没有配置模型")
        return {'models': [], 'initialized_at': time.time()}
    
    loaded_models = []
    for idx, model_cfg in enumerate(models_config):
        try:
            # 获取模型路径（优先使用 ModelResolver 解析后的路径）
            model_path = model_cfg.get('_model_path')

            if not model_path:
                # 兼容旧方式：通过 model_id 动态解析
                model_id = model_cfg.get('model_id')
                if model_id:
                    try:
                        model_path = resolve_model(model_id)
                    except Exception as e:
                        logger.error(f"[YOLO多模型] 模型{idx+1}解析失败 (model_id={model_id}): {e}")
                        continue
                else:
                    logger.error(f"[YOLO多模型] 模型{idx+1}缺少 model_id 或 _model_path")
                    continue

            logger.info(f"[YOLO多模型] 加载模型{idx+1}: {model_path}")
            model = YOLO(model_path)
            
            loaded_models.append({
                'model': model,
                'config': model_cfg,
                'path': model_path
            })
            
            logger.info(f"[YOLO多模型] 模型{idx+1}加载成功: {model_cfg.get('label_name', 'Object')}")
            
        except Exception as e:
            logger.error(f"[YOLO多模型] 模型{idx+1}加载失败: {e}")
            import traceback
            traceback.print_exc()
    
    logger.info(f"[YOLO多模型] 初始化完成，共加载 {len(loaded_models)}/{len(models_config)} 个模型")
    
    return {
        'models': loaded_models,
        'initialized_at': time.time()
    }


def process(frame: np.ndarray, 
            config: dict, 
            roi_regions: list = None,
            state: dict = None) -> dict:
    """
    处理函数 - 执行多模型检测和IOU合并
    
    工作流程：
    1. 转换颜色空间
    2. 创建ROI掩码（如果配置了ROI）
    3. 对每个模型执行检测
    4. 如果是单模型，直接返回结果
    5. 如果是多模型，通过IOU合并检测结果
    6. 应用ROI过滤（post_filter模式）
    
    Args:
        frame: RGB图像
        config: 配置字典
        roi_regions: ROI区域列表
        state: init()返回的状态
        
    Returns:
        dict: 检测结果，多模型时包含stages字段：
            {
                'detections': [
                    {
                        'box': (x1, y1, x2, y2),
                        'label_name': 'Target',
                        'label_color': '#FF0000',
                        'confidence': 0.85,
                        'stages': [                    # 多模型检测明细
                            {
                                'model_name': 'Model1',
                                'box': (x1, y1, x2, y2),
                                'confidence': 0.9
                            },
                            {
                                'model_name': 'Model2',
                                'box': (x1, y1, x2, y2),
                                'confidence': 0.8
                            }
                        ]
                    }
                ]
            }
    """
    from app import logger
    import time
    
    start_time = time.time()
    
    # 1. 检查状态
    if state is None or not state.get('models'):
        logger.warning("[YOLO多模型] 模型未初始化")
        return build_result([], metadata={'error': 'Models not initialized'})
    
    # 2. ROI模式
    roi_mode = config.get('roi_mode', 'post_filter')
    roi_regions_effective = roi_regions
    if roi_regions and roi_mode:
        roi_regions_effective = [{**r, 'mode': roi_mode} for r in roi_regions]

    # 3. 前掩码（若配置了 pre_mask）
    frame_to_detect, _ = apply_roi(frame, [], roi_regions_effective)
    logger.debug(f"[YOLO多模型] ROI模式: {roi_mode}")

    # 4. 转换颜色空间
    frame_bgr = cv2.cvtColor(frame_to_detect.copy(), cv2.COLOR_RGB2BGR)
    
    # 4. 执行多模型检测
    stages_results = {}
    model_debug_info = []  # 用于调试：记录每个模型的检测情况

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

                # 记录调试信息
                detections_count = len(results[0].boxes)
                detections_detail = []
                for box in results[0].boxes:
                    xyxy = box.xyxy[0].tolist()
                    detections_detail.append({
                        'box': xyxy,
                        'confidence': float(box.conf[0]),
                        'class': int(box.cls[0]),
                        'class_name': results[0].names[int(box.cls[0])]
                    })

                model_debug_info.append({
                    'model_name': model_name,
                    'model_path': model_info.get('path', 'unknown'),
                    'success': True,
                    'detections_count': detections_count,
                    'detections': detections_detail,
                    'confidence_threshold': conf_thresh,
                    'class_filter': class_filter
                })

                logger.debug(f"[YOLO多模型] {model_name} 检测到 {detections_count} 个目标")

        except Exception as e:
            logger.error(f"[YOLO多模型] 模型推理失败: {e}")
            model_debug_info.append({
                'model_name': model_cfg.get('label_name', f"Model{len(stages_results)}"),
                'model_path': model_info.get('path', 'unknown'),
                'success': False,
                'error': str(e)
            })
    
    # 5. 处理检测结果
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

        logger.info(f"[YOLO多模型] 发现 {len(detection_groups)} 个多模型确认目标")

        # 记录合并调试信息
        merge_debug_info = {
            'total_models': len(stages_results),
            'iou_threshold': iou_threshold,
            'detection_groups': len(detection_groups),
            'model_names': list(stages_results.keys())
        }

        # 为每个组生成一个检测结果
        for group in detection_groups:
            # 计算外接矩形
            x_min = min(d['bbox'][0] for d in group)
            y_min = min(d['bbox'][1] for d in group)
            x_max = max(d['bbox'][2] for d in group)
            y_max = max(d['bbox'][3] for d in group)
            
            # 构建stages信息（多阶段检测明细）
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
                'label_name': config.get('label_name', 'Target'),
                'label_color': config.get('label_color', '#FF0000'),
                'class': 0,
                'confidence': np.mean([d['confidence'] for d in group]),
                'stages': stages_info  # 包含各模型的检测明细
            })
    
    # 6. ROI后过滤
    detections_before_roi = len(detections)
    roi_filtered_count = 0
    if roi_regions_effective and detections:
        _, detections = apply_roi(frame, detections, roi_regions_effective)
        roi_filtered_count = detections_before_roi - len(detections)
        if roi_filtered_count > 0:
            logger.debug(f"[YOLO多模型] ROI过滤移除 {roi_filtered_count} 个检测")

    # 7. 计算处理时间
    processing_time = (time.time() - start_time) * 1000

    # 8. 构建调试信息
    debug_metadata = {
        'model_count': len(state['models']),
        'inference_time_ms': processing_time,
        'total_detections': len(detections),
        'roi_mode': roi_mode,
        'detections_before_roi': detections_before_roi,  # ROI过滤前的数量
        'roi_filtered_count': roi_filtered_count,  # ROI过滤掉的数量
        'model_debug_info': model_debug_info,  # 每个模型的详细检测情况
    }

    # 如果是多模型，添加合并调试信息
    if len(stages_results) >= 2:
        debug_metadata['merge_debug_info'] = merge_debug_info

    return build_result(detections, metadata=debug_metadata)


def cleanup(state: dict) -> None:
    """
    清理函数 - 释放所有模型资源
    
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
        
        logger.info(f"[YOLO多模型] 清理完成，释放 {len(state['models'])} 个模型")


# ==================== 使用说明 ====================
"""
📖 多模型检测使用指南：

1. 为什么使用多模型？
   - 提高检测精度：两个模型都检测到才确认，减少误报
   - 多阶段检测：先检测人，再检测安全帽
   - 不同专长：一个模型检测大目标，另一个检测小目标

2. 典型使用场景：
   
   场景1: 高精度人员检测
   - 模型1: YOLOv8n (class=0, person)
   - 模型2: YOLOv8m (class=0, person)
   - IOU阈值: 0.5
   → 两个模型都检测到同一个人，才算确认

   场景2: 安全帽检测
   - 模型1: 人员检测模型 (class=0, person)
   - 模型2: 安全帽检测模型 (class=helmet)
   - IOU阈值: 0.3-0.5
   → 检测到人且头部区域有安全帽

3. 参数调优：
   - IOU阈值: 0.3-0.5适合多阶段，0.5-0.7适合同类确认
   - 扩展比例: 0.1-0.2，用于更宽松的匹配
   - 置信度: 单模型时可降低，多模型确认可降低到0.5

4. 性能考虑：
   - 多模型会增加检测时间（约2-3倍）
   - 建议使用GPU加速
   - 考虑使用小型模型（如YOLOv8n）

💡 提示：
- 如果只需要简单检测，使用 simple_yolo_detector.py
- 查看日志了解多模型匹配情况
- 在告警墙可以看到stages字段，显示各模型检测明细
"""
