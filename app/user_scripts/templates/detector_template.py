"""
检测脚本模板

复制此文件创建你的检测脚本
"""
import cv2
import numpy as np


# 脚本元数据（必需）
SCRIPT_METADATA = {
    'name': 'my_detector',           # 脚本名称
    'version': '1.0.0',              # 版本号
    'author': 'Your Name',           # 作者
    'description': 'Description',     # 描述
    'dependencies': [                # 依赖列表
        'opencv-python',
        'numpy'
    ],
    'timeout': 30,                   # 超时时间（秒）
    'memory_limit': 512              # 内存限制（MB）
}


def init(config):
    """
    初始化函数（可选）

    用于加载模型、配置等重型资源

    Args:
        config: dict, 算法配置，包含：
            - model_path: str, 模型路径
            - ext_config: dict, 扩展配置
            - 其他数据库字段

    Returns:
        任意对象，会被传递给process函数
    """
    # 示例：加载YOLO模型
    # from ultralytics import YOLO
    # model = YOLO(config.get('model_path', 'yolov8n.pt'))
    # return {'model': model}

    print(f"[{SCRIPT_METADATA['name']}] 初始化")

    # 返回状态对象（可以是任何内容）
    return {
        'initialized': True,
        'config': config
    }


def process(frame, config, state=None):
    """
    处理函数（必需）

    Args:
        frame: numpy.ndarray, RGB格式图像 (height, width, 3)
        config: dict, 算法配置
        state: init函数的返回值

    Returns:
        dict: {
            'detections': [  # 检测结果列表
                {
                    'box': [x1, y1, x2, y2],  # 边界框坐标
                    'label': 'person',         # 标签名称
                    'confidence': 0.95,        # 置信度 (0-1)
                    'class': 0,                # 类别ID
                    'metadata': {}             # 可选的额外信息
                }
            ],
            'metadata': {},      # 调试信息（可选）
            'skip_next': False   # 是否跳过后续处理（可选）
        }
    """
    # 转换为BGR（OpenCV使用BGR格式）
    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    # ===== 在这里实现你的检测逻辑 =====

    # 示例：使用YOLO检测
    # if state and 'model' in state:
    #     model = state['model']
    #     results = model(frame_bgr, conf=0.7)
    #
    #     detections = []
    #     for r in results:
    #         for box in r.boxes:
    #             x1, y1, x2, y2 = box.xyxy[0].tolist()
    #             conf = float(box.conf[0])
    #             cls = int(box.cls[0])
    #
    #             detections.append({
    #                 'box': [x1, y1, x2, y2],
    #                 'label': model.names[cls],
    #                 'confidence': conf,
    #                 'class': cls
    #             })
    #
    #     return {'detections': detections}

    # 示例：简单的运动检测
    # gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    # _, thresh = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
    # contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    #
    # detections = []
    # for cnt in contours:
    #     if cv2.contourArea(cnt) > 100:
    #         x, y, w, h = cv2.boundingRect(cnt)
    #         detections.append({
    #             'box': [x, y, x+w, y+h],
    #             'label': 'motion',
    #             'confidence': 1.0,
    #             'class': 0
    #         })

    # 临时：返回空结果
    detections = []

    # ===== 检测逻辑结束 =====

    return {
        'detections': detections,
        'metadata': {
            'frame_shape': frame.shape,
            'detection_count': len(detections)
        }
    }


def cleanup(state):
    """
    清理函数（可选）

    用于释放资源

    Args:
        state: init函数的返回值
    """
    print(f"[{SCRIPT_METADATA['name']}] 清理资源")

    # 示例：释放模型
    # if state and 'model' in state:
    #     del state['model']
