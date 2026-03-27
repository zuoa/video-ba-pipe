import importlib.util
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
        exception=lambda *args, **kwargs: None,
    )

    fake_config = types.ModuleType("app.config")
    fake_config.ALERT_IMAGE_CLEANUP_ENABLED = True
    fake_config.ALERT_IMAGE_MIN_FREE_GB = 2
    fake_config.ALERT_IMAGE_RETENTION_DAYS = 7
    fake_config.FRAME_SAVE_PATH = str(PROJECT_ROOT / "data" / "frames")
    fake_config.MEDIA_CLEANUP_INTERVAL_SECONDS = 3600
    fake_config.WINDOW_DETECTION_RETENTION_HOURS = 24

    fake_db_models = types.ModuleType("app.core.database_models")
    fake_db_models.Alert = type("Alert", (), {})
    fake_db_models.db = types.SimpleNamespace(
        connection_context=lambda: types.SimpleNamespace(
            __enter__=lambda self: None,
            __exit__=lambda self, exc_type, exc, tb: None,
        )
    )

    monkeypatch.setitem(sys.modules, "app", fake_app)
    monkeypatch.setitem(sys.modules, "app.config", fake_config)
    monkeypatch.setitem(sys.modules, "app.core.database_models", fake_db_models)

    spec.loader.exec_module(module)
    return module


def test_collect_alert_media_paths_deduplicates_and_parses_detection_images(alert_media_cleaner_module):
    paths = alert_media_cleaner_module.collect_alert_media_paths(
        "source/alert/frame.jpg",
        "source/alert/frame.jpg.ori.jpg",
        '[{"image_path":"source/alert/frame.jpg","image_ori_path":"source/alert/frame.jpg.ori.jpg"},'
        '{"image_path":"source/.window_detection/frame2.jpg"}]',
    )

    assert paths == {
        "source/alert/frame.jpg",
        "source/alert/frame.jpg.ori.jpg",
        "source/.window_detection/frame2.jpg",
    }


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
