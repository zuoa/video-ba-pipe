"""
Safety wrappers and logging helpers.
"""

from typing import Any, Callable, Dict

from app import logger

from app.user_scripts.common.result import build_result


def safe_process(process_fn: Callable[..., Dict[str, Any]], *args, **kwargs) -> Dict[str, Any]:
    try:
        return process_fn(*args, **kwargs)
    except Exception as exc:
        logger.error(f"[user_script] process error: {exc}")
        return build_result([], metadata={'error': str(exc)})
