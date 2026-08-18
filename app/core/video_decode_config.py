"""数据库持久化的视频解码策略配置。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from app import logger
from app.config import DECODE_KEYFRAMES_ONLY
from app.core.database_models import SystemSetting


VIDEO_DECODE_SETTING_KEY = "video_decode_config"
VIDEO_DECODE_CONFIG_REFRESH_SECONDS = 5.0


@dataclass(frozen=True)
class VideoDecodeConfig:
    decode_keyframes_only: bool = DECODE_KEYFRAMES_ONLY

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_video_decode_config(
    data: Optional[Dict[str, Any]],
) -> VideoDecodeConfig:
    data = data if isinstance(data, dict) else {}
    value = data.get("decode_keyframes_only", DECODE_KEYFRAMES_ONLY)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            value = True
        elif normalized in {"false", "0", "no", "off", ""}:
            value = False
        else:
            value = DECODE_KEYFRAMES_ONLY
    return VideoDecodeConfig(decode_keyframes_only=bool(value))


def load_video_decode_config(
    *,
    log_failure: bool = True,
) -> Tuple[VideoDecodeConfig, str, bool]:
    """返回配置、来源（database/environment）以及数据库是否可用。"""
    fallback = VideoDecodeConfig()
    try:
        record = SystemSetting.get_or_none(
            SystemSetting.key == VIDEO_DECODE_SETTING_KEY
        )
        if not record or not record.value:
            return fallback, "environment", True
        return normalize_video_decode_config(json.loads(record.value)), "database", True
    except Exception as exc:
        if log_failure:
            logger.warning(f"读取视频解码配置失败，使用环境变量默认值: {exc}")
        return fallback, "environment", False


def get_video_decode_config() -> VideoDecodeConfig:
    config, _source, _database_available = load_video_decode_config()
    return config


def save_video_decode_config(
    data: Optional[Dict[str, Any]],
    updated_by: str = "system",
) -> VideoDecodeConfig:
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")
    value = data.get("decode_keyframes_only")
    if not isinstance(value, bool):
        raise ValueError("仅解码关键帧必须是布尔值")

    config = VideoDecodeConfig(decode_keyframes_only=value)
    record, _ = SystemSetting.get_or_create(
        key=VIDEO_DECODE_SETTING_KEY,
        defaults={
            "value": "",
            "description": "视频解码策略配置",
            "updated_at": datetime.now(),
            "updated_by": updated_by,
        },
    )
    record.value = json.dumps(config.to_dict(), ensure_ascii=False)
    record.description = "视频解码策略配置"
    record.updated_at = datetime.now()
    record.updated_by = updated_by
    record.save()
    return config
