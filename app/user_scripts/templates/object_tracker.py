"""Object tracker workflow adapter.

Consumes upstream detections, assigns stable track_id values, then optionally
filters by a built-in event (loiter / stay / region cross).
"""

from typing import Any, Dict, List, Optional
import uuid

import numpy as np

from app.user_scripts.common.result import build_result, collect_upstream_detections
from app.user_scripts.common.track_events import apply_event, init_event_state
from app.user_scripts.common.tracker import create_tracker

SCRIPT_METADATA = {
    "name": "目标追踪",
    "version": "v2.0",
    "description": "为上游检测框分配跨帧 track_id，支持 IoU、ByteTrack 与多架构行人 ReID，并可判定徘徊、停留或按方向穿越热区。",
    "author": "system",
    "category": "tracking",
    "tags": ["tracking", "iou", "bytetrack", "botsort", "reid", "identity", "loiter", "stay", "crossing"],
    "config_schema": {
        "backend": {
            "type": "select",
            "label": "跟踪后端",
            "required": True,
            "default": "iou",
            "options": [
                {"value": "iou", "label": "贪心 IoU（静止/慢动）"},
                {"value": "bytetrack", "label": "ByteTrack（移动/遮挡）"},
                {"value": "botsort_reid", "label": "BoT-SORT ReID（行人遮挡/再进入）"},
            ],
            "description": "违停、占道用 IoU；人员徘徊、短暂遮挡用 ByteTrack",
        },
        "reid_model_bundle_id": {
            "type": "reid_model_select",
            "label": "ReID 模型包 ID",
            "min": 1,
            "visible_when": {"backend": "botsort_reid"},
            "description": "在 ReID 模型管理中创建的逻辑模型包；不可用时自动降级为运动跟踪",
        },
        "reid_backend": {
            "type": "select",
            "label": "ReID 推理后端",
            "default": "auto",
            "options": [
                {"value": "auto", "label": "自动选择"},
                {"value": "onnxruntime", "label": "ONNX Runtime CPU"},
                {"value": "onnxruntime-cuda", "label": "ONNX Runtime CUDA"},
                {"value": "tensorrt", "label": "TensorRT EP"},
                {"value": "torchscript", "label": "TorchScript"},
                {"value": "rknn", "label": "RKNNLite"},
            ],
            "visible_when": {"backend": "botsort_reid"},
        },
        "reid_memory_seconds": {
            "type": "float",
            "label": "离场重识别窗口（秒）",
            "default": 300,
            "min": 5,
            "max": 3600,
            "step": 5,
            "visible_when": {"backend": "botsort_reid"},
        },
        "appearance_threshold": {
            "type": "float",
            "label": "外观相似度阈值",
            "min": 0.0,
            "max": 1.0,
            "step": 0.01,
            "visible_when": {"backend": "botsort_reid"},
            "description": "留空使用模型包校准阈值",
        },
        "reid_min_box_height": {
            "type": "int",
            "label": "最小行人框高度",
            "default": 48,
            "min": 16,
            "max": 512,
            "visible_when": {"backend": "botsort_reid"},
        },
        "camera_motion_compensation": {
            "type": "boolean",
            "label": "相机运动补偿",
            "default": False,
            "visible_when": {"backend": "botsort_reid"},
            "description": "固定机位默认关闭；机架抖动或少量转动时可启用，镜头突变会重置运动状态",
        },
        "event": {
            "type": "select",
            "label": "输出事件",
            "required": True,
            "default": "none",
            "options": [
                {"value": "none", "label": "全部轨迹（只赋 ID）"},
                {"value": "loiter", "label": "徘徊（区域内待够久）"},
                {"value": "stay", "label": "停留（原地待够久）"},
                {"value": "region_cross", "label": "穿越热区（按方向）"},
            ],
            "description": "在追踪结果上直接判定，不必再串徘徊/停留/穿越算法。穿越使用上游 ROI。",
        },
        "max_misses": {
            "type": "int",
            "label": "丢失保留次数",
            "min": 1,
            "max": 300,
            "step": 1,
            "description": "连续未匹配多少次后删除轨迹。留空则 IoU=3，ByteTrack=30",
        },
        "min_hits": {
            "type": "int",
            "label": "确认命中次数",
            "default": 1,
            "min": 1,
            "max": 20,
            "step": 1,
            "description": "达到该命中次数后才对外输出",
        },
        "label_filter": {
            "type": "string",
            "label": "类别过滤",
            "default": "",
            "placeholder": "car,motorcycle",
            "description": "留空表示全部；多个类别用逗号分隔",
        },
        "max_tracks": {
            "type": "int",
            "label": "最大轨迹数",
            "default": 256,
            "min": 1,
            "max": 2048,
            "step": 1,
        },
        "history_size": {
            "type": "int",
            "label": "短轨迹点数",
            "default": 32,
            "min": 1,
            "max": 256,
            "step": 1,
            "description": "写入检测 attributes.history 的中心点点数，供画轨迹和停留半径使用",
        },
        "match_iou": {
            "type": "float",
            "label": "关联 IoU",
            "default": 0.3,
            "min": 0.05,
            "max": 0.95,
            "step": 0.05,
            "visible_when": {"backend": "iou"},
            "description": "同类框 IoU 达到该值才视为同一目标",
        },
        "track_high_thresh": {
            "type": "float",
            "label": "高分阈值",
            "default": 0.5,
            "min": 0.05,
            "max": 1.0,
            "step": 0.05,
            "visible_when": {"backend": "bytetrack"},
        },
        "track_low_thresh": {
            "type": "float",
            "label": "低分阈值",
            "default": 0.1,
            "min": 0.0,
            "max": 0.9,
            "step": 0.05,
            "visible_when": {"backend": "bytetrack"},
            "description": "用低分框救回被遮挡轨迹",
        },
        "new_track_thresh": {
            "type": "float",
            "label": "开新轨迹最低分",
            "default": 0.6,
            "min": 0.05,
            "max": 1.0,
            "step": 0.05,
            "visible_when": {"backend": "bytetrack"},
        },
        "match_thresh": {
            "type": "float",
            "label": "ByteTrack 关联阈值",
            "default": 0.8,
            "min": 0.1,
            "max": 0.95,
            "step": 0.05,
            "visible_when": {"backend": "bytetrack"},
            "description": "原文是 1-IoU 代价上限，0.8 约等于 IoU ≥ 0.2",
        },
        "min_dwell_seconds": {
            "type": "float",
            "label": "最短时长（秒）",
            "default": 8,
            "min": 0.5,
            "max": 600,
            "step": 0.5,
            "visible_when": {"event": ["loiter", "stay"]},
            "description": "徘徊/停留要持续出现多久才输出，可在工作流节点上覆盖",
        },
        "min_displace_px": {
            "type": "int",
            "label": "最小位移（像素）",
            "default": 0,
            "min": 0,
            "max": 4000,
            "step": 1,
            "visible_when": {"event": ["loiter", "stay"]},
            "description": "轨迹活动半径低于该值不算。徘徊可用来排除站着不动；0 表示不限制",
        },
        "max_displace_px": {
            "type": "int",
            "label": "最大位移（像素）",
            "min": 0,
            "max": 4000,
            "step": 1,
            "visible_when": {"event": ["loiter", "stay"]},
            "description": "轨迹活动半径超过该值不算。停留建议 48；徘徊留空表示不限制",
        },
        "disappear_seconds": {
            "type": "float",
            "label": "消失后遗忘（秒）",
            "default": 3,
            "min": 0.5,
            "max": 120,
            "step": 0.5,
            "visible_when": {"event": ["loiter", "stay", "region_cross"]},
            "description": "目标丢失超过该时间后重新计时 / 重新检测穿越",
        },
        "cross_mode": {
            "type": "select",
            "label": "穿越方式",
            "default": "enter",
            "options": [
                {"value": "enter", "label": "进入热区"},
                {"value": "exit", "label": "离开热区"},
                {"value": "cross", "label": "进入或离开"},
            ],
            "visible_when": {"event": "region_cross"},
            "description": "热区来自上游 ROI 绘制节点",
        },
        "cross_direction": {
            "type": "select",
            "label": "穿越方向",
            "default": "any",
            "options": [
                {"value": "any", "label": "任意方向"},
                {"value": "left_to_right", "label": "从左到右"},
                {"value": "right_to_left", "label": "从右到左"},
                {"value": "top_to_bottom", "label": "从上到下"},
                {"value": "bottom_to_top", "label": "从下到上"},
            ],
            "visible_when": {"event": "region_cross"},
        },
    },
    "performance": {
        "timeout": 5,
        "memory_limit_mb": 128,
        "gpu_required": False,
        "estimated_time_ms": 5,
    },
    "dependencies": [
        "numpy>=1.19.0",
    ],
}


def _parse_label_filter(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        import json

        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


def _optional_number(config: dict, key: str, cast):
    value = config.get(key)
    if value is None or value == "":
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _tracker_kwargs(config: dict) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "label_filter": _parse_label_filter(config.get("label_filter")),
    }
    for key, cast in (
        ("max_misses", int),
        ("min_hits", int),
        ("max_tracks", int),
        ("history_size", int),
        ("match_iou", float),
        ("track_high_thresh", float),
        ("track_low_thresh", float),
        ("new_track_thresh", float),
        ("match_thresh", float),
        ("appearance_threshold", float),
        ("proximity_threshold", float),
        ("reid_memory_seconds", float),
        ("feature_ema_alpha", float),
    ):
        value = _optional_number(config, key, cast)
        if value is not None:
            kwargs[key] = value
    return kwargs


def _identity_key(config: dict, frame_width: Optional[int], frame_height: Optional[int]):
    return (
        config.get("source_id"),
        int(frame_width or 0),
        int(frame_height or 0),
    )


def _estimate_camera_motion(frame_rgb: np.ndarray, state: dict):
    """Estimate global translation on a small grayscale frame."""
    from app.core.cv2_compat import cv2

    if frame_rgb is None or getattr(frame_rgb, "ndim", 0) != 3:
        return (0.0, 0.0), False
    height, width = frame_rgb.shape[:2]
    target_width = min(320, width)
    scale = target_width / max(1, width)
    target_height = max(1, int(round(height * scale)))
    small = cv2.resize(frame_rgb, (target_width, target_height), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY).astype(np.float32)
    previous = state.get("camera_motion_frame")
    state["camera_motion_frame"] = gray
    if previous is None or previous.shape != gray.shape:
        return (0.0, 0.0), False
    (dx, dy), response = cv2.phaseCorrelate(previous, gray)
    scene_cut = (
        not np.isfinite(dx) or not np.isfinite(dy) or response < 0.05
        or abs(dx) > target_width * 0.25 or abs(dy) > target_height * 0.25
    )
    if scene_cut:
        return (0.0, 0.0), True
    return (float(dx / scale), float(dy / scale)), False


def init(config: dict) -> Dict[str, Any]:
    backend = str(config.get("backend") or "iou")
    kwargs = _tracker_kwargs(config)
    reid_backend = None
    reid_error = None
    reid_contract = None
    if backend.strip().lower() in {"botsort_reid", "botsort", "reid"}:
        try:
            from app.core.database_models import ReIdModelBundle
            from app.core.reid_inference import create_reid_backend

            bundle_id = int(config.get("reid_model_bundle_id"))
            bundle = ReIdModelBundle.get_by_id(bundle_id)
            if "appearance_threshold" not in kwargs:
                kwargs["appearance_threshold"] = float(bundle.default_similarity_threshold)
            reid_backend = create_reid_backend(
                bundle,
                str(config.get("reid_backend") or "auto"),
                config,
            )
            reid_contract = bundle.contract_id
        except Exception as exc:
            reid_error = f"runtime_unavailable:{type(exc).__name__}:{exc}"
    tracker = create_tracker(backend, **kwargs)
    return {
        "tracker": tracker,
        "backend": tracker.backend,
        "identity_key": None,
        "event": init_event_state(config),
        "reid_backend": reid_backend,
        "reid_error": reid_error,
        "reid_contract": reid_contract,
        "tracking_session_id": uuid.uuid4().hex,
        "camera_motion_frame": None,
    }


def process(
    frame: np.ndarray,
    config: dict,
    roi_regions: Optional[List[dict]] = None,
    state: Optional[dict] = None,
    upstream_results: Optional[dict] = None,
    frame_width: Optional[int] = None,
    frame_height: Optional[int] = None,
    frame_timestamp: Optional[float] = None,
    pixel_format: str = "nv12",
    frame_rgb: Optional[np.ndarray] = None,
) -> dict:
    if not isinstance(state, dict):
        return build_result([], metadata={"error": "tracker_state_missing"})
    if "tracker" not in state:
        state.update(init(config))

    identity_key = _identity_key(config, frame_width, frame_height)
    if state.get("identity_key") not in (None, identity_key):
        state["tracker"].reset()
        state["event"] = init_event_state(config)
        state["tracking_session_id"] = uuid.uuid4().hex
        state["camera_motion_frame"] = None
    state["identity_key"] = identity_key
    if "event" not in state or not isinstance(state.get("event"), dict):
        state["event"] = init_event_state(config)

    detections = collect_upstream_detections(upstream_results)
    backend = state.get("backend") or getattr(state["tracker"], "backend", "iou")
    degraded_reason = None
    if backend == "botsort_reid":
        reid_backend = state.get("reid_backend")
        degraded_reason = state.get("reid_error")
        candidates = [
            (index, det) for index, det in enumerate(detections)
            if str(det.get("label") or det.get("label_name") or "").lower() == "person"
            and isinstance(det.get("box") or det.get("bbox"), (list, tuple))
        ]
        if reid_backend is not None and candidates:
            try:
                inference_frame = frame_rgb if frame_rgb is not None else frame
                response = reid_backend.infer(
                    inference_frame,
                    [(det.get("box") or det.get("bbox")) for _, det in candidates],
                )
                by_candidate = {
                    int(item["index"]): item.get("embedding")
                    for item in response.get("details") or []
                    if item.get("embedding") is not None
                }
                rejected_by_candidate = {
                    int(item["index"]): item.get("reason") or "crop_not_eligible"
                    for item in (response.get("metadata") or {}).get("rejected") or []
                    if item.get("index") is not None
                }
                for candidate_index, (detection_index, _det) in enumerate(candidates):
                    detections[detection_index] = dict(detections[detection_index])
                    if candidate_index in by_candidate:
                        detections[detection_index]["_reid_embedding"] = by_candidate[candidate_index]
                    elif candidate_index in rejected_by_candidate:
                        detections[detection_index]["_reid_rejected_reason"] = rejected_by_candidate[candidate_index]
                state["reid_contract"] = (
                    response.get("metadata") or {}
                ).get("model_contract") or state.get("reid_contract")
                degraded_reason = None
            except Exception as exc:
                try:
                    from app.core.shared_inference import SharedInferenceOverloaded
                    reason = "queue_overloaded" if isinstance(exc, SharedInferenceOverloaded) else "runtime_unavailable"
                except Exception:
                    reason = "runtime_unavailable"
                degraded_reason = f"{reason}:{type(exc).__name__}"
        elif reid_backend is None:
            degraded_reason = degraded_reason or "runtime_unavailable"
        camera_motion = (0.0, 0.0)
        scene_cut = False
        if bool(config.get("camera_motion_compensation", False)):
            camera_motion, scene_cut = _estimate_camera_motion(
                frame_rgb if frame_rgb is not None else frame, state
            )
            if scene_cut and hasattr(state["tracker"], "handle_scene_cut"):
                state["tracker"].handle_scene_cut(frame_timestamp or 0.0)
        tracks = state["tracker"].update(
            detections, timestamp=frame_timestamp, degraded_reason=degraded_reason,
            camera_motion=camera_motion,
        )
    else:
        tracks = state["tracker"].update(detections, timestamp=frame_timestamp)
    tracked = [track.to_detection(backend) for track in tracks]
    if backend == "botsort_reid":
        for detection in tracked:
            attributes = dict(detection.get("attributes") or {})
            attributes["reid_model_contract"] = state.get("reid_contract")
            detection["attributes"] = attributes
    height = int(frame_height or 0)
    width = int(frame_width or 0)
    if (height <= 0 or width <= 0) and frame is not None and getattr(frame, "ndim", 0) == 3 and frame.shape[-1] in (3, 4):
        height = int(frame.shape[0])
        width = int(frame.shape[1])
    emitted, event_meta = apply_event(
        tracked,
        config=config,
        state=state["event"],
        timestamp=frame_timestamp,
        roi_regions=roi_regions,
        frame_width=width,
        frame_height=height,
    )
    return build_result(
        emitted,
        metadata={
            "backend": backend,
            "tracking_session_id": state.get("tracking_session_id"),
            "reid_model_contract": state.get("reid_contract"),
            "reid_degraded_reason": degraded_reason,
            "camera_motion_compensation": bool(config.get("camera_motion_compensation", False)),
            "track_count": len(tracks),
            **event_meta,
            "upstream_count": len(detections),
        },
    )


def cleanup(state: Optional[dict] = None) -> None:
    if not state:
        return
    if state.get("tracker") is not None:
        state["tracker"].reset()
    if state.get("reid_backend") is not None:
        state["reid_backend"].cleanup()
        state["reid_backend"] = None
    state["event"] = init_event_state({})
