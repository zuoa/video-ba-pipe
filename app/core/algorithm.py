import os
from abc import ABC, abstractmethod
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app import logger
from app.core.cv2_compat import cv2, require_cv2


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
            frame (np.ndarray): 从环形缓冲区读取的原始视频帧（主格式为 NV12）。
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
        height, width = frame_shape[0], frame_shape[1]
        try:
            cv2_impl = require_cv2()
        except ImportError:
            cv2_impl = None

        # 在每个ROI区域绘制白色多边形
        for region in roi_regions:
            pts = BaseAlgorithm._resolve_roi_points(region, width, height)
            if pts is None:
                continue

            if cv2_impl is not None:
                cv2_impl.fillPoly(mask, [pts.astype(np.int32)], 255)
            else:
                BaseAlgorithm._fill_polygon_numpy(mask, pts, 255)

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
        try:
            cv2_impl = require_cv2()
        except ImportError:
            masked_frame = frame.copy()
            masked_frame[mask == 0] = 0
            return masked_frame
        # 将掩码应用到每个通道
        masked_frame = cv2_impl.bitwise_and(frame, frame, mask=mask)
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
    def _get_detection_box(det):
        """兼容 box/bbox/xyxy 三种字段名。"""
        if not isinstance(det, dict):
            return None
        box = det.get('box')
        if box is None:
            box = det.get('bbox')
        if box is None:
            box = det.get('xyxy')
        if not isinstance(box, (list, tuple)) or len(box) < 4:
            return None
        return box

    @staticmethod
    def _get_detection_label(det, default='Object'):
        """兼容 label/label_name/class_name，并展开显示标签中的 {class}。"""
        if not isinstance(det, dict):
            return default

        label_template = det.get('label_name') or det.get('label') or det.get('class_name') or default
        if not isinstance(label_template, str) or '{class}' not in label_template:
            return label_template

        class_name = det.get('class_name')
        raw_label = det.get('label')
        if class_name is None and raw_label and raw_label != label_template:
            class_name = raw_label
        if class_name is None:
            class_name = det.get('class')
        if class_name is None:
            return label_template
        return label_template.replace('{class}', str(class_name))

    @staticmethod
    def normalize_detection_results(detections):
        """
        将不同检测器的常见字段别名统一成工作流可视化使用的格式。

        不强制要求检测框，避免丢弃 VL 等只返回语义结果的检测项。
        """
        normalized_results = []
        for det in detections or []:
            if not isinstance(det, dict):
                continue

            normalized = dict(det)
            if normalized.get('box') is None:
                box = normalized.get('bbox')
                if box is None:
                    box = normalized.get('xyxy')
                if box is not None:
                    normalized['box'] = box

            if normalized.get('confidence') is None and normalized.get('score') is not None:
                normalized['confidence'] = normalized.get('score')

            if normalized.get('label') is None and normalized.get('class_name') is not None:
                normalized['label'] = normalized.get('class_name')

            normalized['label_name'] = BaseAlgorithm._get_detection_label(normalized)

            stages = normalized.get('stages')
            if isinstance(stages, list):
                normalized['stages'] = BaseAlgorithm.normalize_detection_results(stages)

            normalized_results.append(normalized)

        return normalized_results

    @staticmethod
    def _get_detection_confidence(det, default=1.0):
        """兼容 confidence/score。"""
        if not isinstance(det, dict):
            return default
        conf = det.get('confidence')
        if conf is None:
            conf = det.get('score')
        return conf if conf is not None else default

    @staticmethod
    def _normalize_box_for_canvas(box, width: int, height: int):
        """
        将检测框统一转换为画布坐标。
        兼容绝对像素 xyxy、归一化 xyxy，以及 RKNN 场景中常见的 xywh/cxcywh 表达。
        """
        if not isinstance(box, (list, tuple)) or len(box) < 4 or width <= 0 or height <= 0:
            return None

        try:
            values = [float(v) for v in box[:4]]
        except Exception:
            return None

        if max(abs(v) for v in values) <= 1.5:
            values[0] *= width
            values[1] *= height
            values[2] *= width
            values[3] *= height

        x1, y1, x2, y2 = values

        if x2 <= x1 or y2 <= y1:
            cx, cy, w, h = values
            if w <= 0 or h <= 0:
                return None
            x1 = cx - w / 2.0
            y1 = cy - h / 2.0
            x2 = cx + w / 2.0
            y2 = cy + h / 2.0

        x1 = max(0, min(int(round(x1)), width - 1))
        y1 = max(0, min(int(round(y1)), height - 1))
        x2 = max(0, min(int(round(x2)), width - 1))
        y2 = max(0, min(int(round(y2)), height - 1))

        if x2 <= x1 or y2 <= y1:
            return None

        return x1, y1, x2, y2

    @staticmethod
    def _resolve_roi_points(region, width: int, height: int):
        points = region.get('polygon', region.get('points', []))
        if not points or len(points) < 3:
            return None

        if isinstance(points[0], dict):
            pts = [[float(p['x']) * width, float(p['y']) * height] for p in points]
        else:
            pts = points

        try:
            pts_array = np.array(pts, dtype=np.float32)
        except Exception:
            return None
        if pts_array.ndim != 2 or pts_array.shape[1] < 2:
            return None
        return pts_array[:, :2]

    @staticmethod
    def _fill_polygon_numpy(mask: np.ndarray, pts: np.ndarray, value: int = 255):
        if pts is None or len(pts) < 3:
            return

        height, width = mask.shape[:2]
        min_x = max(0, int(np.floor(np.min(pts[:, 0]))))
        max_x = min(width - 1, int(np.ceil(np.max(pts[:, 0]))))
        min_y = max(0, int(np.floor(np.min(pts[:, 1]))))
        max_y = min(height - 1, int(np.ceil(np.max(pts[:, 1]))))
        if min_x > max_x or min_y > max_y:
            return

        yy, xx = np.mgrid[min_y:max_y + 1, min_x:max_x + 1]
        x = xx.astype(np.float32) + 0.5
        y = yy.astype(np.float32) + 0.5
        inside = np.zeros(x.shape, dtype=bool)

        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            intersects = ((yi > y) != (yj > y)) & (
                x < ((xj - xi) * (y - yi) / ((yj - yi) + 1e-12) + xi)
            )
            inside ^= intersects
            j = i

        mask[min_y:max_y + 1, min_x:max_x + 1][inside] = value

    @staticmethod
    def _draw_rectangle_numpy(img: np.ndarray, box, color, thickness: int = 1):
        x1, y1, x2, y2 = box
        thickness = max(1, int(thickness))
        img[y1:min(y1 + thickness, y2 + 1), x1:x2 + 1] = color
        img[max(y2 - thickness + 1, y1):y2 + 1, x1:x2 + 1] = color
        img[y1:y2 + 1, x1:min(x1 + thickness, x2 + 1)] = color
        img[y1:y2 + 1, max(x2 - thickness + 1, x1):x2 + 1] = color

    @staticmethod
    def _iter_dashed_polygon_segments(pts: np.ndarray, dash_length: int = 10, gap_length: int = 6):
        """Yield visible line segments for a closed dashed polygon."""
        if pts is None:
            return

        points = np.asarray(pts, dtype=np.float32).reshape((-1, 2))
        if len(points) < 2:
            return

        dash_length = max(1, int(dash_length))
        gap_length = max(0, int(gap_length))
        period = dash_length + gap_length

        for index, start in enumerate(points):
            end = points[(index + 1) % len(points)]
            edge = end - start
            edge_length = float(np.linalg.norm(edge))
            if edge_length <= 0:
                continue

            direction = edge / edge_length
            offset = 0.0
            while offset < edge_length:
                segment_end = min(offset + dash_length, edge_length)
                yield start + direction * offset, start + direction * segment_end
                offset += period

    @staticmethod
    def _draw_dashed_polygon(img: np.ndarray, pts: np.ndarray, color, thickness: int = 2,
                             dash_length: int = 10, gap_length: int = 6):
        """Draw a closed dashed polygon with OpenCV."""
        for start, end in BaseAlgorithm._iter_dashed_polygon_segments(
            pts, dash_length=dash_length, gap_length=gap_length
        ):
            cv2.line(
                img,
                tuple(np.rint(start).astype(int)),
                tuple(np.rint(end).astype(int)),
                color,
                thickness,
                cv2.LINE_AA,
            )

    @staticmethod
    def _draw_dashed_polygon_numpy(img: np.ndarray, pts: np.ndarray, color, thickness: int = 2,
                                   dash_length: int = 10, gap_length: int = 6):
        """Draw the same dashed ROI outline when OpenCV is unavailable."""
        height, width = img.shape[:2]
        thickness = max(1, int(thickness))
        before = (thickness - 1) // 2
        after = thickness // 2

        for start, end in BaseAlgorithm._iter_dashed_polygon_segments(
            pts, dash_length=dash_length, gap_length=gap_length
        ):
            delta = end - start
            steps = max(1, int(np.ceil(np.max(np.abs(delta)))))
            samples = np.linspace(start, end, steps + 1)
            for sample_x, sample_y in np.rint(samples).astype(int):
                x1 = max(0, sample_x - before)
                x2 = min(width, sample_x + after + 1)
                y1 = max(0, sample_y - before)
                y2 = min(height, sample_y + after + 1)
                img[y1:y2, x1:x2] = color

    @staticmethod
    @lru_cache(maxsize=8)
    def _load_unicode_font(font_size: int):
        """Load a CJK-capable font for labels that OpenCV cannot render."""
        configured_font = os.getenv('VIDEO_BA_FONT_PATH')
        font_candidates = [
            configured_font,
            '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
            '/System/Library/Fonts/STHeiti Medium.ttc',
            '/System/Library/Fonts/STHeiti Light.ttc',
            '/System/Library/Fonts/Hiragino Sans GB.ttc',
            os.path.expanduser('~/Library/Fonts/LXGWWenKai-Regular.ttf'),
            'C:/Windows/Fonts/msyh.ttc',
            'C:/Windows/Fonts/simhei.ttf',
        ]

        for font_path in font_candidates:
            if not font_path or not os.path.isfile(font_path):
                continue
            try:
                return ImageFont.truetype(font_path, font_size)
            except OSError:
                logger.warning(f"无法加载可视化字体: {font_path}")

        logger.warning(
            "未找到支持中文的可视化字体；可安装 fonts-wqy-zenhei，"
            "或通过 VIDEO_BA_FONT_PATH 指定字体文件"
        )
        return None

    @staticmethod
    def _draw_unicode_text(img: np.ndarray, text: str, origin, color, font_scale: float, thickness: int):
        """Draw Unicode text on a BGR OpenCV image via Pillow."""
        font_size = max(12, int(round(font_scale * 30)))
        font = BaseAlgorithm._load_unicode_font(font_size)
        if font is None:
            return False

        rgb_image = Image.fromarray(np.ascontiguousarray(img[:, :, ::-1]))
        drawer = ImageDraw.Draw(rgb_image)
        x, baseline_y = origin
        stroke_width = max(0, thickness - 1)
        text_box = drawer.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        text_height = text_box[3] - text_box[1]
        top_y = max(0, int(baseline_y - text_height - 2))
        b, g, r = color
        rgb_color = (int(r), int(g), int(b))
        drawer.text(
            (int(x), top_y),
            text,
            font=font,
            fill=rgb_color,
            stroke_width=stroke_width,
            stroke_fill=rgb_color,
        )
        img[:] = np.asarray(rgb_image)[:, :, ::-1]
        return True

    @staticmethod
    def _draw_text(img: np.ndarray, text, origin, color, font_scale: float, thickness: int):
        """Use OpenCV for ASCII labels and Pillow for Unicode labels."""
        label = str(text)
        if not label.isascii() and BaseAlgorithm._draw_unicode_text(
            img, label, origin, color, font_scale, thickness
        ):
            return

        cv2.putText(
            img,
            label,
            origin,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            color,
            thickness,
        )

    @staticmethod
    def _visualize_numpy(img, results, label_color='#FF0000', roi_regions=None):
        if img is None:
            return None

        # Match the OpenCV path's return convention: BGR image.
        if img.ndim == 3 and img.shape[2] >= 3:
            img_vis = img[:, :, :3][:, :, ::-1].copy()
        else:
            img_vis = img.copy()

        height, width = img_vis.shape[:2]
        if roi_regions:
            overlay_mask = np.zeros((height, width), dtype=np.uint8)
            roi_polygons = []
            for region in roi_regions:
                pts = BaseAlgorithm._resolve_roi_points(region, width, height)
                if pts is None:
                    continue
                roi_polygons.append(pts)
                BaseAlgorithm._fill_polygon_numpy(overlay_mask, pts, 255)
            if np.any(overlay_mask):
                img_vis[overlay_mask > 0] = (
                    img_vis[overlay_mask > 0].astype(np.float32) * 0.85
                    + np.array([144, 238, 144], dtype=np.float32) * 0.15
                ).astype(np.uint8)
            for pts in roi_polygons:
                BaseAlgorithm._draw_dashed_polygon_numpy(img_vis, pts, (50, 180, 50), 2)

        main_color = BaseAlgorithm.hex_to_bgr(label_color)
        for result in results or []:
            box = BaseAlgorithm._get_detection_box(result)
            canvas_box = BaseAlgorithm._normalize_box_for_canvas(box, width, height)
            if canvas_box is None:
                continue

            BaseAlgorithm._draw_rectangle_numpy(img_vis, canvas_box, main_color, 3)

            for stage in result.get('stages', []) or []:
                stage_box = BaseAlgorithm._get_detection_box(stage)
                stage_canvas_box = BaseAlgorithm._normalize_box_for_canvas(stage_box, width, height)
                if stage_canvas_box is None:
                    continue
                stage_color = BaseAlgorithm.hex_to_bgr(stage.get('label_color', label_color))
                BaseAlgorithm._draw_rectangle_numpy(img_vis, stage_canvas_box, stage_color, 1)

        return img_vis

    @staticmethod
    def visualize(img, results, save_path=None, label_color='#FF0000', roi_mask=None, roi_regions=None):
        """
        可视化检测结果
        :param img: 用于可视化的 RGB 图像视图（通常由 NV12 主帧按需转换而来）
        :param results: 检测结果列表
        :param save_path: 保存路径
        :param label_color: 标签颜色（十六进制格式）
        :param roi_mask: ROI掩码，如果提供则在图像上显示ROI区域（已弃用，建议使用roi_regions）
        :param roi_regions: ROI热区配置列表，格式为 [{"polygon": [[x1,y1], [x2,y2], ...], ...}]
        """
        try:
            require_cv2()
        except ImportError:
            if save_path:
                logger.warning("OpenCV 不可用，跳过可视化图片保存")
            return BaseAlgorithm._visualize_numpy(img, results, label_color=label_color, roi_regions=roi_regions)

        # OpenCV 绘图函数期望 BGR 格式
        img_vis = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

        # 如果有roi_regions配置，优先使用roi_regions绘制热区（支持多边形）
        if roi_regions and len(roi_regions) > 0:
            # 创建半透明层用于绘制ROI热区
            roi_overlay = img_vis.copy()
            height, width = img_vis.shape[:2]

            for region in roi_regions:
                # 支持两种字段名：'polygon'（新格式）和 'points'（旧格式）
                polygon = region.get('polygon', region.get('points', []))
                if len(polygon) < 3:
                    continue

                # 检查坐标格式
                # 如果是相对坐标格式 [{"x": 0.1, "y": 0.2}, ...]，转换为绝对坐标
                if isinstance(polygon[0], dict):
                    # 相对坐标格式，需要转换为绝对坐标
                    pts_list = [[int(p['x'] * width), int(p['y'] * height)] for p in polygon]
                else:
                    # 已经是绝对坐标格式 [[x1, y1], [x2, y2], ...]
                    pts_list = polygon

                # 转换为numpy数组并reshape为 (N, 1, 2)
                pts = np.array(pts_list, dtype=np.int32).reshape((-1, 1, 2))

                # 在overlay上填充淡绿色半透明区域
                # 使用很淡的绿色：(144, 238, 144) - BGR格式
                cv2.fillPoly(roi_overlay, [pts], (144, 238, 144))

            # 将overlay以很淡的透明度叠加到原图上（0.15表示15%不透明度，非常淡）
            cv2.addWeighted(img_vis, 0.85, roi_overlay, 0.15, 0, img_vis)

            # 叠加后再绘制边界线（完全不透明，更清晰）
            for region in roi_regions:
                polygon = region.get('polygon', region.get('points', []))
                if len(polygon) < 3:
                    continue

                if isinstance(polygon[0], dict):
                    pts_list = [[int(p['x'] * width), int(p['y'] * height)] for p in polygon]
                else:
                    pts_list = polygon

                pts = np.array(pts_list, dtype=np.int32).reshape((-1, 1, 2))
                # 绘制热区虚线边界（完全不透明，使用深绿色）
                BaseAlgorithm._draw_dashed_polygon(img_vis, pts, (50, 180, 50), 2)

        # 如果有ROI掩码（兼容旧代码），在图像上绘制ROI区域轮廓
        elif roi_mask is not None:
            # 找到ROI区域的轮廓
            contours, _ = cv2.findContours(roi_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            # 绘制半透明的ROI区域
            roi_overlay = img_vis.copy()
            cv2.drawContours(roi_overlay, contours, -1, (0, 255, 255), -1)  # 黄色填充
            cv2.addWeighted(img_vis, 0.9, roi_overlay, 0.1, 0, img_vis)
            # 绘制ROI虚线边界
            for contour in contours:
                BaseAlgorithm._draw_dashed_polygon(img_vis, contour, (0, 255, 255), 2)

        # 转换主标签颜色
        main_color = BaseAlgorithm.hex_to_bgr(label_color)
        
        # 绘制检测结果
        for result in results:
            box = BaseAlgorithm._get_detection_box(result)
            canvas_box = BaseAlgorithm._normalize_box_for_canvas(box, img_vis.shape[1], img_vis.shape[0])
            if canvas_box is None:
                logger.debug(f"Skip visualization for invalid detection payload: {result}")
                continue

            x1, y1, x2, y2 = canvas_box
            logger.debug(f"Main detection box: {x1, y1, x2, y2}")

            label_prefix = BaseAlgorithm._get_detection_label(result, 'Object')
            conf = BaseAlgorithm._get_detection_confidence(result, 1.0)
            stages = result.get('stages', [])

            # 主检测框始终绘制，避免多阶段结果只显示子框、主框缺失。
            cv2.rectangle(img_vis, (x1, y1), (x2, y2), main_color, 3)
            label = f"{label_prefix}: {conf:.2f}"
            label_y = y1 - 10 if y1 > 24 else y1 + 22
            BaseAlgorithm._draw_text(img_vis, label, (x1, label_y), main_color, 0.6, 2)

            # 绘制stages信息
            if stages:
                logger.debug(f"Drawing {len(stages)} stages")
                for i, stage in enumerate(stages):
                    stage_box = BaseAlgorithm._get_detection_box(stage)
                    stage_canvas_box = BaseAlgorithm._normalize_box_for_canvas(
                        stage_box, img_vis.shape[1], img_vis.shape[0]
                    )
                    if stage_canvas_box is None:
                        continue

                    stage_x1, stage_y1, stage_x2, stage_y2 = stage_canvas_box
                    stage_model = stage.get('model_name', f'Stage{i+1}')
                    stage_label = BaseAlgorithm._get_detection_label(stage, stage_model)
                    stage_conf = BaseAlgorithm._get_detection_confidence(stage, 0.0)

                    
                    # 使用循环颜色为不同stage分配颜色
                    stage_color = BaseAlgorithm.hex_to_bgr(stage.get('label_color', label_color))
                    
                    # 绘制stage检测框（较细的线条）
                    cv2.rectangle(img_vis, (stage_x1, stage_y1), (stage_x2, stage_y2), stage_color, 1)
                    
                    # 绘制stage标签
                    stage_label = f"{stage_label}: {stage_conf:.2f}"
                    # 在stage框的右下角显示标签
                    label_y = stage_y2 + 15 + (i * 15)  # 垂直偏移避免重叠
                    BaseAlgorithm._draw_text(
                        img_vis, stage_label, (stage_x1, label_y), stage_color, 0.4, 1
                    )

        if save_path:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            # img_vis 已经是 BGR 格式，直接保存
            cv2.imwrite(save_path, img_vis)
            logger.debug(f"已保存可视化结果到 {save_path}")

        return img_vis
