"""OCR runtime capability detection shared by APIs and workers."""

from __future__ import annotations

import importlib.util
from typing import Optional, Tuple


def get_ocr_runtime_status() -> Tuple[bool, Optional[str]]:
    try:
        paddle_spec = importlib.util.find_spec("paddle")
        paddleocr_spec = importlib.util.find_spec("paddleocr")
    except (ImportError, ModuleNotFoundError, ValueError) as exc:
        return False, str(exc)

    missing = []
    if paddle_spec is None:
        missing.append("paddlepaddle")
    if paddleocr_spec is None:
        missing.append("paddleocr")
    if missing:
        return False, f"缺少 OCR 运行时依赖: {', '.join(missing)}"
    return True, None


def is_ocr_runtime_available() -> bool:
    available, _ = get_ocr_runtime_status()
    return available
