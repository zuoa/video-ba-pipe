"""磁盘压力分级，供告警落盘和运维监控共享。"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from app.config import FRAME_SAVE_PATH, VIDEO_SAVE_PATH
from app.core.recording_storage_config import RecordingStorageConfig


class StoragePressureLevel(str, Enum):
    NORMAL = "normal"
    RECORDING_STOPPED = "recording_stopped"
    METADATA_ONLY = "metadata_only"


@dataclass(frozen=True)
class StoragePressure:
    level: StoragePressureLevel
    used_percent: float
    total_bytes: int
    used_bytes: int
    free_bytes: int

    @property
    def allow_recording(self) -> bool:
        return self.level == StoragePressureLevel.NORMAL

    @property
    def allow_media(self) -> bool:
        return self.level != StoragePressureLevel.METADATA_ONLY


def _existing_disk_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else Path.cwd()


def _device_id(path: Path) -> int:
    return os.stat(path).st_dev


def measure_storage_pressure(
    config: RecordingStorageConfig,
    paths: Iterable[str] = (VIDEO_SAVE_PATH, FRAME_SAVE_PATH),
) -> StoragePressure:
    pressures = []
    seen_devices = set()
    for raw_path in paths:
        disk_path = _existing_disk_path(raw_path)
        device = _device_id(disk_path)
        if device in seen_devices:
            continue
        seen_devices.add(device)
        disk = shutil.disk_usage(disk_path)
        used_percent = (disk.used / disk.total * 100.0) if disk.total else 100.0
        if used_percent >= config.metadata_only_percent:
            level = StoragePressureLevel.METADATA_ONLY
        elif used_percent >= config.stop_recording_percent:
            level = StoragePressureLevel.RECORDING_STOPPED
        else:
            level = StoragePressureLevel.NORMAL
        pressures.append(StoragePressure(
            level=level,
            used_percent=used_percent,
            total_bytes=disk.total,
            used_bytes=disk.used,
            free_bytes=disk.free,
        ))

    if not pressures:
        disk = shutil.disk_usage(Path.cwd())
        return StoragePressure(
            level=StoragePressureLevel.METADATA_ONLY,
            used_percent=100.0,
            total_bytes=disk.total,
            used_bytes=disk.used,
            free_bytes=disk.free,
        )
    return max(pressures, key=lambda pressure: pressure.used_percent)
