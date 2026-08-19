"""PaddleOCR backend for local algorithm nodes and the shared inference worker."""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from app.core.ocr_algorithm_config import OCR_DEFAULT_CONFIG


_PADDLE_OPTION_KEYS = {
    "detection_threshold": "text_det_thresh",
    "box_threshold": "text_det_box_thresh",
    "unclip_ratio": "text_det_unclip_ratio",
    "limit_side_len": "text_det_limit_side_len",
}


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


def filter_ocr_detections(
    detections: Iterable[Dict[str, Any]],
    score_threshold: float,
) -> List[Dict[str, Any]]:
    threshold = float(score_threshold or 0)
    filtered: List[Dict[str, Any]] = []
    for detection in detections or []:
        try:
            confidence = float(detection.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < threshold:
            continue
        filtered.append(detection)
    return filtered


def _file_stat(path: str) -> Tuple[int, int]:
    try:
        stat_result = os.stat(path)
        return int(stat_result.st_size), int(stat_result.st_mtime_ns)
    except OSError:
        return 0, 0


def ocr_constructor_config(ocr_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the PaddleOCR constructor options that identify a shared worker."""
    config = ocr_config if isinstance(ocr_config, dict) else {}
    device = str(config.get("device") or OCR_DEFAULT_CONFIG["device"]).strip().lower()
    try:
        batch_size = int(config.get("recognition_batch_size") or 1)
    except (TypeError, ValueError):
        batch_size = 1
    constructor = {
        "device": device or "auto",
        "recognition_batch_size": max(1, batch_size),
    }
    for key in ("detection_threshold", "box_threshold", "unclip_ratio", "limit_side_len"):
        value = config.get(key)
        if value not in (None, ""):
            constructor[key] = value
    return constructor


def build_paddleocr_options(
    detection_path: str,
    recognition_path: str,
    ocr_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    constructor = ocr_constructor_config(ocr_config)
    options: Dict[str, Any] = {
        "text_detection_model_dir": detection_path,
        "text_recognition_model_dir": recognition_path,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "text_recognition_batch_size": constructor["recognition_batch_size"],
    }
    device = constructor.get("device") or "auto"
    if device != "auto":
        options["device"] = device
    for config_key, paddle_key in _PADDLE_OPTION_KEYS.items():
        if constructor.get(config_key) is not None:
            options[paddle_key] = constructor[config_key]
    return options


def build_ocr_model_spec(
    *,
    detection_model_id: Any,
    detection_path: str,
    recognition_model_id: Any,
    recognition_path: str,
    ocr_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    detection_size, detection_mtime_ns = _file_stat(detection_path)
    recognition_size, recognition_mtime_ns = _file_stat(recognition_path)
    constructor = ocr_constructor_config(ocr_config)
    return {
        "model_id": int(detection_model_id) if str(detection_model_id).isdigit() else detection_model_id,
        "model_path": os.path.abspath(detection_path),
        "file_size": detection_size,
        "file_mtime_ns": detection_mtime_ns,
        "recognition_model_id": (
            int(recognition_model_id)
            if str(recognition_model_id).isdigit()
            else recognition_model_id
        ),
        "recognition_model_path": os.path.abspath(recognition_path),
        "recognition_file_size": recognition_size,
        "recognition_file_mtime_ns": recognition_mtime_ns,
        "framework": "paddleocr",
        "model_type": "OCR",
        "backend": "paddleocr",
        "classes": {},
        "model_postprocess": {},
        "input_shape": None,
        "input_width": 640,
        "input_height": 640,
        "backend_config": constructor,
    }


def shared_ocr_client_enabled() -> bool:
    if os.getenv("SHARED_INFERENCE_WORKER", "false").lower() in ("true", "1", "yes", "on"):
        return False
    try:
        from app.config import SHARED_INFERENCE_ENABLED
    except ImportError:
        return False
    return bool(SHARED_INFERENCE_ENABLED)


def _frame_to_bgr(frame: np.ndarray) -> np.ndarray:
    from app.core.cv2_compat import cv2, require_cv2

    require_cv2()
    if frame.ndim == 2:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if frame.ndim == 3 and frame.shape[2] == 3:
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    return frame


class PaddleOCRBackend:
    name = "paddleocr"

    def __init__(
        self,
        detection_path: str,
        recognition_path: str,
        ocr_config: Optional[Dict[str, Any]] = None,
    ):
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError("当前运行平台未安装 PaddleOCR，OCR 仅支持 CPU/CUDA 镜像") from exc

        self.ocr_config = dict(ocr_config or {})
        self.device = str(self.ocr_config.get("device") or "auto")
        self.pipeline = PaddleOCR(
            **build_paddleocr_options(detection_path, recognition_path, self.ocr_config)
        )
        self.model = self.pipeline

    @classmethod
    def from_worker_spec(cls, spec: Dict[str, Any], base_config: Optional[Dict[str, Any]] = None):
        ocr_config = {
            **(spec.get("backend_config") or {}),
            **(base_config or {}),
        }
        recognition_path = spec.get("recognition_model_path")
        if not recognition_path:
            raise ValueError("OCR 共享推理缺少 recognition_model_path")
        return cls(spec["model_path"], recognition_path, ocr_config)

    def infer(self, frame: np.ndarray):
        raw_results = self.pipeline.predict(input=_frame_to_bgr(frame))
        normalized = normalize_ocr_output(raw_results, score_threshold=0)
        detections = normalized["detections"]
        return detections, detections, {
            "full_text": normalized["full_text"],
            "device": self.device,
            "text_count": len(detections),
        }

    def cleanup(self):
        self.pipeline = None
        self.model = None


class SharedOCRBackend:
    name = "paddleocr"

    def __init__(self, spec: Dict[str, Any], ocr_config: Optional[Dict[str, Any]] = None):
        from app.core.shared_inference import SharedInferenceClient

        self.ocr_config = dict(ocr_config or {})
        self.client = SharedInferenceClient(spec=spec, config={})
        self.model = self.client
        self.device = (spec.get("backend_config") or {}).get("device") or "auto"
        self._overload_count = 0
        self._last_overload_log_at = float("-inf")

    def infer(self, frame: np.ndarray):
        import time

        from app import logger
        from app.core.shared_inference import SharedInferenceOverloaded

        try:
            response = self.client.infer(frame, {})
        except SharedInferenceOverloaded as exc:
            self._overload_count += 1
            now = time.monotonic()
            if now - self._last_overload_log_at >= 10.0:
                logger.warning(
                    "[OCR] 共享推理队列已满，已丢弃 %s 个分析帧: %s",
                    self._overload_count,
                    exc,
                )
                self._overload_count = 0
                self._last_overload_log_at = now
            return [], [], {
                "shared_inference": True,
                "overloaded": True,
                "device": self.device,
            }
        metadata = dict(response.get("metadata") or {})
        metadata["shared_inference"] = True
        metadata["model_key"] = self.client.model_key
        return response.get("detections") or [], response.get("details") or [], metadata

    def cleanup(self):
        if getattr(self, "client", None) is not None:
            self.client.close()
            self.client = None
        self.model = None
