"""Side-effect-free algorithm test execution used by the worker test service."""

from __future__ import annotations

import base64
from typing import Any, Dict

import cv2
import numpy as np

from app import logger
from app.core.algorithm import BaseAlgorithm
from app.core.cascade_algorithm_config import normalize_cascade_algorithm_config
from app.core.database_models import Algorithm


class AlgorithmTestInputError(ValueError):
    """A caller-visible validation error raised before inference starts."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _create_algorithm_instance(algorithm_type: str, full_config: Dict[str, Any]):
    if algorithm_type == "vl":
        from app.plugins.vl_algorithm import VLAlgorithm

        return VLAlgorithm(full_config)
    if algorithm_type == "ocr":
        from app.plugins.ocr_algorithm import OCRAlgorithm

        return OCRAlgorithm(full_config)
    if algorithm_type == "cascade":
        from app.plugins.cascade_algorithm import CascadeAlgorithm

        return CascadeAlgorithm(full_config)
    from app.plugins.script_algorithm import ScriptAlgorithm

    return ScriptAlgorithm(full_config)


def _decode_image(image_bytes: bytes) -> np.ndarray:
    if not image_bytes:
        raise AlgorithmTestInputError("没有上传图片")
    bgr_image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if bgr_image is None:
        raise AlgorithmTestInputError("无法读取图片文件")
    return cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)


def _encode_bgr_jpeg(image: np.ndarray) -> str:
    encoded, buffer = cv2.imencode(".jpg", image)
    if not encoded:
        raise RuntimeError("无法编码测试结果图片")
    payload = base64.b64encode(buffer.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{payload}"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _saved_algorithm_config(algorithm: Algorithm) -> tuple[str, Dict[str, Any]]:
    script_config = dict(algorithm.config_dict)
    ext_config = dict(algorithm.ext_config)
    algorithm_type = ext_config.get("algorithm_type") or "script"
    full_config = {
        "id": algorithm.id,
        "name": algorithm.name,
        "label_name": getattr(algorithm, "label_name", None)
        or script_config.get("label_name", "Object"),
        "label_color": getattr(algorithm, "label_color", None)
        or script_config.get("label_color", "#FF0000"),
        "interval_seconds": getattr(algorithm, "interval_seconds", None)
        or script_config.get("interval_seconds", 1),
        "source_id": 0,
        "pixel_format": "rgb24",
        "script_path": algorithm.script_path,
        "entry_function": "process",
        "runtime_timeout": getattr(algorithm, "runtime_timeout", None)
        or script_config.get("runtime_timeout", 30),
        "memory_limit_mb": getattr(algorithm, "memory_limit_mb", None)
        or script_config.get("memory_limit_mb", 512),
        "algorithm_type": algorithm_type,
    }
    full_config.update(script_config)
    full_config.update(ext_config)
    return algorithm_type, full_config


def execute_saved_algorithm_test(algorithm_id: int, image_bytes: bytes) -> Dict[str, Any]:
    try:
        algorithm = Algorithm.get_by_id(int(algorithm_id))
    except (TypeError, ValueError):
        raise AlgorithmTestInputError("无效的算法ID")
    except Algorithm.DoesNotExist:
        raise AlgorithmTestInputError("算法不存在", status_code=404)

    image = _decode_image(image_bytes)
    height, width = image.shape[:2]
    if width < 640 or height < 640:
        scale = max(640 / width, 640 / height)
        image = cv2.resize(
            image,
            (int(width * scale), int(height * scale)),
            interpolation=cv2.INTER_LINEAR,
        )

    algorithm_type, full_config = _saved_algorithm_config(algorithm)
    instance = None
    try:
        instance = _create_algorithm_instance(algorithm_type, full_config)
        result = instance.process(image)
        detections = BaseAlgorithm.normalize_detection_results(result.get("detections", []))
        metadata = result.get("metadata") or {}
        algorithm_error = metadata.get("error") if isinstance(metadata, dict) else None
        result_image = instance.visualize(
            image,
            detections,
            label_color=full_config.get("label_color", "#FF0000"),
        )
        response = {
            "success": not bool(algorithm_error),
            "detections": detections,
            "detection_count": len(detections),
            "metadata": metadata,
            "result_image": _encode_bgr_jpeg(result_image),
        }
        if algorithm_error:
            response["error"] = algorithm_error
        return _json_safe(response)
    finally:
        if instance is not None and hasattr(instance, "cleanup"):
            try:
                instance.cleanup()
            except Exception:
                logger.warning("算法测试资源清理失败", exc_info=True)


def execute_cascade_preview(cascade_config: Dict[str, Any], image_bytes: bytes) -> Dict[str, Any]:
    try:
        normalized = normalize_cascade_algorithm_config(cascade_config)
    except ValueError as exc:
        raise AlgorithmTestInputError(str(exc)) from exc

    image = _decode_image(image_bytes)
    if normalized.get("version") == 2:
        output_config = next(
            node for node in normalized["nodes"] if node.get("type") == "output"
        )
    else:
        output_config = normalized["output"]

    instance = None
    try:
        instance = _create_algorithm_instance(
            "cascade",
            {
                "id": "preview",
                "name": output_config["label"],
                "algorithm_type": "cascade",
                "cascade_config": normalized,
                "pixel_format": "rgb24",
                "label_name": output_config["label"],
                "label_color": output_config["color"],
            },
        )
        result = instance.process(image)
        detections = BaseAlgorithm.normalize_detection_results(result.get("detections", []))
        metadata = result.get("metadata") or {}
        result_image = instance.visualize(
            image,
            detections,
            label_color=output_config["color"],
        )
        stage_previews = []
        for stage in metadata.get("stage_debug") or []:
            stage_detections = BaseAlgorithm.normalize_detection_results(
                stage.get("detections") or []
            )
            stage_image = instance.visualize(
                image,
                stage_detections,
                label_color="#1677ff",
            )
            for crop_box in stage.get("crop_boxes") or []:
                x1, y1, x2, y2 = [int(value) for value in crop_box[:4]]
                cv2.rectangle(stage_image, (x1, y1), (x2, y2), (11, 158, 245), 2)
            stage_previews.append(
                {
                    "stage_id": stage.get("stage_id") or stage.get("node_id"),
                    "stage_name": stage.get("stage_name") or stage.get("node_name"),
                    "node_id": stage.get("node_id") or stage.get("stage_id"),
                    "node_name": stage.get("node_name") or stage.get("stage_name"),
                    "status": stage.get("status"),
                    "input_count": stage.get("input_count"),
                    "detection_count": stage.get("detection_count"),
                    "error_count": stage.get("error_count"),
                    "inference_time_ms": stage.get("inference_time_ms"),
                    "image": _encode_bgr_jpeg(stage_image),
                }
            )
        error = metadata.get("error")
        return _json_safe(
            {
                "success": not bool(error),
                "error": error,
                "detection_count": len(detections),
                "detections": detections,
                "metadata": metadata,
                "result_image": _encode_bgr_jpeg(result_image),
                "stage_previews": stage_previews,
                "node_previews": stage_previews,
                "context_evaluations": metadata.get("context_evaluations") or [],
            }
        )
    finally:
        if instance is not None:
            try:
                instance.cleanup()
            except Exception:
                logger.warning("组合检测预览资源清理失败", exc_info=True)


def execute_algorithm_test_job(job: Dict[str, Any]) -> Dict[str, Any]:
    try:
        image_bytes = base64.b64decode(job.get("image_base64") or "", validate=True)
    except (ValueError, TypeError) as exc:
        raise AlgorithmTestInputError("图片数据格式不正确") from exc

    kind = job.get("kind")
    if kind == "saved_algorithm":
        return execute_saved_algorithm_test(job.get("algorithm_id"), image_bytes)
    if kind == "cascade_preview":
        config = job.get("cascade_config")
        if not isinstance(config, dict):
            raise AlgorithmTestInputError("组合检测配置格式不正确")
        return execute_cascade_preview(config, image_bytes)
    raise AlgorithmTestInputError("不支持的测试任务类型")
