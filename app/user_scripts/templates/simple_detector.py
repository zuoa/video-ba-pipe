"""
简单检测示例 - 使用OpenCV进行颜色检测

这是一个最简单的检测脚本示例
"""
import cv2
import numpy as np


SCRIPT_METADATA = {
    'name': 'color_detector',
    'version': '1.0.0',
    'author': 'Demo',
    'description': 'Simple color detection using OpenCV',
    'dependencies': ['opencv-python', 'numpy'],
    'timeout': 10,
    'memory_limit': 256
}


def init(config):
    """初始化检测器"""
    # 获取配置的颜色范围（HSV）
    color_ranges = config.get('color_ranges', {
        'red': {'lower': [0, 100, 100], 'upper': [10, 255, 255]},
        'blue': {'lower': [100, 100, 100], 'upper': [130, 255, 255]},
        'white': {'lower': [200, 200, 200], 'upper': [255, 255, 255]},
    })

    return {
        'color_ranges': color_ranges
    }


def process(frame, config, state=None):
    """
    检测特定颜色的区域

    Args:
        frame: RGB格式图像
        config: 配置
        state: init返回的状态

    Returns:
        检测结果
    """
    # 转换为BGR再转HSV
    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    frame_hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

    detections = []
    color_ranges = state.get('color_ranges', {})

    # 检测每种颜色
    for color_name, range_config in color_ranges.items():
        lower = np.array(range_config['lower'])
        upper = np.array(range_config['upper'])

        # 创建掩码
        mask = cv2.inRange(frame_hsv, lower, upper)

        # 查找轮廓
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # 提取边界框
        for cnt in contours:
            area = cv2.contourArea(cnt)

            # 过滤小区域
            if area > 1000:  # 最小面积阈值
                x, y, w, h = cv2.boundingRect(cnt)

                detections.append({
                    'box': [int(x), int(y), int(x + w), int(y + h)],
                    'label': color_name,
                    'confidence': min(1.0, area / 10000),  # 简单的置信度计算
                    'class': 0,
                    'metadata': {
                        'area': int(area),
                        'color': color_name
                    }
                })

    return {
        'detections': detections,
        'metadata': {
            'total_detections': len(detections),
            'colors_detected': list(set([d['metadata']['color'] for d in detections]))
        }
    }


def cleanup(state):
    """清理资源"""
    pass
