"""Validation and normalization for built-in cascade detection algorithms."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from app.core.database_models import MLModel


CASCADE_CONFIG_VERSION = 1
MIN_CASCADE_STAGES = 2
MAX_CASCADE_STAGES = 8
DEFAULT_MAX_CANDIDATES = 20
_STAGE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SUPPORTED_MODEL_TYPES = {"YOLO", "ONNX", "RKNN"}
_SUPPORTED_BACKENDS = {"auto", "ultralytics", "onnxruntime", "onnx", "rknn", "rknnlite"}
_INFERENCE_KEYS = {
    "backend",
    "nms_iou",
    "input_width",
    "input_height",
    "rknn_input_format",
    "rknn_core_mask",
    "onnx_input_format",
    "onnx_input_layout",
    "onnx_input_dtype",
    "onnx_normalize",
    "onnx_provider",
    "postprocess_profile",
    "model_postprocess",
    "shared_inference_enabled",
}


def _number(value: Any, field: str, *, minimum: float, maximum: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是数字") from exc
    if not minimum <= normalized <= maximum:
        raise ValueError(f"{field} 必须在 {minimum} 到 {maximum} 之间")
    return normalized


def _positive_int(value: Any, field: str, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是正整数") from exc
    if normalized <= 0:
        raise ValueError(f"{field} 必须是正整数")
    return normalized


def _class_ids(value: Any, field: str) -> list[int]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        value = [value]
    result = []
    for raw_id in value:
        try:
            class_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} 必须是非负整数列表") from exc
        if class_id < 0:
            raise ValueError(f"{field} 必须是非负整数列表")
        if class_id not in result:
            result.append(class_id)
    return result


def _model(model_id: Any, stage_index: int):
    try:
        normalized_id = int(model_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"阶段 {stage_index} 请选择有效模型") from exc
    try:
        model = MLModel.get_by_id(normalized_id)
    except MLModel.DoesNotExist as exc:
        raise ValueError(f"阶段 {stage_index} 的模型不存在: {normalized_id}") from exc
    if not bool(getattr(model, "enabled", True)):
        raise ValueError(f"阶段 {stage_index} 的模型已禁用: {getattr(model, 'name', normalized_id)}")
    model_type = str(getattr(model, "model_type", "") or "").upper()
    if model_type not in _SUPPORTED_MODEL_TYPES:
        raise ValueError(
            f"阶段 {stage_index} 的模型类型不受支持: {model_type or 'unknown'}；"
            "仅支持 YOLO、ONNX、RKNN"
        )
    return model, normalized_id


def _normalize_inference(value: Any, stage_index: int) -> Dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError(f"阶段 {stage_index} 的 inference 必须是对象")
    unknown = sorted(set(value) - _INFERENCE_KEYS)
    if unknown:
        raise ValueError(f"阶段 {stage_index} 包含未知推理参数: {', '.join(unknown)}")

    inference = {key: value[key] for key in _INFERENCE_KEYS if key in value}
    backend = str(inference.get("backend") or "auto").strip().lower()
    if backend not in _SUPPORTED_BACKENDS:
        raise ValueError(f"阶段 {stage_index} 的推理后端不受支持: {backend}")
    inference["backend"] = backend
    inference["nms_iou"] = _number(
        inference.get("nms_iou", 0.45),
        f"阶段 {stage_index} NMS IOU",
        minimum=0.0,
        maximum=1.0,
    )
    for key in ("input_width", "input_height"):
        if inference.get(key) not in (None, ""):
            inference[key] = _positive_int(inference[key], f"阶段 {stage_index} {key}", 640)
    return inference


def normalize_cascade_algorithm_config(
    config: Any,
    current: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a validated, JSON-serializable v1 cascade configuration."""
    if config is None and current is not None:
        config = current
    if not isinstance(config, dict):
        raise ValueError("cascade_config 必须是对象")

    try:
        version = int(config.get("version", CASCADE_CONFIG_VERSION))
    except (TypeError, ValueError) as exc:
        raise ValueError("cascade_config.version 必须是整数") from exc
    if version != CASCADE_CONFIG_VERSION:
        raise ValueError(f"不支持的级联配置版本: {version}")

    raw_stages = config.get("stages")
    if not isinstance(raw_stages, list):
        raise ValueError("cascade_config.stages 必须是数组")
    if not MIN_CASCADE_STAGES <= len(raw_stages) <= MAX_CASCADE_STAGES:
        raise ValueError(
            f"多阶段检测必须包含 {MIN_CASCADE_STAGES} 到 {MAX_CASCADE_STAGES} 个阶段"
        )

    stages = []
    seen_ids = set()
    previous_id = None
    for offset, raw_stage in enumerate(raw_stages):
        index = offset + 1
        if not isinstance(raw_stage, dict):
            raise ValueError(f"阶段 {index} 必须是对象")
        stage_id = str(raw_stage.get("id") or "").strip()
        if not _STAGE_ID_RE.match(stage_id):
            raise ValueError(f"阶段 {index} 的 id 无效")
        if stage_id in seen_ids:
            raise ValueError(f"阶段 id 重复: {stage_id}")
        seen_ids.add(stage_id)

        model, model_id = _model(raw_stage.get("model_id"), index)
        name = str(raw_stage.get("name") or "").strip()
        if not name:
            raise ValueError(f"阶段 {index} 缺少名称")
        if len(name) > 80:
            raise ValueError(f"阶段 {index} 名称不能超过 80 个字符")

        confidence = _number(
            raw_stage.get("confidence", 0.6),
            f"阶段 {index} 置信度",
            minimum=0.0,
            maximum=1.0,
        )
        max_candidates = _positive_int(
            raw_stage.get("max_candidates"),
            f"阶段 {index} 最大候选数",
            DEFAULT_MAX_CANDIDATES,
        )
        if max_candidates > 200:
            raise ValueError(f"阶段 {index} 最大候选数不能超过 200")

        raw_input = raw_stage.get("input") or {}
        if not isinstance(raw_input, dict):
            raise ValueError(f"阶段 {index} 的 input 必须是对象")
        if offset == 0:
            if str(raw_input.get("type") or "frame") != "frame":
                raise ValueError("第一阶段输入必须是完整画面")
            input_config = {"type": "frame"}
        else:
            input_type = str(raw_input.get("type") or "parent_boxes")
            parent_id = str(raw_input.get("parent_stage_id") or "")
            if input_type != "parent_boxes" or parent_id != previous_id:
                raise ValueError(f"阶段 {index} 必须使用上一阶段 {previous_id} 的目标区域")
            input_config = {
                "type": "parent_boxes",
                "parent_stage_id": previous_id,
                "expand_ratio": _number(
                    raw_input.get("expand_ratio", 0.1),
                    f"阶段 {index} 区域扩展比例",
                    minimum=0.0,
                    maximum=1.0,
                ),
            }

        stages.append({
            "id": stage_id,
            "name": name,
            "model_id": model_id,
            "model_name": str(getattr(model, "name", model_id)),
            "class_ids": _class_ids(raw_stage.get("class_ids"), f"阶段 {index} 类别"),
            "confidence": confidence,
            "max_candidates": max_candidates,
            "inference": _normalize_inference(raw_stage.get("inference"), index),
            "input": input_config,
        })
        previous_id = stage_id

    raw_output = config.get("output") or {}
    if not isinstance(raw_output, dict):
        raise ValueError("cascade_config.output 必须是对象")
    label = str(raw_output.get("label") or "").strip()
    if not label:
        raise ValueError("请填写最终输出标签")
    color = str(raw_output.get("color") or "#ff4d4f").strip()
    if not _COLOR_RE.match(color):
        raise ValueError("最终输出颜色必须是 #RRGGBB 格式")

    return {
        "version": CASCADE_CONFIG_VERSION,
        "stages": stages,
        "output": {
            "label": label,
            "color": color.lower(),
            "box_stage_id": stages[0]["id"],
            "confidence_strategy": "minimum",
        },
    }


def cascade_model_ids(config: Any) -> tuple[int, ...]:
    if not isinstance(config, dict):
        return ()
    result = []
    for stage in config.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        try:
            model_id = int(stage.get("model_id"))
        except (TypeError, ValueError):
            continue
        if model_id not in result:
            result.append(model_id)
    return tuple(result)
