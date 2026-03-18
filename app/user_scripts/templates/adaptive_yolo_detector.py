"""
Adaptive YOLO detector.

Thin template around shared YOLO backends. Backend selection is based on model
metadata / suffix, with optional manual override.
"""

import time
from typing import Dict, Any

import numpy as np

from app.core.model_resolver import get_model_resolver
from app.user_scripts.common.result import build_result
from app.user_scripts.common.roi import apply_roi
from app.user_scripts.common.yolo_backends import create_backend


SCRIPT_METADATA = {
    "name": "自适应YOLO检测",
    "version": "v1.3",
    "description": "根据模型类型自动在 ultralytics / ONNX Runtime / RKNNLite 之间切换，并支持模型级后处理适配",
    "author": "system",
    "category": "detection",
    "tags": ["yolo", "adaptive", "rknn", "ultralytics", "single-model"],
    "config_schema": {
        "model_id": {
            "type": "model_select",
            "label": "检测模型",
            "required": True,
            "description": "选择 YOLO / RKNN 模型",
            "filters": {
                "model_type": ["YOLO", "ONNX", "RKNN"],
                "framework": ["ultralytics", "rknn", "rknnlite", "onnx"]
            }
        },
        "backend": {
            "type": "select",
            "label": "推理后端",
            "default": "auto",
            "options": [
                {"value": "auto", "label": "自动"},
                {"value": "ultralytics", "label": "Ultralytics"},
                {"value": "onnxruntime", "label": "ONNX Runtime"},
                {"value": "rknn", "label": "RKNNLite"}
            ],
            "description": "auto 会根据模型后缀和 framework 自动选择"
        },
        "class_filter": {
            "type": "int_list",
            "label": "类别过滤",
            "default": [],
            "description": "留空表示不过滤类别"
        },
        "confidence": {
            "type": "float",
            "label": "置信度阈值",
            "default": 0.6,
            "min": 0.0,
            "max": 1.0,
            "step": 0.05
        },
        "nms_iou": {
            "type": "float",
            "label": "NMS IOU",
            "default": 0.45,
            "min": 0.0,
            "max": 1.0,
            "step": 0.05,
            "description": "仅 RKNN 通用解析路径使用"
        },
        "roi_mode": {
            "type": "select",
            "label": "ROI应用模式",
            "default": "post_filter",
            "options": [
                {"value": "post_filter", "label": "后过滤"},
                {"value": "pre_mask", "label": "前掩码"}
            ]
        },
        "input_width": {
            "type": "int",
            "label": "输入宽度",
            "default": 640,
            "description": "RKNN 模型输入宽度，auto 时会优先读取模型元数据"
        },
        "input_height": {
            "type": "int",
            "label": "输入高度",
            "default": 640,
            "description": "RKNN 模型输入高度，auto 时会优先读取模型元数据"
        },
        "rknn_input_format": {
            "type": "select",
            "label": "RKNN输入颜色",
            "default": "rgb",
            "options": [
                {"value": "rgb", "label": "RGB"},
                {"value": "bgr", "label": "BGR"}
            ],
            "description": "默认使用流水线原始 RGB 输入"
        },
        "rknn_core_mask": {
            "type": "select",
            "label": "NPU核心",
            "default": "auto",
            "options": [
                {"value": "auto", "label": "AUTO"},
                {"value": "core_0", "label": "CORE_0"},
                {"value": "core_1", "label": "CORE_1"},
                {"value": "core_2", "label": "CORE_2"}
            ],
            "description": "RKNNLite init_runtime 使用的 core_mask"
        },
        "onnx_input_format": {
            "type": "select",
            "label": "ONNX输入颜色",
            "default": "rgb",
            "options": [
                {"value": "rgb", "label": "RGB"},
                {"value": "bgr", "label": "BGR"}
            ],
            "description": "ONNX 输入图像颜色顺序"
        },
        "onnx_normalize": {
            "type": "boolean",
            "label": "ONNX归一化",
            "default": True,
            "description": "多数 YOLO ONNX 模型需要除以 255"
        },
        "onnx_provider": {
            "type": "string",
            "label": "ONNX Provider",
            "default": "",
            "description": "可选，例如 CPUExecutionProvider"
        },
        "postprocess_profile": {
            "type": "select",
            "label": "后处理适配",
            "default": "auto",
            "options": [
                {"value": "auto", "label": "自动"},
                {"value": "dense", "label": "Dense输出"},
                {"value": "head_decoded", "label": "多分支已解码Head"},
                {"value": "head_anchor_based", "label": "Anchor-based Head"},
                {"value": "head_dfl", "label": "DFL Split Head"}
            ],
            "description": "auto 会按输出 shape 选择；YOLOv8/RKNN 常见 split head 可选 head_dfl"
        },
        "model_postprocess": {
            "type": "string",
            "label": "模型后处理JSON",
            "default": "",
            "placeholder": "{\"layout\":\"channels_first\",\"strides\":[8,16,32],\"reg_max\":16}",
            "description": "按模型输出 shape 调整 layout/anchors/strides/reg_max/score_mode/bbox_format/apply_sigmoid"
        }
    },
    "performance": {
        "timeout": 15,
        "memory_limit_mb": 256,
        "gpu_required": False,
        "estimated_time_ms": 35
    },
    "dependencies": [
        "opencv-python>=4.5.0",
        "numpy>=1.19.0",
        "ultralytics>=8.0.0",
        "onnxruntime (optional on ONNX deployments)",
        "rknn-toolkit-lite2 (optional on RKNN deployments)"
    ]
}


def _resolve_roi_mode(config: dict, roi_regions: list) -> str:
    roi_mode = config.get("roi_mode")
    if not roi_mode and roi_regions:
        first_region_mode = roi_regions[0].get("mode")
        if first_region_mode:
            roi_mode = "pre_mask" if first_region_mode in ["pre_mask", "preMask"] else "post_filter"
    return roi_mode or "post_filter"


def init(config: dict) -> Dict[str, Any]:
    from app import logger

    model_id = config.get("model_id")
    if not model_id:
        raise ValueError("缺少 model_id 配置，请在向导中选择一个模型")

    resolver = get_model_resolver()
    model_info = resolver._get_model_info(model_id)
    if not model_info:
        raise ValueError(f"无法解析模型信息: model_id={model_id}")

    model_path = model_info["path"]
    backend = create_backend(model_path, model_info, config)
    logger.info(
        f"[自适应YOLO检测] 初始化: model_id={model_id}, backend={backend.name}, "
        f"framework={model_info.get('framework')}, model_type={model_info.get('model_type')}, "
        f"model_path={model_path}, postprocess_profile={config.get('postprocess_profile', 'auto')}"
    )

    return {
        "backend": backend,
        "model_info": model_info,
        "model_path": model_path,
    }


def process(frame: np.ndarray, config: dict, roi_regions: list = None, state: dict = None) -> dict:
    from app import logger

    start_time = time.time()
    if not state or "backend" not in state:
        return build_result([], metadata={"error": "Model not initialized"})

    backend = state["backend"]
    roi_mode = _resolve_roi_mode(config, roi_regions)
    roi_regions_effective = roi_regions
    if roi_regions and roi_mode:
        roi_regions_effective = [{**r, "mode": roi_mode} for r in roi_regions]

    frame_to_detect, _ = apply_roi(frame, [], roi_regions_effective)
    detections, detections_detail, backend_metadata = backend.infer(frame_to_detect)

    detections_before_roi = len(detections)
    roi_filtered_count = 0
    if roi_regions_effective and detections:
        _, detections = apply_roi(frame, detections, roi_regions_effective)
        roi_filtered_count = detections_before_roi - len(detections)

    processing_time = (time.time() - start_time) * 1000.0
    metadata = {
        "backend": backend.name,
        "model_path": state.get("model_path"),
        "model_type": state.get("model_info", {}).get("model_type"),
        "framework": state.get("model_info", {}).get("framework"),
        "total_detections": len(detections),
        "inference_time_ms": processing_time,
        "confidence_threshold": float(config.get("confidence", 0.6)),
        "class_filter": config.get("class_filter") or "all",
        "detections_detail": detections_detail,
        "image_size": {
            "height": frame.shape[0],
            "width": frame.shape[1],
        },
        **(backend_metadata or {}),
    }

    if roi_regions:
        metadata["roi_enabled"] = True
        metadata["roi_mode"] = roi_mode
        metadata["roi_regions_count"] = len(roi_regions)
        metadata["detections_before_roi"] = detections_before_roi
        metadata["roi_filtered_count"] = roi_filtered_count

    logger.info(
        f"[自适应YOLO检测] 完成: backend={backend.name}, detections={len(detections)}, "
        f"time={processing_time:.2f}ms, model={state.get('model_path')}"
    )
    return build_result(detections, metadata=metadata)


def cleanup(state: dict) -> None:
    backend = (state or {}).get("backend")
    if backend is not None and hasattr(backend, "cleanup"):
        backend.cleanup()
