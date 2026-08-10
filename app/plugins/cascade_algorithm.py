"""Built-in linear cascade detector backed by the shared YOLO backend layer."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Sequence

import numpy as np

from app import logger
from app.config import VIDEO_FRAME_PIXEL_FORMAT
from app.core.algorithm import BaseAlgorithm
from app.core.cascade_algorithm_config import normalize_cascade_algorithm_config
from app.core.frame_utils import detect_frame_pixel_format, frame_to_rgb, infer_frame_dimensions
from app.core.model_resolver import get_model_resolver
from app.user_scripts.common.roi import (
    filter_items_by_regions,
    remap_detections_to_full_frame,
    split_regions,
)
from app.user_scripts.common.yolo_backends import create_backend


def _confidence(item: Dict[str, Any]) -> float:
    try:
        return float(item.get("confidence", item.get("score", 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _box(item: Dict[str, Any]) -> Sequence[float] | None:
    value = item.get("box", item.get("bbox")) if isinstance(item, dict) else None
    return value if isinstance(value, (list, tuple)) and len(value) >= 4 else None


def _crop_box(
    detection: Dict[str, Any],
    frame_shape: Sequence[int],
    expand_ratio: float,
) -> List[int] | None:
    box = _box(detection)
    if box is None:
        return None
    height, width = int(frame_shape[0]), int(frame_shape[1])
    x1, y1, x2, y2 = [float(value) for value in box[:4]]
    box_width = max(0.0, x2 - x1)
    box_height = max(0.0, y2 - y1)
    x1 = max(0, int(np.floor(x1 - box_width * expand_ratio)))
    y1 = max(0, int(np.floor(y1 - box_height * expand_ratio)))
    x2 = min(width, int(np.ceil(x2 + box_width * expand_ratio)))
    y2 = min(height, int(np.ceil(y2 + box_height * expand_ratio)))
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def _stage_detail(stage: Dict[str, Any], detection: Dict[str, Any]) -> Dict[str, Any]:
    detail = {
        "stage_id": stage["id"],
        "stage_name": stage["name"],
        "model_id": stage["model_id"],
        "model_name": stage.get("model_name"),
        "box": list(_box(detection) or []),
        "confidence": _confidence(detection),
        "class": detection.get("class"),
        "class_name": detection.get("class_name") or detection.get("label"),
        "label": detection.get("label") or detection.get("class_name") or stage["name"],
    }
    return detail


class CascadeAlgorithm(BaseAlgorithm):
    name = "cascade_algorithm"

    def load_model(self):
        self.cascade_config = normalize_cascade_algorithm_config(
            self.config.get("cascade_config")
        )
        self.stage_runtimes: List[Dict[str, Any]] = []
        resolver = get_model_resolver()
        try:
            for stage in self.cascade_config["stages"]:
                model_info = resolver._get_model_info(stage["model_id"])
                if not model_info:
                    raise RuntimeError(f"阶段 {stage['name']} 的模型不存在")
                inference_config = {
                    **stage.get("inference", {}),
                    "model_id": stage["model_id"],
                    "confidence": stage["confidence"],
                    "class_filter": stage["class_ids"],
                }
                backend = create_backend(model_info["path"], model_info, inference_config)
                self.stage_runtimes.append({
                    "stage": stage,
                    "backend": backend,
                    "model_info": model_info,
                })
        except Exception:
            self.cleanup()
            raise
        logger.info(
            "[Cascade] 已加载 %s 个阶段: %s",
            len(self.stage_runtimes),
            " -> ".join(item["stage"]["name"] for item in self.stage_runtimes),
        )

    @staticmethod
    def _empty_result(error: str | None, stage_debug: list, started_at: float) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "cascade_checked": error is None,
            "stage_count": len(stage_debug),
            "stage_debug": stage_debug,
            "inference_time_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
        }
        if error:
            metadata.update({"error": error, "error_code": "cascade_stage_failed"})
        return {"detections": [], "metadata": metadata}

    def process(self, frame: np.ndarray, roi_regions: list = None, upstream_results: dict = None) -> dict:
        started_at = time.perf_counter()
        stage_debug = []
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
        except Exception as exc:
            logger.error("[Cascade] 输入帧转换失败: %s", exc, exc_info=True)
            return self._empty_result(f"输入帧转换失败: {exc}", stage_debug, started_at)

        pre_mask_regions, _, post_filter_regions = split_regions(roi_regions)
        first_stage_frame = frame_rgb
        if pre_mask_regions:
            roi_mask = BaseAlgorithm.create_roi_mask(frame_rgb.shape, pre_mask_regions)
            first_stage_frame = BaseAlgorithm.apply_roi_mask(frame_rgb, roi_mask)

        paths: List[Dict[str, Any]] = []
        for stage_index, runtime in enumerate(self.stage_runtimes):
            stage = runtime["stage"]
            backend = runtime["backend"]
            stage_started_at = time.perf_counter()
            errors = []
            crop_boxes: List[List[int]] = []
            stage_detections: List[Dict[str, Any]] = []
            input_count = 1 if stage_index == 0 else len(paths)
            successful_inferences = 0

            if stage_index == 0:
                try:
                    detections, _, _ = backend.infer(first_stage_frame)
                    successful_inferences = 1
                except Exception as exc:
                    logger.error("[Cascade] 阶段 %s 推理失败: %s", stage["name"], exc, exc_info=True)
                    errors.append(str(exc))
                    detections = []
                if post_filter_regions and detections:
                    detections = filter_items_by_regions(
                        detections,
                        frame_rgb.shape,
                        post_filter_regions,
                        metric="ioa",
                        threshold=0.3,
                    )
                detections = sorted(detections, key=_confidence, reverse=True)[:stage["max_candidates"]]
                stage_detections = [dict(item) for item in detections]
                paths = [
                    {
                        "root_index": index,
                        "root": dict(detection),
                        "current": dict(detection),
                        "score": _confidence(detection),
                        "stages": [_stage_detail(stage, detection)],
                    }
                    for index, detection in enumerate(detections)
                ]
            else:
                parent_paths = list(paths)
                next_paths = []
                expand_ratio = float(stage["input"].get("expand_ratio", 0.1))
                for parent_path in parent_paths:
                    crop_box = _crop_box(parent_path["current"], frame_rgb.shape, expand_ratio)
                    if crop_box is None:
                        errors.append("父阶段目标框无效")
                        continue
                    crop_boxes.append(crop_box)
                    x1, y1, x2, y2 = crop_box
                    cropped = frame_rgb[y1:y2, x1:x2]
                    try:
                        detections, _, _ = backend.infer(cropped)
                        successful_inferences += 1
                    except Exception as exc:
                        errors.append(str(exc))
                        logger.warning(
                            "[Cascade] 阶段 %s 的一个候选推理失败: %s",
                            stage["name"],
                            exc,
                            exc_info=True,
                        )
                        continue
                    remapped = remap_detections_to_full_frame(detections, crop_box)
                    stage_detections.extend(dict(item) for item in remapped)
                    for detection in remapped:
                        next_paths.append({
                            **parent_path,
                            "current": dict(detection),
                            "score": min(parent_path["score"], _confidence(detection)),
                            "stages": parent_path["stages"] + [_stage_detail(stage, detection)],
                        })
                paths = sorted(next_paths, key=lambda item: item["score"], reverse=True)[
                    :stage["max_candidates"]
                ]

            debug_item = {
                "stage_id": stage["id"],
                "stage_name": stage["name"],
                "model_id": stage["model_id"],
                "backend": backend.name,
                "status": "degraded" if errors and successful_inferences else "ok",
                "input_count": input_count,
                "successful_inferences": successful_inferences,
                "detection_count": len(stage_detections),
                "detections": stage_detections,
                "crop_boxes": crop_boxes,
                "error_count": len(errors),
                "errors": errors[:5],
                "inference_time_ms": round((time.perf_counter() - stage_started_at) * 1000.0, 2),
            }
            stage_debug.append(debug_item)

            if input_count > 0 and successful_inferences == 0 and errors:
                debug_item["status"] = "failed"
                return self._empty_result(
                    f"阶段“{stage['name']}”推理失败: {errors[0]}",
                    stage_debug,
                    started_at,
                )
            if not paths:
                return self._empty_result(None, stage_debug, started_at)

        best_by_root: Dict[int, Dict[str, Any]] = {}
        for path in paths:
            root_index = int(path["root_index"])
            current = best_by_root.get(root_index)
            if current is None or path["score"] > current["score"]:
                best_by_root[root_index] = path

        output = self.cascade_config["output"]
        detections = []
        for path in best_by_root.values():
            root_box = list(_box(path["root"]) or [])
            detections.append({
                "box": root_box,
                "bbox": root_box,
                "label": output["label"],
                "label_name": output["label"],
                "class_name": output["label"],
                "label_color": output["color"],
                "confidence": float(path["score"]),
                "stages": path["stages"],
            })

        return {
            "detections": detections,
            "metadata": {
                "cascade_checked": True,
                "stage_count": len(self.stage_runtimes),
                "completed_paths": len(paths),
                "total_detections": len(detections),
                "stage_debug": stage_debug,
                "inference_time_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
                "output_box_stage_id": output["box_stage_id"],
                "confidence_strategy": output["confidence_strategy"],
            },
        }

    def cleanup(self):
        runtimes = list(getattr(self, "stage_runtimes", []) or [])
        self.stage_runtimes = []
        for runtime in reversed(runtimes):
            backend = runtime.get("backend")
            if backend is None or not hasattr(backend, "cleanup"):
                continue
            try:
                backend.cleanup()
            except Exception:
                logger.warning("[Cascade] 后端清理失败", exc_info=True)
