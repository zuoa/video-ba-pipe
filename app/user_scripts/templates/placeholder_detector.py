"""
使用模型引用的检测器模板

这个模板展示了如何在脚本中直接引用模型管理中的模型

✨ 使用方式：在脚本中直接调用 resolve_model() 函数
   - resolve_model() 已自动注入，无需导入
   - 可以通过模型ID或名称引用模型

优势：
1. 脚本自包含，清晰声明需要哪些模型
2. 无需在外部配置中手动填写路径
3. 支持通过ID或名称引用
4. 模型变更无需修改配置，只需更新脚本

使用方法：
1. 在模型管理中上传模型（记录ID或名称）
2. 在脚本中直接调用 resolve_model(id_or_name)
3. 系统自动从数据库查询模型路径
"""
import cv2
import numpy as np

# ==================== 在脚本中直接引用模型 ====================
# resolve_model() 函数已自动注入，可直接使用（也可以显式导入）

try:
    from app.core.model_resolver import resolve_model, get_model_info, list_available_models
except ImportError:
    # 如果导入失败，函数应该已经被注入到全局命名空间
    pass

# 通过ID引用（推荐，更稳定）
try:
    PRIMARY_MODEL_PATH = resolve_model(1)  # 引用ID=1的模型
except:
    PRIMARY_MODEL_PATH = None

# 通过名称引用
try:
    BACKUP_MODEL_PATH = resolve_model('yolov8n-person')  # 引用名为yolov8n-person的模型
except:
    BACKUP_MODEL_PATH = None

# 获取完整模型信息
try:
    MODEL_INFO = get_model_info(1)
    # MODEL_INFO 包含: path, classes, input_shape, description 等
except:
    MODEL_INFO = None

# 脚本元数据
SCRIPT_METADATA = {
    'name': 'model_reference_detector',
    'version': '1.0.0',
    'author': 'System',
    'description': '使用模型引用的示例检测器',
    'dependencies': ['opencv-python', 'numpy', 'ultralytics'],
    'timeout': 30,
    'memory_limit': 1024
}


def init(config):
    """
    初始化函数 - 加载模型
    
    ==================== 方式1：脚本顶部声明模型（推荐） ====================
    不需要外部配置，脚本自包含
    """
    from ultralytics import YOLO
    
    print(f"[{SCRIPT_METADATA['name']}] 开始初始化...")
    
    state = {'models': []}
    
    # === 方式1: 使用脚本顶部声明的模型路径 ===
    if PRIMARY_MODEL_PATH:
        print(f"[{SCRIPT_METADATA['name']}] 加载主模型: {PRIMARY_MODEL_PATH}")
        model = YOLO(PRIMARY_MODEL_PATH)
        state['models'].append({
            'model': model,
            'config': {
                'name': 'primary',
                'confidence': config.get('confidence', 0.7),
                'label_name': config.get('label_name', 'Object')
            }
        })

    
    if not state['models']:
        print(f"[{SCRIPT_METADATA['name']}] 警告: 没有加载任何模型！")
        print("请确保:")
        print("  1. 在模型管理中上传了模型")
        print("  2. 在脚本顶部正确设置了模型ID")
        print("  3. 或在配置中传入 model_ids")
    
    print(f"[{SCRIPT_METADATA['name']}] 初始化完成，共加载 {len(state['models'])} 个模型")
    
    return state


def process(frame, config, state=None, roi_regions=None):
    """
    处理函数 - 执行检测
    
    Args:
        frame: RGB图像
        config: 配置字典
        state: init()返回的状态
        roi_regions: ROI配置
        
    Returns:
        检测结果
    """
    if not state or not state.get('models'):
        return {'detections': []}
    
    detections = []
    
    # 转换为BGR（YOLO需要）
    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    
    # 执行所有模型的检测
    for model_info in state['models']:
        model = model_info['model']
        model_config = model_info['config']
        
        confidence = model_config.get('confidence', 0.7)
        class_filter = model_config.get('class_filter')
        label_name = model_config.get('label_name', 'Object')
        
        try:
            # YOLO推理
            results = model.predict(
                frame_bgr,
                save=False,
                classes=[class_filter] if class_filter is not None else None,
                conf=confidence,
                verbose=False
            )
            
            if results and len(results) > 0:
                for det in results[0].boxes.data.tolist():
                    x1, y1, x2, y2, conf, cls = det
                    detections.append({
                        'box': [x1, y1, x2, y2],
                        'label': label_name,
                        'confidence': float(conf),
                        'class': int(cls),
                        'model': model_config.get('name', 'unknown')
                    })
        
        except Exception as e:
            print(f"[{SCRIPT_METADATA['name']}] 模型推理失败: {e}")
    
    return {
        'detections': detections,
        'metadata': {
            'models_count': len(state['models']),
            'detections_count': len(detections)
        }
    }


def cleanup(state):
    """清理函数 - 释放资源"""
    print(f"[{SCRIPT_METADATA['name']}] 清理资源...")
    
    if state and 'models' in state:
        for model_info in state['models']:
            if 'model' in model_info:
                del model_info['model']
    
    print(f"[{SCRIPT_METADATA['name']}] 清理完成")
