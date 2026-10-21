import json
import os
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from peewee import SqliteDatabase

from app.core import alert_media_cleaner
from app.core.database_models import (
    Alert,
    AlertDeliveryTask,
    SystemSetting,
    VideoSource,
    Workflow,
    WorkflowTestResult,
)
from app.core.recording_storage_config import (
    RecordingStorageConfig,
    save_recording_storage_config,
)


@pytest.fixture
def retention_env(tmp_path, monkeypatch):
    test_db = SqliteDatabase(str(tmp_path / "retention.db"))
    models = [SystemSetting, VideoSource, Workflow, Alert, AlertDeliveryTask, WorkflowTestResult]
    frames = tmp_path / "frames"
    videos = tmp_path / "videos"
    frames.mkdir()
    videos.mkdir()
    image = frames / "alert.jpg"
    original = frames / "alert.ori.jpg"
    detection = frames / "detection.jpg"
    video = videos / "alert.mp4"
    for index, path in enumerate((image, original, detection, video)):
        path.write_bytes(b"x" * 20)
        modified = time.time() - 1000 + index * 10
        os.utime(path, (modified, modified))

    monkeypatch.setattr(alert_media_cleaner, "db", test_db)
    monkeypatch.setattr(alert_media_cleaner, "FRAME_SAVE_PATH", str(frames))
    monkeypatch.setattr(alert_media_cleaner, "VIDEO_SAVE_PATH", str(videos))
    # Keep capacity tests small: one configured GB represents 100 test bytes.
    monkeypatch.setattr(alert_media_cleaner, "GIB", 100)
    monkeypatch.setattr(
        alert_media_cleaner.shutil, "disk_usage",
        lambda _path: SimpleNamespace(total=10000, used=1000, free=9000),
    )
    monkeypatch.setattr(
        alert_media_cleaner, "get_ops_notification_config",
        lambda: SimpleNamespace(
            notify_disk_pressure=False,
            notify_cleanup_failure=False,
            notify_alert_growth=False,
        ),
    )
    cleaner = alert_media_cleaner.AlertMediaCleaner()
    cleaner.enabled = True
    monkeypatch.setattr(cleaner, "_cleanup_expired_face_events", lambda: 0)
    config = RecordingStorageConfig(
        image_max_gb=1, video_max_gb=1, min_free_gb=1,
        image_retention_days=0, video_retention_days=0,
    ).to_dict()

    with test_db.bind_ctx(models):
        test_db.create_tables(models)
        source = VideoSource.create(name="Camera", source_code="camera-1", source_url="rtsp://camera/live")
        alert = Alert.create(
            video_source=source,
            alert_time=datetime.now() - timedelta(days=40),
            alert_type="person",
            alert_image=image.name,
            alert_image_ori=original.name,
            detection_images=json.dumps([{"image_path": detection.name}]),
            alert_video=video.name,
        )
        yield SimpleNamespace(
            cleaner=cleaner, config=config, alert_id=alert.id,
            frames=frames, image=image, original=original, detection=detection, video=video,
        )
        test_db.close()


@pytest.mark.parametrize("image_days,video_days", [(0, 0), (90, 90), (0, 7), (7, 0)])
def test_cleanup_uses_saved_retention_for_each_media_type(retention_env, image_days, video_days):
    env = retention_env
    env.config.update(image_retention_days=image_days, video_retention_days=video_days)
    save_recording_storage_config(env.config)

    env.cleaner.run_once()

    record = Alert.get_by_id(env.alert_id)
    keep_images = image_days in (0, 90)
    assert env.image.exists() is keep_images
    assert env.original.exists() is keep_images
    assert env.detection.exists() is keep_images
    assert bool(record.alert_image) is keep_images
    assert bool(record.alert_image_ori) is keep_images
    assert bool(record.detection_images) is keep_images
    assert env.video.exists() is (video_days in (0, 90))
    assert bool(record.alert_video) is (video_days in (0, 90))


def test_retention_changes_apply_to_running_cleaner(retention_env):
    env = retention_env
    save_recording_storage_config(env.config)
    env.cleaner.run_once()
    assert env.image.exists()
    assert env.video.exists()

    env.config["image_retention_days"] = 7
    save_recording_storage_config(env.config)
    env.cleaner.run_once()

    assert not env.image.exists()
    assert env.video.exists()
    assert Alert.get_by_id(env.alert_id).alert_image is None


def test_zero_retention_still_evicts_oldest_images_at_capacity(retention_env):
    env = retention_env
    newest = env.frames / "newest.jpg"
    newest.write_bytes(b"x" * 60)
    save_recording_storage_config(env.config)

    env.cleaner.run_once()

    assert not env.image.exists()
    assert not env.original.exists()
    assert newest.exists()
    assert env.detection.exists()
    assert env.video.exists()
    assert Alert.get_by_id(env.alert_id).alert_image is None


def test_zero_retention_still_recovers_low_disk_space(retention_env, monkeypatch):
    env = retention_env
    monkeypatch.setattr(
        alert_media_cleaner.shutil, "disk_usage",
        lambda _path: SimpleNamespace(
            total=10000, used=10000 if env.video.exists() else 1000,
            free=0 if env.video.exists() else 9000,
        ),
    )
    save_recording_storage_config(env.config)

    env.cleaner.run_once()

    assert not env.video.exists()
    assert env.image.exists()
    assert env.original.exists()
    assert env.detection.exists()
    assert Alert.get_by_id(env.alert_id).alert_video is None
