"""PaddleOCR-backed local OCR algorithm."""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from app import logger
from app.config import VIDEO_FRAME_PIXEL_FORMAT
from app.core.algorithm import BaseAlgorithm
from app.core.cv2_compat import cv2, require_cv2
from app.core.frame_utils import detect_frame_pixel_format, frame_to_rgb, infer_frame_dimensions
from app.core.model_resolver import get_model_resolver


def _jsonable_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    payload = getattr(result, "json", None)
    if callable(payload):
        payload = payload()
    if isinstance(payload, dict):
        return payload.get("res", payload)
    return {}


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, np.ndarray):
        return value.tolist()
    return list(value) if isinstance(value, (list, tuple)) else []


def _first_present(payload: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def normalize_ocr_output(results: Iterable[Any], score_threshold: float = 0.5) -> Dict[str, Any]:
    detections: List[Dict[str, Any]] = []
    for raw_result in results or []:
        payload = _jsonable_result(raw_result)
        texts = _as_list(_first_present(payload, "rec_texts", "texts"))
        scores = _as_list(_first_present(payload, "rec_scores", "scores"))
        polygons = _as_list(_first_present(payload, "rec_polys", "dt_polys", "polygons"))
        boxes = _as_list(_first_present(payload, "rec_boxes", "boxes"))

        for index, raw_text in enumerate(texts):
            text = str(raw_text or "").strip()
            if not text:
                continue
            try:
                confidence = float(scores[index]) if index < len(scores) else 1.0
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence < score_threshold:
                continue

            polygon = _as_list(polygons[index]) if index < len(polygons) else []
            box = _as_list(boxes[index]) if index < len(boxes) else []
            if polygon:
                polygon = [[float(point[0]), float(point[1])] for point in polygon if len(point) >= 2]
            if len(box) >= 4:
                box = [float(value) for value in box[:4]]
            elif polygon:
                xs = [point[0] for point in polygon]
                ys = [point[1] for point in polygon]
                box = [min(xs), min(ys), max(xs), max(ys)]
            else:
                box = []

            detection = {
                "text": text,
                "label_name": text,
                "class_name": "text",
                "confidence": confidence,
            }
            if box:
                detection["box"] = box
                detection["bbox"] = box
            if polygon:
                detection["polygon"] = polygon
            detections.append(detection)

    return {
        "detections": detections,
        "full_text": "\n".join(item["text"] for item in detections),
    }


class OCRAlgorithm(BaseAlgorithm):
    name = "ocr_algorithm"

    def load_model(self):
        self.ocr_config = dict(self.config.get("ocr_config") or {})
        resolver = get_model_resolver()
        detection_model = resolver._get_model_info(self.ocr_config.get("detection_model_id"))
        recognition_model = resolver._get_model_info(self.ocr_config.get("recognition_model_id"))
        if not detection_model or not recognition_model:
            raise RuntimeError("OCR 检测或识别模型不存在")

        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError("当前运行平台未安装 PaddleOCR，OCR 仅支持 CPU/CUDA 镜像") from exc

        options: Dict[str, Any] = {
            "text_detection_model_dir": detection_model["path"],
            "text_recognition_model_dir": recognition_model["path"],
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "text_recognition_batch_size": int(self.ocr_config.get("recognition_batch_size") or 1),
        }
        device = self.ocr_config.get("device", "auto")
        if device != "auto":
            options["device"] = device
        optional_parameters = {
            "detection_threshold": "text_det_thresh",
            "box_threshold": "text_det_box_thresh",
            "unclip_ratio": "text_det_unclip_ratio",
            "limit_side_len": "text_det_limit_side_len",
        }
        for config_key, paddle_key in optional_parameters.items():
            if self.ocr_config.get(config_key) is not None:
                options[paddle_key] = self.ocr_config[config_key]

        self.pipeline = PaddleOCR(**options)
        self.detection_model_id = self.ocr_config["detection_model_id"]
        self.recognition_model_id = self.ocr_config["recognition_model_id"]
        logger.info(
            "[OCR] 模型加载完成: detection=%s recognition=%s device=%s",
            self.detection_model_id,
            self.recognition_model_id,
            device,
        )

    def _empty_result(self, error: str, latency_ms: Optional[float] = None) -> Dict[str, Any]:
        metadata = {
            "ocr_checked": False,
            "error": error,
            "detection_model_id": self.ocr_config.get("detection_model_id"),
            "recognition_model_id": self.ocr_config.get("recognition_model_id"),
        }
        if latency_ms is not None:
            metadata["inference_time_ms"] = round(latency_ms, 2)
        return {"detections": [], "metadata": metadata}

    def process(self, frame: np.ndarray, roi_regions: list = None, upstream_results: dict = None) -> dict:
        started_at = time.perf_counter()
        try:
            pixel_format = detect_frame_pixel_format(
                frame,
                pixel_format=self.config.get("pixel_format", VIDEO_FRAME_PIXEL_FORMAT),
            )
            frame_width, frame_height = infer_frame_dimensions(frame, pixel_format=pixel_format)
            frame_rgb = frame_to_rgb(
                frame,
                pixel_format=pixel_format,
                width=frame_width,
                height=frame_height,
            )
            roi_mask = None
            input_rgb = frame_rgb
            if roi_regions:
                roi_mask = self.create_roi_mask(frame_rgb.shape, roi_regions)
                input_rgb = self.apply_roi_mask(frame_rgb, roi_mask)

            require_cv2()
            input_bgr = cv2.cvtColor(input_rgb, cv2.COLOR_RGB2BGR)
            raw_results = self.pipeline.predict(input=input_bgr)
            normalized = normalize_ocr_output(
                raw_results,
                score_threshold=float(self.ocr_config.get("recognition_score_threshold") or 0),
            )

            detections = normalized["detections"]
            before_roi = len(detections)
            if roi_mask is not None:
                filtered = []
                for detection in detections:
                    box = detection.get("box")
                    if not box:
                        continue
                    center_x = max(0, min(int((box[0] + box[2]) / 2), frame_width - 1))
                    center_y = max(0, min(int((box[1] + box[3]) / 2), frame_height - 1))
                    if roi_mask[center_y, center_x] > 0:
                        filtered.append(detection)
                detections = filtered

            latency_ms = (time.perf_counter() - started_at) * 1000
            full_text = "\n".join(item["text"] for item in detections)
            return {
                "detections": detections,
                "roi_mask": roi_mask,
                "metadata": {
                    "ocr_checked": True,
                    "full_text": full_text,
                    "text_count": len(detections),
                    "inference_time_ms": round(latency_ms, 2),
                    "detection_model_id": self.detection_model_id,
                    "recognition_model_id": self.recognition_model_id,
                    "roi_applied": roi_mask is not None,
                    "roi_filtered_count": before_roi - len(detections),
                },
            }
        except Exception as exc:
            latency_ms = (time.perf_counter() - started_at) * 1000
            logger.error("[OCR] 推理失败: %s", exc, exc_info=True)
            return self._empty_result(str(exc), latency_ms)
