"""OCR runtime capability detection shared by APIs and workers."""

from __future__ import annotations

import importlib
import importlib.util
import os
from typing import List, Optional, Sequence, Tuple


OCR_BACKEND_PADDLE = "paddleocr"
OCR_BACKEND_RKNN = "rknn_ocr"
OCR_BACKEND_LABELS = {
    OCR_BACKEND_PADDLE: "PaddleOCR",
    OCR_BACKEND_RKNN: "RKNN PPOCR",
}


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def list_ocr_backends() -> List[str]:
    backends: List[str] = []
    if _has_module("paddle") and _has_module("paddleocr"):
        backends.append(OCR_BACKEND_PADDLE)
    if _has_module("rknnlite.api"):
        try:
            importlib.import_module("rknnlite.api")
        except (ImportError, ModuleNotFoundError, OSError, ValueError):
            pass
        else:
            backends.append(OCR_BACKEND_RKNN)
    return backends


def ocr_backend_family(path: Optional[str] = None, framework: Optional[str] = None) -> str:
    framework_name = str(framework or "").strip().lower()
    if "rknn" in framework_name:
        return OCR_BACKEND_RKNN

    normalized = os.path.abspath(str(path or "").strip()) if path else ""
    if normalized.lower().endswith(".rknn"):
        return OCR_BACKEND_RKNN
    if normalized and os.path.isdir(normalized):
        for root, _directories, filenames in os.walk(normalized):
            for name in filenames:
                if str(name).lower().endswith(".rknn"):
                    return OCR_BACKEND_RKNN
    return OCR_BACKEND_PADDLE


def get_ocr_runtime_status(
    required_backend: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    try:
        backends = list_ocr_backends()
    except (ImportError, ModuleNotFoundError, ValueError) as exc:
        return False, str(exc)

    if required_backend:
        if required_backend in backends:
            return True, None
        label = OCR_BACKEND_LABELS.get(required_backend, required_backend)
        return False, f"当前环境不支持 {label}"

    if backends:
        return True, None

    return False, "缺少 OCR 运行时依赖: paddlepaddle/paddleocr 或 rknnlite"


def is_ocr_runtime_available(required_backend: Optional[str] = None) -> bool:
    available, _ = get_ocr_runtime_status(required_backend=required_backend)
    return available


def ocr_runtime_payload() -> dict:
    backends = list_ocr_backends()
    available, error = get_ocr_runtime_status()
    return {
        "available": available,
        "error": error,
        "backends": backends,
    }


def require_ocr_backend(family: str, available_backends: Optional[Sequence[str]] = None) -> None:
    backends = list(available_backends) if available_backends is not None else list_ocr_backends()
    if family in backends:
        return
    if family == OCR_BACKEND_RKNN:
        raise ValueError("当前环境未安装 RKNNLite，无法使用 RKNN OCR 模型")
    raise ValueError("当前环境未安装 PaddleOCR，无法使用 Paddle OCR 模型")
