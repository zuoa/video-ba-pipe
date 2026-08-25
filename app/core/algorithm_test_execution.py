"""Side-effect-free algorithm test execution used by the worker test service."""

from __future__ import annotations

import base64
from typing import Any, Dict

import cv2
import numpy as np

from app import logger
from app.core.algorithm import BaseAlgorithm
from app.core.cascade_algorithm_config import normalize_cascade_algorithm_config
from app.core.database_models import Algorithm, FaceModelBundle


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


def _saved_algorithm_config(
    algorithm: Algorithm,
    *,
    execution_owner: str | None = None,
    execution_role: str | None = None,
) -> tuple[str, Dict[str, Any]]:
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
    # A preview is always side-effect-free. These trusted fields are applied
    # after saved configuration so a script owner cannot override them.
    trusted_owner = str(
        execution_owner or getattr(algorithm, 'created_by', None) or 'system'
    )
    full_config.update({
        'source_id': 0,
        'workflow_id': None,
        'created_by': trusted_owner,
        '_execution_owner': trusted_owner,
        '_execution_role': str(execution_role or 'user').lower(),
        '_preview_mode': True,
        'save_events': False,
    })
    return algorithm_type, full_config


def execute_saved_algorithm_test(
    algorithm_id: int,
    image_bytes: bytes,
    *,
    execution_owner: str | None = None,
    execution_role: str | None = None,
) -> Dict[str, Any]:
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

    algorithm_type, full_config = _saved_algorithm_config(
        algorithm,
        execution_owner=execution_owner,
        execution_role=execution_role,
    )
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
                    "execution_state": stage.get("execution_state"),
                    "reason_code": stage.get("reason_code"),
                    "reason": stage.get("reason"),
                    "upstream_node_id": stage.get("upstream_node_id"),
                    "upstream_node_name": stage.get("upstream_node_name"),
                    "input_kind": stage.get("input_kind"),
                    "input_count": stage.get("input_count"),
                    "successful_inferences": stage.get("successful_inferences"),
                    "failed_inferences": stage.get("failed_inferences"),
                    "detection_count": stage.get("detection_count"),
                    "forwarded_count": stage.get("forwarded_count"),
                    "pruned_count": stage.get("pruned_count"),
                    "error_count": stage.get("error_count"),
                    "errors": stage.get("errors") or [],
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
                "diagnosis": metadata.get("diagnosis"),
            }
        )
    finally:
        if instance is not None:
            try:
                instance.cleanup()
            except Exception:
                logger.warning("组合检测预览资源清理失败", exc_info=True)


def execute_face_enrollment(
    bundle_id: int,
    image_bytes: bytes,
    backend: str = 'auto',
    min_face_size: int = 80,
) -> Dict[str, Any]:
    """Extract one enrollment template on the hardware-capable worker."""
    bundle = _face_enrollment_bundle(bundle_id)

    from app.core.face_inference import FaceWorkerBackend

    runtime = None
    try:
        selected_backend = str(backend or 'auto').lower()
        runtime = FaceWorkerBackend(
            bundle,
            selected_backend,
            {
                'face_detection_confidence': 0.6,
                'face_nms_iou': 0.4,
                'min_face_size': int(min_face_size),
            },
        )
        return _execute_face_enrollment_with_runtime(bundle, runtime, image_bytes)
    finally:
        if runtime is not None:
            runtime.cleanup()


def _face_enrollment_bundle(bundle_id: int):
    try:
        bundle = FaceModelBundle.get_by_id(int(bundle_id))
    except (TypeError, ValueError):
        raise AlgorithmTestInputError('无效的人脸模型包ID')
    except FaceModelBundle.DoesNotExist:
        raise AlgorithmTestInputError('人脸模型包不存在', status_code=404)
    if not bundle.enabled:
        raise AlgorithmTestInputError('人脸模型包已禁用')
    return bundle


def _execute_face_enrollment_with_runtime(bundle, runtime, image_bytes):
    """Extract one template with an already-loaded face runtime."""

    from app.core.face_gallery import serialize_embedding

    image = _decode_image(image_bytes)
    detections, details, metadata = runtime.infer(image)
    qualified = [item for item in details if item.get('embedding') is not None]
    if not detections:
        raise AlgorithmTestInputError('录入图片中未检测到人脸', status_code=422)
    if len(detections) != 1:
        raise AlgorithmTestInputError('录入图片必须且只能包含一张人脸', status_code=422)
    if not qualified:
        quality = details[0].get('quality') or {}
        reason = quality.get('reason') or 'low_quality'
        raise AlgorithmTestInputError(f'录入人脸质量不合格: {reason}', status_code=422)
    embedding = qualified[0]['embedding']
    if len(embedding) != int(bundle.embedding_dimension):
        raise AlgorithmTestInputError(
            '特征维度与模型包契约不一致: '
            f'expected={bundle.embedding_dimension}, actual={len(embedding)}',
            status_code=422,
        )
    payload = base64.b64encode(serialize_embedding(embedding)).decode('ascii')
    return _json_safe({
        'success': True,
        'embedding_base64': payload,
        'quality': qualified[0].get('quality') or {},
        'box': qualified[0].get('box'),
        'model_contract': bundle.contract_id,
        'backend': metadata.get('backend') or runtime.pipeline.backend,
        'metadata': metadata,
    })


def execute_face_enrollment_batch(
    bundle_id: int,
    images: list[bytes],
    backend: str = 'auto',
    min_face_size: int = 80,
) -> Dict[str, Any]:
    """Extract multiple templates while loading model artifacts only once."""
    if not images or len(images) > 64:
        raise AlgorithmTestInputError('批量录入每批必须包含 1 到 64 张图片')
    bundle = _face_enrollment_bundle(bundle_id)

    from app.core.face_inference import FaceWorkerBackend

    runtime = None
    try:
        runtime = FaceWorkerBackend(
            bundle,
            str(backend or 'auto').lower(),
            {
                'face_detection_confidence': 0.6,
                'face_nms_iou': 0.4,
                'min_face_size': int(min_face_size),
            },
        )
        results = []
        for image_bytes in images:
            try:
                results.append(
                    _execute_face_enrollment_with_runtime(bundle, runtime, image_bytes)
                )
            except AlgorithmTestInputError as exc:
                results.append({
                    'success': False,
                    'error': str(exc),
                    'status_code': int(exc.status_code),
                })
        return {'success': True, 'results': results}
    finally:
        if runtime is not None:
            runtime.cleanup()


def execute_algorithm_test_job(job: Dict[str, Any]) -> Dict[str, Any]:
    kind = job.get("kind")
    if kind == 'face_enrollment_batch':
        encoded_images = job.get('images_base64')
        if not isinstance(encoded_images, list) or not encoded_images:
            raise AlgorithmTestInputError('批量录入图片数据格式不正确')
        images = []
        try:
            for encoded in encoded_images:
                images.append(base64.b64decode(encoded or '', validate=True))
        except (ValueError, TypeError) as exc:
            raise AlgorithmTestInputError('批量录入图片数据格式不正确') from exc
        return execute_face_enrollment_batch(
            job.get('bundle_id'),
            images,
            backend=str(job.get('backend') or 'auto'),
            min_face_size=int(job.get('min_face_size') or 80),
        )

    try:
        image_bytes = base64.b64decode(job.get("image_base64") or "", validate=True)
    except (ValueError, TypeError) as exc:
        raise AlgorithmTestInputError("图片数据格式不正确") from exc

    if kind == "saved_algorithm":
        return execute_saved_algorithm_test(
            job.get("algorithm_id"),
            image_bytes,
            execution_owner=job.get('execution_owner'),
            execution_role=job.get('execution_role'),
        )
    if kind == "cascade_preview":
        config = job.get("cascade_config")
        if not isinstance(config, dict):
            raise AlgorithmTestInputError("组合检测配置格式不正确")
        return execute_cascade_preview(config, image_bytes)
    if kind == 'face_enrollment':
        return execute_face_enrollment(
            job.get('bundle_id'),
            image_bytes,
            backend=str(job.get('backend') or 'auto'),
            min_face_size=int(job.get('min_face_size') or 80),
        )
    raise AlgorithmTestInputError("不支持的测试任务类型")
