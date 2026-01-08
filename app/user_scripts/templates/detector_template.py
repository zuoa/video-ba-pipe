"""
检测脚本开发模板

这是一个空白模板，用于开发自定义检测脚本。
包含完整的生命周期函数和详细注释。

使用方法：
1. 复制此文件并重命名
2. 修改 SCRIPT_METADATA
3. 实现 init()、process()、cleanup() 函数
4. 测试并部署

作者: System
版本: v1.1
更新: 2025-01-08
"""

import cv2
import numpy as np
from typing import Any, Dict, List, Optional

# ==================== 脚本元数据（必需） ====================

SCRIPT_METADATA = {
    # === 基础信息（必需） ===
    "name": "my_detector",                    # 脚本名称，建议使用英文
    "version": "v1.0",                        # 版本号
    "description": "自定义检测器描述",          # 简短描述
    "author": "Your Name",                    # 作者
    "category": "detection",                  # 类别：detection, tracking, classification等
    "tags": ["custom", "template"],           # 标签列表，方便搜索
    
    # === 配置模式定义（可选） ===
    # 定义配置项，会在向导中自动生成表单
    "config_schema": {
        # 示例：模型选择
        "model_id": {
            "type": "model_select",           # 类型：model_select, model_list, float, int, string, boolean, select, color, int_list
            "label": "检测模型",
            "required": True,                 # 是否必填
            "description": "选择一个YOLO模型",
            "filters": {                      # 模型过滤条件
                "model_type": ["YOLO"],
                "framework": ["ultralytics"]
            }
        },
        
        # 示例：浮点数配置
        "confidence": {
            "type": "float",
            "label": "置信度阈值",
            "default": 0.6,                   # 默认值
            "min": 0.0,                       # 最小值
            "max": 1.0,                       # 最大值
            "step": 0.05,                     # 步长
            "description": "检测置信度阈值"
        },
        
        # 示例：整数列表配置
        "class_filter": {
            "type": "int_list",
            "label": "类别过滤",
            "default": [],
            "description": "只检测指定类别，例如 [0, 1, 2]",
            "placeholder": "[0, 1, 2]"
        },
        
        # 示例：字符串配置
        "label_name": {
            "type": "string",
            "label": "显示标签",
            "default": "Object",
            "placeholder": "输入标签名称"
        },
        
        # 示例：颜色选择
        "label_color": {
            "type": "color",
            "label": "标签颜色",
            "default": "#FF0000"
        },
        
        # 示例：下拉选择
        "detection_mode": {
            "type": "select",
            "label": "检测模式",
            "default": "normal",
            "options": [
                {"value": "normal", "label": "普通模式"},
                {"value": "fast", "label": "快速模式"},
                {"value": "accurate", "label": "精确模式"}
            ]
        },
        
        # 示例：布尔开关
        "enable_tracking": {
            "type": "boolean",
            "label": "启用跟踪",
            "default": False
        }
    },
    
    # === 性能配置（可选） ===
    "performance": {
        "timeout": 30,                        # 单次处理超时时间（秒）
        "memory_limit_mb": 512,               # 内存限制（MB）
        "gpu_required": False,                # 是否需要GPU
        "estimated_time_ms": 50               # 预估单帧处理时间（毫秒）
    },
    
    # === 依赖列表（可选） ===
    "dependencies": [
        "opencv-python>=4.5.0",
        "numpy>=1.19.0",
        # 添加你的依赖包
    ]
}


# ==================== 生命周期函数 ====================

def init(config: dict) -> Dict[str, Any]:
    """
    初始化函数（可选，但推荐实现）
    
    在算法启动时执行一次，用于：
    - 加载模型
    - 初始化资源
    - 预处理配置
    - 分配内存
    
    Args:
        config: dict, 配置字典，包含：
            - config_schema 中定义的所有字段
            - 运行时配置（如 label_name, interval_seconds 等）
            
    Returns:
        dict: 状态对象，会传递给 process() 和 cleanup()
              可以包含任何内容，如模型实例、配置缓存等
              
    Raises:
        ValueError: 配置错误
        Exception: 初始化失败
        
    示例：
        return {
            'model': loaded_model,
            'config': processed_config,
            'initialized_at': time.time()
        }
    """
    from app import logger
    
    logger.info(f"[{SCRIPT_METADATA['name']}] 开始初始化...")
    
    # ===== 在这里实现你的初始化逻辑 =====
    
    # 示例1：加载模型
    # model_id = config.get('model_id')
    # if not model_id:
    #     raise ValueError("缺少 model_id 配置")
    # model_path = resolve_model(model_id)
    # from ultralytics import YOLO
    # model = YOLO(model_path)
    
    # 示例2：预处理配置
    # confidence = config.get('confidence', 0.6)
    # class_filter = config.get('class_filter', [])
    
    # 示例3：初始化追踪器
    # if config.get('enable_tracking'):
    #     tracker = SomeTracker()
    
    logger.info(f"[{SCRIPT_METADATA['name']}] 初始化完成")
    
    # 返回状态对象
    return {
        'initialized': True,
        'config': config
        # 添加你需要的状态
    }


def process(frame: np.ndarray, 
            config: dict, 
            roi_regions: Optional[List[dict]] = None,
            state: Optional[dict] = None) -> dict:
    """
    处理函数（必需）
    
    在每一帧上执行，这是核心检测逻辑。
    
    Args:
        frame: np.ndarray, RGB格式的图像 (height, width, 3)
               注意：系统传入的是RGB格式，不是BGR！
               
        config: dict, 配置字典，包含所有配置项
        
        roi_regions: Optional[List[dict]], ROI区域列表（可选）
            每个ROI包含：
            {
                'name': 'area1',
                'points': [[x1, y1], [x2, y2], ...],  # 多边形顶点
                'enabled': True
            }
            
        state: Optional[dict], init() 返回的状态对象
        
    Returns:
        dict: 检测结果，格式：
        {
            'detections': [                   # 检测结果列表（必需）
                {
                    'box': (x1, y1, x2, y2),  # 边界框坐标（必需）
                    'label': 'person',         # 类别名称（推荐）
                    'label_name': 'Person',    # 显示标签（推荐）
                    'confidence': 0.95,        # 置信度 0-1（推荐）
                    'class': 0,                # 类别ID（可选）
                    'label_color': '#FF0000',  # 标签颜色（可选）
                    'metadata': {              # 额外信息（可选）
                        'custom_field': 'value'
                    }
                }
            ],
            'metadata': {                      # 调试信息（可选）
                'processing_time_ms': 50,
                'custom_info': 'value'
            }
        }
        
    注意事项：
    1. 如果没有检测到目标，返回空列表：{'detections': []}
    2. box 坐标必须是 (x1, y1, x2, y2) 格式，左上角到右下角
    3. 如果要支持ROI，需要自己实现过滤逻辑
    4. 处理异常要妥善，不要让异常中断整个流程
    """
    from app import logger
    
    # ===== 在这里实现你的检测逻辑 =====
    
    # 1. 颜色空间转换（如果需要）
    # OpenCV函数需要BGR格式
    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    
    # 2. 获取配置参数
    # confidence = config.get('confidence', 0.6)
    # class_filter = config.get('class_filter', [])
    
    # 3. 执行检测
    detections = []
    
    # 示例：使用YOLO检测
    # if state and 'model' in state:
    #     model = state['model']
    #     results = model.predict(
    #         frame_bgr,
    #         save=False,
    #         conf=confidence,
    #         classes=class_filter if class_filter else None,
    #         verbose=False
    #     )
    #     
    #     if results and len(results) > 0:
    #         for det in results[0].boxes.data.tolist():
    #             x1, y1, x2, y2, conf, cls = det
    #             detections.append({
    #                 'box': (x1, y1, x2, y2),
    #                 'label': results[0].names[int(cls)],
    #                 'label_name': config.get('label_name', results[0].names[int(cls)]),
    #                 'confidence': float(conf),
    #                 'class': int(cls)
    #             })
    
    # 4. ROI过滤（如果需要）
    # if roi_regions and detections:
    #     detections = filter_by_roi(detections, roi_regions)
    
    # 5. 返回结果
    return {
        'detections': detections,
        'metadata': {
            'total_detections': len(detections)
        }
    }


def cleanup(state: Optional[dict]) -> None:
    """
    清理函数（可选，但推荐实现）
    
    在算法停止时执行，用于：
    - 释放模型资源
    - 关闭文件句柄
    - 清理内存
    - 保存状态
    
    Args:
        state: Optional[dict], init() 返回的状态对象
        
    注意：
    - 必须妥善处理异常，不要让清理失败影响系统
    - 即使 init() 失败，cleanup() 也可能被调用
    """
    from app import logger
    
    logger.info(f"[{SCRIPT_METADATA['name']}] 开始清理...")
    
    if not state:
        return
    
    # ===== 在这里实现你的清理逻辑 =====
    
    # 示例：释放模型
    # if 'model' in state:
    #     model = state['model']
    #     if hasattr(model, 'close'):
    #         try:
    #             model.close()
    #         except Exception as e:
    #             logger.error(f"关闭模型失败: {e}")
    
    # 示例：删除临时文件
    # if 'temp_file' in state:
    #     try:
    #         os.remove(state['temp_file'])
    #     except:
    #         pass
    
    logger.info(f"[{SCRIPT_METADATA['name']}] 清理完成")


# ==================== 辅助函数示例 ====================

def filter_by_roi(detections: List[dict], roi_regions: List[dict]) -> List[dict]:
    """
    ROI过滤示例函数
    
    只保留中心点在ROI内的检测结果。
    
    Args:
        detections: 检测结果列表
        roi_regions: ROI区域列表
        
    Returns:
        过滤后的检测结果
    """
    if not roi_regions:
        return detections
    
    filtered = []
    for det in detections:
        box = det.get('box', [])
        if len(box) >= 4:
            # 计算检测框中心点
            center_x = int((box[0] + box[2]) / 2)
            center_y = int((box[1] + box[3]) / 2)
            
            # 检查是否在任意ROI内
            for region in roi_regions:
                if not region.get('enabled', True):
                    continue
                
                points = region.get('points', [])
                if len(points) >= 3:
                    pts = np.array(points, dtype=np.int32)
                    # 使用OpenCV的点在多边形内判断
                    result = cv2.pointPolygonTest(pts, (center_x, center_y), False)
                    if result >= 0:  # 在多边形内或边上
                        filtered.append(det)
                        break
    
    return filtered


# ==================== 开发指南 ====================
"""
📖 开发自定义检测脚本指南：

1. 开发流程：
   ① 复制此模板文件
   ② 修改 SCRIPT_METADATA，定义配置模式
   ③ 实现 init() - 加载模型和资源
   ④ 实现 process() - 核心检测逻辑
   ⑤ 实现 cleanup() - 清理资源
   ⑥ 本地测试
   ⑦ 上传到脚本管理
   ⑧ 创建算法并测试

2. 配置模式 (config_schema) 字段类型：
   - model_select: 单个模型选择器
   - model_list: 多个模型列表（每个模型可独立配置）
   - float: 浮点数输入框
   - int: 整数输入框
   - int_list: 整数数组，如 [0, 1, 2]
   - string: 文本输入框
   - select: 下拉选择框
   - boolean: 开关
   - color: 颜色选择器

3. 可用的全局函数：
   - resolve_model(id_or_name): 解析模型路径
   - get_model_info(id_or_name): 获取模型完整信息
   - list_available_models(): 列出所有可用模型

4. 调试技巧：
   - 使用 logger.info/debug/error() 记录日志
   - 日志文件位置：app/data/logs/debug.log
   - 在 process() 中添加 metadata 字段返回调试信息
   - 使用算法测试功能上传图片测试

5. 性能优化：
   - 在 init() 中加载模型，不要在 process() 中加载
   - 避免在 process() 中进行耗时操作
   - 使用 numpy 向量化操作而非循环
   - 考虑使用 GPU 加速

6. 错误处理：
   - 使用 try-except 捕获异常
   - 不要让异常导致整个流程中断
   - 在 metadata 中返回错误信息
   - 使用 logger.error() 记录错误

7. 返回格式要求：
   - 必须返回 dict 包含 'detections' 键
   - detections 是列表，每项必须包含 'box' 字段
   - box 格式：(x1, y1, x2, y2) 左上角到右下角
   - 推荐包含 label_name, confidence 字段
   - 可选 metadata 字段用于调试

💡 最佳实践：
- 先从简单功能开始，逐步增加复杂度
- 详细注释你的代码
- 定义清晰的 config_schema，让用户易于配置
- 提供合理的默认值
- 测试各种边界情况
"""
