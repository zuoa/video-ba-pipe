"""Validation and normalization for built-in cascade detection algorithms."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any, Dict, Optional

from app.core.database_models import MLModel


CASCADE_CONFIG_VERSION = 1
COMBINATION_CONFIG_VERSION = 2
MIN_CASCADE_STAGES = 2
MAX_CASCADE_STAGES = 8
DEFAULT_MAX_CANDIDATES = 20
_STAGE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SUPPORTED_MODEL_TYPES = {"YOLO", "ONNX", "RKNN"}
_SUPPORTED_BACKENDS = {"auto", "ultralytics", "onnxruntime", "onnx", "rknn", "rknnlite"}
_INFERENCE_MODE_ALIASES = {
    "letterbox": "letterbox",
    "standard": "letterbox",
    "sahi": "sahi",
    "slice": "sahi",
    "sliced": "sahi",
}
_NODE_TYPES = {"frame", "detector", "predicate", "logic", "output"}
_PREDICATE_OPERATORS = {"exists", "not_exists", "eq", "ne", "gt", "gte", "lt", "lte"}
_LOGIC_OPERATORS = {"and", "or", "not"}
_INFERENCE_KEYS = {
    "backend",
    "inference_mode",
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


def _nonnegative_int(value: Any, field: str, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是非负整数") from exc
    if normalized < 0:
        raise ValueError(f"{field} 必须是非负整数")
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
    inference_mode = str(
        inference.get("inference_mode") or "letterbox"
    ).strip().lower()
    if inference_mode not in _INFERENCE_MODE_ALIASES:
        raise ValueError(
            f"阶段 {stage_index} 的推理模式不受支持: {inference_mode}"
        )
    inference["inference_mode"] = _INFERENCE_MODE_ALIASES[inference_mode]
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
    if version == COMBINATION_CONFIG_VERSION:
        return _normalize_combination_config(config)
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


def _node_id(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not _STAGE_ID_RE.match(normalized):
        raise ValueError(f"{field} 无效")
    return normalized


def _topological_order(node_ids: set[str], edges: list[dict], kind: str) -> list[str]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in node_ids}
    for edge in edges:
        if edge["kind"] != kind:
            continue
        source, target = edge["source"], edge["target"]
        if source not in node_ids or target not in node_ids:
            continue
        adjacency[source].append(target)
        indegree[target] += 1
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    ordered = []
    while queue:
        node_id = queue.popleft()
        ordered.append(node_id)
        for target in adjacency[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(ordered) != len(node_ids):
        graph_name = "检测数据流" if kind == "data" else "判定规则"
        raise ValueError(f"{graph_name}不能形成循环")
    return ordered


def _normalize_combination_config(config: Dict[str, Any]) -> Dict[str, Any]:
    raw_nodes = config.get("nodes")
    raw_edges = config.get("edges")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("cascade_config.nodes 必须是非空数组")
    if not isinstance(raw_edges, list):
        raise ValueError("cascade_config.edges 必须是数组")

    nodes = []
    node_types: dict[str, str] = {}
    for offset, raw_node in enumerate(raw_nodes):
        index = offset + 1
        if not isinstance(raw_node, dict):
            raise ValueError(f"节点 {index} 必须是对象")
        node_id = _node_id(raw_node.get("id"), f"节点 {index} 的 id")
        if node_id in node_types:
            raise ValueError(f"节点 id 重复: {node_id}")
        node_type = str(raw_node.get("type") or "").strip().lower()
        if node_type not in _NODE_TYPES:
            raise ValueError(f"节点 {node_id} 的类型无效")
        node_types[node_id] = node_type
        name = str(raw_node.get("name") or "").strip() or {
            "frame": "画面输入",
            "detector": "检测目标",
            "predicate": "检测条件",
            "logic": "逻辑判断",
            "output": "最终输出",
        }[node_type]
        normalized: Dict[str, Any] = {"id": node_id, "type": node_type, "name": name[:80]}

        if node_type == "detector":
            model, model_id = _model(raw_node.get("model_id"), index)
            normalized.update({
                "model_id": model_id,
                "model_name": str(getattr(model, "name", model_id)),
                "class_ids": _class_ids(raw_node.get("class_ids"), f"节点 {name} 类别"),
                "confidence": _number(
                    raw_node.get("confidence", 0.6), f"节点 {name} 置信度", minimum=0.0, maximum=1.0
                ),
                "max_candidates": _positive_int(
                    raw_node.get("max_candidates"), f"节点 {name} 最大候选数", DEFAULT_MAX_CANDIDATES
                ),
                "expand_ratio": _number(
                    raw_node.get("expand_ratio", 0.1), f"节点 {name} 区域扩展比例", minimum=0.0, maximum=1.0
                ),
                "inference": _normalize_inference(raw_node.get("inference"), index),
            })
            if normalized["max_candidates"] > 200:
                raise ValueError(f"节点 {name} 最大候选数不能超过 200")
        elif node_type == "predicate":
            operator = str(raw_node.get("operator") or "exists").strip().lower()
            if operator not in _PREDICATE_OPERATORS:
                raise ValueError(f"条件节点 {name} 的操作符无效")
            normalized["operator"] = operator
            if operator not in {"exists", "not_exists"}:
                normalized["value"] = _nonnegative_int(
                    raw_node.get("value"), f"条件节点 {name} 的比较值", 1
                )
        elif node_type == "logic":
            operator = str(raw_node.get("operator") or "and").strip().lower()
            if operator not in _LOGIC_OPERATORS:
                raise ValueError(f"逻辑节点 {name} 的操作符无效")
            normalized["operator"] = operator
        elif node_type == "output":
            label = str(raw_node.get("label") or "").strip()
            if not label:
                raise ValueError("请填写最终输出标签")
            color = str(raw_node.get("color") or "#ff4d4f").strip().lower()
            if not _COLOR_RE.match(color):
                raise ValueError("最终输出颜色必须是 #RRGGBB 格式")
            normalized.update({
                "label": label[:80],
                "color": color,
                "box_source_node_id": (
                    _node_id(raw_node.get("box_source_node_id"), "输出框来源")
                    if raw_node.get("box_source_node_id") not in (None, "") else None
                ),
            })
        nodes.append(normalized)

    counts = {node_type: list(node_types.values()).count(node_type) for node_type in _NODE_TYPES}
    if counts["frame"] != 1:
        raise ValueError("组合检测必须包含且仅包含一个画面输入节点")
    if not 1 <= counts["detector"] <= MAX_CASCADE_STAGES:
        raise ValueError(f"组合检测必须包含 1 到 {MAX_CASCADE_STAGES} 个检测节点")
    if counts["output"] != 1:
        raise ValueError("组合检测必须包含且仅包含一个输出节点")

    edges = []
    edge_keys = set()
    incoming: dict[tuple[str, str], list[str]] = defaultdict(list)
    for offset, raw_edge in enumerate(raw_edges):
        if not isinstance(raw_edge, dict):
            raise ValueError(f"连线 {offset + 1} 必须是对象")
        source = _node_id(raw_edge.get("source"), f"连线 {offset + 1} 起点")
        target = _node_id(raw_edge.get("target"), f"连线 {offset + 1} 终点")
        kind = str(raw_edge.get("kind") or "").strip().lower()
        if source not in node_types or target not in node_types:
            raise ValueError(f"连线 {source} → {target} 引用了不存在的节点")
        if kind not in {"data", "rule"}:
            raise ValueError(f"连线 {source} → {target} 类型无效")
        key = (source, target, kind)
        if key in edge_keys:
            continue
        edge_keys.add(key)
        source_type, target_type = node_types[source], node_types[target]
        if kind == "data" and not (
            source_type in {"frame", "detector"} and target_type == "detector"
        ):
            raise ValueError(f"数据流只能从画面或检测节点连接到检测节点: {source} → {target}")
        if kind == "rule" and not (
            (source_type == "detector" and target_type == "predicate")
            or (source_type in {"predicate", "logic"} and target_type in {"logic", "output"})
        ):
            raise ValueError(f"判定线端口不匹配: {source} → {target}")
        edge = {
            "id": str(raw_edge.get("id") or f"{kind}_{source}_{target}"),
            "source": source,
            "target": target,
            "kind": kind,
        }
        edges.append(edge)
        incoming[(kind, target)].append(source)

    for node_id, node_type in node_types.items():
        data_inputs = incoming.get(("data", node_id), [])
        rule_inputs = incoming.get(("rule", node_id), [])
        if node_type == "detector" and len(data_inputs) != 1:
            raise ValueError(f"检测节点 {node_id} 必须有且仅有一个数据输入")
        if node_type == "predicate" and len(rule_inputs) != 1:
            raise ValueError(f"条件节点 {node_id} 必须连接一个检测节点")
        if node_type == "logic":
            operator = next(node["operator"] for node in nodes if node["id"] == node_id)
            expected = 1 if operator == "not" else 2
            if (operator == "not" and len(rule_inputs) != 1) or (
                operator != "not" and len(rule_inputs) < expected
            ):
                suffix = "一个" if operator == "not" else "至少两个"
                raise ValueError(f"逻辑节点 {node_id} 必须连接{suffix}判定输入")
        if node_type == "output" and len(rule_inputs) != 1:
            raise ValueError("输出节点必须连接一个最终判定")

    data_node_ids = {node_id for node_id, node_type in node_types.items() if node_type in {"frame", "detector"}}
    rule_node_ids = {node_id for node_id, node_type in node_types.items() if node_type in {"predicate", "logic", "output"}}
    output_id = next(node_id for node_id, node_type in node_types.items() if node_type == "output")
    rule_dependencies = {node_id: incoming.get(("rule", node_id), []) for node_id in rule_node_ids}
    connected_to_output = set()
    pending = [output_id]
    while pending:
        node_id = pending.pop()
        if node_id in connected_to_output:
            continue
        connected_to_output.add(node_id)
        pending.extend(rule_dependencies.get(node_id, []))
    disconnected_rules = sorted(rule_node_ids - connected_to_output)
    if disconnected_rules:
        raise ValueError(f"判定节点未连接到最终输出: {', '.join(disconnected_rules)}")

    _topological_order(data_node_ids, edges, "data")
    _topological_order(rule_node_ids, edges, "rule")

    raw_evaluation = config.get("evaluation") or {}
    if not isinstance(raw_evaluation, dict):
        raise ValueError("evaluation 必须是对象")
    scope = str(raw_evaluation.get("scope") or "per_anchor").strip().lower()
    if scope not in {"per_anchor", "frame"}:
        raise ValueError("判定范围仅支持 per_anchor 或 frame")
    anchor_node_id = raw_evaluation.get("anchor_node_id")
    if scope == "per_anchor":
        anchor_node_id = _node_id(anchor_node_id, "逐主体锚点")
        if node_types.get(anchor_node_id) != "detector":
            raise ValueError("逐主体锚点必须是检测节点")
    else:
        anchor_node_id = None

    for node in nodes:
        if node["type"] == "output" and node.get("box_source_node_id") is not None:
            if node_types.get(node["box_source_node_id"]) != "detector":
                raise ValueError("输出框来源必须是检测节点")

    raw_layout = config.get("layout") or {}
    raw_positions = raw_layout.get("nodes") if isinstance(raw_layout, dict) else {}
    positions = {}
    if isinstance(raw_positions, dict):
        for node_id, position in raw_positions.items():
            if node_id not in node_types or not isinstance(position, dict):
                continue
            try:
                positions[node_id] = {"x": float(position.get("x", 0)), "y": float(position.get("y", 0))}
            except (TypeError, ValueError):
                continue

    return {
        "version": COMBINATION_CONFIG_VERSION,
        "evaluation": {"scope": scope, "anchor_node_id": anchor_node_id},
        "nodes": nodes,
        "edges": edges,
        "layout": {"nodes": positions},
    }


def cascade_v1_to_v2(config: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a normalized linear v1 cascade into an equivalent graph."""
    if int(config.get("version", 1)) == COMBINATION_CONFIG_VERSION:
        return config
    stages = config.get("stages") or []
    nodes: list[dict] = [{"id": "frame", "type": "frame", "name": "画面输入"}]
    edges: list[dict] = []
    predicate_ids = []
    positions = {"frame": {"x": 40, "y": 80}}
    previous = "frame"
    for index, stage in enumerate(stages):
        detector = {
            "id": stage["id"], "type": "detector", "name": stage["name"],
            "model_id": stage["model_id"], "class_ids": stage.get("class_ids", []),
            "confidence": stage.get("confidence", 0.6),
            "max_candidates": stage.get("max_candidates", DEFAULT_MAX_CANDIDATES),
            "expand_ratio": stage.get("input", {}).get("expand_ratio", 0.1),
            "inference": stage.get("inference", {}),
        }
        predicate_id = f"{stage['id']}_exists"
        nodes.extend([
            detector,
            {"id": predicate_id, "type": "predicate", "name": f"{stage['name']}存在", "operator": "exists"},
        ])
        edges.extend([
            {"source": previous, "target": stage["id"], "kind": "data"},
            {"source": stage["id"], "target": predicate_id, "kind": "rule"},
        ])
        predicate_ids.append(predicate_id)
        positions[stage["id"]] = {"x": 260 + index * 240, "y": 80}
        positions[predicate_id] = {"x": 260 + index * 240, "y": 260}
        previous = stage["id"]
    rule_id = "all_stages"
    output_id = "output"
    nodes.extend([
        {"id": rule_id, "type": "logic", "name": "全部阶段命中", "operator": "and"},
        {
            "id": output_id, "type": "output", "name": "最终输出",
            "label": config.get("output", {}).get("label", "复合事件"),
            "color": config.get("output", {}).get("color", "#ff4d4f"),
            "box_source_node_id": stages[0]["id"] if stages else None,
        },
    ])
    edges.extend({"source": predicate_id, "target": rule_id, "kind": "rule"} for predicate_id in predicate_ids)
    edges.append({"source": rule_id, "target": output_id, "kind": "rule"})
    positions[rule_id] = {"x": 500, "y": 420}
    positions[output_id] = {"x": 500, "y": 580}
    return {
        "version": COMBINATION_CONFIG_VERSION,
        "evaluation": {"scope": "per_anchor", "anchor_node_id": stages[0]["id"] if stages else None},
        "nodes": nodes,
        "edges": edges,
        "layout": {"nodes": positions},
    }


def cascade_model_ids(config: Any) -> tuple[int, ...]:
    if not isinstance(config, dict):
        return ()
    result = []
    candidates = config.get("nodes") if int(config.get("version", 1) or 1) == 2 else config.get("stages")
    for stage in candidates or []:
        if not isinstance(stage, dict):
            continue
        if stage.get("type") not in (None, "detector"):
            continue
        try:
            model_id = int(stage.get("model_id"))
        except (TypeError, ValueError):
            continue
        if model_id not in result:
            result.append(model_id)
    return tuple(result)
