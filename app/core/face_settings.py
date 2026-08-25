"""Database-backed system settings for portable face recognition."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from app import logger
from app.core.database_models import SystemSetting


FACE_RECOGNITION_SETTING_KEY = "face_recognition_config"
_BACKEND_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_last_known_config: Optional["FaceRecognitionConfig"] = None
_last_known_lock = threading.RLock()


@dataclass(frozen=True)
class FaceRecognitionConfig:
    known_retention_days: int = 90
    unknown_retention_days: int = 30
    inference_backend: str = "auto"
    require_commercial_models: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _bounded_days(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(3650, parsed))


def _boolean(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return default


def normalize_face_recognition_config(
    data: Optional[Dict[str, Any]],
) -> FaceRecognitionConfig:
    data = data if isinstance(data, dict) else {}
    backend = str(data.get("inference_backend") or "auto").strip().lower()
    if backend != "auto" and not _BACKEND_PATTERN.fullmatch(backend):
        backend = "auto"
    return FaceRecognitionConfig(
        known_retention_days=_bounded_days(data.get("known_retention_days"), 90),
        unknown_retention_days=_bounded_days(data.get("unknown_retention_days"), 30),
        inference_backend=backend,
        require_commercial_models=_boolean(
            data.get("require_commercial_models"), False
        ),
    )


def load_face_recognition_config(
    *, log_failure: bool = True,
) -> Tuple[FaceRecognitionConfig, str, bool]:
    """Return config, source (database/default/cache), and DB availability.

    This deliberately performs a fresh read. API, workers, and source hosts can
    therefore share the setting without process-local environment drift.
    """
    global _last_known_config
    fallback = FaceRecognitionConfig()
    try:
        record = SystemSetting.get_or_none(
            SystemSetting.key == FACE_RECOGNITION_SETTING_KEY
        )
        if not record or not record.value:
            with _last_known_lock:
                _last_known_config = fallback
            return fallback, "default", True
        config = normalize_face_recognition_config(json.loads(record.value))
        with _last_known_lock:
            _last_known_config = config
        return config, "database", True
    except Exception as exc:
        if log_failure:
            logger.warning(f"读取人脸识别系统配置失败: {exc}")
        with _last_known_lock:
            if _last_known_config is not None:
                return _last_known_config, "cache", False
        return fallback, "default", False


def get_face_recognition_config() -> FaceRecognitionConfig:
    config, _source, _database_available = load_face_recognition_config()
    return config


def available_face_inference_backends() -> list[str]:
    try:
        from app.core.face_inference import available_face_runtimes

        runtimes, _errors = available_face_runtimes()
        return ["auto", *[item for item in runtimes if item != "auto"]]
    except Exception:
        return ["auto"]


def save_face_recognition_config(
    data: Optional[Dict[str, Any]],
    updated_by: str = "system",
) -> FaceRecognitionConfig:
    global _last_known_config
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")

    required = {
        "known_retention_days",
        "unknown_retention_days",
        "inference_backend",
        "require_commercial_models",
    }
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"缺少配置项: {', '.join(missing)}")

    for field, label in (
        ("known_retention_days", "已识别事件保留天数"),
        ("unknown_retention_days", "陌生人事件保留天数"),
    ):
        value = data.get(field)
        if isinstance(value, bool):
            raise ValueError(f"{label}必须是 0 到 3650 的整数")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}必须是 0 到 3650 的整数") from exc
        if parsed != value or not 0 <= parsed <= 3650:
            raise ValueError(f"{label}必须是 0 到 3650 的整数")

    backend = str(data.get("inference_backend") or "").strip().lower()
    if backend not in set(available_face_inference_backends()):
        raise ValueError("推理后端在当前主机不可用")
    if not isinstance(data.get("require_commercial_models"), bool):
        raise ValueError("商用模型门禁必须是布尔值")

    config = FaceRecognitionConfig(
        known_retention_days=int(data["known_retention_days"]),
        unknown_retention_days=int(data["unknown_retention_days"]),
        inference_backend=backend,
        require_commercial_models=data["require_commercial_models"],
    )
    record, _ = SystemSetting.get_or_create(
        key=FACE_RECOGNITION_SETTING_KEY,
        defaults={
            "value": "",
            "description": "人脸识别运行与数据保留策略",
            "updated_at": datetime.now(),
            "updated_by": updated_by,
        },
    )
    record.value = json.dumps(config.to_dict(), ensure_ascii=False)
    record.description = "人脸识别运行与数据保留策略"
    record.updated_at = datetime.now()
    record.updated_by = updated_by
    record.save()
    with _last_known_lock:
        _last_known_config = config
    return config
