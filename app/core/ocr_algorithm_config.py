"""OCR algorithm configuration validation."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.database_models import MLModel


OCR_DEFAULT_CONFIG = {
    "device": "auto",
    "recognition_score_threshold": 0.5,
    "detection_threshold": None,
    "box_threshold": None,
    "unclip_ratio": None,
    "limit_side_len": None,
    "recognition_batch_size": 1,
    "input_mode": "frame",
    "expand_ratio": 0.1,
    "max_candidates": 8,
    "min_crop_side": 8,
    "upstream_class_filter": [],
}

_CROP_INPUT_MODES = {"frame", "upstream_crops"}


def _optional_float(value: Any, field_name: str, minimum: float, maximum: float) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是数字") from exc
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"{field_name} 必须在 {minimum} 到 {maximum} 之间")
    return normalized


def _optional_int(value: Any, field_name: str, minimum: int, maximum: int) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是整数") from exc
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"{field_name} 必须在 {minimum} 到 {maximum} 之间")
    return normalized


def validate_ocr_crop_node_config(config: Any) -> Dict[str, Any]:
    """只校验节点 overlay。不要求模型 ID，不返回完整 ocr_config。"""
    if config in (None, ""):
        return {}
    if not isinstance(config, dict):
        raise ValueError("OCR 节点配置必须是 JSON 对象")

    overlay: Dict[str, Any] = {}
    if config.get("input_mode") not in (None, ""):
        mode = str(config.get("input_mode")).strip()
        if mode not in _CROP_INPUT_MODES:
            raise ValueError("input_mode 仅支持 frame 或 upstream_crops")
        overlay["input_mode"] = mode

    if "expand_ratio" in config:
        expand_ratio = _optional_float(config.get("expand_ratio"), "expand_ratio", 0, 1)
        if expand_ratio is not None:
            overlay["expand_ratio"] = expand_ratio

    if "max_candidates" in config:
        max_candidates = _optional_int(config.get("max_candidates"), "max_candidates", 1, 32)
        if max_candidates is not None:
            overlay["max_candidates"] = max_candidates

    if "min_crop_side" in config:
        min_crop_side = _optional_int(config.get("min_crop_side"), "min_crop_side", 1, 64)
        if min_crop_side is not None:
            overlay["min_crop_side"] = min_crop_side

    if "upstream_class_filter" in config and config.get("upstream_class_filter") is not None:
        raw_filter = config.get("upstream_class_filter")
        if not isinstance(raw_filter, list) or any(not isinstance(item, str) for item in raw_filter):
            raise ValueError("upstream_class_filter 必须是字符串列表")
        overlay["upstream_class_filter"] = list(raw_filter)

    return overlay


def _required_model(model_id: Any, role: str) -> MLModel:
    try:
        normalized_id = int(model_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"OCR {role} 模型不能为空") from exc

    try:
        model = MLModel.get_by_id(normalized_id)
    except MLModel.DoesNotExist as exc:
        raise ValueError(f"OCR {role} 模型不存在: {normalized_id}") from exc

    if not model.enabled:
        raise ValueError(f"OCR {role} 模型已禁用: {model.name}")
    if str(model.model_type or "").upper() != "OCR" or model.model_role != role:
        raise ValueError(f"模型 {model.name} 不是 OCR {role} 模型")
    return model


def normalize_ocr_algorithm_config(config: Any, current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if config is None:
        config = current
    elif isinstance(config, dict) and isinstance(current, dict):
        config = {**current, **config}

    if not isinstance(config, dict):
        raise ValueError("ocr_config 必须是 JSON 对象")

    detection_model = _required_model(config.get("detection_model_id"), "detection")
    recognition_model = _required_model(config.get("recognition_model_id"), "recognition")
    device = str(config.get("device") or OCR_DEFAULT_CONFIG["device"]).strip().lower()
    if device not in ("auto", "cpu", "gpu"):
        raise ValueError("OCR device 仅支持 auto、cpu 或 gpu")

    try:
        batch_size = int(config.get("recognition_batch_size") or 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("recognition_batch_size 必须是整数") from exc
    if batch_size < 1 or batch_size > 64:
        raise ValueError("recognition_batch_size 必须在 1 到 64 之间")

    limit_side_len = config.get("limit_side_len")
    if limit_side_len in (None, ""):
        normalized_limit = None
    else:
        try:
            normalized_limit = int(limit_side_len)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit_side_len 必须是整数") from exc
        if normalized_limit < 32 or normalized_limit > 4096:
            raise ValueError("limit_side_len 必须在 32 到 4096 之间")

    crop = validate_ocr_crop_node_config(config)
    return {
        "detection_model_id": detection_model.id,
        "recognition_model_id": recognition_model.id,
        "device": device,
        "recognition_score_threshold": _optional_float(
            config.get("recognition_score_threshold", 0.5),
            "recognition_score_threshold",
            0,
            1,
        ),
        "detection_threshold": _optional_float(config.get("detection_threshold"), "detection_threshold", 0, 1),
        "box_threshold": _optional_float(config.get("box_threshold"), "box_threshold", 0, 1),
        "unclip_ratio": _optional_float(config.get("unclip_ratio"), "unclip_ratio", 0.1, 10),
        "limit_side_len": normalized_limit,
        "recognition_batch_size": batch_size,
        "input_mode": crop.get("input_mode", OCR_DEFAULT_CONFIG["input_mode"]),
        "expand_ratio": crop.get("expand_ratio", OCR_DEFAULT_CONFIG["expand_ratio"]),
        "max_candidates": crop.get("max_candidates", OCR_DEFAULT_CONFIG["max_candidates"]),
        "min_crop_side": crop.get("min_crop_side", OCR_DEFAULT_CONFIG["min_crop_side"]),
        "upstream_class_filter": list(
            crop.get("upstream_class_filter", OCR_DEFAULT_CONFIG["upstream_class_filter"])
        ),
    }
