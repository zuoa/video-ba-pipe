"""Built-in linear cascade detector backed by the shared YOLO backend layer."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Sequence

import numpy as np

from app import logger
from app.config import VIDEO_FRAME_PIXEL_FORMAT
from app.core.algorithm import BaseAlgorithm
from app.core.cascade_algorithm_config import (
    normalize_cascade_algorithm_config,
)
from app.core.frame_utils import detect_frame_pixel_format, frame_to_rgb, infer_frame_dimensions
from app.core.model_resolver import get_model_resolver
from app.user_scripts.common.roi import (
    expand_and_clip_box,
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
    return expand_and_clip_box(box, frame_shape, expand_ratio)


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
        self.node_runtimes: Dict[str, Dict[str, Any]] = {}
        resolver = get_model_resolver()
        try:
            detector_nodes = (
                [node for node in self.cascade_config.get("nodes", []) if node.get("type") == "detector"]
                if self.cascade_config.get("version") == 2
                else self.cascade_config["stages"]
            )
            for stage in detector_nodes:
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
                runtime = {
                    "stage": stage,
                    "backend": backend,
                    "model_info": model_info,
                }
                self.stage_runtimes.append(runtime)
                self.node_runtimes[stage["id"]] = runtime
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
        if self.cascade_config.get("version") == 2:
            return self._process_combination(frame, roi_regions=roi_regions)
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

    @staticmethod
    def _graph_order(config: Dict[str, Any], kind: str, allowed_types: set[str]) -> List[str]:
        node_ids = {
            node["id"] for node in config["nodes"] if node.get("type") in allowed_types
        }
        adjacency: Dict[str, List[str]] = defaultdict(list)
        indegree = {node_id: 0 for node_id in node_ids}
        for edge in config["edges"]:
            if edge["kind"] != kind or edge["source"] not in node_ids or edge["target"] not in node_ids:
                continue
            adjacency[edge["source"]].append(edge["target"])
            indegree[edge["target"]] += 1
        queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
        result = []
        while queue:
            node_id = queue.popleft()
            result.append(node_id)
            for target in adjacency[node_id]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        return result

    @staticmethod
    def _records_for_context(
        records: List[Dict[str, Any]],
        anchor: Dict[str, Any] | None,
    ) -> List[Dict[str, Any]]:
        if anchor is None:
            return records
        anchor_id = anchor["node_id"]
        anchor_record_id = anchor["record_id"]
        selected = []
        for record in records:
            lineage = record["lineage"]
            if lineage.get(anchor_id) == anchor_record_id:
                selected.append(record)
            elif record["node_id"] in anchor["lineage"] and (
                record["record_id"] == anchor["lineage"].get(record["node_id"])
            ):
                selected.append(record)
        return selected

    @staticmethod
    def _predicate_state(operator: str, count: int, value: int | None) -> bool:
        if operator == "exists":
            return count > 0
        if operator == "not_exists":
            return count == 0
        expected = int(value or 0)
        return {
            "eq": count == expected,
            "ne": count != expected,
            "gt": count > expected,
            "gte": count >= expected,
            "lt": count < expected,
            "lte": count <= expected,
        }[operator]

    @staticmethod
    def _predicate_reason(
        source_name: str,
        operator: str,
        count: int,
        value: int | None,
        state: bool | None,
    ) -> str:
        if state is None:
            return f"检测节点“{source_name}”执行失败或结果不完整，当前条件无法判断"
        if operator == "exists":
            expectation = "至少检测到 1 个目标"
        elif operator == "not_exists":
            expectation = "没有检测到目标"
        else:
            symbol = {
                "eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤",
            }[operator]
            expectation = f"目标数量 {symbol} {int(value or 0)}"
        conclusion = "满足" if state else "不满足"
        return f"检测节点“{source_name}”命中 {count} 个目标，{conclusion}条件“{expectation}”"

    def _process_combination(
        self,
        frame: np.ndarray,
        roi_regions: list | None = None,
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        config = self.cascade_config
        node_by_id = {node["id"]: node for node in config["nodes"]}
        data_parent = {
            edge["target"]: edge["source"]
            for edge in config["edges"] if edge["kind"] == "data"
        }
        rule_inputs: Dict[str, List[str]] = defaultdict(list)
        predicate_source = {}
        for edge in config["edges"]:
            if edge["kind"] != "rule":
                continue
            rule_inputs[edge["target"]].append(edge["source"])
            if node_by_id[edge["target"]]["type"] == "predicate":
                predicate_source[edge["target"]] = edge["source"]

        try:
            pixel_format = detect_frame_pixel_format(
                frame, pixel_format=self.config.get("pixel_format", VIDEO_FRAME_PIXEL_FORMAT)
            )
            frame_width, frame_height = infer_frame_dimensions(frame, pixel_format=pixel_format)
            frame_rgb = frame_to_rgb(
                frame, pixel_format=pixel_format, width=frame_width, height=frame_height
            )
        except Exception as exc:
            return self._empty_result(f"输入帧转换失败: {exc}", [], started_at)

        pre_mask_regions, _, post_filter_regions = split_regions(roi_regions)
        frame_input = frame_rgb
        if pre_mask_regions:
            roi_mask = BaseAlgorithm.create_roi_mask(frame_rgb.shape, pre_mask_regions)
            frame_input = BaseAlgorithm.apply_roi_mask(frame_rgb, roi_mask)

        # observations_by_node 保留节点的完整检测事实，供规则和输出使用；
        # records_by_node 只保留 max_candidates 个候选，限制继续流向下游的推理量。
        observations_by_node: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        records_by_node: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        debug_by_node: Dict[str, Dict[str, Any]] = {}
        record_sequence = 0
        data_order = self._graph_order(config, "data", {"frame", "detector"})
        detector_order = [node_id for node_id in data_order if node_by_id[node_id]["type"] == "detector"]
        for node_id in detector_order:
            node = node_by_id[node_id]
            runtime = self.node_runtimes.get(node_id)
            if runtime is None:
                runtime = next(item for item in self.stage_runtimes if item["stage"]["id"] == node_id)
            backend = runtime["backend"]
            parent_id = data_parent[node_id]
            parent_is_detector = node_by_id[parent_id]["type"] == "detector"
            parent_records = records_by_node[parent_id] if parent_is_detector else [None]
            parent_debug = debug_by_node.get(parent_id) if parent_is_detector else None
            node_started = time.perf_counter()
            candidate_records = []
            crop_boxes = []
            errors = []
            unknown_lineage_ids = set()
            inherited_global_failure = False
            if parent_debug:
                unknown_lineage_ids.update(parent_debug["unknown_lineage_ids"])
                unknown_lineage_ids.update(parent_debug["pruned_lineage_ids"])
                inherited_global_failure = bool(parent_debug["failed_global"])
            successful_inferences = 0

            for parent_record in parent_records:
                if parent_record is None:
                    inference_frame = frame_input
                    crop_box = None
                else:
                    crop_box = _crop_box(parent_record["detection"], frame_rgb.shape, node["expand_ratio"])
                    if crop_box is None:
                        errors.append("父检测目标框无效")
                        unknown_lineage_ids.update(parent_record["lineage"].values())
                        continue
                    crop_boxes.append(crop_box)
                    x1, y1, x2, y2 = crop_box
                    inference_frame = frame_rgb[y1:y2, x1:x2]
                try:
                    detections, _, _ = backend.infer(inference_frame)
                    successful_inferences += 1
                except Exception as exc:
                    errors.append(str(exc))
                    if parent_record is not None:
                        unknown_lineage_ids.update(parent_record["lineage"].values())
                    logger.warning("[Combination] 节点 %s 推理失败: %s", node["name"], exc, exc_info=True)
                    continue
                if crop_box is not None:
                    detections = remap_detections_to_full_frame(detections, crop_box)
                elif post_filter_regions and detections:
                    detections = filter_items_by_regions(
                        detections, frame_rgb.shape, post_filter_regions, metric="ioa", threshold=0.3
                    )
                # 单次后端异常返回过多结果时先做局部保护；所有父裁剪合并后还会再次全局限流。
                detections = sorted(detections, key=_confidence, reverse=True)[:node["max_candidates"]]
                for detection in detections:
                    record_sequence += 1
                    lineage = dict(parent_record["lineage"]) if parent_record is not None else {}
                    lineage[node_id] = record_sequence
                    candidate_records.append({
                        "record_id": record_sequence,
                        "node_id": node_id,
                        "detection": dict(detection),
                        "lineage": lineage,
                    })

            ordered_records = sorted(
                candidate_records,
                key=lambda record: _confidence(record["detection"]),
                reverse=True,
            )
            observations_by_node[node_id] = ordered_records
            records_by_node[node_id] = ordered_records[:node["max_candidates"]]
            pruned_records = ordered_records[node["max_candidates"]:]
            pruned_lineage_ids = {
                lineage_id
                for record in pruned_records
                for lineage_id in record["lineage"].values()
            }
            failed_global = inherited_global_failure or bool(
                errors and successful_inferences == 0 and parent_records
            )
            has_unknown_inputs = failed_global or bool(errors) or bool(unknown_lineage_ids)
            detection_count = len(ordered_records)
            forwarded_count = len(records_by_node[node_id])
            pruned_count = len(pruned_records)
            if (inherited_global_failure or unknown_lineage_ids) and not parent_records:
                execution_state = "blocked"
                reason_code = "upstream_failed"
                reason = f"未执行：上游节点“{node_by_id[parent_id]['name']}”执行失败或结果不完整"
            elif not parent_records:
                execution_state = "skipped"
                reason_code = "upstream_empty"
                reason = f"未执行：上游节点“{node_by_id[parent_id]['name']}”没有可继续检测的目标"
            elif errors and successful_inferences == 0:
                execution_state = "failed"
                reason_code = "inference_failed"
                reason = f"执行失败：{errors[0]}"
            elif errors:
                execution_state = "degraded"
                reason_code = "partial_failure"
                reason = (
                    f"部分完成：收到 {len(parent_records)} 个输入，成功执行 {successful_inferences} 次，"
                    f"失败 {len(errors)} 次，命中 {detection_count} 个目标"
                )
            elif has_unknown_inputs:
                execution_state = "degraded"
                reason_code = "upstream_incomplete"
                reason = (
                    f"已执行 {successful_inferences} 次，命中 {detection_count} 个目标；"
                    f"但上游节点“{node_by_id[parent_id]['name']}”有失败或被截断的候选，结果不完整"
                )
            elif detection_count:
                execution_state = "matched"
                reason_code = "matched"
                reason = (
                    f"已执行 {successful_inferences} 次，命中 {detection_count} 个目标"
                    + (f"，向下游传递 {forwarded_count} 个" if parent_is_detector else "")
                )
            else:
                execution_state = "not_matched"
                reason_code = "not_matched"
                reason = (
                    f"已执行 {successful_inferences} 次，但没有检测到目标"
                    + (f"；输入来自上游节点“{node_by_id[parent_id]['name']}”" if parent_is_detector else "")
                )
            debug_by_node[node_id] = {
                "node_id": node_id,
                "node_name": node["name"],
                "node_type": "detector",
                "model_id": node["model_id"],
                "backend": backend.name,
                "status": "failed" if failed_global else "degraded" if has_unknown_inputs else "ok",
                "execution_state": execution_state,
                "reason_code": reason_code,
                "reason": reason,
                "upstream_node_id": parent_id,
                "upstream_node_name": node_by_id[parent_id]["name"],
                "input_kind": "crops" if parent_is_detector else "frame",
                "input_count": len(parent_records),
                "successful_inferences": successful_inferences,
                "failed_inferences": len(errors),
                "detection_count": detection_count,
                "forwarded_count": forwarded_count,
                "pruned_count": pruned_count,
                "detections": [dict(record["detection"]) for record in ordered_records],
                "crop_boxes": crop_boxes,
                "error_count": len(errors),
                "errors": errors[:5],
                "failed_global": failed_global,
                "has_unknown_inputs": has_unknown_inputs,
                "unknown_lineage_ids": unknown_lineage_ids,
                "pruned_lineage_ids": pruned_lineage_ids,
                "inference_time_ms": round((time.perf_counter() - node_started) * 1000.0, 2),
            }

        evaluation = config["evaluation"]
        anchor_node_id = evaluation.get("anchor_node_id")
        anchors = records_by_node.get(anchor_node_id, []) if evaluation["scope"] == "per_anchor" else [None]
        output_node = next(node for node in config["nodes"] if node["type"] == "output")
        final_rule_id = rule_inputs[output_node["id"]][0]
        context_results = []
        output_detections = []

        def evaluate(node_id: str, anchor: Dict[str, Any] | None, memo: dict):
            if node_id in memo:
                return memo[node_id]
            node = node_by_id[node_id]
            if node["type"] == "predicate":
                detector_id = predicate_source[node_id]
                debug = debug_by_node[detector_id]
                relevant = self._records_for_context(observations_by_node[detector_id], anchor)
                is_unknown = (
                    debug["has_unknown_inputs"] if anchor is None
                    else debug["failed_global"]
                    or anchor["record_id"] in debug["unknown_lineage_ids"]
                )
                state = None if is_unknown else self._predicate_state(
                    node["operator"], len(relevant), node.get("value")
                )
                evidence = relevant if state is True and node["operator"] != "not_exists" else []
                memo[node_id] = (state, evidence, len(relevant))
                return memo[node_id]
            values = [evaluate(input_id, anchor, memo) for input_id in rule_inputs[node_id]]
            states = [value[0] for value in values]
            if node["operator"] == "and":
                state = False if False in states else None if None in states else True
            elif node["operator"] == "or":
                state = True if True in states else None if None in states else False
            else:
                state = None if states[0] is None else not states[0]
            evidence = [record for value in values for record in value[1]] if state is True else []
            memo[node_id] = (state, evidence, None)
            return memo[node_id]

        for anchor in anchors:
            memo = {}
            state, evidence, _ = evaluate(final_rule_id, anchor, memo)
            predicate_details = []
            for node in config["nodes"]:
                if node["type"] != "predicate" or node["id"] not in memo:
                    continue
                predicate_state, _, count = memo[node["id"]]
                source_id = predicate_source[node["id"]]
                source_name = node_by_id[source_id]["name"]
                predicate_details.append({
                    "node_id": node["id"], "name": node["name"], "operator": node["operator"],
                    "value": node.get("value"), "count": count,
                    "source_node_id": source_id, "source_node_name": source_name,
                    "state": "unknown" if predicate_state is None else "true" if predicate_state else "false",
                    "reason": self._predicate_reason(
                        source_name, node["operator"], count, node.get("value"), predicate_state
                    ),
                })
            rule_details = []
            for rule_node_id, (rule_state, _, _) in memo.items():
                rule_node = node_by_id[rule_node_id]
                if rule_node["type"] not in {"predicate", "logic"}:
                    continue
                rule_details.append({
                    "node_id": rule_node_id,
                    "name": rule_node["name"],
                    "node_type": rule_node["type"],
                    "operator": rule_node["operator"],
                    "input_node_ids": list(rule_inputs.get(rule_node_id, [])),
                    "state": "unknown" if rule_state is None else "true" if rule_state else "false",
                })
            false_predicates = [item["name"] for item in predicate_details if item["state"] == "false"]
            unknown_predicates = [item["name"] for item in predicate_details if item["state"] == "unknown"]
            if state is True:
                context_summary = "全部规则成立，产生业务结果"
            elif state is None:
                names = "、".join(unknown_predicates) or "上游条件"
                context_summary = f"无法完成判断：{names} 的结果未知"
            else:
                names = "、".join(false_predicates) or "组合逻辑"
                context_summary = f"未产生业务结果：{names} 不成立"
            context_results.append({
                "anchor_record_id": anchor["record_id"] if anchor else None,
                "anchor_box": list(_box(anchor["detection"]) or []) if anchor else None,
                "state": "unknown" if state is None else "true" if state else "false",
                "summary": context_summary,
                "predicates": predicate_details,
                "rules": rule_details,
            })
            if state is not True:
                continue
            box_records = self._records_for_context(
                observations_by_node.get(output_node.get("box_source_node_id"), []), anchor
            ) if output_node.get("box_source_node_id") else []
            evidence_confidences = [_confidence(record["detection"]) for record in evidence]
            confidence = min(evidence_confidences) if evidence_confidences else 1.0
            if anchor is not None:
                selected_box_records = [max(
                    box_records, key=lambda record: _confidence(record["detection"]), default=None
                )]
            else:
                selected_box_records = box_records or [None]
            for box_record in selected_box_records:
                output_box = list(_box(box_record["detection"]) or []) if box_record else []
                output_detections.append({
                    "box": output_box, "bbox": output_box,
                    "label": output_node["label"], "label_name": output_node["label"],
                    "class_name": output_node["label"], "label_color": output_node["color"],
                    "confidence": float(confidence),
                    "confidence_source": "detection_evidence" if evidence_confidences else "logical",
                    "anchor_node_id": anchor_node_id,
                    "anchor_record_id": anchor["record_id"] if anchor else None,
                })

        node_debug = []
        for node_id in detector_order:
            item = dict(debug_by_node[node_id])
            item["unknown_lineage_ids"] = sorted(item["unknown_lineage_ids"])
            item["pruned_lineage_ids"] = sorted(item["pruned_lineage_ids"])
            node_debug.append(item)
        failed_nodes = [item for item in node_debug if item["failed_global"]]
        problem_nodes = [
            item for item in node_debug
            if item["execution_state"] in {"failed", "degraded", "blocked"}
        ]
        true_contexts = sum(item["state"] == "true" for item in context_results)
        unknown_contexts = sum(item["state"] == "unknown" for item in context_results)
        anchor_debug = debug_by_node.get(anchor_node_id) if anchor_node_id else None
        anchor_unknown_without_context = bool(
            evaluation["scope"] == "per_anchor"
            and not anchors
            and anchor_debug
            and anchor_debug["has_unknown_inputs"]
        )
        if failed_nodes or unknown_contexts or anchor_unknown_without_context:
            diagnosis_state = "unknown"
            first_problem = (
                failed_nodes[0] if failed_nodes else
                anchor_debug if anchor_unknown_without_context else
                problem_nodes[0] if problem_nodes else None
            )
            diagnosis_summary = (
                first_problem["reason"] if first_problem else
                f"{unknown_contexts} 个判定上下文因检测结果不完整而无法判断"
            )
        elif evaluation["scope"] == "per_anchor" and not anchors:
            diagnosis_state = "no_context"
            first_problem = anchor_debug
            diagnosis_summary = (
                f"未进入规则判断：锚点节点“{node_by_id[anchor_node_id]['name']}”没有检测到目标"
            )
        elif true_contexts:
            diagnosis_state = "matched"
            first_problem = None
            diagnosis_summary = f"{true_contexts} 个判定上下文满足全部规则，输出 {len(output_detections)} 个业务结果"
        else:
            diagnosis_state = "not_matched"
            first_problem = None
            diagnosis_summary = "检测节点已执行，但没有判定上下文满足全部规则"
            for context in context_results:
                failed_predicate = next(
                    (item for item in context["predicates"] if item["state"] == "false"), None
                )
                if failed_predicate:
                    diagnosis_summary = failed_predicate["reason"]
                    first_problem = debug_by_node.get(failed_predicate["source_node_id"])
                    break
        diagnosis = {
            "state": diagnosis_state,
            "summary": diagnosis_summary,
            "first_break_node_id": first_problem["node_id"] if first_problem else None,
            "first_break_node_name": first_problem["node_name"] if first_problem else None,
        }
        metadata = {
            "cascade_checked": not bool(failed_nodes),
            "combination_checked": True,
            "config_version": 2,
            "evaluation_scope": evaluation["scope"],
            "anchor_node_id": anchor_node_id,
            "stage_count": len(node_debug),
            "completed_paths": sum(item["state"] == "true" for item in context_results),
            "node_debug": node_debug,
            "stage_debug": node_debug,
            "context_evaluations": context_results,
            "diagnosis": diagnosis,
            "total_detections": len(output_detections),
            "inference_time_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
        }
        if failed_nodes:
            first_failed = failed_nodes[0]
            detail = first_failed["errors"][0] if first_failed["errors"] else "推理失败"
            metadata.update({
                "error": f"检测节点“{first_failed['node_name']}”执行失败: {detail}",
                "error_code": "combination_node_failed",
            })
        return {
            "detections": output_detections,
            "metadata": metadata,
        }

    def cleanup(self):
        runtimes = list(getattr(self, "stage_runtimes", []) or [])
        self.stage_runtimes = []
        self.node_runtimes = {}
        for runtime in reversed(runtimes):
            backend = runtime.get("backend")
            if backend is None or not hasattr(backend, "cleanup"):
                continue
            try:
                backend.cleanup()
            except Exception:
                logger.warning("[Cascade] 后端清理失败", exc_info=True)
