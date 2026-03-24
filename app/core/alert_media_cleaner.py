import json
import os
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional, Set

from app import logger
from app.config import (
    ALERT_IMAGE_CLEANUP_ENABLED,
    ALERT_IMAGE_MIN_FREE_GB,
    ALERT_IMAGE_RETENTION_DAYS,
    FRAME_SAVE_PATH,
    MEDIA_CLEANUP_INTERVAL_SECONDS,
    WINDOW_DETECTION_RETENTION_HOURS,
)
from app.core.database_models import Alert, db


def _load_detection_images(raw_value) -> list:
    if not raw_value:
        return []
    if isinstance(raw_value, list):
        return raw_value
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def collect_alert_media_paths(alert_image: Optional[str], alert_image_ori: Optional[str], detection_images) -> Set[str]:
    paths: Set[str] = set()
    for candidate in (alert_image, alert_image_ori):
        if isinstance(candidate, str) and candidate.strip():
            paths.add(candidate.strip())

    for item in _load_detection_images(detection_images):
        if not isinstance(item, dict):
            continue
        for key in ("image_path", "image_ori_path"):
            candidate = item.get(key)
            if isinstance(candidate, str) and candidate.strip():
                paths.add(candidate.strip())

    return paths


def resolve_frame_media_path(relative_path: Optional[str]) -> Optional[Path]:
    if not relative_path:
        return None

    base_path = Path(FRAME_SAVE_PATH).resolve()
    candidate = (base_path / relative_path).resolve()
    try:
        candidate.relative_to(base_path)
    except ValueError:
        return None
    return candidate


def cleanup_expired_window_detection_files(base_dir: str, max_age_seconds: int, now: Optional[float] = None) -> int:
    if max_age_seconds <= 0:
        return 0

    now_ts = now if now is not None else time.time()
    removed_count = 0
    base_path = Path(base_dir)
    if not base_path.exists():
        return 0

    for path in base_path.rglob("*"):
        if not path.is_file():
            continue
        if ".window_detection" not in path.parts:
            continue
        try:
            if now_ts - path.stat().st_mtime < max_age_seconds:
                continue
            path.unlink()
            removed_count += 1
        except FileNotFoundError:
            continue
        except Exception as exc:
            logger.warning(f"[AlertMediaCleaner] 删除窗口检测临时图片失败 {path}: {exc}")

    for path in sorted(base_path.rglob(".window_detection"), reverse=True):
        if not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            continue

    return removed_count


class AlertMediaCleaner:
    def __init__(self):
        self.enabled = ALERT_IMAGE_CLEANUP_ENABLED
        self.interval_seconds = MEDIA_CLEANUP_INTERVAL_SECONDS
        self.retention_days = ALERT_IMAGE_RETENTION_DAYS
        self.min_free_bytes = int(ALERT_IMAGE_MIN_FREE_GB * 1024 * 1024 * 1024)
        self.window_detection_retention_seconds = WINDOW_DETECTION_RETENTION_HOURS * 3600
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self):
        if not self.enabled:
            logger.info("[AlertMediaCleaner] 未启用告警图片自动清理")
            return
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="alert-media-cleaner",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[AlertMediaCleaner] 已启动: "
            f"retention_days={self.retention_days}, "
            f"window_detection_retention_hours={WINDOW_DETECTION_RETENTION_HOURS}, "
            f"min_free_gb={ALERT_IMAGE_MIN_FREE_GB}, "
            f"interval_seconds={self.interval_seconds}"
        )

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run_loop(self):
        self.run_once()
        while not self._stop_event.wait(self.interval_seconds):
            self.run_once()

    def run_once(self):
        try:
            expired_alerts = self._cleanup_expired_alert_media()
            expired_window_files = cleanup_expired_window_detection_files(
                FRAME_SAVE_PATH,
                self.window_detection_retention_seconds,
            )
            reclaimed_alerts = self._cleanup_for_low_disk_space()

            if expired_alerts or expired_window_files or reclaimed_alerts:
                logger.info(
                    "[AlertMediaCleaner] 清理完成: "
                    f"expired_alerts={expired_alerts}, "
                    f"window_detection_files={expired_window_files}, "
                    f"reclaimed_alerts={reclaimed_alerts}, "
                    f"free_gb={self._get_free_bytes() / 1024 / 1024 / 1024:.2f}"
                )
        except Exception as exc:
            logger.exception(f"[AlertMediaCleaner] 清理失败: {exc}")

    def _cleanup_expired_alert_media(self) -> int:
        if self.retention_days <= 0:
            return 0

        cutoff = datetime.now() - timedelta(days=self.retention_days)
        removed_alerts = 0

        with db.connection_context():
            query = (
                Alert.select()
                .where(
                    (Alert.alert_time < cutoff) &
                    (
                        Alert.alert_image.is_null(False) |
                        Alert.alert_image_ori.is_null(False) |
                        Alert.detection_images.is_null(False)
                    )
                )
                .order_by(Alert.alert_time.asc())
            )
            for alert in query.iterator():
                if self._purge_alert_media(alert):
                    removed_alerts += 1

        return removed_alerts

    def _cleanup_for_low_disk_space(self) -> int:
        if self.min_free_bytes <= 0:
            return 0

        removed_alerts = 0
        with db.connection_context():
            query = (
                Alert.select()
                .where(
                    Alert.alert_image.is_null(False) |
                    Alert.alert_image_ori.is_null(False) |
                    Alert.detection_images.is_null(False)
                )
                .order_by(Alert.alert_time.asc())
            )
            for alert in query.iterator():
                if self._get_free_bytes() >= self.min_free_bytes:
                    break
                if self._purge_alert_media(alert):
                    removed_alerts += 1

        return removed_alerts

    def _purge_alert_media(self, alert: Alert) -> bool:
        media_paths = collect_alert_media_paths(
            alert.alert_image,
            alert.alert_image_ori,
            alert.detection_images,
        )
        deleted_any_file = False

        for relative_path in media_paths:
            full_path = resolve_frame_media_path(relative_path)
            if full_path is None:
                logger.warning(f"[AlertMediaCleaner] 跳过非法图片路径: {relative_path}")
                continue
            try:
                if full_path.exists():
                    full_path.unlink()
                    deleted_any_file = True
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.warning(f"[AlertMediaCleaner] 删除告警图片失败 {full_path}: {exc}")

        if alert.alert_image is None and alert.alert_image_ori is None and alert.detection_images is None:
            return deleted_any_file

        alert.alert_image = None
        alert.alert_image_ori = None
        alert.detection_images = None
        alert.save(only=[Alert.alert_image, Alert.alert_image_ori, Alert.detection_images])
        return True

    @staticmethod
    def _get_free_bytes() -> int:
        try:
            return shutil.disk_usage(FRAME_SAVE_PATH).free
        except FileNotFoundError:
            return 0
