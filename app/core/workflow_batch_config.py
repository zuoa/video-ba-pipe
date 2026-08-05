"""Safe, field-scoped batch updates for workflow runtime configuration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.core.time_schedule import validate_weekly_schedule


class BatchConfigValidationError(ValueError):
    """Raised when a requested batch patch cannot be applied safely."""


SUPPORTED_CHANGES = {
    "algorithm": {"confidence", "interval_seconds"},
    "alert": {"trigger_condition", "suppression"},
    "time_schedule": {"weekly_schedule"},
}


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BatchConfigValidationError(f"{label} 必须是数字")
    numeric = float(value)
    if numeric < minimum or numeric > maximum:
        raise BatchConfigValidationError(f"{label} 必须在 {minimum:g}-{maximum:g} 之间")
    return numeric


def _positive_integer(value: Any, label: str, maximum: int) -> int:
    numeric = _number(value, label, 1, maximum)
    if not numeric.is_integer():
        raise BatchConfigValidationError(f"{label} 必须是整数")
    return int(numeric)


def _normalize_trigger_condition(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("enable"), bool):
        raise BatchConfigValidationError("窗口检测配置必须包含 enable")
    if not value["enable"]:
        return {"enable": False}

    mode = value.get("mode")
    if mode not in {"count", "ratio", "consecutive"}:
        raise BatchConfigValidationError("窗口检测模式无效")
    window_size = _positive_integer(value.get("window_size"), "时间窗口", 300)
    if mode == "ratio":
        threshold: float | int = _number(value.get("threshold"), "检测比例", 0, 1)
    else:
        threshold = _positive_integer(value.get("threshold"), "检测次数", 100)
    return {
        "enable": True,
        "window_size": window_size,
        "mode": mode,
        "threshold": threshold,
    }


def _normalize_suppression(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("enable"), bool):
        raise BatchConfigValidationError("告警抑制配置必须包含 enable")
    if not value["enable"]:
        return {"enable": False}
    return {
        "enable": True,
        "seconds": _positive_integer(value.get("seconds"), "抑制时长", 3600),
    }


def _normalize_changes(node_type: str, changes: Any) -> dict[str, Any]:
    if node_type not in SUPPORTED_CHANGES:
        raise BatchConfigValidationError(f"不支持批量配置节点类型: {node_type}")
    if not isinstance(changes, dict) or not changes:
        raise BatchConfigValidationError("至少选择一个要应用的参数")

    unknown = set(changes) - SUPPORTED_CHANGES[node_type]
    if unknown:
        raise BatchConfigValidationError(f"包含不支持的参数: {', '.join(sorted(unknown))}")

    normalized: dict[str, Any] = {}
    if "confidence" in changes:
        normalized["confidence"] = _number(changes["confidence"], "置信度", 0, 1)
    if "interval_seconds" in changes:
        normalized["interval_seconds"] = _number(changes["interval_seconds"], "检测间隔", 0.1, 60)
    if "trigger_condition" in changes:
        normalized["trigger_condition"] = _normalize_trigger_condition(changes["trigger_condition"])
    if "suppression" in changes:
        normalized["suppression"] = _normalize_suppression(changes["suppression"])
    if "weekly_schedule" in changes:
        valid, error = validate_weekly_schedule(changes["weekly_schedule"])
        if not valid:
            raise BatchConfigValidationError(error or "周计划配置无效")
        normalized["weekly_schedule"] = deepcopy(changes["weekly_schedule"])
    return normalized


def apply_batch_node_changes(
    workflow_data: dict[str, Any],
    *,
    node_id: str,
    node_type: str,
    changes: dict[str, Any],
) -> tuple[dict[str, Any], list[str], str]:
    """Return patched workflow data without mutating the supplied value."""
    if not isinstance(workflow_data, dict):
        raise BatchConfigValidationError("编排数据格式无效")
    normalized = _normalize_changes(node_type, changes)
    updated = deepcopy(workflow_data)
    matches = [
        node for node in updated.get("nodes", [])
        if isinstance(node, dict) and str(node.get("id")) == str(node_id)
    ]
    if len(matches) != 1:
        raise BatchConfigValidationError(
            f"节点 {node_id} {'不存在' if not matches else '不唯一'}"
        )
    node = matches[0]
    actual_type = node.get("type")
    if actual_type != node_type:
        raise BatchConfigValidationError(
            f"节点 {node_id} 类型不匹配，实际为 {actual_type or '未知'}"
        )

    if node_type == "algorithm":
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        config = deepcopy(config)
        if "confidence" in normalized:
            config["confidence"] = normalized["confidence"]
            config["confidence_override_enabled"] = True
        if "interval_seconds" in normalized:
            config["interval_seconds"] = normalized["interval_seconds"]
        node["config"] = config
    else:
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        data = deepcopy(data)
        if "trigger_condition" in normalized:
            data["triggerCondition"] = normalized["trigger_condition"]
        if "suppression" in normalized:
            data["suppression"] = normalized["suppression"]
        if "weekly_schedule" in normalized:
            data["weeklySchedule"] = normalized["weekly_schedule"]
        node["data"] = data

    return updated, sorted(normalized), node.get("name") or str(node_id)
