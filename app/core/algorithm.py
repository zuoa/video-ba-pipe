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
    def process(self, frame: np.ndarray, roi_regions: list = None) -> dict:
        """
        处理单帧图像的核心方法。

        Args:
            frame (np.ndarray): 从环形缓冲区读取的原始视频帧 (RGB格式)。
            roi_regions (list): ROI热区配置，格式为 [{"points": [[x1,y1], [x2,y2], ...], "name": "区域1"}]
                               如果为None或空列表，则使用全画面检测。

        Returns:
            dict: 结构化的检测结果。例如：
                  {'detections': [{'box': [x1,y1,x2,y2], 'label': 'person', 'confidence': 0.98}]}
                  如果没有检测到任何东西，应返回一个空的结果结构，例如 {'detections': []}。
        """
        pass
    
    @staticmethod
    def create_roi_mask(frame_shape: tuple, roi_regions: list) -> np.ndarray:
        """
        根据ROI热区配置创建掩码
        
        Args:
            frame_shape: 图像形状 (height, width, channels)
            roi_regions: ROI热区配置列表
            
        Returns:
            mask: 二值掩码，热区内为255，热区外为0
        """
        if not roi_regions:
            # 如果没有ROI配置，返回全白掩码（全画面检测）
            return np.ones((frame_shape[0], frame_shape[1]), dtype=np.uint8) * 255
        
        # 创建黑色掩码
        mask = np.zeros((frame_shape[0], frame_shape[1]), dtype=np.uint8)
        
        # 在每个ROI区域绘制白色多边形
        for region in roi_regions:
            points = region.get('points', [])
            if len(points) >= 3:
                # 转换为numpy数组
                pts = np.array(points, dtype=np.int32)
                # 填充多边形
                cv2.fillPoly(mask, [pts], 255)
        
        return mask
    
    @staticmethod
    def apply_roi_mask(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """
        将ROI掩码应用到图像上
        
        Args:
            frame: 原始图像
            mask: ROI掩码
            
        Returns:
            masked_frame: 应用掩码后的图像
        """
        # 将掩码应用到每个通道
        masked_frame = cv2.bitwise_and(frame, frame, mask=mask)
        return masked_frame
    
    @staticmethod
    def filter_detections_by_roi(detections: list, mask: np.ndarray) -> list:
        """
        根据ROI掩码过滤检测结果，只保留中心点在ROI内的检测
        
        Args:
            detections: 检测结果列表
            mask: ROI掩码
            
        Returns:
            filtered_detections: 过滤后的检测结果
        """
        if mask is None:
            return detections
        
        filtered = []
        for det in detections:
            box = det.get('box', [])
            if len(box) >= 4:
                # 计算边界框中心点
                center_x = int((box[0] + box[2]) / 2)
                center_y = int((box[1] + box[3]) / 2)
                
                # 检查中心点是否在ROI内
                if (0 <= center_y < mask.shape[0] and 
                    0 <= center_x < mask.shape[1] and 
                    mask[center_y, center_x] > 0):
                    filtered.append(det)
        
        return filtered

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
    def visualize(img, results, save_path=None, label_color='#FF0000', roi_mask=None, roi_regions=None):
        """
        可视化检测结果
        :param img: 原始图像
        :param results: 检测结果列表
        :param save_path: 保存路径
        :param label_color: 标签颜色（十六进制格式）
        :param roi_mask: ROI掩码，如果提供则在图像上显示ROI区域（已弃用，建议使用roi_regions）
        :param roi_regions: ROI热区配置列表，格式为 [{"polygon": [[x1,y1], [x2,y2], ...], ...}]
        """
        img_vis = img.copy()
        img_vis = cv2.cvtColor(img_vis, cv2.COLOR_RGB2BGR)

        # 如果有roi_regions配置，优先使用roi_regions绘制热区（支持多边形）
        if roi_regions and len(roi_regions) > 0:
            # 创建半透明层用于绘制ROI热区
            roi_overlay = img_vis.copy()

            for region in roi_regions:
                polygon = region.get('polygon', [])
                if len(polygon) >= 3:
                    # 转换为numpy数组
                    pts = np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))

                    # 在overlay上填充淡绿色半透明区域
                    # 使用很淡的绿色：(144, 238, 144) - BGR格式
                    cv2.fillPoly(roi_overlay, [pts], (144, 238, 144))

                    # 绘制热区边界线（稍深一点的绿色）
                    cv2.polylines(roi_overlay, [pts], True, (100, 200, 100), 2)

            # 将overlay以很淡的透明度叠加到原图上（0.15表示15%不透明度，非常淡）
            cv2.addWeighted(img_vis, 0.85, roi_overlay, 0.15, 0, img_vis)

        # 如果有ROI掩码（兼容旧代码），在图像上绘制ROI区域轮廓
        elif roi_mask is not None:
            # 找到ROI区域的轮廓
            contours, _ = cv2.findContours(roi_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            # 绘制半透明的ROI区域
            roi_overlay = img_vis.copy()
            cv2.drawContours(roi_overlay, contours, -1, (0, 255, 255), -1)  # 黄色填充
            cv2.addWeighted(img_vis, 0.9, roi_overlay, 0.1, 0, img_vis)
            # 绘制ROI边界线
            cv2.drawContours(img_vis, contours, -1, (0, 255, 255), 2)  # 黄色边线

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
