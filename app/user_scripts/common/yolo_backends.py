"""
Reusable YOLO inference backends.

The goal is to keep script templates thin and move backend-specific logic here.
Current backends:
- ultralytics.YOLO
- RKNNLite
- ONNX Runtime
"""

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from app import logger

try:
    from app.config import SHARED_INFERENCE_ENABLED
except ImportError:  # pragma: no cover - standalone adapter tests/templates
    SHARED_INFERENCE_ENABLED = False

_SHARED_INFERENCE_CLIENT_MODE = (
    SHARED_INFERENCE_ENABLED
    and os.getenv("SHARED_INFERENCE_WORKER", "false").lower()
    not in ("true", "1", "yes", "on")
)

# Both runtimes are loaded only when their local backend is actually created.
# In particular, importing this adapter in an RKNN-only source host must not
# import Ultralytics -> PyTorch -> CUDA and retain their allocator state.
YOLO = None
ULTRALYTICS_IMPORT_ERROR = None
_ULTRALYTICS_IMPORT_ATTEMPTED = False
RKNNLite = None
RKNNLITE_IMPORT_ERROR = None
_RKNNLITE_IMPORT_ATTEMPTED = False


def _load_ultralytics():
    global YOLO, ULTRALYTICS_IMPORT_ERROR, _ULTRALYTICS_IMPORT_ATTEMPTED
    if YOLO is not None:
        return YOLO
    if _SHARED_INFERENCE_CLIENT_MODE:
        return None
    if _ULTRALYTICS_IMPORT_ATTEMPTED:
        return None
    _ULTRALYTICS_IMPORT_ATTEMPTED = True
    try:
        from ultralytics import YOLO as loaded_yolo
        YOLO = loaded_yolo
    except Exception as exc:
        ULTRALYTICS_IMPORT_ERROR = exc
    return YOLO


def _load_rknnlite():
    global RKNNLite, RKNNLITE_IMPORT_ERROR, _RKNNLITE_IMPORT_ATTEMPTED
    if RKNNLite is not None:
        return RKNNLite
    if _RKNNLITE_IMPORT_ATTEMPTED:
        return None
    _RKNNLITE_IMPORT_ATTEMPTED = True
    try:
        from rknnlite.api import RKNNLite as loaded_rknnlite
        RKNNLite = loaded_rknnlite
    except Exception as exc:
        RKNNLITE_IMPORT_ERROR = exc
    return RKNNLite

# ONNX Runtime performs device discovery during import.  Shared Ultralytics
# workers never use it, so importing it eagerly both delays model readiness and
# emits irrelevant /sys/class/drm warnings on NVIDIA-only containers.
ort = None
ONNXRUNTIME_IMPORT_ERROR = None


def _load_onnxruntime():
    global ort, ONNXRUNTIME_IMPORT_ERROR
    if ort is not None:
        return ort
    if ONNXRUNTIME_IMPORT_ERROR is not None:
        return None
    try:
        import onnxruntime as loaded_ort
        ort = loaded_ort
    except Exception as exc:
        ONNXRUNTIME_IMPORT_ERROR = exc
    return ort


def parse_classes(model_info: Dict[str, Any]) -> Dict[int, str]:
    classes = {}
    for key, value in (model_info.get("classes") or {}).items():
        try:
            classes[int(key)] = str(value)
        except Exception:
            continue
    return classes


def parse_input_shape(shape_value: Any) -> Tuple[int, int]:
    """Return ``(width, height)`` from common NCHW/NHWC metadata shapes."""
    if not shape_value:
        return 0, 0

    def _dimensions_to_size(dimensions: List[int]) -> Tuple[int, int]:
        if len(dimensions) < 2:
            return 0, 0
        if len(dimensions) >= 4 and dimensions[-1] in (1, 3, 4):
            return dimensions[-2], dimensions[-3]
        return dimensions[-1], dimensions[-2]

    if isinstance(shape_value, str):
        normalized = shape_value.lower().replace("[", "").replace("]", "").replace(" ", "")
        if "x" in normalized:
            parts = normalized.split("x")
            if len(parts) >= 2 and all(part.isdigit() for part in parts):
                return _dimensions_to_size([int(part) for part in parts])
        parts = normalized.split(",")
        if len(parts) >= 2 and all(part.isdigit() for part in parts):
            return _dimensions_to_size([int(part) for part in parts])

    if isinstance(shape_value, (list, tuple)) and len(shape_value) >= 2:
        try:
            return _dimensions_to_size([int(value) for value in shape_value])
        except Exception:
            return 0, 0

    return 0, 0


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off"):
            return False
    raise ValueError(f"无法解析布尔配置值: {value!r}")


def _bounded_float(config: Dict[str, Any], key: str, default: float) -> float:
    raw_value = config.get(key, default)
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 必须是数字，当前值: {raw_value!r}") from exc
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{key} 必须在 0 到 1 之间，当前值: {value}")
    return value


def normalize_backend_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize the configuration shared by all YOLO backends."""
    if not isinstance(config, dict):
        raise ValueError("检测器配置必须是对象")

    normalized = dict(config)
    backend_aliases = {
        "auto": "auto",
        "ultralytics": "ultralytics",
        "onnx": "onnxruntime",
        "onnxruntime": "onnxruntime",
        "rknn": "rknn",
        "rknnlite": "rknn",
    }
    requested_backend = str(normalized.get("backend") or "auto").strip().lower()
    if requested_backend not in backend_aliases:
        supported = ", ".join(sorted(backend_aliases))
        raise ValueError(f"不支持的推理后端: {requested_backend}；可选值: {supported}")
    normalized["backend"] = backend_aliases[requested_backend]
    normalized["confidence"] = _bounded_float(normalized, "confidence", 0.6)
    normalized["nms_iou"] = _bounded_float(normalized, "nms_iou", 0.45)
    normalized["class_filter"] = _parse_int_list(normalized.get("class_filter"))

    for key in ("input_width", "input_height"):
        raw_value = normalized.get(key)
        if raw_value in (None, ""):
            continue
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} 必须是正整数，当前值: {raw_value!r}") from exc
        if value <= 0:
            raise ValueError(f"{key} 必须是正整数，当前值: {value}")
        normalized[key] = value

    for key in ("rknn_input_format", "onnx_input_format"):
        value = str(normalized.get(key) or "rgb").strip().lower()
        if value not in ("rgb", "bgr"):
            raise ValueError(f"{key} 仅支持 rgb 或 bgr，当前值: {value}")
        normalized[key] = value

    layout = str(normalized.get("onnx_input_layout") or "auto").strip().lower()
    if layout not in ("auto", "nchw", "nhwc"):
        raise ValueError(f"onnx_input_layout 仅支持 auto、nchw 或 nhwc，当前值: {layout}")
    normalized["onnx_input_layout"] = layout

    dtype = str(normalized.get("onnx_input_dtype") or "auto").strip().lower()
    if dtype not in ("auto", "float32", "uint8"):
        raise ValueError(f"onnx_input_dtype 仅支持 auto、float32 或 uint8，当前值: {dtype}")
    normalized["onnx_input_dtype"] = dtype
    normalized["onnx_normalize"] = _parse_bool(normalized.get("onnx_normalize"), True)
    return normalized


def select_backend(model_path: str, model_info: Dict[str, Any], config: Dict[str, Any]) -> str:
    requested = (config.get("backend") or "auto").lower()
    if requested in ("ultralytics", "rknn", "onnxruntime"):
        return requested

    framework = (model_info.get("framework") or "").lower()
    ext = os.path.splitext(model_path)[1].lower()
    if ext == ".rknn" or "rknn" in framework:
        return "rknn"
    if ext == ".onnx" or framework == "onnx":
        return "onnxruntime"
    return "ultralytics"


def _resolve_rknn_core_mask(config: Dict[str, Any]):
    runtime_class = _load_rknnlite()
    if runtime_class is None:
        return None

    mapping = {
        "auto": getattr(runtime_class, "NPU_CORE_AUTO", None),
        "core_0": getattr(runtime_class, "NPU_CORE_0", None),
        "core_1": getattr(runtime_class, "NPU_CORE_1", None),
        "core_2": getattr(runtime_class, "NPU_CORE_2", None),
    }
    return mapping.get((config.get("rknn_core_mask") or "auto").lower())


def _letterbox(frame: np.ndarray, target_w: int, target_h: int) -> Tuple[np.ndarray, float, int, int]:
    src_h, src_w = frame.shape[:2]
    scale = min(target_w / src_w, target_h / src_h)
    resized_w = int(round(src_w * scale))
    resized_h = int(round(src_h * scale))
    resized = cv2.resize(frame, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)

    canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    pad_x = (target_w - resized_w) // 2
    pad_y = (target_h - resized_h) // 2
    canvas[pad_y:pad_y + resized_h, pad_x:pad_x + resized_w] = resized
    return canvas, scale, pad_x, pad_y


def _clip_box(x1: float, y1: float, x2: float, y2: float, width: int, height: int):
    x1 = max(0.0, min(x1, float(width - 1)))
    y1 = max(0.0, min(y1, float(height - 1)))
    x2 = max(0.0, min(x2, float(width - 1)))
    y2 = max(0.0, min(y2, float(height - 1)))
    return x1, y1, x2, y2


def _sigmoid(value: np.ndarray) -> np.ndarray:
    clipped = np.clip(value, -60.0, 60.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _safe_json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _parse_int_list(value: Any) -> List[int]:
    parsed = _safe_json_loads(value)
    if parsed is not None:
        value = parsed

    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            try:
                result.append(int(item))
            except Exception:
                continue
        return result

    if isinstance(value, str):
        result = []
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                result.append(int(item))
            except Exception:
                continue
        return result

    return []


def _parse_anchors(value: Any) -> List[List[List[float]]]:
    parsed = _safe_json_loads(value)
    if parsed is not None:
        value = parsed

    if not isinstance(value, (list, tuple)):
        return []

    branches = []
    for branch in value:
        if not isinstance(branch, (list, tuple)) or not branch:
            continue

        normalized_branch = []
        if isinstance(branch[0], (int, float, str)):
            flat_values = []
            for item in branch:
                try:
                    flat_values.append(float(item))
                except Exception:
                    continue
            for idx in range(0, len(flat_values) - 1, 2):
                normalized_branch.append([flat_values[idx], flat_values[idx + 1]])
        else:
            for anchor in branch:
                if not isinstance(anchor, (list, tuple)) or len(anchor) < 2:
                    continue
                try:
                    normalized_branch.append([float(anchor[0]), float(anchor[1])])
                except Exception:
                    continue

        if normalized_branch:
            branches.append(normalized_branch)

    return branches


def _describe_output_shapes(outputs: List[Any]) -> List[List[int]]:
    return [list(np.asarray(output).shape) for output in (outputs or [])]


def _squeeze_output_array(output: Any) -> np.ndarray:
    arr = np.asarray(output)
    while arr.ndim > 2 and arr.shape[0] == 1:
        arr = arr[0]
    return arr


def _looks_like_spatial_head_output(output: Any) -> bool:
    arr = _squeeze_output_array(output)
    if arr.ndim < 3:
        return False
    if arr.ndim >= 4:
        return True
    if arr.shape[1] > 1 and arr.shape[2] > 1:
        return True
    return sum(dim > 4 for dim in arr.shape) >= 2


def _guess_class_count(classes: Dict[int, str], config: Dict[str, Any]) -> int:
    configured = config.get("postprocess_num_classes")
    if configured not in (None, ""):
        try:
            return max(0, int(configured))
        except Exception:
            pass
    if classes:
        return max(classes.keys()) + 1
    return 0


def _decode_box(
    raw_box: np.ndarray,
    input_w: int,
    input_h: int,
    scale: float,
    pad_x: int,
    pad_y: int,
    frame_w: int,
    frame_h: int,
    bbox_format: str = "auto"
) -> List[float]:
    box = np.asarray(raw_box, dtype=np.float32).reshape(-1)[:4]

    if np.max(np.abs(box)) <= 1.5:
        box = box.copy()
        box[0] *= input_w
        box[1] *= input_h
        box[2] *= input_w
        box[3] *= input_h

    use_xyxy = bbox_format == "xyxy"
    if bbox_format == "auto":
        use_xyxy = bool(box[2] > box[0] and box[3] > box[1])

    if use_xyxy:
        x1, y1, x2, y2 = box.tolist()
    else:
        cx, cy, w, h = box.tolist()
        x1 = cx - w / 2.0
        y1 = cy - h / 2.0
        x2 = cx + w / 2.0
        y2 = cy + h / 2.0

    x1 = (x1 - pad_x) / scale
    y1 = (y1 - pad_y) / scale
    x2 = (x2 - pad_x) / scale
    y2 = (y2 - pad_y) / scale
    x1, y1, x2, y2 = _clip_box(x1, y1, x2, y2, frame_w, frame_h)
    return [x1, y1, x2, y2]


def _flatten_output(output: Any) -> np.ndarray:
    arr = np.asarray(output)
    if arr.size == 0:
        return np.empty((0, 0), dtype=np.float32)

    arr = _squeeze_output_array(arr)

    if arr.ndim == 1:
        if arr.size % 6 == 0:
            return arr.reshape(-1, 6)
        return np.empty((0, 0), dtype=np.float32)

    if arr.ndim == 2:
        if arr.shape[0] == 1 and arr.shape[1] >= 5:
            return arr
        # 当一维明显是属性维（如单类检测常见的 5x8400），应转置成 NxA。
        if 1 < arr.shape[0] < 6 and arr.shape[1] >= 6:
            arr_t = arr.T
            if arr_t.shape[1] >= 5:
                return arr_t
            return np.empty((0, 0), dtype=np.float32)
        if arr.shape[1] < 6 and arr.shape[0] >= 6:
            arr_t = arr.T
            if arr_t.shape[1] >= 5:
                return arr_t
        # Prefer the orientation whose last dimension looks like attributes,
        # so common CxN exports such as (84, 8400) become (8400, 84).
        if arr.shape[1] >= 6 and arr.shape[1] <= arr.shape[0]:
            return arr
        if arr.shape[0] >= 6:
            arr_t = arr.T
            if arr_t.shape[1] >= 6:
                return arr_t
        return np.empty((0, 0), dtype=np.float32)

    if _looks_like_spatial_head_output(arr):
        return np.empty((0, 0), dtype=np.float32)

    last_dim = arr.shape[-1]
    if last_dim >= 6:
        return arr.reshape(-1, last_dim)
    return np.empty((0, 0), dtype=np.float32)


def _normalize_score_value(value: float) -> float:
    score = float(value)
    if score < 0.0 or score > 1.0:
        score = float(_sigmoid(np.asarray([score], dtype=np.float32))[0])
    return score


def _normalize_score_array(values: np.ndarray) -> np.ndarray:
    scores = np.asarray(values, dtype=np.float32)
    if scores.size == 0:
        return scores
    if float(np.min(scores)) < 0.0 or float(np.max(scores)) > 1.0:
        scores = _sigmoid(scores)
    return scores


def _collect_row_items_from_dense_outputs(outputs: List[Any]) -> List[Dict[str, Any]]:
    row_items = []
    for output_idx, output in enumerate(outputs or []):
        rows = _flatten_output(output)
        if rows.size == 0:
            continue
        for row_idx, row in enumerate(rows):
            row_items.append({
                "row": np.asarray(row, dtype=np.float32).reshape(-1),
                "output_index": output_idx,
                "row_index": row_idx,
            })
    return row_items


def _extract_confidence_and_class(
    row: np.ndarray,
    class_count: int,
    score_mode: str
) -> Optional[Tuple[float, int]]:
    if row.size < 5:
        return None

    if score_mode == "flat":
        if row.size < 6:
            return None
        return _normalize_score_value(row[4]), int(round(row[5]))

    if score_mode == "class_only":
        class_scores = _normalize_score_array(row[4:])
        if class_scores.size == 0:
            return None
        cls = int(np.argmax(class_scores))
        return float(class_scores[cls]), cls

    if score_mode == "objectness_class":
        objectness = _normalize_score_value(row[4])
        class_scores = _normalize_score_array(row[5:])
        if class_scores.size == 0:
            return objectness, 0
        cls = int(np.argmax(class_scores))
        class_conf = float(class_scores[cls])
        conf = objectness * class_conf
        return conf, cls

    if row.size == 5:
        return _normalize_score_value(row[4]), 0

    if row.size == 6:
        return _normalize_score_value(row[4]), int(round(row[5]))

    if class_count > 0:
        if row.size == 4 + class_count:
            return _extract_confidence_and_class(row, class_count, "class_only")
        if row.size == 5 + class_count:
            return _extract_confidence_and_class(row, class_count, "objectness_class")

    if row.size > 6 and abs(row[5] - round(row[5])) < 1e-3 and row[4] <= 1.0:
        return float(row[4]), int(round(row[5]))

    return _extract_confidence_and_class(row, class_count, "objectness_class")


def _build_detections_from_rows(
    row_items: List[Dict[str, Any]],
    classes: Dict[int, str],
    config: Dict[str, Any],
    frame_shape: Tuple[int, int, int],
    input_width: int,
    input_height: int,
    scale: float,
    pad_x: int,
    pad_y: int,
    class_count: int,
    bbox_format: str = "auto",
    score_mode: str = "auto"
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    confidence_threshold = float(config.get("confidence", 0.6))
    class_filter = set(config.get("class_filter", []) or [])
    frame_h, frame_w = frame_shape[:2]

    candidates = []
    for item in row_items:
        row = np.asarray(item["row"], dtype=np.float32).reshape(-1)
        if row.size < 5:
            continue

        score_result = _extract_confidence_and_class(row, class_count, score_mode)
        if score_result is None:
            continue
        conf, cls = score_result

        if conf < confidence_threshold:
            continue
        if class_filter and cls not in class_filter:
            continue

        decoded_box = _decode_box(
            row[:4],
            input_width,
            input_height,
            scale,
            pad_x,
            pad_y,
            frame_w,
            frame_h,
            bbox_format=bbox_format,
        )
        label = classes.get(cls, f"class_{cls}")
        candidates.append({
            "box": decoded_box,
            "label": label,
            "label_name": config.get("label_name", label),
            "class": cls,
            "confidence": float(conf),
            "_output_index": int(item.get("output_index", 0)),
            "_row_index": int(item.get("row_index", 0)),
            "_branch_index": int(item.get("branch_index", -1)),
        })

    if not candidates:
        return [], []

    nms_boxes = []
    nms_scores = []
    for det in candidates:
        x1, y1, x2, y2 = det["box"]
        nms_boxes.append([x1, y1, max(1.0, x2 - x1), max(1.0, y2 - y1)])
        nms_scores.append(float(det["confidence"]))

    indices = cv2.dnn.NMSBoxes(
        bboxes=nms_boxes,
        scores=nms_scores,
        score_threshold=confidence_threshold,
        nms_threshold=float(config.get("nms_iou", 0.45)),
    )
    if len(indices) == 0:
        return [], []

    if isinstance(indices, np.ndarray):
        indices = indices.flatten().tolist()
    else:
        indices = [int(item[0]) if isinstance(item, (list, tuple, np.ndarray)) else int(item) for item in indices]

    detections = []
    details = []
    for idx in indices:
        det = candidates[int(idx)]
        detections.append({
            "box": det["box"],
            "label": det["label"],
            "label_name": det["label_name"],
            "class": det["class"],
            "confidence": float(det["confidence"]),
        })
        details.append({
            "box": [float(v) for v in det["box"]],
            "confidence": float(det["confidence"]),
            "class": int(det["class"]),
            "class_name": det["label"],
            "output_index": int(det["_output_index"]),
            "row_index": int(det["_row_index"]),
            "branch_index": int(det["_branch_index"]),
        })

    return detections, details


class YoloOutputAdapter:
    """根据模型输出 signature 选择合适的后处理路径。"""

    def __init__(
        self,
        model_info: Dict[str, Any],
        config: Dict[str, Any],
        classes: Dict[int, str],
        input_width: int,
        input_height: int
    ):
        self.model_info = model_info
        self.config = config
        self.classes = classes
        self.input_width = input_width
        self.input_height = input_height
        self.adapter_config = (
            _safe_json_loads(config.get("model_postprocess"))
            or _safe_json_loads(model_info.get("model_postprocess"))
            or _safe_json_loads(model_info.get("postprocess"))
            or {}
        )
        if not isinstance(self.adapter_config, dict):
            self.adapter_config = {}

        self.profile = str(self._get_option("postprocess_profile", "profile", default="auto")).lower()
        self.layout = str(self._get_option("postprocess_layout", "layout", default="auto")).lower()
        self.bbox_format = str(self._get_option("postprocess_bbox_format", "bbox_format", default="auto")).lower()
        self.score_mode = str(self._get_option("postprocess_score_mode", "score_mode", default="auto")).lower()
        self.apply_sigmoid = self._get_option("postprocess_apply_sigmoid", "apply_sigmoid", default="auto")
        self.strides = _parse_int_list(self._get_option("postprocess_strides", "strides"))
        self.anchors = _parse_anchors(self._get_option("postprocess_anchors", "anchors"))
        try:
            self.anchor_count = int(self._get_option("postprocess_anchor_count", "anchor_count", default=0) or 0)
        except Exception:
            self.anchor_count = 0
        try:
            self.reg_max = int(self._get_option("postprocess_reg_max", "reg_max", default=0) or 0)
        except Exception:
            self.reg_max = 0
        self.class_count = _guess_class_count(classes, config)
        self._warnings_emitted = set()

    def update_input_size(self, input_width: int, input_height: int):
        self.input_width = input_width
        self.input_height = input_height

    def parse(
        self,
        outputs: List[Any],
        frame_shape: Tuple[int, int, int],
        input_width: int,
        input_height: int,
        scale: float,
        pad_x: int,
        pad_y: int
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        self.update_input_size(input_width, input_height)

        output_shapes = _describe_output_shapes(outputs)
        resolved_profile, warning = self._resolve_profile(outputs)
        metadata = {
            "postprocess_profile": resolved_profile,
            "postprocess_layout": self.layout,
            "postprocess_output_shapes": output_shapes,
        }
        if warning:
            metadata["postprocess_warning"] = warning

        row_items: List[Dict[str, Any]] = []
        bbox_format = self.bbox_format
        score_mode = self.score_mode

        if resolved_profile == "dense":
            row_items = _collect_row_items_from_dense_outputs(outputs)
        elif resolved_profile == "head_decoded":
            row_items = self._collect_decoded_head_rows(outputs)
        elif resolved_profile == "head_anchor_based":
            row_items = self._collect_anchor_based_rows(outputs)
            bbox_format = "xywh"
            score_mode = "objectness_class"
        elif resolved_profile == "head_dfl":
            row_items = self._collect_dfl_head_rows(outputs)
            bbox_format = "xyxy"
            score_mode = "class_only"
        else:
            return [], [], metadata

        if not row_items and warning is None:
            metadata["postprocess_warning"] = (
                "模型输出未能匹配当前后处理适配配置，请根据 output shape 调整 profile/layout/anchors/strides。"
            )

        detections, details = _build_detections_from_rows(
            row_items=row_items,
            classes=self.classes,
            config=self.config,
            frame_shape=frame_shape,
            input_width=input_width,
            input_height=input_height,
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
            class_count=self.class_count,
            bbox_format=bbox_format,
            score_mode=score_mode,
        )
        return detections, details, metadata

    def _resolve_profile(self, outputs: List[Any]) -> Tuple[str, Optional[str]]:
        if self.profile in ("dense", "head_decoded", "head_anchor_based", "head_dfl"):
            return self.profile, None

        if any(_looks_like_spatial_head_output(output) for output in (outputs or [])):
            if self.anchors:
                return "head_anchor_based", None

            if self._can_resolve_dfl_profile(outputs):
                return "head_dfl", None

            for output_idx, output in enumerate(outputs or []):
                if self._normalize_decoded_head(output, output_idx, allow_multi_anchor=False) is not None:
                    return "head_decoded", None

            warning = (
                "检测到多分支/raw head 输出，但缺少可用的模型级后处理配置。"
                "请为该模型设置 postprocess_profile 或 model_postprocess。"
            )
            self._warn_once(warning)
            return "unsupported", warning

        return "dense", None

    def _collect_decoded_head_rows(self, outputs: List[Any]) -> List[Dict[str, Any]]:
        row_items = []
        for output_idx, output in enumerate(outputs or []):
            rows = self._normalize_decoded_head(output, output_idx)
            if rows is None or rows.size == 0:
                continue
            for row_idx, row in enumerate(rows):
                row_items.append({
                    "row": np.asarray(row, dtype=np.float32).reshape(-1),
                    "output_index": output_idx,
                    "row_index": row_idx,
                    "branch_index": output_idx,
                })
        return row_items

    def _collect_anchor_based_rows(self, outputs: List[Any]) -> List[Dict[str, Any]]:
        row_items = []
        for output_idx, output in enumerate(outputs or []):
            normalized = self._normalize_anchor_head(output, output_idx)
            if normalized is None:
                continue

            head, stride, branch_anchors = normalized
            preds = head.astype(np.float32)
            if self._should_apply_sigmoid(default=True):
                preds = _sigmoid(preds)

            anchor_count, grid_h, grid_w, attr_count = preds.shape
            grid_y, grid_x = np.meshgrid(
                np.arange(grid_h, dtype=np.float32),
                np.arange(grid_w, dtype=np.float32),
                indexing="ij",
            )
            grid_x = grid_x[None, :, :, None]
            grid_y = grid_y[None, :, :, None]
            anchors = np.asarray(branch_anchors, dtype=np.float32).reshape(anchor_count, 1, 1, 2)

            decoded = np.empty_like(preds)
            decoded[..., 0] = (preds[..., 0] * 2.0 - 0.5 + grid_x[..., 0]) * stride
            decoded[..., 1] = (preds[..., 1] * 2.0 - 0.5 + grid_y[..., 0]) * stride
            decoded[..., 2] = (preds[..., 2] * 2.0) ** 2 * anchors[..., 0]
            decoded[..., 3] = (preds[..., 3] * 2.0) ** 2 * anchors[..., 1]
            decoded[..., 4:] = preds[..., 4:]

            rows = decoded.reshape(-1, attr_count)
            for row_idx, row in enumerate(rows):
                row_items.append({
                    "row": np.asarray(row, dtype=np.float32).reshape(-1),
                    "output_index": output_idx,
                    "row_index": row_idx,
                    "branch_index": output_idx,
                })

        if not row_items:
            warning = (
                "anchor-based 后处理未能匹配输出 shape，请检查 model_postprocess 中的 anchors/strides/layout。"
            )
            self._warn_once(warning)
        return row_items

    def _collect_dfl_head_rows(self, outputs: List[Any]) -> List[Dict[str, Any]]:
        row_items = []
        for group in self._group_spatial_outputs(outputs):
            box_item = self._select_dfl_box_item(group)
            cls_item = self._select_dfl_class_item(group, box_item)
            if box_item is None or cls_item is None:
                continue

            box_cf = box_item["channels_first"]
            cls_cf = cls_item["channels_first"]
            obj_item = self._select_dfl_objectness_item(group, box_item, cls_item)
            reg_max = self._resolve_reg_max(box_cf.shape[0])
            if reg_max <= 1 or box_cf.shape[0] != reg_max * 4:
                continue

            stride = self._resolve_spatial_stride(box_cf.shape[1], box_cf.shape[2], box_item["output_index"])
            if stride <= 0:
                continue

            box_logits = box_cf.astype(np.float32, copy=False).reshape(4, reg_max, box_cf.shape[1], box_cf.shape[2])
            box_logits = np.transpose(box_logits, (2, 3, 0, 1))
            box_logits = box_logits - np.max(box_logits, axis=-1, keepdims=True)
            box_probs = np.exp(box_logits)
            box_probs_sum = np.sum(box_probs, axis=-1, keepdims=True)
            box_probs = box_probs / np.clip(box_probs_sum, 1e-9, None)
            bins = np.arange(reg_max, dtype=np.float32)
            distances = np.sum(box_probs * bins, axis=-1) * stride

            cls_scores = cls_cf.astype(np.float32, copy=False)
            if self._should_apply_sigmoid(default=True):
                cls_scores = _sigmoid(cls_scores)
            cls_scores = np.transpose(cls_scores, (1, 2, 0))

            if obj_item is not None:
                objectness = obj_item["channels_first"].astype(np.float32, copy=False)
                if self._should_apply_sigmoid(default=True):
                    objectness = _sigmoid(objectness)
                objectness = np.transpose(objectness, (1, 2, 0))
                cls_scores = cls_scores * objectness

            grid_y, grid_x = np.meshgrid(
                np.arange(box_cf.shape[1], dtype=np.float32),
                np.arange(box_cf.shape[2], dtype=np.float32),
                indexing="ij",
            )
            center_x = (grid_x + 0.5) * stride
            center_y = (grid_y + 0.5) * stride

            boxes = np.empty((box_cf.shape[1], box_cf.shape[2], 4), dtype=np.float32)
            boxes[..., 0] = center_x - distances[..., 0]
            boxes[..., 1] = center_y - distances[..., 1]
            boxes[..., 2] = center_x + distances[..., 2]
            boxes[..., 3] = center_y + distances[..., 3]

            for row_idx, (box, cls_score) in enumerate(zip(boxes.reshape(-1, 4), cls_scores.reshape(-1, cls_scores.shape[-1]))):
                row = np.concatenate([box, cls_score], axis=0)
                row_items.append({
                    "row": row.astype(np.float32, copy=False),
                    "output_index": int(box_item["output_index"]),
                    "row_index": int(row_idx),
                    "branch_index": int(box_item["output_index"]),
                })

        if not row_items:
            warning = (
                "DFL/head-split 后处理未能匹配输出 shape，请检查 model_postprocess 中的 reg_max/strides/layout。"
            )
            self._warn_once(warning)
        return row_items

    def _normalize_decoded_head(
        self,
        output: Any,
        output_idx: int,
        allow_multi_anchor: bool = True
    ) -> Optional[np.ndarray]:
        arr = _squeeze_output_array(output)
        if arr.size == 0:
            return None

        if arr.ndim == 2 and arr.shape[1] >= 6:
            return arr.astype(np.float32, copy=False)

        if arr.ndim == 3:
            if self.layout in ("auto", "channels_last") and arr.shape[-1] >= 6:
                return arr.reshape(-1, arr.shape[-1]).astype(np.float32, copy=False)

            if self.layout in ("auto", "channels_first"):
                attr_count, anchor_count = self._guess_attr_count(arr.shape[0], output_idx)
                if attr_count >= 6 and anchor_count > 0:
                    if not allow_multi_anchor and anchor_count > 1:
                        return None
                    reshaped = arr.reshape(anchor_count, attr_count, arr.shape[1], arr.shape[2])
                    reshaped = np.transpose(reshaped, (0, 2, 3, 1))
                    return reshaped.reshape(-1, attr_count).astype(np.float32, copy=False)

        if arr.ndim == 4 and arr.shape[-1] >= 6:
            if self.layout in ("auto", "anchors_first") and arr.shape[0] in self._candidate_anchor_counts(output_idx):
                return arr.reshape(-1, arr.shape[-1]).astype(np.float32, copy=False)
            if self.layout in ("auto", "anchors_last") and arr.shape[2] in self._candidate_anchor_counts(output_idx):
                return arr.reshape(-1, arr.shape[-1]).astype(np.float32, copy=False)
            if self.layout == "auto":
                return arr.reshape(-1, arr.shape[-1]).astype(np.float32, copy=False)

        return None

    def _normalize_anchor_head(
        self,
        output: Any,
        output_idx: int
    ) -> Optional[Tuple[np.ndarray, float, List[List[float]]]]:
        arr = _squeeze_output_array(output)
        if arr.size == 0:
            return None

        branch_anchors = self.anchors[output_idx] if output_idx < len(self.anchors) else []
        anchor_count = len(branch_anchors) or self.anchor_count
        if anchor_count <= 0:
            return None

        head = None
        if arr.ndim == 3:
            channel_dim, grid_h, grid_w = arr.shape
            if channel_dim % anchor_count != 0:
                return None
            attr_count = channel_dim // anchor_count
            if attr_count < 5:
                return None
            head = arr.reshape(anchor_count, attr_count, grid_h, grid_w)
            head = np.transpose(head, (0, 2, 3, 1))
        elif arr.ndim == 4 and arr.shape[-1] >= 5:
            if arr.shape[0] == anchor_count:
                head = arr
            elif arr.shape[2] == anchor_count:
                head = np.transpose(arr, (2, 0, 1, 3))

        if head is None or not branch_anchors:
            return None

        stride = 0.0
        if output_idx < len(self.strides):
            stride = float(self.strides[output_idx])
        elif len(self.strides) == 1:
            stride = float(self.strides[0])
        else:
            grid_h = head.shape[1]
            grid_w = head.shape[2]
            stride_h = self.input_height / float(grid_h) if grid_h > 0 else 0.0
            stride_w = self.input_width / float(grid_w) if grid_w > 0 else 0.0
            if stride_h > 0 and stride_w > 0 and abs(stride_h - stride_w) <= 1.0:
                stride = (stride_h + stride_w) / 2.0

        if stride <= 0:
            return None

        return head.astype(np.float32, copy=False), stride, branch_anchors

    def _normalize_spatial_output(self, output: Any) -> Optional[np.ndarray]:
        arr = _squeeze_output_array(output)
        if arr.size == 0 or arr.ndim != 3:
            return None

        if self.layout == "channels_first":
            if arr.shape[1] <= 1 or arr.shape[2] <= 1:
                return None
            return arr.astype(np.float32, copy=False)

        if self.layout == "channels_last":
            if arr.shape[0] <= 1 or arr.shape[1] <= 1:
                return None
            return np.transpose(arr, (2, 0, 1)).astype(np.float32, copy=False)

        first_is_channel = arr.shape[0] < arr.shape[1] and arr.shape[0] < arr.shape[2]
        last_is_channel = arr.shape[2] < arr.shape[0] and arr.shape[2] < arr.shape[1]

        if first_is_channel and arr.shape[1] > 1 and arr.shape[2] > 1:
            return arr.astype(np.float32, copy=False)
        if last_is_channel and arr.shape[0] > 1 and arr.shape[1] > 1:
            return np.transpose(arr, (2, 0, 1)).astype(np.float32, copy=False)
        return None

    def _group_spatial_outputs(self, outputs: List[Any]) -> List[List[Dict[str, Any]]]:
        groups: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
        for output_idx, output in enumerate(outputs or []):
            normalized = self._normalize_spatial_output(output)
            if normalized is None:
                continue
            key = (int(normalized.shape[1]), int(normalized.shape[2]))
            groups.setdefault(key, []).append({
                "output_index": output_idx,
                "channels_first": normalized,
                "channel_count": int(normalized.shape[0]),
            })
        return list(groups.values())

    def _resolve_reg_max(self, channel_count: int) -> int:
        if self.reg_max > 1 and channel_count == self.reg_max * 4:
            return self.reg_max
        if channel_count % 4 == 0:
            inferred = channel_count // 4
            if inferred > 1:
                return inferred
        return 0

    def _select_dfl_box_item(self, group: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        candidates = []
        for item in group:
            reg_max = self._resolve_reg_max(item["channel_count"])
            if reg_max > 1:
                candidates.append((reg_max, item))
        if not candidates:
            return None
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        return candidates[0][1]

    def _select_dfl_class_item(
        self,
        group: List[Dict[str, Any]],
        box_item: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        candidates = []
        for item in group:
            if box_item is not None and item["output_index"] == box_item["output_index"]:
                continue
            if item["channel_count"] <= 0:
                continue
            candidates.append(item)

        if not candidates:
            return None

        if self.class_count > 0:
            exact = [item for item in candidates if item["channel_count"] == self.class_count]
            if exact:
                return exact[0]

        non_objectness = [item for item in candidates if item["channel_count"] > 1]
        if non_objectness:
            non_objectness.sort(key=lambda item: item["channel_count"], reverse=True)
            return non_objectness[0]
        return None

    def _select_dfl_objectness_item(
        self,
        group: List[Dict[str, Any]],
        box_item: Optional[Dict[str, Any]],
        cls_item: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        for item in group:
            if box_item is not None and item["output_index"] == box_item["output_index"]:
                continue
            if cls_item is not None and item["output_index"] == cls_item["output_index"]:
                continue
            if item["channel_count"] == 1:
                return item
        return None

    def _resolve_spatial_stride(self, grid_h: int, grid_w: int, output_idx: int) -> float:
        if output_idx < len(self.strides):
            return float(self.strides[output_idx])
        if len(self.strides) == 1:
            return float(self.strides[0])

        stride_h = self.input_height / float(grid_h) if grid_h > 0 else 0.0
        stride_w = self.input_width / float(grid_w) if grid_w > 0 else 0.0
        if stride_h > 0 and stride_w > 0 and abs(stride_h - stride_w) <= 1.0:
            return (stride_h + stride_w) / 2.0
        return 0.0

    def _can_resolve_dfl_profile(self, outputs: List[Any]) -> bool:
        for group in self._group_spatial_outputs(outputs):
            box_item = self._select_dfl_box_item(group)
            cls_item = self._select_dfl_class_item(group, box_item)
            if box_item is None or cls_item is None:
                continue
            reg_max = self._resolve_reg_max(box_item["channel_count"])
            if reg_max <= 1:
                continue
            stride = self._resolve_spatial_stride(
                box_item["channels_first"].shape[1],
                box_item["channels_first"].shape[2],
                box_item["output_index"],
            )
            if stride > 0:
                return True
        return False

    def _guess_attr_count(self, channel_dim: int, output_idx: int) -> Tuple[int, int]:
        expected_counts = []
        if self.class_count > 0:
            expected_counts.extend([4 + self.class_count, 5 + self.class_count])

        candidate_anchor_counts = self._candidate_anchor_counts(output_idx)
        for anchor_count in candidate_anchor_counts:
            if anchor_count <= 0:
                continue
            for attr_count in expected_counts:
                if attr_count >= 6 and channel_dim == anchor_count * attr_count:
                    return attr_count, anchor_count

        if self.class_count <= 0 and channel_dim >= 6:
            return channel_dim, 1

        for anchor_count in candidate_anchor_counts:
            if anchor_count <= 0:
                continue
            if channel_dim % anchor_count != 0:
                continue
            attr_count = channel_dim // anchor_count
            if attr_count >= 6:
                return attr_count, anchor_count

        return 0, 0

    def _candidate_anchor_counts(self, output_idx: int) -> List[int]:
        candidates = []
        if output_idx < len(self.anchors):
            candidates.append(len(self.anchors[output_idx]))
        if self.anchor_count > 0:
            candidates.append(self.anchor_count)
        candidates.extend([1, 3])

        deduped = []
        for item in candidates:
            if item > 0 and item not in deduped:
                deduped.append(item)
        return deduped

    def _should_apply_sigmoid(self, default: bool) -> bool:
        value = self.apply_sigmoid
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("true", "1", "yes", "on"):
                return True
            if normalized in ("false", "0", "no", "off"):
                return False
        return default

    def _get_option(self, *keys: str, default: Any = None) -> Any:
        for key in keys:
            value = self.config.get(key)
            if value not in (None, ""):
                return value

        for key in keys:
            value = self.adapter_config.get(key)
            if value not in (None, ""):
                return value

        for key in keys:
            value = self.model_info.get(key)
            if value not in (None, ""):
                return value

        return default

    def _warn_once(self, message: str):
        if message in self._warnings_emitted:
            return
        self._warnings_emitted.add(message)
        logger.warning(f"[YoloOutputAdapter] {message}")


class BaseYoloBackend:
    def __init__(self, model_path: str, model_info: Dict[str, Any], config: Dict[str, Any]):
        self.model_path = model_path
        self.model_info = model_info
        self.config = config
        self.model = None
        self.classes = parse_classes(model_info)
        metadata_w, metadata_h = parse_input_shape(model_info.get("input_shape"))
        input_w = int(config.get("input_width") or metadata_w or 640)
        input_h = int(config.get("input_height") or metadata_h or 640)
        self.input_width = input_w
        self.input_height = input_h
        self.output_adapter = YoloOutputAdapter(
            model_info=model_info,
            config=config,
            classes=self.classes,
            input_width=self.input_width,
            input_height=self.input_height,
        )

    @property
    def name(self) -> str:
        raise NotImplementedError

    def infer(self, frame: np.ndarray) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        raise NotImplementedError

    def cleanup(self):
        if self.model is not None and hasattr(self.model, "close"):
            self.model.close()
        self.model = None


class UltralyticsBackend(BaseYoloBackend):
    @property
    def name(self) -> str:
        return "ultralytics"

    def __init__(self, model_path: str, model_info: Dict[str, Any], config: Dict[str, Any]):
        super().__init__(model_path, model_info, config)
        runtime_class = _load_ultralytics()
        if runtime_class is None:
            if _SHARED_INFERENCE_CLIENT_MODE:
                # 客户端进程按设计不本地加载 ultralytics;走到这里说明本应委托给
                # model worker 的节点错误地走了本地加载路径(多见于 create_backend
                # 的 shared 分支未命中),请检查 SHARED_INFERENCE_WORKER 配置。
                raise ImportError(
                    "当前进程为共享推理客户端，按设计不本地加载 ultralytics"
                    "（若此处被触发，请检查 SHARED_INFERENCE_WORKER / "
                    "create_backend 的 shared 分支是否生效）"
                )
            # 非客户端模式:已尝试本地 import ultralytics 但失败。透出真实异常,
            # 而不是笼统的"未安装"(曾掩盖 libcudnn.so.9 缺失这类环境问题)。
            raise ImportError(
                "加载 ultralytics 失败：已尝试本地 import 但抛出异常。"
                f"真实异常: {type(ULTRALYTICS_IMPORT_ERROR).__name__}"
                f": {ULTRALYTICS_IMPORT_ERROR}"
            ) from ULTRALYTICS_IMPORT_ERROR
        self.model = runtime_class(model_path)

    @staticmethod
    def _predict_kwargs(config: Dict[str, Any]) -> Dict[str, Any]:
        kwargs = {
            "save": False,
            "conf": float(config.get("confidence", 0.6)),
            "iou": float(config.get("nms_iou", 0.45)),
            "verbose": False,
        }
        class_filter = config.get("class_filter", [])
        if class_filter:
            kwargs["classes"] = class_filter
        return kwargs

    @staticmethod
    def _parse_result(result, config: Dict[str, Any]):
        detections = []
        details = []
        if result is not None:
            for det in result.boxes.data.tolist():
                x1, y1, x2, y2, conf, cls = det
                class_name = result.names[int(cls)]
                detections.append({
                    "box": (x1, y1, x2, y2),
                    "label": class_name,
                    "label_name": config.get("label_name") or class_name,
                    "class": int(cls),
                    "confidence": float(conf),
                })
                details.append({
                    "box": [float(x1), float(y1), float(x2), float(y2)],
                    "confidence": float(conf),
                    "class": int(cls),
                    "class_name": class_name,
                })

        return detections, details, {
            "nms_iou": float(config.get("nms_iou", 0.45)),
        }

    def infer(self, frame: np.ndarray):
        results = self.model.predict(frame, **self._predict_kwargs(self.config))
        result = results[0] if results and len(results) > 0 else None
        return self._parse_result(result, self.config)

    def infer_batch(self, frames: List[np.ndarray], configs: List[Dict[str, Any]]):
        if not frames:
            return []
        if len(frames) != len(configs):
            raise ValueError("frames/configs batch length mismatch")
        results = self.model.predict(frames, **self._predict_kwargs(configs[0]))
        parsed = []
        for index, config in enumerate(configs):
            result = results[index] if results and index < len(results) else None
            parsed.append(self._parse_result(result, config))
        return parsed


class SharedInferenceBackend(BaseYoloBackend):
    """Lightweight proxy to the process-global shared model service."""

    backend_name = "shared"

    @property
    def name(self) -> str:
        return self.backend_name

    def __init__(self, model_path: str, model_info: Dict[str, Any], config: Dict[str, Any]):
        super().__init__(model_path, model_info, config)
        from app.core.shared_inference import SharedInferenceClient

        self.client = SharedInferenceClient(model_path, model_info, config)
        self.model = self.client
        self._overload_count = 0
        self._last_overload_log_at = float("-inf")

    def infer(self, frame: np.ndarray):
        from app.core.shared_inference import SharedInferenceOverloaded

        try:
            response = self.client.infer(frame, self.config)
        except SharedInferenceOverloaded as exc:
            self._overload_count += 1
            now = time.monotonic()
            if now - self._last_overload_log_at >= 10.0:
                logger.warning(
                    f"共享推理队列已满，已丢弃 {self._overload_count} "
                    f"个分析帧: {exc}"
                )
                self._overload_count = 0
                self._last_overload_log_at = now
            return [], [], {
                "shared_inference": True,
                "overloaded": True,
                "nms_iou": float(self.config.get("nms_iou", 0.45)),
            }
        metadata = dict(response.get("metadata") or {})
        metadata["shared_inference"] = True
        metadata["model_key"] = self.client.model_key
        return response.get("detections") or [], response.get("details") or [], metadata

    def cleanup(self):
        if getattr(self, "client", None) is not None:
            self.client.close()
            self.client = None
        self.model = None


class SharedUltralyticsBackend(SharedInferenceBackend):
    backend_name = "ultralytics"


class SharedRKNNBackend(SharedInferenceBackend):
    backend_name = "rknn"


class _RKNNRuntimeEntry:
    def __init__(self, runtime):
        self.runtime = runtime
        self.references = 1
        self.inference_lock = threading.Lock()


_RKNN_RUNTIME_POOL: Dict[Tuple[Any, ...], _RKNNRuntimeEntry] = {}
_RKNN_RUNTIME_POOL_LOCK = threading.RLock()
_RKNN_RUNTIME_POOL_PID = os.getpid()


def _rknn_runtime_key(model_path: str, config: Dict[str, Any]) -> Tuple[Any, ...]:
    absolute_path = os.path.realpath(os.path.abspath(model_path))
    try:
        stat_result = os.stat(absolute_path)
        identity = (int(stat_result.st_size), int(stat_result.st_mtime_ns))
    except OSError:
        identity = (0, 0)
    core_mask = str(config.get("rknn_core_mask") or "auto").strip().lower()
    return absolute_path, identity, core_mask


def _ensure_rknn_pool_process() -> None:
    global _RKNN_RUNTIME_POOL, _RKNN_RUNTIME_POOL_PID
    current_pid = os.getpid()
    if current_pid != _RKNN_RUNTIME_POOL_PID:
        # A runtime created before fork must never be used by the child. Do not
        # call release() on the inherited native handle; just forget the copy.
        _RKNN_RUNTIME_POOL = {}
        _RKNN_RUNTIME_POOL_PID = current_pid


def _acquire_rknn_runtime(model_path: str, config: Dict[str, Any]):
    runtime_class = _load_rknnlite()
    if runtime_class is None:
        message = (
            "当前环境未安装 rknnlite.api，无法加载 .rknn 模型。"
            "请使用 RK3588 镜像，并在构建镜像时安装 rknn-toolkit-lite2 wheel "
            "（可通过 Dockerfile.rk 的 RKNN_TOOLKIT_LITE2_WHL build-arg 传入），"
            "同时在运行时挂载 /opt/rknn。"
        )
        if RKNNLITE_IMPORT_ERROR is not None:
            raise ImportError(f"{message} 原始错误: {RKNNLITE_IMPORT_ERROR}") from RKNNLITE_IMPORT_ERROR
        raise ImportError(message)

    key = _rknn_runtime_key(model_path, config)
    with _RKNN_RUNTIME_POOL_LOCK:
        _ensure_rknn_pool_process()
        entry = _RKNN_RUNTIME_POOL.get(key)
        if entry is not None:
            entry.references += 1
            return key, entry

        runtime = runtime_class()
        try:
            ret = runtime.load_rknn(model_path)
            if ret != 0:
                raise RuntimeError(f"RKNNLite.load_rknn 失败，返回码: {ret}")
            core_mask = _resolve_rknn_core_mask(config)
            ret = (
                runtime.init_runtime(core_mask=core_mask)
                if core_mask is not None
                else runtime.init_runtime()
            )
            if ret != 0:
                raise RuntimeError(
                    f"RKNNLite.init_runtime 失败，返回码: {ret}。"
                    "请确认当前容器可访问 /dev/dri、/proc/device-tree/compatible，"
                    "并已挂载 /usr/lib/librknnrt.so"
                )
        except Exception:
            if hasattr(runtime, "release"):
                runtime.release()
            raise
        entry = _RKNNRuntimeEntry(runtime)
        _RKNN_RUNTIME_POOL[key] = entry
        return key, entry


def _release_rknn_runtime(key, entry) -> None:
    with _RKNN_RUNTIME_POOL_LOCK:
        _ensure_rknn_pool_process()
        current = _RKNN_RUNTIME_POOL.get(key)
        if current is not entry:
            return
        entry.references = max(0, entry.references - 1)
        if entry.references > 0:
            return
        _RKNN_RUNTIME_POOL.pop(key, None)
        with entry.inference_lock:
            if hasattr(entry.runtime, "release"):
                entry.runtime.release()


def _reset_rknn_runtime_pool_for_tests() -> None:
    """Release all local runtimes; intended for isolated unit tests only."""
    with _RKNN_RUNTIME_POOL_LOCK:
        _ensure_rknn_pool_process()
        entries = list(_RKNN_RUNTIME_POOL.values())
        _RKNN_RUNTIME_POOL.clear()
        for entry in entries:
            if hasattr(entry.runtime, "release"):
                entry.runtime.release()


class RKNNBackend(BaseYoloBackend):
    @property
    def name(self) -> str:
        return "rknn"

    def __init__(self, model_path: str, model_info: Dict[str, Any], config: Dict[str, Any]):
        super().__init__(model_path, model_info, config)
        self.rknn_input_format = (config.get("rknn_input_format") or "rgb").lower()
        self._runtime_key = None
        self._runtime_entry = None
        self._runtime_key, self._runtime_entry = _acquire_rknn_runtime(model_path, config)
        self.model = self._runtime_entry.runtime

    def infer(self, frame: np.ndarray):
        image, scale, pad_x, pad_y = _letterbox(frame, self.input_width, self.input_height)
        if self.rknn_input_format == "bgr":
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        image = np.expand_dims(np.ascontiguousarray(image), axis=0)
        entry = self._runtime_entry
        if entry is None:
            raise RuntimeError("RKNN backend 已关闭")
        with entry.inference_lock:
            outputs = self.model.inference(inputs=[image])
        detections, details, adapter_metadata = self.output_adapter.parse(
            outputs=outputs,
            frame_shape=frame.shape,
            input_width=self.input_width,
            input_height=self.input_height,
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
        )
        return detections, details, {
            "input_size": {
                "width": int(self.input_width),
                "height": int(self.input_height),
            },
            "rknn_input_format": self.rknn_input_format,
            "nms_iou": float(self.config.get("nms_iou", 0.45)),
            **adapter_metadata,
        }

    def cleanup(self):
        entry = self._runtime_entry
        key = self._runtime_key
        self._runtime_entry = None
        self._runtime_key = None
        self.model = None
        if entry is not None:
            _release_rknn_runtime(key, entry)


class ONNXRuntimeBackend(BaseYoloBackend):
    @property
    def name(self) -> str:
        return "onnxruntime"

    def __init__(self, model_path: str, model_info: Dict[str, Any], config: Dict[str, Any]):
        super().__init__(model_path, model_info, config)
        runtime = _load_onnxruntime()
        if runtime is None:
            detail = (
                f"。真实异常: {type(ONNXRUNTIME_IMPORT_ERROR).__name__}: "
                f"{ONNXRUNTIME_IMPORT_ERROR}"
                if ONNXRUNTIME_IMPORT_ERROR is not None
                else ""
            )
            raise ImportError(f"当前环境无法加载 onnxruntime{detail}")

        provider_name = config.get("onnx_provider") or config.get("onnx_execution_provider")
        providers = [provider_name] if provider_name else None
        self.session = runtime.InferenceSession(model_path, providers=providers)
        self.model = self.session
        input_definition = self.session.get_inputs()[0]
        self.input_name = input_definition.name
        self.input_shape = input_definition.shape
        self.onnx_input_format = (config.get("onnx_input_format") or "rgb").lower()
        self.onnx_input_layout = self._resolve_input_layout(config.get("onnx_input_layout"))
        self.onnx_input_dtype = self._resolve_input_dtype(
            config.get("onnx_input_dtype"),
            getattr(input_definition, "type", None),
        )
        self.onnx_normalize = _parse_bool(config.get("onnx_normalize"), True)
        if self.onnx_input_dtype == "uint8":
            self.onnx_normalize = False
        self._logged_signature = False

        if isinstance(self.input_shape, (list, tuple)) and len(self.input_shape) >= 4:
            height_index, width_index = (2, 3) if self.onnx_input_layout == "nchw" else (1, 2)
            if isinstance(self.input_shape[height_index], int) and self.input_shape[height_index] > 0:
                self.input_height = int(self.input_shape[height_index])
            if isinstance(self.input_shape[width_index], int) and self.input_shape[width_index] > 0:
                self.input_width = int(self.input_shape[width_index])
        self.output_adapter.update_input_size(self.input_width, self.input_height)

    def _resolve_input_layout(self, requested_layout: Any) -> str:
        requested = str(requested_layout or "auto").strip().lower()
        if requested in ("nchw", "nhwc"):
            return requested
        if isinstance(self.input_shape, (list, tuple)) and len(self.input_shape) >= 4:
            channel_first = self.input_shape[1]
            channel_last = self.input_shape[-1]
            if isinstance(channel_first, int) and channel_first in (1, 3, 4):
                return "nchw"
            if isinstance(channel_last, int) and channel_last in (1, 3, 4):
                return "nhwc"
        return "nchw"

    @staticmethod
    def _resolve_input_dtype(requested_dtype: Any, runtime_type: Any) -> str:
        requested = str(requested_dtype or "auto").strip().lower()
        if requested in ("float32", "uint8"):
            return requested
        normalized_runtime_type = str(runtime_type or "").strip().lower()
        return "uint8" if "uint8" in normalized_runtime_type else "float32"

    def infer(self, frame: np.ndarray):
        image, scale, pad_x, pad_y = _letterbox(frame, self.input_width, self.input_height)
        if self.onnx_input_format == "bgr":
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        tensor_dtype = np.uint8 if self.onnx_input_dtype == "uint8" else np.float32
        tensor = image.astype(tensor_dtype)
        if self.onnx_normalize:
            tensor = tensor / 255.0

        if self.onnx_input_layout == "nchw":
            tensor = np.transpose(tensor, (2, 0, 1))
            tensor = np.expand_dims(np.ascontiguousarray(tensor), axis=0)
        else:
            tensor = np.expand_dims(np.ascontiguousarray(tensor), axis=0)

        outputs = self.session.run(None, {self.input_name: tensor})
        if not self._logged_signature:
            self._logged_signature = True
            logger.info(
                f"[ONNXRuntimeBackend] 首次推理: input_name={self.input_name}, "
                f"input_shape={self.input_shape}, tensor_shape={tensor.shape}, "
                f"providers={self.session.get_providers()}, "
                f"output_shapes={[np.asarray(out).shape for out in outputs]}"
            )
        detections, details, adapter_metadata = self.output_adapter.parse(
            outputs=outputs,
            frame_shape=frame.shape,
            input_width=self.input_width,
            input_height=self.input_height,
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
        )
        return detections, details, {
            "input_size": {
                "width": int(self.input_width),
                "height": int(self.input_height),
            },
            "onnx_input_format": self.onnx_input_format,
            "onnx_input_layout": self.onnx_input_layout,
            "onnx_input_dtype": self.onnx_input_dtype,
            "onnx_normalize": self.onnx_normalize,
            "onnx_provider": self.session.get_providers()[0] if self.session.get_providers() else None,
            "nms_iou": float(self.config.get("nms_iou", 0.45)),
            **adapter_metadata,
        }

    def cleanup(self):
        self.session = None
        self.model = None


def create_backend(model_path: str, model_info: Dict[str, Any], config: Dict[str, Any]) -> BaseYoloBackend:
    if not str(model_path or "").strip():
        raise ValueError("模型路径不能为空")
    normalized_config = normalize_backend_config(config)
    backend_name = select_backend(model_path, model_info, normalized_config)
    if backend_name == "rknn":
        if _SHARED_INFERENCE_CLIENT_MODE and normalized_config.get("shared_inference_enabled", True):
            return SharedRKNNBackend(model_path, model_info, normalized_config)
        return RKNNBackend(model_path, model_info, normalized_config)
    if backend_name == "onnxruntime":
        return ONNXRuntimeBackend(model_path, model_info, normalized_config)
    if _SHARED_INFERENCE_CLIENT_MODE and normalized_config.get("shared_inference_enabled", True):
        return SharedUltralyticsBackend(model_path, model_info, normalized_config)
    return UltralyticsBackend(model_path, model_info, normalized_config)
