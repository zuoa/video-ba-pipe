"""
简单YOLO检测器 - 推荐新手使用

这是一个单模型YOLO检测脚本，配置简单，适合快速开始。
支持从模型库选择模型，支持类别过滤和置信度调整。

使用场景：
- 单一目标检测（人员、车辆、安全帽等）
- 简单的检测需求
- 快速搭建检测系统

作者: system
版本: v1.1
更新: 2025-01-08
"""

import cv2
import numpy as np
from ultralytics import YOLO
from typing import Any, Dict

# ==================== 脚本元数据（必需） ====================

SCRIPT_METADATA = {
    # === 基础信息 ===
    "name": "简单YOLO检测",
    "version": "v1.1",
    "description": "单模型YOLO检测，适合新手快速开始",
    "author": "system",
    "category": "detection",
    "tags": ["yolo", "simple", "single-model", "recommended"],
    
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
        roi_regions: list, ROI区域列表（可选）
            每个ROI包含: {'points': [[x1,y1], [x2,y2], ...], 'name': 'area1'}
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
                    'total_detections': 3
                }
            }
    """
    from app import logger
    
    # 1. 检查状态
    if not state or 'model' not in state:
        logger.warning("[简单YOLO检测] 模型未初始化")
        return {'detections': []}
    
    # 2. 获取配置参数
    model = state['model']
    confidence = config.get('confidence', 0.6)
    class_filter = config.get('class_filter', [])
    
    # 3. 转换颜色空间
    # YOLO需要BGR格式，但系统传入的是RGB格式
    frame_bgr = cv2.cvtColor(frame.copy(), cv2.COLOR_RGB2BGR)
    
    # 4. 执行YOLO检测
    kwargs = {
        'save': False,           # 不保存结果图片
        'conf': confidence,      # 置信度阈值
        'verbose': False         # 不打印详细日志
    }
    
    # 如果配置了类别过滤，只检测指定类别
    if class_filter:
        kwargs['classes'] = class_filter
    
    results = model.predict(frame_bgr, **kwargs)
    
    # 5. 转换检测结果为标准格式
    detections = []
    if results and len(results) > 0:
        for det in results[0].boxes.data.tolist():
            x1, y1, x2, y2, conf, cls = det
            
            # 获取类别名称
            class_name = results[0].names[int(cls)]
            
            detections.append({
                'box': (x1, y1, x2, y2),
                'label': class_name,                                    # YOLO原始类别名
                'label_name': config.get('label_name', class_name),    # 自定义显示名称
                'class': int(cls),
                'confidence': float(conf)
            })
    
    # 6. 返回结果
    return {
        'detections': detections,
        'metadata': {
            'model_path': state['model_path'],
            'total_detections': len(detections)
        }
    }


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

3. 配置工作流
   - 创建或编辑工作流
   - 添加视频源节点
   - 添加算法节点（选择刚创建的算法）
   - 添加输出节点（告警或录像）
   - 连接节点并保存

4. 启动检测
   - 激活工作流
   - 查看实时检测结果

💡 提示：
- 第一次运行可能需要下载YOLO模型依赖，会比较慢
- 如果检测不准确，可以调整置信度阈值
- 使用class_filter可以提升检测速度
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
