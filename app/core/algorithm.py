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
        self.model = None
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
    def visualize(img, result, label_prefix='Object', save_path=None):
        """
        可视化检测结果
        :param label_prefix:
        :param img: 原始图像
        :param result: 第一阶段检测结果
        :param save_path: 保存路径
        """
        img_vis = img.copy()

        # 绘制第一阶段检测框（蓝色）
        for box in result.boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0].cpu().numpy())
            cls = int(box.cls[0])
            conf = float(box.conf[0])

            cv2.rectangle(img_vis, (x1, y1), (x2, y2), (255, 0, 0), 2)
            label = f"{label_prefix}-C{cls}: {conf:.2f}"
            cv2.putText(img_vis, label, (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)

        if save_path:
            cv2.imwrite(save_path, img_vis)
            logger.debug(f"已保存可视化结果到 {save_path}")

        return img_vis
