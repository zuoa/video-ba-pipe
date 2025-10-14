import os
from abc import ABC, abstractmethod

import cv2
import numpy as np

from app import logger


class BaseAlgorithm(ABC):
    """
    所有算法插件必须继承的抽象基类。
    它定义了插件的生命周期和核心处理方法。
    """

    def __init__(self, algo_config: dict):
        """
        初始化算法实例。

        Args:
            algo_config (dict): 从数据库'algorithms'表中读取的配置，
                               包含 'model_path' 和其他自定义参数。
        """
        self.config = algo_config
        self.models = []
        self.load_model()

    @property
    @abstractmethod
    def name(self) -> str:
        """
        算法的唯一名称，必须与数据库中 'algorithms.name' 字段完全匹配。
        """
        pass

    @abstractmethod
    def load_model(self):
        """
        加载模型到内存（CPU或GPU）。
        此方法在 __init__ 中被调用。
        """
        pass

    @abstractmethod
    def process(self, frame: np.ndarray) -> dict:
        """
        处理单帧图像的核心方法。

        Args:
            frame (np.ndarray): 从环形缓冲区读取的原始视频帧 (RGB格式)。

        Returns:
            dict: 结构化的检测结果。例如：
                  {'detections': [{'box': [x1,y1,x2,y2], 'label': 'person', 'confidence': 0.98}]}
                  如果没有检测到任何东西，应返回一个空的结果结构，例如 {'detections': []}。
        """
        pass

    @staticmethod
    def hex_to_bgr(hex_color):
        """
        将十六进制颜色转换为BGR格式
        :param hex_color: 十六进制颜色字符串，如 '#FF0000'
        :return: BGR颜色元组
        """
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return (b, g, r)  # OpenCV使用BGR格式

    @staticmethod
    def visualize(img, results, save_path=None, label_color='#FF0000'):
        """
        可视化检测结果
        :param img: 原始图像
        :param results: 检测结果列表
        :param save_path: 保存路径
        :param label_color: 标签颜色（十六进制格式）
        """
        img_vis = img.copy()
        img_vis = cv2.cvtColor(img_vis, cv2.COLOR_RGB2BGR)

        # 转换主标签颜色
        main_color = BaseAlgorithm.hex_to_bgr(label_color)
        
        # 定义stages的颜色列表（不同颜色用于区分不同stage）
        stage_colors = [
            (0, 255, 0),    # 绿色
            (255, 0, 255),  # 洋红色
            (0, 255, 255),  # 黄色
            (255, 255, 0),  # 青色
            (128, 0, 128),  # 紫色
            (255, 165, 0),  # 橙色
        ]

        # 绘制检测结果
        for result in results:
            x1, y1, x2, y2 = map(int, result.get('box', [0, 0, 0, 0]))
            logger.debug(f"Main detection box: {x1, y1, x2, y2}")

            label_prefix = result.get('label_name', 'Object')
            cls = result.get('class', 0)
            conf = result.get('confidence', 1.0)
            stages = result.get('stages', [])


            # 绘制stages信息
            if stages:
                logger.debug(f"Drawing {len(stages)} stages")
                for i, stage in enumerate(stages):
                    stage_x1, stage_y1, stage_x2, stage_y2 = map(int, stage.get('box', [0, 0, 0, 0]))
                    stage_model = stage.get('model_name', f'Stage{i+1}')
                    stage_label = stage.get('label_name', stage_model)
                    stage_conf = stage.get('confidence', 0.0)

                    
                    # 使用循环颜色为不同stage分配颜色
                    stage_color = BaseAlgorithm.hex_to_bgr(stage.get('label_color', label_color))
                    
                    # 绘制stage检测框（较细的线条）
                    cv2.rectangle(img_vis, (stage_x1, stage_y1), (stage_x2, stage_y2), stage_color, 1)
                    
                    # 绘制stage标签
                    stage_label = f"{stage_label}: {stage_conf:.2f}"
                    # 在stage框的右下角显示标签
                    label_y = stage_y2 + 15 + (i * 15)  # 垂直偏移避免重叠
                    cv2.putText(img_vis, stage_label, (stage_x1, label_y),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, stage_color, 1)
            else:
                # 绘制主检测框（使用label_color）
                cv2.rectangle(img_vis, (x1, y1), (x2, y2), main_color, 3)
                label = f"{label_prefix}: {conf:.2f}"
                cv2.putText(img_vis, label, (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, main_color, 2)

        if save_path:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            cv2.imwrite(save_path, img_vis)
            logger.debug(f"已保存可视化结果到 {save_path}")

        return img_vis
