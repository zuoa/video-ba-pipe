import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Set

from app import logger
from app.config import (
    ALERT_IMAGE_CLEANUP_ENABLED,
    ALERT_IMAGE_RETENTION_DAYS,
    ALERT_RECORD_RETENTION_DAYS,
    ALERT_VIDEO_RETENTION_DAYS,
    FRAME_SAVE_PATH,
    MEDIA_CLEANUP_INTERVAL_SECONDS,
    VIDEO_SAVE_PATH,
    WINDOW_DETECTION_RETENTION_HOURS,
)
from app.core.database_models import Alert, WorkflowTestResult, db
from app.core.recording_storage_config import (
    RecordingStorageConfig,
    get_recording_storage_config,
    load_recording_storage_config_with_status,
)
from app.core.dingtalk_notifier import notify_ops_event
from app.core.ops_notification_config import (
    OpsNotificationConfig,
    get_ops_notification_config,
)
from app.core.storage_pressure import (
    StoragePressureLevel,
    measure_storage_pressure,
)


GIB = 1024 * 1024 * 1024
CAPACITY_CLEANUP_TARGET_RATIO = 0.9
ACTIVE_FILE_GRACE_SECONDS = 120


@dataclass
class FilesystemCleanupResult:
    removed_files: int = 0
    removed_bytes: int = 0
    failed_files: int = 0

    def add(self, other: "FilesystemCleanupResult") -> None:
        self.removed_files += other.removed_files
        self.removed_bytes += other.removed_bytes
        self.failed_files += other.failed_files


def _directory_files(base_dir: str):
    files = []
    total_bytes = 0
    base_path = Path(base_dir)
    if not base_path.exists():
        return total_bytes, files

    for path in base_path.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        total_bytes += stat.st_size
        files.append((stat.st_mtime, stat.st_size, path))
    return total_bytes, files


def directory_usage_bytes(base_dir: str) -> int:
    total_bytes, _ = _directory_files(base_dir)
    return total_bytes


def cleanup_directory_to_limit(
    base_dir: str,
    max_bytes: int,
    *,
    target_ratio: float = CAPACITY_CLEANUP_TARGET_RATIO,
    now: Optional[float] = None,
) -> FilesystemCleanupResult:
    """超过容量上限时删除最老文件，回收到目标水位。"""
    result = FilesystemCleanupResult()
    if max_bytes <= 0:
        return result

    total_bytes, files = _directory_files(base_dir)
    if total_bytes <= max_bytes:
        return result

    target_bytes = int(max_bytes * min(1.0, max(0.5, target_ratio)))
    now_ts = time.time() if now is None else now
    old_files = [
        item for item in files
        if now_ts - item[0] >= ACTIVE_FILE_GRACE_SECONDS
    ]
    # 正常优先保护刚写入文件；若没有足够旧文件，再按时间处理全部文件，
    # 确保容量保护不会因持续高写入而失效。
    candidates = old_files if sum(item[1] for item in old_files) >= total_bytes - target_bytes else files
    for _mtime, size, path in sorted(candidates, key=lambda item: item[0]):
        if total_bytes <= target_bytes:
            break
        try:
            path.unlink()
            total_bytes -= size
            result.removed_files += 1
            result.removed_bytes += size
        except FileNotFoundError:
            total_bytes -= size
        except OSError as exc:
            result.failed_files += 1
            logger.warning(f"[AlertMediaCleaner] 容量回收删除失败 {path}: {exc}")
    return result


def cleanup_directory_for_free_space(
    base_dir: str,
    min_free_bytes: int,
    *,
    now: Optional[float] = None,
) -> FilesystemCleanupResult:
    """所在分区低于安全水位时，独立于数据库删除最老媒体文件。"""
    result = FilesystemCleanupResult()
    if min_free_bytes <= 0:
        return result
    try:
        free_bytes = shutil.disk_usage(base_dir).free
    except FileNotFoundError:
        return result
    if free_bytes >= min_free_bytes:
        return result

    _total_bytes, files = _directory_files(base_dir)
    now_ts = time.time() if now is None else now
    candidates = [
        item for item in files
        if now_ts - item[0] >= ACTIVE_FILE_GRACE_SECONDS
    ]
    for _mtime, size, path in sorted(candidates, key=lambda item: item[0]):
        if free_bytes >= min_free_bytes:
            break
        try:
            path.unlink()
            result.removed_files += 1
            result.removed_bytes += size
        except FileNotFoundError:
            pass
        except OSError as exc:
            result.failed_files += 1
            logger.warning(f"[AlertMediaCleaner] 磁盘水位回收删除失败 {path}: {exc}")
        try:
            # 不能用文件逻辑大小推算：被录像进程打开的 inode 在 unlink 后仍占用空间。
            free_bytes = shutil.disk_usage(base_dir).free
        except OSError as exc:
            result.failed_files += 1
            logger.warning(f"[AlertMediaCleaner] 删除后复查磁盘水位失败 {base_dir}: {exc}")
            break
    return result


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


def reconcile_detection_image_entries(detection_images) -> tuple[list, bool]:
    """逐字段移除失效图片路径，并为仍存在的原图补齐可展示主图。"""
    original = _load_detection_images(detection_images)
    reconciled = []
    changed = False
    for item in original:
        if not isinstance(item, dict):
            changed = True
            continue
        updated = dict(item)
        for key in ("image_path", "image_ori_path"):
            path = updated.get(key)
            if path and not _media_file_exists(resolve_frame_media_path, path):
                updated.pop(key, None)
                changed = True
        if not updated.get("image_path") and updated.get("image_ori_path"):
            updated["image_path"] = updated["image_ori_path"]
            changed = True
        if not updated.get("image_path"):
            changed = True
            continue
        reconciled.append(updated)
        if updated != item:
            changed = True
    return reconciled, changed


def collect_alert_media_paths(
    alert_image: Optional[str],
    alert_image_ori: Optional[str],
    detection_images,
    alert_video: Optional[str] = None,
) -> Set[str]:
    paths: Set[str] = set()
    for candidate in (alert_image, alert_image_ori, alert_video):
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


def resolve_media_path(base_dir: str, relative_path: Optional[str]) -> Optional[Path]:
    if not relative_path:
        return None

    base_path = Path(base_dir).resolve()
    candidate = (base_path / relative_path).resolve()
    try:
        candidate.relative_to(base_path)
    except ValueError:
        return None
    return candidate


def resolve_frame_media_path(relative_path: Optional[str]) -> Optional[Path]:
    return resolve_media_path(FRAME_SAVE_PATH, relative_path)


def resolve_video_media_path(relative_path: Optional[str]) -> Optional[Path]:
    return resolve_media_path(VIDEO_SAVE_PATH, relative_path)


def _media_file_exists(resolver, relative_path: Optional[str]) -> bool:
    resolved = resolver(relative_path)
    return bool(resolved and resolved.is_file())


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
        self.image_retention_days = ALERT_IMAGE_RETENTION_DAYS
        self.video_retention_days = ALERT_VIDEO_RETENTION_DAYS
        self.record_retention_days = ALERT_RECORD_RETENTION_DAYS
        self.window_detection_retention_seconds = WINDOW_DETECTION_RETENTION_HOURS * 3600
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_notified_pressure_level: Optional[StoragePressureLevel] = None

    def start(self):
        if not self.enabled:
            logger.info("[AlertMediaCleaner] 未启用告警媒体自动清理")
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
            f"image_retention_days={self.image_retention_days}, "
            f"video_retention_days={self.video_retention_days}, "
            f"record_retention_days={self.record_retention_days}, "
            f"window_detection_retention_hours={WINDOW_DETECTION_RETENTION_HOURS}, "
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
        recording_config = get_recording_storage_config()
        notification_config = get_ops_notification_config()
        self._monitor_disk_pressure(recording_config, notification_config)

        try:
            filesystem_result = self.run_filesystem_cleanup_once(recording_config)
        except Exception as exc:
            filesystem_result = FilesystemCleanupResult()
            logger.exception(f"[AlertMediaCleaner] 文件系统容量清理失败: {exc}")
            self._notify_cleanup_failure(notification_config, str(exc))

        if filesystem_result.failed_files:
            self._notify_cleanup_failure(
                notification_config,
                f"本轮有 {filesystem_result.failed_files} 个媒体文件删除失败",
            )

        self._monitor_disk_pressure(recording_config, notification_config)
        try:
            expired_alerts = self._cleanup_expired_alert_media()
            expired_window_files = cleanup_expired_window_detection_files(
                FRAME_SAVE_PATH,
                self.window_detection_retention_seconds,
            )
            expired_records = self._cleanup_expired_alert_records()
            reconciled_records = (
                self._reconcile_missing_media_references()
                if filesystem_result.removed_files or expired_window_files
                else 0
            )

            if (
                filesystem_result.removed_files
                or expired_alerts
                or expired_window_files
                or expired_records
                or reconciled_records
            ):
                logger.info(
                    "[AlertMediaCleaner] 清理完成: "
                    f"capacity_files={filesystem_result.removed_files}, "
                    f"capacity_gb={filesystem_result.removed_bytes / GIB:.2f}, "
                    f"expired_alerts={expired_alerts}, "
                    f"window_detection_files={expired_window_files}, "
                    f"expired_records={expired_records}, "
                    f"reconciled_records={reconciled_records}, "
                    f"free_gb={self._get_free_bytes() / 1024 / 1024 / 1024:.2f}"
                )
            self._monitor_alert_growth(notification_config)
        except Exception as exc:
            # 文件系统容量保护已经先执行；数据库不可用不应阻断磁盘自救。
            logger.exception(f"[AlertMediaCleaner] 数据库媒体清理失败: {exc}")
            self._notify_cleanup_failure(notification_config, f"数据库媒体清理失败: {exc}")

    def run_startup_filesystem_cleanup(
        self,
        *,
        wait_for_database_seconds: float = 0.0,
    ) -> FilesystemCleanupResult:
        """启动自救：优先读取持久化上限，数据库超时后才使用环境默认值。"""
        deadline = time.monotonic() + max(0.0, wait_for_database_seconds)
        while True:
            config, database_available = load_recording_storage_config_with_status(
                log_failure=False,
            )
            if database_available:
                logger.info("[AlertMediaCleaner] 启动清理使用数据库持久化配置")
                break
            if time.monotonic() >= deadline:
                logger.warning("[AlertMediaCleaner] 数据库不可用，启动清理回退环境默认配置")
                break
            time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
        return self.run_filesystem_cleanup_once(config)

    def _monitor_disk_pressure(
        self,
        recording_config: RecordingStorageConfig,
        notification_config: OpsNotificationConfig,
    ) -> None:
        try:
            pressure = measure_storage_pressure(recording_config)
        except Exception as exc:
            logger.error(f"[AlertMediaCleaner] 读取磁盘水位失败: {exc}")
            self._notify_cleanup_failure(notification_config, f"读取磁盘水位失败: {exc}")
            return

        if not notification_config.notify_disk_pressure:
            return

        previous = self._last_notified_pressure_level
        if pressure.level == previous:
            return
        if pressure.level == StoragePressureLevel.NORMAL:
            if previous is None:
                if notification_config.enabled:
                    self._last_notified_pressure_level = pressure.level
                return
            delivered = notify_ops_event(
                "disk_pressure_recovered",
                "磁盘水位已恢复",
                f"当前使用率 {pressure.used_percent:.1f}%，剩余 {pressure.free_bytes / GIB:.1f} GB。",
                config=notification_config,
            )
            if delivered:
                self._last_notified_pressure_level = pressure.level
            return

        action = (
            "系统已停止新录像"
            if pressure.level == StoragePressureLevel.RECORDING_STOPPED
            else "系统已进入仅保留告警元数据模式"
        )
        delivered = notify_ops_event(
            f"disk_pressure_{pressure.level.value}",
            "磁盘水位告警",
            f"当前使用率 {pressure.used_percent:.1f}%，剩余 {pressure.free_bytes / GIB:.1f} GB；{action}。",
            config=notification_config,
        )
        if delivered:
            self._last_notified_pressure_level = pressure.level

    @staticmethod
    def _notify_cleanup_failure(
        notification_config: OpsNotificationConfig,
        details: str,
    ) -> None:
        if notification_config.notify_cleanup_failure:
            notify_ops_event(
                "media_cleanup_failure",
                "媒体清理失败",
                details,
                config=notification_config,
            )

    @staticmethod
    def _monitor_alert_growth(notification_config: OpsNotificationConfig) -> None:
        if not notification_config.notify_alert_growth:
            return
        cutoff = datetime.now() - timedelta(
            minutes=notification_config.alert_growth_window_minutes
        )
        with db.connection_context():
            alert_count = Alert.select().where(Alert.alert_time >= cutoff).count()
        if alert_count < notification_config.alert_growth_threshold:
            return
        notify_ops_event(
            "abnormal_alert_growth",
            "告警数量异常增长",
            (
                f"最近 {notification_config.alert_growth_window_minutes} 分钟产生 "
                f"{alert_count} 条告警，已达到阈值 {notification_config.alert_growth_threshold} 条。"
            ),
            config=notification_config,
        )

    def run_filesystem_cleanup_once(
        self,
        config: Optional[RecordingStorageConfig] = None,
    ) -> FilesystemCleanupResult:
        result = FilesystemCleanupResult()
        if not self.enabled:
            return result

        config = config or get_recording_storage_config()
        result.add(cleanup_directory_to_limit(
            VIDEO_SAVE_PATH,
            int(config.video_max_gb * GIB),
        ))
        result.add(cleanup_directory_to_limit(
            FRAME_SAVE_PATH,
            int(config.image_max_gb * GIB),
        ))

        min_free_bytes = int(config.min_free_gb * GIB)
        seen_devices = set()
        for media_path in (VIDEO_SAVE_PATH, FRAME_SAVE_PATH):
            try:
                device = os.stat(media_path).st_dev
            except FileNotFoundError:
                continue
            if device in seen_devices:
                continue
            seen_devices.add(device)
            # 同盘时优先淘汰录像，再淘汰图片。
            roots = (VIDEO_SAVE_PATH, FRAME_SAVE_PATH) if media_path == VIDEO_SAVE_PATH else (media_path,)
            for root in roots:
                result.add(cleanup_directory_for_free_space(root, min_free_bytes))
                if shutil.disk_usage(root).free >= min_free_bytes:
                    break
        return result

    def _cleanup_expired_alert_media(self) -> int:
        if self.image_retention_days <= 0 and self.video_retention_days <= 0:
            return 0

        now = datetime.now()
        image_cutoff = now - timedelta(days=self.image_retention_days)
        video_cutoff = now - timedelta(days=self.video_retention_days)
        image_fields_present = (
            Alert.alert_image.is_null(False) |
            Alert.alert_image_ori.is_null(False) |
            Alert.detection_images.is_null(False)
        )
        video_field_present = Alert.alert_video.is_null(False)

        predicates = []
        if self.image_retention_days > 0:
            predicates.append((Alert.alert_time < image_cutoff) & image_fields_present)
        if self.video_retention_days > 0:
            predicates.append((Alert.alert_time < video_cutoff) & video_field_present)

        media_expired = predicates[0]
        for predicate in predicates[1:]:
            media_expired |= predicate

        removed_alerts = 0

        with db.connection_context():
            query = (
                Alert.select()
                .where(media_expired)
                .order_by(Alert.alert_time.asc())
            )
            for alert in query.iterator():
                if self._purge_alert_media(
                    alert,
                    purge_images=(
                        self.image_retention_days > 0
                        and alert.alert_time < image_cutoff
                    ),
                    purge_video=(
                        self.video_retention_days > 0
                        and alert.alert_time < video_cutoff
                    ),
                ):
                    removed_alerts += 1

        return removed_alerts

    def _cleanup_expired_alert_records(self) -> int:
        if self.record_retention_days <= 0:
            return 0

        cutoff = datetime.now() - timedelta(days=self.record_retention_days)
        with db.connection_context():
            return (
                Alert.delete()
                .where(
                    (Alert.alert_time < cutoff) &
                    Alert.alert_image.is_null(True) &
                    Alert.alert_image_ori.is_null(True) &
                    Alert.detection_images.is_null(True) &
                    Alert.alert_video.is_null(True)
                )
                .execute()
            )

    def _reconcile_missing_media_references(self) -> int:
        reconciled = 0
        with db.connection_context():
            for model in (Alert, WorkflowTestResult):
                query = model.select().where(
                    model.alert_image.is_null(False) |
                    model.alert_image_ori.is_null(False) |
                    model.detection_images.is_null(False) |
                    model.alert_video.is_null(False)
                )
                for record in query.iterator():
                    if self._reconcile_record_media_references(record, model):
                        reconciled += 1
        return reconciled

    @staticmethod
    def _reconcile_record_media_references(record, model) -> bool:
        fields_to_save = []
        primary_exists = _media_file_exists(
            resolve_frame_media_path,
            record.alert_image,
        )
        original_exists = _media_file_exists(
            resolve_frame_media_path,
            record.alert_image_ori,
        )
        if record.alert_image and not primary_exists:
            record.alert_image = record.alert_image_ori if original_exists else None
            fields_to_save.append(model.alert_image)
        if record.alert_image_ori and not original_exists:
            record.alert_image_ori = None
            fields_to_save.append(model.alert_image_ori)
        if record.alert_video and not _media_file_exists(
            resolve_video_media_path,
            record.alert_video,
        ):
            record.alert_video = None
            fields_to_save.append(model.alert_video)

        remaining, detection_images_changed = reconcile_detection_image_entries(
            record.detection_images
        )
        if detection_images_changed:
            record.detection_images = (
                json.dumps(remaining, ensure_ascii=False) if remaining else None
            )
            fields_to_save.append(model.detection_images)

        if fields_to_save:
            record.save(only=fields_to_save)
            return True
        return False

    def _purge_alert_media(self, alert: Alert, purge_images: bool, purge_video: bool) -> bool:
        image_paths = collect_alert_media_paths(
            alert.alert_image,
            alert.alert_image_ori,
            alert.detection_images,
        ) if purge_images else set()
        video_paths = {alert.alert_video.strip()} if (
            purge_video and isinstance(alert.alert_video, str) and alert.alert_video.strip()
        ) else set()
        deleted_any_file = False

        for relative_path in image_paths:
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

        for relative_path in video_paths:
            full_path = resolve_video_media_path(relative_path)
            if full_path is None:
                logger.warning(f"[AlertMediaCleaner] 跳过非法视频路径: {relative_path}")
                continue
            try:
                if full_path.exists():
                    full_path.unlink()
                    deleted_any_file = True
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.warning(f"[AlertMediaCleaner] 删除告警视频失败 {full_path}: {exc}")

        fields_to_save = []
        if purge_images:
            if alert.alert_image is not None:
                fields_to_save.append(Alert.alert_image)
            if alert.alert_image_ori is not None:
                fields_to_save.append(Alert.alert_image_ori)
            if alert.detection_images is not None:
                fields_to_save.append(Alert.detection_images)
            alert.alert_image = None
            alert.alert_image_ori = None
            alert.detection_images = None

        if purge_video:
            if alert.alert_video is not None:
                fields_to_save.append(Alert.alert_video)
            alert.alert_video = None

        if fields_to_save:
            alert.save(only=fields_to_save)
        return deleted_any_file or bool(fields_to_save)

    @staticmethod
    def _get_free_bytes() -> int:
        free_values = []
        seen_devices = set()
        for media_path in (FRAME_SAVE_PATH, VIDEO_SAVE_PATH):
            try:
                stat = os.stat(media_path)
                if stat.st_dev in seen_devices:
                    continue
                seen_devices.add(stat.st_dev)
                free_values.append(shutil.disk_usage(media_path).free)
            except FileNotFoundError:
                free_values.append(0)
        return min(free_values) if free_values else 0
