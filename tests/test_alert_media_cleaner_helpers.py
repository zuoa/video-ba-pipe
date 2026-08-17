import importlib.util
from enum import Enum
import os
import sys
import time
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "app" / "core" / "alert_media_cleaner.py"


@pytest.fixture
def alert_media_cleaner_module(monkeypatch):
    spec = importlib.util.spec_from_file_location("test_alert_media_cleaner", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None

    fake_app = types.ModuleType("app")
    fake_app.logger = types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
    )

    fake_config = types.ModuleType("app.config")
    fake_config.ALERT_IMAGE_CLEANUP_ENABLED = True
    fake_config.ALERT_IMAGE_MIN_FREE_GB = 2
    fake_config.ALERT_IMAGE_RETENTION_DAYS = 7
    fake_config.ALERT_VIDEO_RETENTION_DAYS = 7
    fake_config.ALERT_RECORD_RETENTION_DAYS = 30
    fake_config.FRAME_SAVE_PATH = str(PROJECT_ROOT / "data" / "frames")
    fake_config.VIDEO_SAVE_PATH = str(PROJECT_ROOT / "data" / "videos")
    fake_config.MEDIA_CLEANUP_INTERVAL_SECONDS = 3600
    fake_config.WINDOW_DETECTION_RETENTION_HOURS = 24

    fake_db_models = types.ModuleType("app.core.database_models")
    fake_db_models.Alert = type(
        "Alert",
        (),
        {
            "alert_image": "alert_image",
            "alert_image_ori": "alert_image_ori",
            "detection_images": "detection_images",
            "alert_video": "alert_video",
            "alert_time": "alert_time",
        },
    )
    fake_db_models.WorkflowTestResult = type(
        "WorkflowTestResult",
        (),
        {
            "alert_image": "test_alert_image",
            "alert_image_ori": "test_alert_image_ori",
            "detection_images": "test_detection_images",
            "alert_video": "test_alert_video",
        },
    )
    fake_db_models.db = types.SimpleNamespace(
        connection_context=lambda: types.SimpleNamespace(
            __enter__=lambda self: None,
            __exit__=lambda self, exc_type, exc, tb: None,
        )
    )
    fake_recording_config = types.ModuleType("app.core.recording_storage_config")
    fake_recording_config.RecordingStorageConfig = object
    fake_recording_config.get_recording_storage_config = lambda: types.SimpleNamespace(
        video_max_gb=20,
        image_max_gb=10,
        min_free_gb=2,
    )
    fake_recording_config.load_recording_storage_config_with_status = (
        lambda **kwargs: (fake_recording_config.get_recording_storage_config(), True)
    )

    monkeypatch.setitem(sys.modules, "app", fake_app)
    monkeypatch.setitem(sys.modules, "app.config", fake_config)
    monkeypatch.setitem(sys.modules, "app.core.database_models", fake_db_models)
    monkeypatch.setitem(
        sys.modules,
        "app.core.recording_storage_config",
        fake_recording_config,
    )
    fake_notifier = types.ModuleType("app.core.dingtalk_notifier")
    fake_notifier.notify_ops_event = lambda *args, **kwargs: False
    fake_ops_config = types.ModuleType("app.core.ops_notification_config")
    fake_ops_config.OpsNotificationConfig = object
    fake_ops_config.get_ops_notification_config = lambda: types.SimpleNamespace(
        notify_disk_pressure=False,
        notify_cleanup_failure=False,
        notify_alert_growth=False,
    )
    fake_storage_pressure = types.ModuleType("app.core.storage_pressure")
    class FakeStoragePressureLevel(str, Enum):
        NORMAL = "normal"
        RECORDING_STOPPED = "recording_stopped"
        METADATA_ONLY = "metadata_only"

    fake_storage_pressure.StoragePressureLevel = FakeStoragePressureLevel
    fake_storage_pressure.measure_storage_pressure = lambda config: types.SimpleNamespace(
        level=FakeStoragePressureLevel.NORMAL,
        used_percent=10.0,
        free_bytes=10 * 1024 ** 3,
    )
    monkeypatch.setitem(sys.modules, "app.core.dingtalk_notifier", fake_notifier)
    monkeypatch.setitem(sys.modules, "app.core.ops_notification_config", fake_ops_config)
    monkeypatch.setitem(sys.modules, "app.core.storage_pressure", fake_storage_pressure)

    spec.loader.exec_module(module)
    return module


def test_collect_alert_media_paths_deduplicates_and_parses_detection_images(alert_media_cleaner_module):
    paths = alert_media_cleaner_module.collect_alert_media_paths(
        "source/alert/frame.jpg",
        "source/alert/frame.jpg.ori.jpg",
        '[{"image_path":"source/alert/frame.jpg","image_ori_path":"source/alert/frame.jpg.ori.jpg"},'
        '{"image_path":"source/.window_detection/frame2.jpg"}]',
        "1/alert_1_20260801_120000.mp4",
    )

    assert paths == {
        "source/alert/frame.jpg",
        "source/alert/frame.jpg.ori.jpg",
        "source/.window_detection/frame2.jpg",
        "1/alert_1_20260801_120000.mp4",
    }


def test_resolve_video_media_path_rejects_path_escape(tmp_path: Path, alert_media_cleaner_module, monkeypatch):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    monkeypatch.setattr(alert_media_cleaner_module, "VIDEO_SAVE_PATH", str(video_dir))

    assert alert_media_cleaner_module.resolve_video_media_path("1/alert_1.mp4") == (
        video_dir / "1" / "alert_1.mp4"
    )
    assert alert_media_cleaner_module.resolve_video_media_path("../outside.mp4") is None


def test_purge_alert_media_removes_images_and_video(tmp_path: Path, alert_media_cleaner_module, monkeypatch):
    frame_dir = tmp_path / "frames"
    video_dir = tmp_path / "videos"
    image_path = frame_dir / "source" / "frame.jpg"
    original_path = frame_dir / "source" / "frame.jpg.ori.jpg"
    video_path = video_dir / "1" / "alert_1.mp4"
    for path in (image_path, original_path, video_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"media")

    monkeypatch.setattr(alert_media_cleaner_module, "FRAME_SAVE_PATH", str(frame_dir))
    monkeypatch.setattr(alert_media_cleaner_module, "VIDEO_SAVE_PATH", str(video_dir))

    saved_fields = []
    alert = types.SimpleNamespace(
        alert_image="source/frame.jpg",
        alert_image_ori="source/frame.jpg.ori.jpg",
        detection_images=None,
        alert_video="1/alert_1.mp4",
    )
    alert.save = lambda only: saved_fields.extend(only)

    cleaner = alert_media_cleaner_module.AlertMediaCleaner()
    assert cleaner._purge_alert_media(alert, purge_images=True, purge_video=True)
    assert not image_path.exists()
    assert not original_path.exists()
    assert not video_path.exists()
    assert alert.alert_image is None
    assert alert.alert_image_ori is None
    assert alert.alert_video is None
    assert saved_fields == ["alert_image", "alert_image_ori", "alert_video"]


def test_cleanup_expired_window_detection_files_removes_old_files(
    tmp_path: Path,
    alert_media_cleaner_module,
):
    old_file = tmp_path / "source" / ".window_detection" / "old.jpg"
    new_file = tmp_path / "source" / ".window_detection" / "new.jpg"
    old_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_text("old", encoding="utf-8")
    new_file.write_text("new", encoding="utf-8")

    stale_time = time.time() - 7200
    os.utime(old_file, (stale_time, stale_time))

    removed = alert_media_cleaner_module.cleanup_expired_window_detection_files(
        str(tmp_path),
        max_age_seconds=3600,
        now=time.time(),
    )

    assert removed == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_cleanup_directory_to_limit_deletes_oldest_files_first(
    tmp_path: Path,
    alert_media_cleaner_module,
):
    files = [tmp_path / f"file-{index}.mp4" for index in range(3)]
    now = time.time()
    for index, path in enumerate(files):
        path.write_bytes(b"x" * 10)
        modified = now - 1000 + index * 100
        os.utime(path, (modified, modified))

    result = alert_media_cleaner_module.cleanup_directory_to_limit(
        str(tmp_path),
        max_bytes=20,
        now=now,
    )

    assert result.removed_files == 2
    assert result.removed_bytes == 20
    assert not files[0].exists()
    assert not files[1].exists()
    assert files[2].exists()


def test_cleanup_directory_counts_delete_failures(
    tmp_path: Path,
    alert_media_cleaner_module,
    monkeypatch,
):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"x" * 10)
    second.write_bytes(b"x" * 10)

    original_unlink = Path.unlink

    def fail_first(path, *args, **kwargs):
        if path == first:
            raise PermissionError("read only")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first)
    result = alert_media_cleaner_module.cleanup_directory_to_limit(
        str(tmp_path),
        max_bytes=10,
        now=time.time() + 1000,
    )

    assert result.failed_files == 1


def test_low_space_cleanup_protects_recent_files_and_rechecks_real_free_space(
    tmp_path: Path,
    alert_media_cleaner_module,
    monkeypatch,
):
    now = time.time()
    files = [tmp_path / "old-1.mp4", tmp_path / "old-2.mp4", tmp_path / "active.mp4"]
    for path in files:
        path.write_bytes(b"x" * 100)
    os.utime(files[0], (now - 1000, now - 1000))
    os.utime(files[1], (now - 900, now - 900))
    os.utime(files[2], (now - 10, now - 10))

    free_values = iter((0, 0, 100))
    monkeypatch.setattr(
        alert_media_cleaner_module.shutil,
        "disk_usage",
        lambda _path: types.SimpleNamespace(free=next(free_values)),
    )

    result = alert_media_cleaner_module.cleanup_directory_for_free_space(
        str(tmp_path),
        min_free_bytes=50,
        now=now,
    )

    assert result.removed_files == 2
    assert files[2].exists()


def test_disk_pressure_notification_retries_until_delivery(
    alert_media_cleaner_module,
    monkeypatch,
):
    pressure = types.SimpleNamespace(
        level=alert_media_cleaner_module.StoragePressureLevel.RECORDING_STOPPED,
        used_percent=85.0,
        free_bytes=10 * 1024 ** 3,
    )
    monkeypatch.setattr(
        alert_media_cleaner_module,
        "measure_storage_pressure",
        lambda _config: pressure,
    )
    deliveries = iter((False, True))
    calls = []
    monkeypatch.setattr(
        alert_media_cleaner_module,
        "notify_ops_event",
        lambda *args, **kwargs: calls.append(args[0]) or next(deliveries),
    )
    notification_config = types.SimpleNamespace(
        enabled=True,
        notify_disk_pressure=True,
    )
    cleaner = alert_media_cleaner_module.AlertMediaCleaner()

    cleaner._monitor_disk_pressure(object(), notification_config)
    assert cleaner._last_notified_pressure_level is None
    cleaner._monitor_disk_pressure(object(), notification_config)

    assert calls == ["disk_pressure_recording_stopped"] * 2
    assert cleaner._last_notified_pressure_level == pressure.level


def test_reconcile_detection_entries_removes_only_missing_path(
    tmp_path: Path,
    alert_media_cleaner_module,
    monkeypatch,
):
    frame_dir = tmp_path / "frames"
    existing_original = frame_dir / "source" / "frame.ori.jpg"
    existing_original.parent.mkdir(parents=True)
    existing_original.write_bytes(b"image")
    monkeypatch.setattr(alert_media_cleaner_module, "FRAME_SAVE_PATH", str(frame_dir))

    reconciled, changed = alert_media_cleaner_module.reconcile_detection_image_entries([
        {
            "image_path": "source/missing.jpg",
            "image_ori_path": "source/frame.ori.jpg",
            "timestamp": 1,
        }
    ])

    assert changed is True
    assert reconciled == [{
        "image_path": "source/frame.ori.jpg",
        "image_ori_path": "source/frame.ori.jpg",
        "timestamp": 1,
    }]


def test_reconcile_workflow_test_record_clears_deleted_media(
    tmp_path: Path,
    alert_media_cleaner_module,
    monkeypatch,
):
    frame_dir = tmp_path / "frames"
    video_dir = tmp_path / "videos"
    frame_dir.mkdir()
    video_dir.mkdir()
    monkeypatch.setattr(alert_media_cleaner_module, "FRAME_SAVE_PATH", str(frame_dir))
    monkeypatch.setattr(alert_media_cleaner_module, "VIDEO_SAVE_PATH", str(video_dir))
    saved_fields = []
    record = types.SimpleNamespace(
        alert_image="workflow_test/missing.jpg",
        alert_image_ori=None,
        alert_video="workflow_test/missing.mp4",
        detection_images='[{"image_path":"workflow_test/missing.jpg"}]',
        save=lambda only: saved_fields.extend(only),
    )
    model = alert_media_cleaner_module.WorkflowTestResult

    assert alert_media_cleaner_module.AlertMediaCleaner._reconcile_record_media_references(
        record,
        model,
    )
    assert record.alert_image is None
    assert record.alert_video is None
    assert record.detection_images is None
    assert saved_fields == [
        model.alert_image,
        model.alert_video,
        model.detection_images,
    ]


def test_startup_cleanup_uses_loaded_persisted_config(
    alert_media_cleaner_module,
    monkeypatch,
):
    persisted = types.SimpleNamespace(video_max_gb=100, image_max_gb=50, min_free_gb=5)
    monkeypatch.setattr(
        alert_media_cleaner_module,
        "load_recording_storage_config_with_status",
        lambda **kwargs: (persisted, True),
    )
    cleaner = alert_media_cleaner_module.AlertMediaCleaner()
    captured = []
    monkeypatch.setattr(
        cleaner,
        "run_filesystem_cleanup_once",
        lambda config: captured.append(config) or alert_media_cleaner_module.FilesystemCleanupResult(),
    )

    cleaner.run_startup_filesystem_cleanup()

    assert captured == [persisted]


def test_filesystem_capacity_cleanup_continues_when_delivery_query_fails(
    tmp_path: Path,
    alert_media_cleaner_module,
    monkeypatch,
):
    frame_dir = tmp_path / "frames"
    video_dir = tmp_path / "videos"
    frame_dir.mkdir()
    video_dir.mkdir()
    monkeypatch.setattr(alert_media_cleaner_module, "FRAME_SAVE_PATH", str(frame_dir))
    monkeypatch.setattr(alert_media_cleaner_module, "VIDEO_SAVE_PATH", str(video_dir))
    cleaner = alert_media_cleaner_module.AlertMediaCleaner()
    monkeypatch.setattr(
        cleaner,
        "_pending_delivery_paths",
        lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    calls = []
    monkeypatch.setattr(
        alert_media_cleaner_module,
        "cleanup_directory_to_limit",
        lambda base_dir, max_bytes, **kwargs: calls.append(base_dir)
        or alert_media_cleaner_module.FilesystemCleanupResult(),
    )
    config = types.SimpleNamespace(video_max_gb=1, image_max_gb=1, min_free_gb=0)

    cleaner.run_filesystem_cleanup_once(config)

    assert calls == [str(video_dir), str(frame_dir)]
