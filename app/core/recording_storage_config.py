"""数据库持久化的录像与本地媒体容量保护配置。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from app import logger
from app.config import (
    ALERT_IMAGE_MAX_STORAGE_GB,
    ALERT_IMAGE_MIN_FREE_GB,
    ALERT_VIDEO_MAX_STORAGE_GB,
    POST_ALERT_DURATION,
    PRE_ALERT_DURATION,
    RECORDING_ENABLED,
    RECORDING_FPS,
)
from app.core.database_models import SystemSetting


RECORDING_STORAGE_SETTING_KEY = "recording_storage_config"
MIN_STORAGE_LIMIT_GB = 1.0
MAX_STORAGE_LIMIT_GB = 4096.0
MAX_RECORDING_WINDOW_SECONDS = 300
MAX_RECORDING_FPS = 30
MIN_DISK_PRESSURE_PERCENT = 1.0
MAX_DISK_PRESSURE_PERCENT = 99.0


@dataclass(frozen=True)
class RecordingStorageConfig:
    recording_enabled: bool = RECORDING_ENABLED
    pre_alert_seconds: int = PRE_ALERT_DURATION
    post_alert_seconds: int = POST_ALERT_DURATION
    recording_fps: int = RECORDING_FPS
    video_max_gb: float = ALERT_VIDEO_MAX_STORAGE_GB
    image_max_gb: float = ALERT_IMAGE_MAX_STORAGE_GB
    min_free_gb: float = ALERT_IMAGE_MIN_FREE_GB
    stop_recording_percent: float = 80.0
    metadata_only_percent: float = 90.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def _safe_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    if value is None:
        return default
    return bool(value)


def normalize_recording_storage_config(
    data: Optional[Dict[str, Any]],
) -> RecordingStorageConfig:
    defaults = RecordingStorageConfig()
    data = data if isinstance(data, dict) else {}
    return RecordingStorageConfig(
        recording_enabled=_safe_bool(
            data.get("recording_enabled"),
            defaults.recording_enabled,
        ),
        pre_alert_seconds=_safe_int(
            data.get("pre_alert_seconds"),
            defaults.pre_alert_seconds,
            0,
            MAX_RECORDING_WINDOW_SECONDS,
        ),
        post_alert_seconds=_safe_int(
            data.get("post_alert_seconds"),
            defaults.post_alert_seconds,
            0,
            MAX_RECORDING_WINDOW_SECONDS,
        ),
        recording_fps=_safe_int(
            data.get("recording_fps"),
            defaults.recording_fps,
            1,
            MAX_RECORDING_FPS,
        ),
        video_max_gb=_safe_float(
            data.get("video_max_gb"),
            defaults.video_max_gb,
            MIN_STORAGE_LIMIT_GB,
            MAX_STORAGE_LIMIT_GB,
        ),
        image_max_gb=_safe_float(
            data.get("image_max_gb"),
            defaults.image_max_gb,
            MIN_STORAGE_LIMIT_GB,
            MAX_STORAGE_LIMIT_GB,
        ),
        min_free_gb=_safe_float(
            data.get("min_free_gb"),
            defaults.min_free_gb,
            MIN_STORAGE_LIMIT_GB,
            MAX_STORAGE_LIMIT_GB,
        ),
        stop_recording_percent=_safe_float(
            data.get("stop_recording_percent"),
            defaults.stop_recording_percent,
            MIN_DISK_PRESSURE_PERCENT,
            MAX_DISK_PRESSURE_PERCENT,
        ),
        metadata_only_percent=_safe_float(
            data.get("metadata_only_percent"),
            defaults.metadata_only_percent,
            MIN_DISK_PRESSURE_PERCENT,
            MAX_DISK_PRESSURE_PERCENT,
        ),
    )


def load_recording_storage_config_with_status(
    *,
    log_failure: bool = True,
) -> Tuple[RecordingStorageConfig, bool]:
    """读取持久化配置，并显式返回数据库是否可用。"""
    config = RecordingStorageConfig()
    try:
        record = SystemSetting.get_or_none(
            SystemSetting.key == RECORDING_STORAGE_SETTING_KEY
        )
        if record and record.value:
            config = normalize_recording_storage_config(json.loads(record.value))
    except Exception as exc:
        if log_failure:
            logger.warning(f"读取录像与存储配置失败，使用安全默认值: {exc}")
        return config, False
    return config, True


def get_recording_storage_config() -> RecordingStorageConfig:
    config, _database_available = load_recording_storage_config_with_status()
    return config


def _validate_number(
    data: Dict[str, Any],
    key: str,
    label: str,
    minimum: float,
    maximum: float,
    integer: bool = False,
) -> None:
    try:
        value = int(data.get(key)) if integer else float(data.get(key))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是数字") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{label}必须在 {minimum:g} 到 {maximum:g} 之间")


def save_recording_storage_config(
    data: Optional[Dict[str, Any]],
    updated_by: str = "system",
) -> RecordingStorageConfig:
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")

    defaults = RecordingStorageConfig()
    recording_enabled = _safe_bool(data.get("recording_enabled"), defaults.recording_enabled)

    # 录像相关的数值字段仅在录像启用时强制校验：录像关闭时前端同样不把它们设为必填，
    # 此时缺省/空值交给 normalize 回退到安全默认值，避免误报 "告警前录像时长必须是数字"。
    if recording_enabled:
        _validate_number(
            data, "pre_alert_seconds", "告警前录像时长", 0, MAX_RECORDING_WINDOW_SECONDS, integer=True
        )
        _validate_number(
            data, "post_alert_seconds", "告警后录像时长", 0, MAX_RECORDING_WINDOW_SECONDS, integer=True
        )
        _validate_number(data, "recording_fps", "录像帧率", 1, MAX_RECORDING_FPS, integer=True)

    # 存储容量字段始终必填（与录像开关无关，用于本地媒体磁盘水位保护）
    _validate_number(
        data, "video_max_gb", "录像容量上限", MIN_STORAGE_LIMIT_GB, MAX_STORAGE_LIMIT_GB
    )
    _validate_number(
        data, "image_max_gb", "图片容量上限", MIN_STORAGE_LIMIT_GB, MAX_STORAGE_LIMIT_GB
    )
    _validate_number(
        data, "min_free_gb", "最低剩余空间", MIN_STORAGE_LIMIT_GB, MAX_STORAGE_LIMIT_GB
    )
    _validate_number(
        data,
        "stop_recording_percent",
        "自动停录像水位",
        MIN_DISK_PRESSURE_PERCENT,
        MAX_DISK_PRESSURE_PERCENT,
    )
    _validate_number(
        data,
        "metadata_only_percent",
        "仅保留元数据水位",
        MIN_DISK_PRESSURE_PERCENT,
        MAX_DISK_PRESSURE_PERCENT,
    )

    stop_recording_percent = float(data["stop_recording_percent"])
    metadata_only_percent = float(data["metadata_only_percent"])
    if metadata_only_percent <= stop_recording_percent:
        raise ValueError("仅保留元数据水位必须高于自动停录像水位")

    config = normalize_recording_storage_config(data)
    record, _ = SystemSetting.get_or_create(
        key=RECORDING_STORAGE_SETTING_KEY,
        defaults={
            "value": "",
            "description": "录像与本地媒体容量保护配置",
            "updated_at": datetime.now(),
            "updated_by": updated_by,
        },
    )
    record.value = json.dumps(config.to_dict(), ensure_ascii=False)
    record.description = "录像与本地媒体容量保护配置"
    record.updated_at = datetime.now()
    record.updated_by = updated_by
    record.save()
    return config
