"""RKNN PPOCR backend: detection + recognition on NPU."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from app.core.ocr_postprocess import (
    DEFAULT_DET_SIZE,
    DEFAULT_REC_SIZE,
    ctc_greedy_decode,
    db_detect_polygons,
    default_character_dict_path,
    find_character_dict_path,
    get_rotate_crop_image,
    load_character_dict,
    parse_input_size,
    prepare_recognition_image,
    resize_detection_image,
    resolve_rknn_model_path,
    sort_text_polygons,
)


def _frame_to_bgr(frame: np.ndarray) -> np.ndarray:
    from app.core.cv2_compat import cv2, require_cv2

    require_cv2()
    if frame.ndim == 2:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if frame.ndim == 3 and frame.shape[2] == 3:
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    return frame


def _to_model_color(image_bgr: np.ndarray, input_format: str) -> np.ndarray:
    from app.core.cv2_compat import cv2, require_cv2

    if input_format == "bgr":
        return image_bgr
    require_cv2()
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)


def _config_value(config: Dict[str, Any], key: str, default: Any) -> Any:
    value = config.get(key)
    return default if value in (None, "") else value


class RKNNOcrBackend:
    name = "rknn_ocr"

    def __init__(
        self,
        detection_path: str,
        recognition_path: str,
        ocr_config: Optional[Dict[str, Any]] = None,
        *,
        character_dict_path: Optional[str] = None,
        detection_input_shape: Optional[object] = None,
        recognition_input_shape: Optional[object] = None,
    ):
        from app.user_scripts.common.yolo_backends import (
            _acquire_rknn_runtime,
            _release_rknn_runtime,
        )

        self.ocr_config = dict(ocr_config or {})
        try:
            recognition_batch_size = int(
                _config_value(self.ocr_config, "recognition_batch_size", 1)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("RKNN OCR recognition_batch_size 必须为 1") from exc
        if recognition_batch_size != 1:
            raise ValueError("RKNN OCR 暂不支持批量识别，recognition_batch_size 必须为 1")
        self.device = str(self.ocr_config.get("device") or "auto")
        self.rknn_input_format = str(
            self.ocr_config.get("rknn_input_format") or "rgb"
        ).strip().lower()
        if self.rknn_input_format not in ("rgb", "bgr"):
            self.rknn_input_format = "rgb"

        self.detection_path = resolve_rknn_model_path(detection_path)
        self.recognition_path = resolve_rknn_model_path(recognition_path)
        self.det_width, self.det_height = parse_input_size(
            detection_input_shape or self.ocr_config.get("det_input_shape"),
            DEFAULT_DET_SIZE,
        )
        self.rec_width, self.rec_height = parse_input_size(
            recognition_input_shape or self.ocr_config.get("rec_input_shape"),
            DEFAULT_REC_SIZE,
        )
        dict_path = (
            character_dict_path
            or find_character_dict_path(self.recognition_path)
            or default_character_dict_path()
        )
        self.characters = load_character_dict(dict_path)
        self.character_dict_path = dict_path

        runtime_config = {
            "rknn_core_mask": self.ocr_config.get("rknn_core_mask") or "auto",
        }
        self._det_key, self._det_entry = _acquire_rknn_runtime(self.detection_path, runtime_config)
        try:
            self._rec_key, self._rec_entry = _acquire_rknn_runtime(
                self.recognition_path, runtime_config
            )
        except Exception:
            _release_rknn_runtime(self._det_key, self._det_entry)
            self._det_entry = None
            raise
        self.model = self._det_entry.runtime if self._det_entry is not None else None

    @classmethod
    def from_worker_spec(cls, spec: Dict[str, Any], base_config: Optional[Dict[str, Any]] = None):
        ocr_config = {
            **(spec.get("backend_config") or {}),
            **(base_config or {}),
        }
        recognition_path = spec.get("recognition_model_path")
        if not recognition_path:
            raise ValueError("OCR 共享推理缺少 recognition_model_path")
        return cls(
            spec["model_path"],
            recognition_path,
            ocr_config,
            character_dict_path=spec.get("character_dict_path"),
            detection_input_shape=(spec.get("input_width"), spec.get("input_height")),
            recognition_input_shape=spec.get("recognition_input_shape"),
        )

    def _infer_runtime(self, entry, image: np.ndarray) -> Sequence[np.ndarray]:
        from app.user_scripts.common.yolo_backends import _rknn_native_call_lock

        batched = np.expand_dims(np.ascontiguousarray(image), axis=0)
        with entry.inference_lock:
            with _rknn_native_call_lock():
                outputs = entry.runtime.inference(inputs=[batched])
        return outputs or []

    def infer(self, frame: np.ndarray):
        if self._det_entry is None or self._rec_entry is None:
            raise RuntimeError("RKNN OCR backend 已关闭")

        image_bgr = _frame_to_bgr(frame)
        source_h, source_w = image_bgr.shape[:2]
        model_image = _to_model_color(image_bgr, self.rknn_input_format)
        det_input = resize_detection_image(model_image, self.det_width, self.det_height)
        det_outputs = self._infer_runtime(self._det_entry, det_input)
        if not det_outputs:
            return [], [], self._metadata(0)

        thresh = float(_config_value(self.ocr_config, "detection_threshold", 0.3))
        box_thresh = float(_config_value(self.ocr_config, "box_threshold", 0.6))
        unclip_ratio = float(_config_value(self.ocr_config, "unclip_ratio", 1.5))
        max_candidates = int(_config_value(self.ocr_config, "det_max_candidates", 1000))
        polygons = sort_text_polygons(db_detect_polygons(
            det_outputs[0],
            source_w,
            source_h,
            thresh=thresh,
            box_thresh=box_thresh,
            unclip_ratio=unclip_ratio,
            max_candidates=max_candidates,
        ))

        detections: List[Dict[str, Any]] = []
        for points, det_score in polygons:
            try:
                crop = get_rotate_crop_image(model_image, points)
            except ValueError:
                continue
            rec_input = prepare_recognition_image(crop, self.rec_width, self.rec_height)
            rec_tensor = rec_input.astype(np.float32) / 255.0
            rec_outputs = self._infer_runtime(self._rec_entry, rec_tensor)
            if not rec_outputs:
                continue
            text, rec_score = ctc_greedy_decode(rec_outputs[0], self.characters)
            text = str(text or "").strip()
            if not text:
                continue
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            box = [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]
            detections.append(
                {
                    "text": text,
                    "label_name": text,
                    "class_name": "text",
                    "confidence": float(rec_score if rec_score > 0 else det_score),
                    "box": box,
                    "bbox": box,
                    "polygon": [[float(x), float(y)] for x, y in points],
                }
            )

        # Keep RKNN and Paddle results on the same normalization path.
        from app.core.ocr_backend import normalize_ocr_output

        normalized = normalize_ocr_output(
            [{
                "rec_texts": [item["text"] for item in detections],
                "rec_scores": [item["confidence"] for item in detections],
                "rec_polys": [item["polygon"] for item in detections],
                "rec_boxes": [item["box"] for item in detections],
            }],
            score_threshold=0,
        )
        normalized_detections = normalized["detections"]
        metadata = self._metadata(len(normalized_detections))
        metadata["full_text"] = normalized["full_text"]
        return normalized_detections, normalized_detections, metadata

    def _metadata(self, text_count: int) -> Dict[str, Any]:
        return {
            "full_text": "",
            "device": self.device,
            "backend": self.name,
            "text_count": text_count,
            "rknn_input_format": self.rknn_input_format,
            "character_dict_path": self.character_dict_path,
        }

    def cleanup(self):
        from app.user_scripts.common.yolo_backends import _release_rknn_runtime

        if getattr(self, "_rec_entry", None) is not None:
            _release_rknn_runtime(self._rec_key, self._rec_entry)
            self._rec_entry = None
        if getattr(self, "_det_entry", None) is not None:
            _release_rknn_runtime(self._det_key, self._det_entry)
            self._det_entry = None
        self.model = None
