import cv2
import numpy as np
from ultralytics import YOLO

from app import logger
from app.core.algorithm import BaseAlgorithm


# 假设你使用 some_yolo_library 来进行推理
# import torch

class PersonDetector(BaseAlgorithm):
    """一个具体的人流检测算法实现"""

    @property
    def name(self) -> str:
        # 这个名称必须和数据库 'algorithms' 表中的 'name' 字段一致
        return "person_detection"

    def load_model(self):
        print(f"[{self.name}] 正在加载模型: {self.config['model_path']}...")
        # 你的模型加载代码
        self.model = YOLO(self.config['model_path'])
        print(f"[{self.name}] 模型加载成功。")

    def process(self, frame: np.ndarray) -> dict:
        confidence_threshold = self.config.get("confidence", 0.5)

        frame = frame.copy()
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        # 3) 只检测人
        people_results = None
        try:
            people_results = self.model.predict(frame, save=False, classes=[0], conf=confidence_threshold)
        except Exception as e:
            logger.warn(f"[] person_model.predict 出错: {e}")

        if people_results:
            return {'detections': people_results[0]}

        return {'detections': None}
