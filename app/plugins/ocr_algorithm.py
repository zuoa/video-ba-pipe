"""OCR algorithm with PaddleOCR or RKNN PPOCR backends."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app import logger
from app.config import VIDEO_FRAME_PIXEL_FORMAT
from app.core.algorithm import BaseAlgorithm
from app.core.frame_utils import detect_frame_pixel_format, frame_to_rgb, infer_frame_dimensions
from app.core.model_resolver import get_model_resolver
from app.core.ocr_backend import (
    PaddleOCRBackend,
    RKNNOcrBackend,
    SharedOCRBackend,
    build_ocr_model_spec,
    filter_ocr_detections,
    normalize_ocr_output,  # noqa: F401 - backward-compatible public import
    shared_ocr_client_enabled,
)
from app.core.ocr_runtime import OCR_BACKEND_RKNN
from app.user_scripts.common.roi import (
    crop_frame,
    expand_and_clip_box,
    filter_items_by_regions,
    remap_detections_to_full_frame,
    split_regions,
)

_DEFAULT_INPUT_MODE = "frame"
_DEFAULT_EXPAND_RATIO = 0.1
_DEFAULT_MAX_CANDIDATES = 8
_DEFAULT_MIN_CROP_SIDE = 8
_MAX_CANDIDATES_CAP = 32
_MIN_CROP_SIDE_CAP = 64


def _polygon_bounding_box(polygon: Any) -> Optional[List[float]]:
    if not isinstance(polygon, (list, tuple, np.ndarray)):
        return None
    xs: List[float] = []
    ys: List[float] = []
    for point in polygon:
        if isinstance(point, dict):
            xs.append(float(point.get("x", 0.0)))
            ys.append(float(point.get("y", 0.0)))
        elif isinstance(point, (list, tuple, np.ndarray)) and len(point) >= 2:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _source_box(item: Dict[str, Any]) -> Optional[List[float]]:
    box = BaseAlgorithm._get_detection_box(item)
    if box is not None:
        return [float(value) for value in box[:4]]
    return _polygon_bounding_box(item.get("polygon"))


def _detection_confidence(item: Dict[str, Any]) -> float:
    try:
        return float(item.get("confidence", item.get("score", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _matches_class_filter(item: Dict[str, Any], class_filter: Sequence[Any]) -> bool:
    if not class_filter:
        return True
    allowed = {str(name) for name in class_filter}
    for key in ("class_name", "label", "label_name"):
        value = item.get(key)
        if value is not None and str(value) in allowed:
            return True
    return False


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
            detection_info=detection_model,
            recognition_info=recognition_model,
        )
        if shared_ocr_client_enabled():
            self.backend = SharedOCRBackend(spec, self.ocr_config)
            logger.info(
                "[OCR] 使用共享推理: detection=%s recognition=%s device=%s backend=%s key=%s",
                self.detection_model_id,
                self.recognition_model_id,
                device,
                spec.get("backend"),
                self.backend.client.model_key,
            )
            return

        if spec.get("backend") == OCR_BACKEND_RKNN:
            self.backend = RKNNOcrBackend(
                spec["model_path"],
                spec["recognition_model_path"],
                self.ocr_config,
                character_dict_path=spec.get("character_dict_path"),
                detection_input_shape=(spec.get("input_width"), spec.get("input_height")),
                recognition_input_shape=spec.get("recognition_input_shape"),
            )
        else:
            self.backend = PaddleOCRBackend(
                detection_model["path"],
                recognition_model["path"],
                self.ocr_config,
            )
            self.pipeline = self.backend.pipeline
        logger.info(
            "[OCR] 本地加载模型: detection=%s recognition=%s device=%s backend=%s",
            self.detection_model_id,
            self.recognition_model_id,
            device,
            spec.get("backend"),
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

    def _skip_upstream_empty(self, started_at: float) -> Dict[str, Any]:
        latency_ms = (time.perf_counter() - started_at) * 1000
        return {
            "detections": [],
            "metadata": {
                "ocr_checked": False,
                "execution_state": "skipped",
                "reason_code": "upstream_empty",
                "skipped": True,
                "input_kind": "crops",
                "input_count": 0,
                "inference_time_ms": round(latency_ms, 2),
                "detection_model_id": self.ocr_config.get("detection_model_id"),
                "recognition_model_id": self.ocr_config.get("recognition_model_id"),
            },
        }

    def _convert_frame_rgb(self, frame: np.ndarray) -> np.ndarray:
        pixel_format = detect_frame_pixel_format(
            frame,
            pixel_format=self.config.get("pixel_format", VIDEO_FRAME_PIXEL_FORMAT),
        )
        frame_width, frame_height = infer_frame_dimensions(frame, pixel_format=pixel_format)
        return frame_to_rgb(
            frame,
            pixel_format=pixel_format,
            width=frame_width,
            height=frame_height,
        )

    def _crop_runtime_config(self) -> Tuple[str, float, int, int, List[Any]]:
        input_mode = (
            self.config.get("input_mode")
            or self.ocr_config.get("input_mode")
            or _DEFAULT_INPUT_MODE
        )
        expand_ratio = self.config.get("expand_ratio", self.ocr_config.get("expand_ratio", _DEFAULT_EXPAND_RATIO))
        max_candidates = self.config.get(
            "max_candidates",
            self.ocr_config.get("max_candidates", _DEFAULT_MAX_CANDIDATES),
        )
        min_crop_side = self.config.get(
            "min_crop_side",
            self.ocr_config.get("min_crop_side", _DEFAULT_MIN_CROP_SIDE),
        )
        class_filter = self.config.get(
            "upstream_class_filter",
            self.ocr_config.get("upstream_class_filter") or [],
        )
        try:
            expand_ratio = min(max(float(expand_ratio), 0.0), 1.0)
        except (TypeError, ValueError):
            expand_ratio = _DEFAULT_EXPAND_RATIO
        try:
            max_candidates = int(max_candidates)
        except (TypeError, ValueError):
            max_candidates = _DEFAULT_MAX_CANDIDATES
        max_candidates = min(max(max_candidates, 1), _MAX_CANDIDATES_CAP)
        try:
            min_crop_side = int(min_crop_side)
        except (TypeError, ValueError):
            min_crop_side = _DEFAULT_MIN_CROP_SIDE
        min_crop_side = min(max(min_crop_side, 1), _MIN_CROP_SIDE_CAP)
        if not isinstance(class_filter, (list, tuple)):
            class_filter = []
        return str(input_mode).strip(), expand_ratio, max_candidates, min_crop_side, list(class_filter)

    def _collect_upstream_candidates(
        self,
        upstream_results: Dict[str, Any],
        class_filter: Sequence[Any],
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for upstream_id, result in upstream_results.items():
            if not isinstance(result, dict):
                continue
            detections = result.get("detections")
            if not isinstance(detections, list):
                continue
            for detection in detections:
                if not isinstance(detection, dict):
                    continue
                box = _source_box(detection)
                if box is None:
                    continue
                if not _matches_class_filter(detection, class_filter):
                    continue
                candidates.append({
                    "parent_node_id": upstream_id,
                    "box": box,
                    "confidence": _detection_confidence(detection),
                })
        return candidates

    def _process_full_frame(
        self,
        frame_rgb: np.ndarray,
        roi_regions: Optional[list],
        started_at: float,
    ) -> Dict[str, Any]:
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
            "input_kind": "frame",
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

    def _process_upstream_crops(
        self,
        frame: np.ndarray,
        upstream_results: Dict[str, Any],
        started_at: float,
        expand_ratio: float,
        max_candidates: int,
        min_crop_side: int,
        class_filter: Sequence[Any],
    ) -> Dict[str, Any]:
        if not isinstance(upstream_results, dict) or not upstream_results:
            return self._skip_upstream_empty(started_at)

        candidates = self._collect_upstream_candidates(upstream_results, class_filter)
        if not candidates:
            return self._skip_upstream_empty(started_at)

        candidates = sorted(candidates, key=lambda item: item["confidence"], reverse=True)
        pruned_count = 0
        if len(candidates) > max_candidates:
            pruned_count = len(candidates) - max_candidates
            candidates = candidates[:max_candidates]

        frame_rgb = self._convert_frame_rgb(frame)
        crops = []
        for candidate in candidates:
            crop_box = expand_and_clip_box(candidate["box"], frame_rgb.shape, expand_ratio)
            if crop_box is None:
                continue
            x1, y1, x2, y2 = crop_box
            if (x2 - x1) < min_crop_side or (y2 - y1) < min_crop_side:
                continue
            cropped = crop_frame(frame_rgb, crop_box)
            if cropped.size == 0:
                continue
            crops.append({
                "parent_node_id": candidate["parent_node_id"],
                "crop_box": crop_box,
                "crop_rgb": cropped,
            })

        if not crops:
            return self._skip_upstream_empty(started_at)

        detections: List[Dict[str, Any]] = []
        crop_boxes = []
        successful = 0
        failed = 0
        first_error = None
        last_success_metadata: Dict[str, Any] = {}
        score_threshold = float(self.ocr_config.get("recognition_score_threshold") or 0)

        for index, crop in enumerate(crops):
            crop_boxes.append(list(crop["crop_box"]))
            try:
                crop_detections, _details, infer_metadata = self.backend.infer(crop["crop_rgb"])
            except Exception as exc:
                failed += 1
                if first_error is None:
                    first_error = str(exc)
                logger.warning("[OCR] 裁剪推理失败: crop=%s error=%s", index, exc, exc_info=True)
                continue
            infer_metadata = infer_metadata or {}
            if infer_metadata.get("overloaded"):
                failed += 1
                if first_error is None:
                    first_error = "shared_inference_overloaded"
                continue
            successful += 1
            last_success_metadata = infer_metadata
            crop_detections = filter_ocr_detections(crop_detections, score_threshold)
            remapped = remap_detections_to_full_frame(crop_detections, crop["crop_box"])
            for item in remapped:
                item["parent_node_id"] = crop["parent_node_id"]
                item["parent_box"] = list(crop["crop_box"])
                item["source_crop_index"] = index
            detections.extend(remapped)

        latency_ms = (time.perf_counter() - started_at) * 1000
        if successful == 0:
            return self._empty_result(first_error or "ocr_crop_inference_failed", latency_ms)

        execution_state = "degraded" if failed else ("matched" if detections else "not_matched")
        full_text = "\n".join(item.get("text") or "" for item in detections)
        metadata = {
            "ocr_checked": True,
            "full_text": full_text,
            "text_count": len(detections),
            "inference_time_ms": round(latency_ms, 2),
            "detection_model_id": self.detection_model_id,
            "recognition_model_id": self.recognition_model_id,
            "roi_applied": False,
            "shared_inference": bool(last_success_metadata.get("shared_inference")),
            "input_kind": "crops",
            "input_count": len(crops),
            "pruned_count": pruned_count,
            "successful_inferences": successful,
            "failed_inferences": failed,
            "crop_boxes": crop_boxes,
            "expand_ratio": expand_ratio,
            "execution_state": execution_state,
            "skipped": False,
        }
        if last_success_metadata.get("model_key"):
            metadata["model_key"] = last_success_metadata["model_key"]
        if last_success_metadata.get("device"):
            metadata["device"] = last_success_metadata["device"]
        logger.debug(
            "[OCR] input_mode=upstream_crops input_count=%s forwarded_count=%s pruned_count=%s "
            "expand_ratio=%s inference_time_ms=%s execution_state=%s",
            len(crops),
            successful + failed,
            pruned_count,
            expand_ratio,
            metadata["inference_time_ms"],
            execution_state,
        )
        return {
            "detections": detections,
            "roi_mask": None,
            "metadata": metadata,
        }

    def process(self, frame: np.ndarray, roi_regions: list = None, upstream_results: dict = None) -> dict:
        started_at = time.perf_counter()
        try:
            input_mode, expand_ratio, max_candidates, min_crop_side, class_filter = self._crop_runtime_config()
            if input_mode != "upstream_crops":
                frame_rgb = self._convert_frame_rgb(frame)
                return self._process_full_frame(frame_rgb, roi_regions, started_at)

            # None = algorithm test page; {} = workflow empty upstream. Do not treat them the same.
            if upstream_results is None:
                frame_rgb = self._convert_frame_rgb(frame)
                result = self._process_full_frame(frame_rgb, roi_regions, started_at)
                metadata = result.setdefault("metadata", {})
                metadata["input_fallback"] = "frame"
                metadata["input_kind"] = "frame"
                return result

            return self._process_upstream_crops(
                frame,
                upstream_results,
                started_at,
                expand_ratio=expand_ratio,
                max_candidates=max_candidates,
                min_crop_side=min_crop_side,
                class_filter=class_filter,
            )
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
