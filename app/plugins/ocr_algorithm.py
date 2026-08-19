"""PaddleOCR-backed OCR algorithm. Uses shared inference when enabled."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import numpy as np

from app import logger
from app.config import VIDEO_FRAME_PIXEL_FORMAT
from app.core.algorithm import BaseAlgorithm
from app.core.frame_utils import detect_frame_pixel_format, frame_to_rgb, infer_frame_dimensions
from app.core.model_resolver import get_model_resolver
from app.core.ocr_backend import (
    PaddleOCRBackend,
    SharedOCRBackend,
    build_ocr_model_spec,
    filter_ocr_detections,
    normalize_ocr_output,
    shared_ocr_client_enabled,
)
from app.user_scripts.common.roi import filter_items_by_regions, split_regions


class OCRAlgorithm(BaseAlgorithm):
    name = "ocr_algorithm"

    def load_model(self):
        self.ocr_config = dict(self.config.get("ocr_config") or {})
        resolver = get_model_resolver()
        detection_model = resolver._get_model_info(self.ocr_config.get("detection_model_id"))
        recognition_model = resolver._get_model_info(self.ocr_config.get("recognition_model_id"))
        if not detection_model or not recognition_model:
            raise RuntimeError("OCR 检测或识别模型不存在")

        self.detection_model_id = self.ocr_config["detection_model_id"]
        self.recognition_model_id = self.ocr_config["recognition_model_id"]
        self.pipeline = None
        device = self.ocr_config.get("device", "auto")
        spec = build_ocr_model_spec(
            detection_model_id=self.detection_model_id,
            detection_path=detection_model["path"],
            recognition_model_id=self.recognition_model_id,
            recognition_path=recognition_model["path"],
            ocr_config=self.ocr_config,
        )
        if shared_ocr_client_enabled():
            self.backend = SharedOCRBackend(spec, self.ocr_config)
            logger.info(
                "[OCR] 使用共享推理: detection=%s recognition=%s device=%s key=%s",
                self.detection_model_id,
                self.recognition_model_id,
                device,
                self.backend.client.model_key,
            )
            return

        self.backend = PaddleOCRBackend(
            detection_model["path"],
            recognition_model["path"],
            self.ocr_config,
        )
        self.pipeline = self.backend.pipeline
        logger.info(
            "[OCR] 本地加载模型: detection=%s recognition=%s device=%s",
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
            roi_filter_regions = []
            input_rgb = frame_rgb
            if roi_regions:
                roi_mask = self.create_roi_mask(frame_rgb.shape, roi_regions)
                input_rgb = self.apply_roi_mask(frame_rgb, roi_mask)
                _, crop_infer_regions, post_filter_regions = split_regions(roi_regions)
                roi_filter_regions = crop_infer_regions + post_filter_regions

            detections, _details, infer_metadata = self.backend.infer(input_rgb)
            if infer_metadata.get("overloaded"):
                latency_ms = (time.perf_counter() - started_at) * 1000
                return self._empty_result("shared_inference_overloaded", latency_ms)

            detections = filter_ocr_detections(
                detections,
                float(self.ocr_config.get("recognition_score_threshold") or 0),
            )
            before_roi = len(detections)
            if roi_filter_regions:
                detections = filter_items_by_regions(
                    detections,
                    frame_shape=frame_rgb.shape,
                    roi_regions=roi_filter_regions,
                    metric="center",
                )

            latency_ms = (time.perf_counter() - started_at) * 1000
            full_text = "\n".join(item.get("text") or "" for item in detections)
            metadata = {
                "ocr_checked": True,
                "full_text": full_text,
                "text_count": len(detections),
                "inference_time_ms": round(latency_ms, 2),
                "detection_model_id": self.detection_model_id,
                "recognition_model_id": self.recognition_model_id,
                "roi_applied": roi_mask is not None,
                "roi_filtered_count": before_roi - len(detections),
                "shared_inference": bool(infer_metadata.get("shared_inference")),
            }
            if infer_metadata.get("model_key"):
                metadata["model_key"] = infer_metadata["model_key"]
            if infer_metadata.get("device"):
                metadata["device"] = infer_metadata["device"]
            return {
                "detections": detections,
                "roi_mask": roi_mask,
                "metadata": metadata,
            }
        except Exception as exc:
            latency_ms = (time.perf_counter() - started_at) * 1000
            logger.error("[OCR] 推理失败: %s", exc, exc_info=True)
            return self._empty_result(str(exc), latency_ms)

    def cleanup(self):
        backend = getattr(self, "backend", None)
        if backend is not None and hasattr(backend, "cleanup"):
            backend.cleanup()
        self.backend = None
        self.pipeline = None
