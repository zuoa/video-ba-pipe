"""
简单YOLO检测器 - 推荐新手使用

这是一个单模型YOLO检测脚本，配置简单，适合快速开始。
支持从模型库选择模型，支持类别过滤、置信度调整和ROI热区过滤。

使用场景：
- 单一目标检测（人员、车辆、安全帽等）
- 需要ROI热区的特定区域检测
- 简单的检测需求
- 快速搭建检测系统

作者: system
版本: v1.2
更新: 2025-01-08
新增功能：
- 支持ROI热区过滤（pre_mask/post_filter两种模式）
- 支持相对坐标和绝对坐标两种ROI格式
- 与工作流ROI节点无缝集成
"""

import cv2
import numpy as np
from ultralytics import YOLO
from typing import Any, Dict

from app.user_scripts.common.result import build_result
from app.user_scripts.common.roi import apply_roi

# ==================== 脚本元数据（必需） ====================

SCRIPT_METADATA = {
    # === 基础信息 ===
    "name": "简单YOLO检测",
    "version": "v1.2",
    "description": "单模型YOLO检测，支持ROI热区过滤，适合新手快速开始",
    "author": "system",
    "category": "detection",
    "tags": ["yolo", "simple", "single-model", "roi", "recommended"],
    
    # === 配置模式定义 ===
    # 这里定义的配置项会在向导中自动生成表单
    "config_schema": {
        "model_id": {
            "type": "model_select",           # 模型选择器
            "label": "检测模型",
            "required": True,                 # 必填项
            "description": "从模型库中选择一个YOLO模型",
            "filters": {                      # 过滤条件
                "model_type": ["YOLO", "ONNX"],
                "framework": ["ultralytics"]
            }
        },
        "class_filter": {
            "type": "int_list",               # 整数列表
            "label": "类别过滤",
            "default": [],                    # 默认值：空数组表示检测所有类别
            "description": "只检测指定类别，例如 [0, 1, 2] 表示只检测person、bicycle、car；留空检测所有类别",
            "placeholder": "[0, 1, 2]"
        },
        "confidence": {
            "type": "float",                  # 浮点数
            "label": "置信度阈值",
            "default": 0.6,
            "min": 0.0,
            "max": 1.0,
            "step": 0.05,
            "description": "检测置信度阈值，值越大误报越少但可能漏检"
        },
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
        "timeout": 10,                        # 单次处理超时时间（秒）
        "memory_limit_mb": 256,               # 内存限制（MB）
        "gpu_required": False,                # 是否需要GPU
        "estimated_time_ms": 30               # 预估单帧处理时间（毫秒）
    },
    
    # === 依赖列表 ===
    "dependencies": [
        "opencv-python>=4.5.0",
        "numpy>=1.19.0",
        "ultralytics>=8.0.0"
    ]
}


# ==================== ROI 辅助函数 ====================

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

    # 在ROI区域填充白色
    for region in roi_regions:
        # 支持两种数据格式：polygon 和 points
        polygon = region.get('polygon', []) or region.get('points', [])

        # 如果坐标是相对坐标（0-1），转换为绝对坐标
        points = []
        for point in polygon:
            x = point.get('x', 0)
            y = point.get('y', 0)
            # 相对坐标转换为绝对坐标
            if 0 <= x <= 1 and 0 <= y <= 1:
                x = int(x * frame_shape[1])
                y = int(y * frame_shape[0])
            else:
                x = int(x)
                y = int(y)
            points.append([x, y])

        if len(points) >= 3:
            pts = np.array(points, dtype=np.int32)
            cv2.fillPoly(mask, [pts], 255)

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


# ==================== 生命周期函数 ====================

def init(config: dict) -> Dict[str, Any]:
    """
    初始化函数 - 加载YOLO模型
    
    这个函数在算法启动时执行一次，用于加载模型等重型资源。
    返回的状态对象会传递给 process() 函数。
    
    Args:
        config: dict, 配置字典，包含：
            - model_id: int, 模型ID（从配置模式中获取）
            - class_filter: list, 类别过滤列表
            - confidence: float, 置信度阈值
            
    Returns:
        dict: 状态对象，包含：
            - model: YOLO模型实例
            - model_path: 模型文件路径
            
    Raises:
        ValueError: 如果缺少必需的配置项
        Exception: 如果模型加载失败
    """
    from app import logger
    
    # 1. 获取模型ID
    model_id = config.get('model_id')
    if not model_id:
        raise ValueError("缺少 model_id 配置，请在向导中选择一个模型")
    
    # 2. 使用模型解析器获取模型路径
    # resolve_model() 函数会从数据库查询模型信息并返回文件路径
    model_path = resolve_model(model_id)
    logger.info(f"[简单YOLO检测] 加载模型: {model_path}")
    
    # 3. 加载YOLO模型
    model = YOLO(model_path)
    logger.info(f"[简单YOLO检测] 模型加载成功")
    
    # 4. 返回状态对象
    return {
        'model': model,
        'model_path': model_path
    }


def process(frame: np.ndarray, config: dict, roi_regions: list = None, state: dict = None) -> dict:
    """
    处理函数 - 执行检测

    这个函数会在每一帧上执行，进行目标检测。

    Args:
        frame: np.ndarray, RGB格式的图像 (height, width, 3)
        config: dict, 配置字典
            - confidence: 置信度阈值
            - class_filter: 类别过滤列表
            - roi_mode: ROI应用模式 ('pre_mask' 或 'post_filter')
        roi_regions: list, ROI区域列表（可选）
            支持两种数据格式：
            格式1（相对坐标，推荐）:
            [
                {
                    'name': '区域1',
                    'mode': 'post_filter',
                    'polygon': [
                        {'x': 0.1, 'y': 0.2},
                        {'x': 0.3, 'y': 0.4},
                        ...
                    ]
                }
            ]
            格式2（绝对坐标）:
            [
                {
                    'points': [[100, 200], [300, 400], ...],
                    'name': 'area1'
                }
            ]
        state: dict, init() 返回的状态对象

    Returns:
        dict: 检测结果，格式：
            {
                'detections': [
                    {
                        'box': (x1, y1, x2, y2),        # 边界框坐标
                        'label': 'person',               # 类别名称
                        'label_name': 'Person',          # 显示标签（可自定义）
                        'class': 0,                      # 类别ID
                        'confidence': 0.95               # 置信度 (0-1)
                    }
                ],
                'metadata': {                           # 调试信息
                    'model_path': '/path/to/model.pt',
                    'total_detections': 3,
                    'roi_enabled': True,                # 是否启用ROI
                    'roi_mode': 'post_filter',          # ROI模式
                    'roi_regions_count': 1,             # ROI区域数量
                    'detections_detail': [...]         # 检测详情
                }
            }
    """
    from app import logger
    import time

    start_time = time.time()

    # 1. 检查状态
    if not state or 'model' not in state:
        logger.warning("[简单YOLO检测] 模型未初始化")
        return build_result([], metadata={'error': 'Model not initialized'})

    # 2. 获取配置参数
    model = state['model']
    confidence = config.get('confidence', 0.6)
    class_filter = config.get('class_filter', [])

    # ROI模式确定逻辑（优先级从高到低）：
    # 1. 算法节点配置的 config.roi_mode
    # 2. ROI区域中第一个区域的 mode（如果通过ROI节点传递）
    # 3. 默认值 'post_filter'
    roi_mode = config.get('roi_mode')
    if not roi_mode and roi_regions and len(roi_regions) > 0:
        # 从ROI区域读取模式（支持 ROI 节点的配置）
        first_region_mode = roi_regions[0].get('mode')
        if first_region_mode:
            # 转换命名格式：pre_mask -> preMask, post_filter -> postFilter
            roi_mode = 'pre_mask' if first_region_mode in ['pre_mask', 'preMask'] else 'post_filter'
            logger.debug(f"[简单YOLO检测] 从ROI区域读取模式: {first_region_mode} -> {roi_mode}")

    if not roi_mode:
        roi_mode = 'post_filter'  # 默认使用后过滤模式

    logger.debug(f"[简单YOLO检测] ROI模式: {roi_mode}")

    # 3. 根据ROI模式准备区域配置（以算法节点配置为准）
    roi_regions_effective = roi_regions
    if roi_regions and roi_mode:
        roi_regions_effective = [{**r, 'mode': roi_mode} for r in roi_regions]

    # 4. 前掩码（若配置了 pre_mask）
    frame_to_detect, _ = apply_roi(frame, [], roi_regions_effective)

    # 5. 转换颜色空间（YOLO需要BGR格式）
    frame_bgr = cv2.cvtColor(frame_to_detect.copy(), cv2.COLOR_RGB2BGR)

    # 6. 执行YOLO检测
    kwargs = {
        'save': False,           # 不保存结果图片
        'conf': confidence,      # 置信度阈值
        'verbose': False         # 不打印详细日志
    }

    # 如果配置了类别过滤，只检测指定类别
    if class_filter:
        kwargs['classes'] = class_filter

    results = model.predict(frame_to_detect, **kwargs)

    # 7. 转换检测结果为标准格式
    detections = []
    detections_detail = []  # 用于调试：记录所有检测详情

    if results and len(results) > 0:
        for det in results[0].boxes.data.tolist():
            x1, y1, x2, y2, conf, cls = det

            # 获取类别名称
            class_name = results[0].names[int(cls)]

            detection = {
                'box': (x1, y1, x2, y2),
                'label': class_name,                                    # YOLO原始类别名
                'label_name': config.get('label_name', class_name),    # 自定义显示名称
                'class': int(cls),
                'confidence': float(conf)
            }

            detections.append(detection)

            # 记录检测详情（用于调试）
            detections_detail.append({
                'box': [float(x1), float(y1), float(x2), float(y2)],
                'confidence': float(conf),
                'class': int(cls),
                'class_name': class_name
            })

    # 8. ROI后过滤（post_filter 模式）
    detections_before_roi = len(detections)
    roi_filtered_count = 0
    if roi_regions_effective and detections:
        _, detections = apply_roi(frame, detections, roi_regions_effective)
        roi_filtered_count = detections_before_roi - len(detections)
        if roi_filtered_count > 0:
            logger.debug(f"[简单YOLO检测] ROI后过滤移除 {roi_filtered_count} 个检测")

    # 9. 计算处理时间
    processing_time = (time.time() - start_time) * 1000

    # 10. 返回结果（包含调试信息）
    metadata = {
        'model_path': state.get('model_path', 'unknown'),
        'total_detections': len(detections),
        'inference_time_ms': processing_time,
        'confidence_threshold': confidence,
        'class_filter': class_filter if class_filter else 'all',
        'detections_detail': detections_detail,  # 所有检测的详细信息
        'image_size': {
            'height': frame.shape[0],
            'width': frame.shape[1]
        }
    }

    # 如果启用了ROI，添加ROI相关信息
    if roi_regions:
        metadata['roi_enabled'] = True
        metadata['roi_mode'] = roi_mode
        metadata['roi_regions_count'] = len(roi_regions)
        metadata['detections_before_roi'] = detections_before_roi  # ROI过滤前的数量
        metadata['roi_filtered_count'] = roi_filtered_count  # ROI过滤掉的数量

    return build_result(detections, metadata=metadata)


def cleanup(state: dict) -> None:
    """
    清理函数 - 释放资源
    
    这个函数在算法停止时执行，用于释放模型等资源。
    
    Args:
        state: dict, init() 返回的状态对象
    """
    if state and 'model' in state:
        model = state['model']
        # 关闭模型（如果支持）
        if hasattr(model, 'close'):
            model.close()


# ==================== 使用说明 ====================
"""
📖 如何使用这个脚本：

1. 准备模型
   - 访问 模型管理 页面
   - 上传你的YOLO模型文件（.pt格式）
   - 记录模型ID

2. 创建算法
   - 访问 算法管理 页面
   - 点击"创建算法"按钮
   - 在向导中选择这个脚本
   - 配置参数：
     * model_id: 选择刚上传的模型
     * class_filter: 留空检测所有类别，或填写 [0, 1] 只检测特定类别
     * confidence: 设置置信度阈值，建议0.5-0.7
     * roi_mode: 选择ROI应用模式（可选）
       - post_filter（推荐）：全帧检测后过滤，精度高
       - pre_mask：检测前应用掩码，速度快

3. 配置工作流
   - 创建或编辑工作流
   - 添加视频源节点
   - 添加ROI节点（可选）：配置ROI热区
   - 添加算法节点（选择刚创建的算法）
   - 添加输出节点（告警或录像）
   - 连接节点并保存
   - 连接顺序：source -> roi_draw -> algorithm -> output

4. ROI配置说明
   方式1：使用ROI节点（推荐）
   - 在工作流中添加"热区绘制"节点
   - 在视频画面上绘制多边形ROI区域
   - ROI节点的配置会自动传递给后续算法节点

   方式2：直接在算法节点配置ROI
   - 在算法节点的高级配置中添加 roi_regions 字段
   - 格式示例（相对坐标）：
     [
       {
         "name": "门口",
         "mode": "post_filter",
         "polygon": [
           {"x": 0.1, "y": 0.2},
           {"x": 0.3, "y": 0.2},
           {"x": 0.3, "y": 0.5},
           {"x": 0.1, "y": 0.5}
         ]
       }
     ]

5. ROI模式选择
   - post_filter（后过滤，推荐）：
     * 对全帧进行检测，然后过滤ROI外的结果
     * 优点：精度高，边缘准确
     * 缺点：稍慢
     * 适合：对精度要求高的场景

   - pre_mask（前掩码）：
     * 检测前将ROI外区域置黑
     * 优点：速度快，减少计算量
     * 缺点：边缘可能误检
     * 适合：ROI占比较小，对速度要求高的场景

6. 启动检测
   - 激活工作流
   - 查看实时检测结果
   - 检查日志确认ROI是否生效

💡 提示：
- 第一次运行可能需要下载YOLO模型依赖，会比较慢
- 如果检测不准确，可以调整置信度阈值
- 使用class_filter可以提升检测速度
- 配置ROI后，查看日志确认"已创建ROI掩码"信息
- 查看日志文件了解详细执行信息

🔍 常见YOLO类别ID：
0: person
1: bicycle
2: car
3: motorcycle
5: bus
7: truck
...（共80个类别，具体查看COCO数据集）
"""
