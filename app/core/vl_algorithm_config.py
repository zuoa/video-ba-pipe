"""Validation helpers for algorithm-level VL API configuration."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from urllib.parse import urlparse


def ensure_json_object(value: Any, field_name: str) -> Dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} JSON 格式错误: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是 JSON 对象")
    return value


def normalize_vl_algorithm_config(
    value: Any,
    current: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    incoming = ensure_json_object(value, "vl_config")
    config = dict(current or {})
    config.update(incoming)

    if not str(incoming.get("api_key") or "").strip() and current:
        config["api_key"] = current.get("api_key", "")

    for field in ("base_url", "api_key", "model_name", "prompt_template"):
        config[field] = str(config.get(field) or "").strip()
        if not config[field]:
            raise ValueError(f"VL 配置缺少必填字段: {field}")

    parsed_url = urlparse(config["base_url"])
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise ValueError("VL base_url 必须是完整的 HTTP(S) 地址")

    config["temperature"] = float(config.get("temperature", 0))
    if not 0 <= config["temperature"] <= 2:
        raise ValueError("VL temperature 必须在 0 到 2 之间")
    config["max_tokens"] = int(config.get("max_tokens") or 512)
    if not 1 <= config["max_tokens"] <= 32768:
        raise ValueError("VL max_tokens 必须在 1 到 32768 之间")
    config["timeout_seconds"] = int(config.get("timeout_seconds") or 30)
    if not 1 <= config["timeout_seconds"] <= 300:
        raise ValueError("VL timeout_seconds 必须在 1 到 300 之间")
    config["image_detail"] = config.get("image_detail") or "auto"
    if config["image_detail"] not in ("auto", "low", "high"):
        raise ValueError("VL image_detail 仅支持 auto、low 或 high")
    config["extra_headers"] = ensure_json_object(config.get("extra_headers"), "extra_headers")
    config["extra_body"] = ensure_json_object(config.get("extra_body"), "extra_body")

    reserved_fields = {"model", "messages", "response_format"} & set(config["extra_body"])
    if reserved_fields:
        fields = ", ".join(sorted(reserved_fields))
        raise ValueError(f"VL extra_body 不允许覆盖固定字段: {fields}")
    return config
