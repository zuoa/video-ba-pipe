from types import SimpleNamespace

import pytest

from app.core.recording_storage_config import RecordingStorageConfig
from app.core.storage_pressure import (
    StoragePressureLevel,
    measure_storage_pressure,
)


@pytest.mark.parametrize(
    "used,expected",
    [
        (79, StoragePressureLevel.NORMAL),
        (80, StoragePressureLevel.RECORDING_STOPPED),
        (89, StoragePressureLevel.RECORDING_STOPPED),
        (90, StoragePressureLevel.METADATA_ONLY),
    ],
)
def test_storage_pressure_levels(tmp_path, monkeypatch, used, expected):
    monkeypatch.setattr(
        "app.core.storage_pressure.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=100, used=used, free=100 - used),
    )

    pressure = measure_storage_pressure(
        RecordingStorageConfig(),
        paths=(str(tmp_path),),
    )

    assert pressure.level == expected
    assert pressure.allow_recording is (expected == StoragePressureLevel.NORMAL)
    assert pressure.allow_media is (expected != StoragePressureLevel.METADATA_ONLY)


def test_storage_pressure_uses_worst_distinct_filesystem(tmp_path, monkeypatch):
    video_dir = tmp_path / "videos"
    frame_dir = tmp_path / "frames"
    video_dir.mkdir()
    frame_dir.mkdir()
    monkeypatch.setattr(
        "app.core.storage_pressure._device_id",
        lambda path: 1 if str(path) == str(video_dir) else 2,
    )
    monkeypatch.setattr(
        "app.core.storage_pressure.shutil.disk_usage",
        lambda path: (
            SimpleNamespace(total=100, used=40, free=60)
            if str(path) == str(video_dir)
            else SimpleNamespace(total=100, used=95, free=5)
        ),
    )

    pressure = measure_storage_pressure(
        RecordingStorageConfig(),
        paths=(str(video_dir), str(frame_dir)),
    )

    assert pressure.level == StoragePressureLevel.METADATA_ONLY
    assert pressure.used_percent == 95
    assert pressure.free_bytes == 5
