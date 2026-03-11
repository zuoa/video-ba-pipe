"""
Reusable YOLO inference backends.

The goal is to keep script templates thin and move backend-specific logic here.
Current backends:
- ultralytics.YOLO
- RKNNLite
"""

import os
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np
from app import logger

try:
    from ultralytics import YOLO
except Exception:
    YOLO = None

try:
    from rknnlite.api import RKNNLite
except Exception:
    RKNNLite = None

try:
    import onnxruntime as ort
except Exception:
    ort = None


def parse_classes(model_info: Dict[str, Any]) -> Dict[int, str]:
    classes = {}
    for key, value in (model_info.get("classes") or {}).items():
        try:
            classes[int(key)] = str(value)
        except Exception:
            continue
    return classes


def parse_input_shape(shape_value: Any) -> Tuple[int, int]:
    if not shape_value:
        return 0, 0

    if isinstance(shape_value, str):
        normalized = shape_value.lower().replace("[", "").replace("]", "").replace(" ", "")
        if "x" in normalized:
            parts = normalized.split("x")
            if len(parts) >= 2 and parts[-2].isdigit() and parts[-1].isdigit():
                return int(parts[-2]), int(parts[-1])
        parts = [p for p in normalized.split(",") if p.isdigit()]
        if len(parts) >= 2:
            return int(parts[-2]), int(parts[-1])

    if isinstance(shape_value, (list, tuple)) and len(shape_value) >= 2:
        try:
            return int(shape_value[-2]), int(shape_value[-1])
        except Exception:
            return 0, 0

    return 0, 0


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
    if RKNNLite is None:
        return None

    mapping = {
        "auto": getattr(RKNNLite, "NPU_CORE_AUTO", None),
        "core_0": getattr(RKNNLite, "NPU_CORE_0", None),
        "core_1": getattr(RKNNLite, "NPU_CORE_1", None),
        "core_2": getattr(RKNNLite, "NPU_CORE_2", None),
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


def _decode_box(
    raw_box: np.ndarray,
    input_w: int,
    input_h: int,
    scale: float,
    pad_x: int,
    pad_y: int,
    frame_w: int,
    frame_h: int
) -> List[float]:
    box = np.asarray(raw_box, dtype=np.float32).reshape(-1)[:4]

    if np.max(np.abs(box)) <= 1.5:
        box = box.copy()
        box[0] *= input_w
        box[1] *= input_h
        box[2] *= input_w
        box[3] *= input_h

    if box[2] > box[0] and box[3] > box[1]:
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

    if arr.ndim >= 3 and arr.shape[0] == 1:
        arr = arr[0]

    if arr.ndim == 1:
        if arr.size % 6 == 0:
            return arr.reshape(-1, 6)
        return np.empty((0, 0), dtype=np.float32)

    if arr.ndim == 2:
        if arr.shape[1] >= 6:
            return arr
        if arr.shape[0] >= 6:
            arr_t = arr.T
            if arr_t.shape[1] >= 6:
                return arr_t
        return np.empty((0, 0), dtype=np.float32)

    last_dim = arr.shape[-1]
    if last_dim >= 6:
        return arr.reshape(-1, last_dim)
    return np.empty((0, 0), dtype=np.float32)


def _parse_dense_outputs(
    outputs: List[Any],
    classes: Dict[int, str],
    config: Dict[str, Any],
    frame_shape: Tuple[int, int, int],
    input_width: int,
    input_height: int,
    scale: float,
    pad_x: int,
    pad_y: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    confidence_threshold = float(config.get("confidence", 0.6))
    class_filter = set(config.get("class_filter", []) or [])
    frame_h, frame_w = frame_shape[:2]

    candidates = []
    for output_idx, output in enumerate(outputs or []):
        rows = _flatten_output(output)
        if rows.size == 0:
            continue

        for row in rows:
            row = np.asarray(row, dtype=np.float32).reshape(-1)
            if row.size < 6:
                continue

            if row.size == 6 or (
                row.size > 6 and abs(row[5] - round(row[5])) < 1e-3 and row[4] <= 1.0
            ):
                box = row[:4]
                conf = float(row[4])
                cls = int(round(row[5]))
            else:
                box = row[:4]
                objectness = float(row[4])
                class_scores = row[5:]
                if class_scores.size == 0:
                    continue
                cls = int(np.argmax(class_scores))
                class_conf = float(class_scores[cls])
                conf = objectness * class_conf if objectness <= 1.0 else class_conf

            if conf < confidence_threshold:
                continue
            if class_filter and cls not in class_filter:
                continue

            decoded_box = _decode_box(
                box, input_width, input_height, scale, pad_x, pad_y, frame_w, frame_h
            )
            label = classes.get(cls, f"class_{cls}")
            candidates.append({
                "box": decoded_box,
                "label": label,
                "label_name": config.get("label_name", label),
                "class": cls,
                "confidence": conf,
                "_output_index": output_idx,
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
        })

    return detections, details


class BaseYoloBackend:
    def __init__(self, model_path: str, model_info: Dict[str, Any], config: Dict[str, Any]):
        self.model_path = model_path
        self.model_info = model_info
        self.config = config
        self.model = None
        self.classes = parse_classes(model_info)
        input_w, input_h = parse_input_shape(model_info.get("input_shape"))
        if input_w <= 0 or input_h <= 0:
            input_w = int(config.get("input_width", 640))
            input_h = int(config.get("input_height", 640))
        self.input_width = input_w
        self.input_height = input_h

    @property
    def name(self) -> str:
        raise NotImplementedError

    def infer(self, frame: np.ndarray) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        raise NotImplementedError

    def cleanup(self):
        if self.model is not None and hasattr(self.model, "close"):
            self.model.close()


class UltralyticsBackend(BaseYoloBackend):
    @property
    def name(self) -> str:
        return "ultralytics"

    def __init__(self, model_path: str, model_info: Dict[str, Any], config: Dict[str, Any]):
        super().__init__(model_path, model_info, config)
        if YOLO is None:
            raise ImportError("当前环境未安装 ultralytics，无法加载 YOLO 模型")
        self.model = YOLO(model_path)

    def infer(self, frame: np.ndarray):
        kwargs = {
            "save": False,
            "conf": float(self.config.get("confidence", 0.6)),
            "iou": float(self.config.get("nms_iou", 0.45)),
            "verbose": False,
        }
        class_filter = self.config.get("class_filter", [])
        if class_filter:
            kwargs["classes"] = class_filter

        detections = []
        details = []
        results = self.model.predict(frame, **kwargs)
        if results and len(results) > 0:
            for det in results[0].boxes.data.tolist():
                x1, y1, x2, y2, conf, cls = det
                class_name = results[0].names[int(cls)]
                detections.append({
                    "box": (x1, y1, x2, y2),
                    "label": class_name,
                    "label_name": self.config.get("label_name", class_name),
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
            "nms_iou": float(self.config.get("nms_iou", 0.45)),
        }


class RKNNBackend(BaseYoloBackend):
    @property
    def name(self) -> str:
        return "rknn"

    def __init__(self, model_path: str, model_info: Dict[str, Any], config: Dict[str, Any]):
        super().__init__(model_path, model_info, config)
        if RKNNLite is None:
            raise ImportError("当前环境未安装 rknnlite.api，无法加载 .rknn 模型")

        self.rknn_input_format = (config.get("rknn_input_format") or "rgb").lower()
        self.model = RKNNLite()

        ret = self.model.load_rknn(model_path)
        if ret != 0:
            raise RuntimeError(f"RKNNLite.load_rknn 失败，返回码: {ret}")

        core_mask = _resolve_rknn_core_mask(config)
        if core_mask is not None:
            ret = self.model.init_runtime(core_mask=core_mask)
        else:
            ret = self.model.init_runtime()
        if ret != 0:
            raise RuntimeError(f"RKNNLite.init_runtime 失败，返回码: {ret}")

    def infer(self, frame: np.ndarray):
        image, scale, pad_x, pad_y = _letterbox(frame, self.input_width, self.input_height)
        if self.rknn_input_format == "bgr":
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        image = np.expand_dims(np.ascontiguousarray(image), axis=0)
        outputs = self.model.inference(inputs=[image])
        detections, details = _parse_dense_outputs(
            outputs=outputs,
            classes=self.classes,
            config=self.config,
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
        }

    def cleanup(self):
        if self.model is not None and hasattr(self.model, "release"):
            self.model.release()


class ONNXRuntimeBackend(BaseYoloBackend):
    @property
    def name(self) -> str:
        return "onnxruntime"

    def __init__(self, model_path: str, model_info: Dict[str, Any], config: Dict[str, Any]):
        super().__init__(model_path, model_info, config)
        if ort is None:
            raise ImportError("当前环境未安装 onnxruntime，无法加载 .onnx 模型")

        provider_name = config.get("onnx_provider") or config.get("onnx_execution_provider")
        providers = [provider_name] if provider_name else None
        self.session = ort.InferenceSession(model_path, providers=providers)
        self.model = self.session
        self.input_name = self.session.get_inputs()[0].name
        self.input_shape = self.session.get_inputs()[0].shape
        self.onnx_input_format = (config.get("onnx_input_format") or "rgb").lower()
        self.onnx_normalize = bool(config.get("onnx_normalize", True))
        self._logged_signature = False

        if isinstance(self.input_shape, list) and len(self.input_shape) >= 4:
            if isinstance(self.input_shape[-2], int) and self.input_shape[-2] > 0:
                self.input_height = int(self.input_shape[-2])
            if isinstance(self.input_shape[-1], int) and self.input_shape[-1] > 0:
                self.input_width = int(self.input_shape[-1])

    def infer(self, frame: np.ndarray):
        image, scale, pad_x, pad_y = _letterbox(frame, self.input_width, self.input_height)
        if self.onnx_input_format == "bgr":
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        tensor = image.astype(np.float32)
        if self.onnx_normalize:
            tensor = tensor / 255.0

        # Infer input layout from first input shape. Typical YOLO ONNX uses NCHW.
        if len(self.input_shape) >= 4 and self.input_shape[1] in (1, 3):
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
        detections, details = _parse_dense_outputs(
            outputs=outputs,
            classes=self.classes,
            config=self.config,
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
            "onnx_normalize": self.onnx_normalize,
            "onnx_provider": self.session.get_providers()[0] if self.session.get_providers() else None,
            "nms_iou": float(self.config.get("nms_iou", 0.45)),
        }


def create_backend(model_path: str, model_info: Dict[str, Any], config: Dict[str, Any]) -> BaseYoloBackend:
    backend_name = select_backend(model_path, model_info, config)
    if backend_name == "rknn":
        return RKNNBackend(model_path, model_info, config)
    if backend_name == "onnxruntime":
        return ONNXRuntimeBackend(model_path, model_info, config)
    return UltralyticsBackend(model_path, model_info, config)
