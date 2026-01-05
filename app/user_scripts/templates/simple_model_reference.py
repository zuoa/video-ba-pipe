"""
简单模型引用示例

最简单的使用方式：在脚本中直接写模型ID或名称

步骤：
1. 在模型管理中上传模型
2. 记录模型ID（如ID=1）
3. 在脚本中写: PRIMARY_MODEL = resolve_model(1)
4. 完成！
"""
import cv2
import numpy as np

# ==================== 配置区域 ====================
# 在这里修改你要使用的模型ID或名称

# 方式1: 通过ID引用（推荐）
try:
    PRIMARY_MODEL = resolve_model(1)  # <-- 修改这里的数字为你的模型ID
except Exception as e:
    print(f"❌ 无法加载模型ID=1: {e}")
    print("💡 提示: 请在模型管理中上传模型，然后修改这里的数字为正确的模型ID")
    PRIMARY_MODEL = None

# 方式2: 通过名称引用
# PRIMARY_MODEL = resolve_model('yolov8n-person')  # <-- 或者用模型名称

# ==================== 脚本元数据 ====================
SCRIPT_METADATA = {
    'name': 'simple_model_reference',
    'version': '1.0.0',
    'author': 'System',
    'description': '简单模型引用示例',
    'dependencies': ['opencv-python', 'numpy', 'ultralytics']
}


# ==================== 脚本函数 ====================

def init(config):
    """初始化 - 加载模型"""
    from ultralytics import YOLO
    
    if not PRIMARY_MODEL:
        raise ValueError("模型路径未设置！请检查脚本顶部的 resolve_model() 调用")
    
    print(f"✓ 加载模型: {PRIMARY_MODEL}")
    model = YOLO(PRIMARY_MODEL)
    
    return {'model': model}


def process(frame, config, state=None, roi_regions=None):
    """处理帧 - 执行检测"""
    if not state or 'model' not in state:
        return {'detections': []}
    
    model = state['model']
    
    # 转换为BGR
    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    
    # YOLO推理
    results = model.predict(
        frame_bgr,
        save=False,
        conf=config.get('confidence', 0.7),
        verbose=False
    )
    
    # 解析结果
    detections = []
    if results and len(results) > 0:
        for det in results[0].boxes.data.tolist():
            x1, y1, x2, y2, conf, cls = det
            detections.append({
                'box': [x1, y1, x2, y2],
                'label': config.get('label_name', 'Object'),
                'confidence': float(conf),
                'class': int(cls)
            })
    
    return {'detections': detections}


def cleanup(state):
    """清理资源"""
    if state and 'model' in state:
        del state['model']


# ==================== 使用说明 ====================
"""
📝 如何使用这个脚本：

1. 上传模型到模型管理
   - 进入 Web UI -> 模型管理
   - 上传你的 YOLO 模型文件（如 yolov8n.pt）
   - 记录分配的模型ID（例如 ID=1）

2. 修改脚本配置
   - 找到脚本顶部的这一行:
     PRIMARY_MODEL = resolve_model(1)
   - 将数字 1 改为你的模型ID

3. 创建算法
   - 进入 Web UI -> 脚本管理
   - 上传或保存这个脚本
   - 进入算法管理 -> 创建算法
   - 选择这个脚本
   - 配置参数（可选）:
     {
       "confidence": 0.7,
       "label_name": "Person"
     }

4. 完成！
   - 系统会自动从数据库查询模型路径
   - 无需在配置中手动填写模型路径

💡 提示：
- 使用 ID 引用更稳定（模型名称可能变化）
- 可以在脚本中引用多个模型
- 使用 get_model_info(1) 可以获取模型的完整信息
- 使用 list_available_models() 可以查看所有可用模型

🔍 调试：
如果模型加载失败，检查：
1. 模型是否已上传到模型管理
2. 模型ID是否正确
3. 模型文件是否存在
4. 查看日志: app/data/logs/debug.log
"""

