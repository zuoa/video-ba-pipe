"""视频源轮转检测配置与稳定的 round-robin 选择器。"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from app import logger
from app.core.database_models import SystemSetting


SOURCE_ROTATION_SETTING_KEY = "source_rotation_config"
MIN_DWELL_SECONDS = 10


@dataclass(frozen=True)
class SourceRotationConfig:
    enabled: bool = False
    batch_size: int = 20
    dwell_seconds: int = 30

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def estimate_rotation_revisit_seconds(
    *,
    candidate_count: int,
    effective_concurrency: int,
    dwell_seconds: int,
    startup_p95_seconds: float = 0.0,
    drain_p95_seconds: float = 0.0,
    startup_timeout_seconds: int = 0,
    drain_timeout_seconds: int = 0,
) -> Dict[str, int]:
    """Return transparent best/P95/protection-bound revisit estimates."""
    if candidate_count <= 0:
        return {'best': 0, 'p95': 0, 'worst': 0, 'batches': 0}
    batches = math.ceil(candidate_count / max(1, effective_concurrency))
    best = batches * max(0, int(dwell_seconds))
    p95 = math.ceil(
        batches * (
            max(0, int(dwell_seconds))
            + max(0.0, float(startup_p95_seconds))
            + max(0.0, float(drain_p95_seconds))
        )
    )
    worst = batches * (
        max(0, int(dwell_seconds))
        + max(0, int(startup_timeout_seconds))
        + max(0, int(drain_timeout_seconds))
    )
    return {'best': best, 'p95': p95, 'worst': worst, 'batches': batches}


def _positive_int(value: Any, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def normalize_source_rotation_config(data: Optional[Dict[str, Any]]) -> SourceRotationConfig:
    data = data if isinstance(data, dict) else {}
    return SourceRotationConfig(
        enabled=bool(data.get("enabled", False)),
        batch_size=_positive_int(data.get("batch_size"), 20),
        dwell_seconds=_positive_int(
            data.get("dwell_seconds"),
            30,
            minimum=MIN_DWELL_SECONDS,
        ),
    )


def get_source_rotation_config() -> Dict[str, Any]:
    config = SourceRotationConfig()
    try:
        record = SystemSetting.get_or_none(
            SystemSetting.key == SOURCE_ROTATION_SETTING_KEY
        )
        if record and record.value:
            config = normalize_source_rotation_config(json.loads(record.value))
    except Exception as exc:
        logger.warning(f"读取视频轮转配置失败，使用默认配置: {exc}")
    return config.to_dict()


def save_source_rotation_config(
    data: Optional[Dict[str, Any]],
    updated_by: str = "system",
) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")
    try:
        batch_size = int(data.get("batch_size", 20))
        dwell_seconds = int(data.get("dwell_seconds", 30))
    except (TypeError, ValueError) as exc:
        raise ValueError("轮转路数和检测时长必须是整数") from exc
    if batch_size < 1:
        raise ValueError("每批检测路数必须大于 0")
    if dwell_seconds < MIN_DWELL_SECONDS:
        raise ValueError(f"单批检测时长不能少于 {MIN_DWELL_SECONDS} 秒")

    config = normalize_source_rotation_config(data)
    record, _ = SystemSetting.get_or_create(
        key=SOURCE_ROTATION_SETTING_KEY,
        defaults={
            "value": "",
            "description": "视频源轮转检测配置",
            "updated_at": datetime.now(),
            "updated_by": updated_by,
        },
    )
    record.value = json.dumps(config.to_dict(), ensure_ascii=False)
    record.description = "视频源轮转检测配置"
    record.updated_at = datetime.now()
    record.updated_by = updated_by
    record.save()
    return config.to_dict()


class RoundRobinBatchSelector:
    """按 source id 稳定轮转，并在尾部环回补满批次。"""

    def __init__(self):
        self.last_selected_id: Optional[int] = None

    def reset(self) -> None:
        self.last_selected_id = None

    def select(self, candidate_ids: Iterable[int], batch_size: int) -> List[int]:
        candidates = sorted({int(source_id) for source_id in candidate_ids})
        if not candidates:
            return []

        requested_size = min(max(1, int(batch_size)), len(candidates))
        start_index = 0
        if self.last_selected_id is not None:
            for index, source_id in enumerate(candidates):
                if source_id > self.last_selected_id:
                    start_index = index
                    break
            else:
                start_index = 0

        selected = [
            candidates[(start_index + offset) % len(candidates)]
            for offset in range(requested_size)
        ]
        self.last_selected_id = selected[-1]
        return selected
