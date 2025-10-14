import cv2
import numpy as np
from ultralytics import YOLO

from app import logger
from app.core.algorithm import BaseAlgorithm
from app.core.utils import find_multimodel_groups


# 假设你使用 some_yolo_library 来进行推理
# import torch

class TargetDetector(BaseAlgorithm):
    """一个具体的人流检测算法实现"""

    @property
    def name(self) -> str:
        # 这个名称必须和数据库 'algorithms' 表中的 'name' 字段一致
        return "target_detection"

    def load_model(self):
        logger.info(f"[{self.name}] 正在加载模型...")
        # 你的模型加载代码

        for model in self.config.get('models_config', {}).get("models", []):
            self.models.append(YOLO(model.get('path')))
            logger.info(f"[{self.name}] 模型{model.get('path')}加载成功。")

    def process(self, frame: np.ndarray) -> dict:

        frame = frame.copy()
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        confidence_threshold = 0.7  # 默认置信度阈值
        stages_results = {}
        # 1) 多模型检测
        for model_cfg, model in zip(self.config.get('models_config', {}).get("models", []), self.models):
            conf_thresh = model_cfg.get('confidence', confidence_threshold)
            try:
                results = model.predict(frame, save=False, classes=[0], conf=conf_thresh)
                if results and len(results) > 0:
                    stages_results[model_cfg.get('name')] = {}
                    stages_results[model_cfg.get('name')]['result'] = results[0]
                    stages_results[model_cfg.get('name')]['model_config'] = model_cfg
            except Exception as e:
                logger.warn(f"[{self.name}] 模型 {model_cfg.get('name')} 出错: {e}")
                logger.exception(e)

        detections = []
        # logger.info(stages_results)
        # 如果参与检测的模型少于2个，则无需进行碰撞检测
        if len(stages_results) < 2:
            # 直接返回第一个模型的结果（如果有的话）
            if stages_results:
                first_model_name = list(stages_results.keys())[0]
                first_model_results = stages_results[first_model_name].get('result')
                first_model_config = stages_results[first_model_name].get('model_config')
                for det in first_model_results.boxes.data.tolist():
                    x1, y1, x2, y2, conf, cls = det
                    detections.append({
                        'box': (x1, y1, x2, y2),
                        'label_name': first_model_config.get('label_name', 'Object'),
                        'label_color': first_model_config.get('label_color', '#00FF00'),
                        'class': int(cls),
                        'confidence': float(conf),
                    })
        else:
            # 2) 查找被所有模型共同检测到的目标组
            iou_thresh = 0.4  # 对于关联性检测，IoU可以适当放宽
            detection_groups = find_multimodel_groups(stages_results, iou_threshold=iou_thresh)

            # 3) 处理结果
            if detection_groups:
                logger.info(f"成功发现 {len(detection_groups)} 个被所有模型共同检测到的目标组:")
                for i, group in enumerate(detection_groups):
                    print(f"--- 目标组 {i + 1} ---")
                    # 打印组内每个检测框的详细信息
                    for detection in group:
                        logger.info(detection)
                        print(f"  - 来自模型 '{detection['model_name']}', "
                              f"检测到 '{detection['class_name']}' (置信度: {detection['confidence']:.2f})")
                    # 在这里可以进行更复杂的操作，比如计算这个组的平均置信度，

                    # 或者找到一个能包裹所有框的最小外接矩形作为最终结果等。

                    #能包裹所有框的最小外接矩形作为最终结果
                    x_min = min(d['bbox'][0] for d in group)
                    y_min = min(d['bbox'][1] for d in group)
                    x_max = max(d['bbox'][2] for d in group)
                    y_max = max(d['bbox'][3] for d in group)

                    # 构建stages信息，包含每个模型的检测结果
                    stages_info = []
                    for d in group:
                        stages_info.append({
                            'model_name': d['model_name'],
                            'box': tuple(d['bbox']),
                            'label_name': stages_results[d['model_name']].get('model_config', {}).get('label_name', 'Object'),
                            'label_color': stages_results[d['model_name']].get('model_config', {}).get('label_color', '#00FF00'),
                            'class': 0,  # 统一为Person类
                            'confidence': d['confidence']
                        })

                    detections.append({
                        'box': (x_min, y_min, x_max, y_max),
                        'label_name': self.config.get('label_name', 'Object'),
                        'label_color': self.config.get('label_color', '#00FF00'),
                        'class': 0,
                        'confidence': np.mean([d['confidence'] for d in group]),
                        'stages': stages_info
                    })

        # 3) 只检测人
        # people_results = None
        # try:
        #     people_results = self.model.predict(frame, save=False, classes=[0], conf=confidence_threshold)
        # except Exception as e:
        #     logger.warn(f"[] person_model.predict 出错: {e}")
        #
        # if people_results:
        #     return {'detections': people_results[0]}
        # logger.info(detections)
        return {'detections': detections}
