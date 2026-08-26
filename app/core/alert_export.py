"""Asynchronous alert record export: CSV + annotated/original images as ZIP."""

from __future__ import annotations

import csv
import fcntl
import io
import json
import logging
import os
import shutil
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from peewee import JOIN

from app.config import EXPORT_SAVE_PATH
from app.core.alert_media_cleaner import resolve_frame_media_path
from app.core.alert_query import build_alert_query, build_filter_summary, parse_alert_filters
from app.core.database_models import Alert, AlertExportTask, VideoSource, Workflow, db


logger = logging.getLogger(__name__)

def _read_max_export_records() -> int:
    raw = os.getenv('MAX_EXPORT_RECORDS', '50000').strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 50_000


MAX_EXPORT_RECORDS = _read_max_export_records()
EXPORT_RETENTION_DAYS = 7
LEASE_SECONDS = 120
STALE_PENDING_SECONDS = 60
POLL_INTERVAL_SECONDS = 1.0
PROGRESS_BATCH_SIZE = 20
ACTIVE_STATUSES = ('pending', 'running')
TERMINAL_STATUSES = ('succeeded', 'failed', 'cancelled')

CSV_COLUMNS = (
    'id',
    'alert_time',
    'alert_type',
    'alert_level',
    'alert_message',
    'source_id',
    'source_name',
    'source_code',
    'workflow_id',
    'workflow_name',
    'detection_count',
    'annotated_image',
    'original_image',
)

_worker_lock = threading.Lock()
_worker: Optional['AlertExportWorker'] = None


class ExportCancelled(Exception):
    """Raised when an in-flight export is cancelled."""


class ExportValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now()


def _task_filters(task: AlertExportTask) -> dict:
    raw = task.filters_json or '{}'
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def serialize_export_task(task: AlertExportTask) -> dict[str, Any]:
    total = int(task.total_count or 0)
    processed = int(task.processed_count or 0)
    percent = 0
    if task.status == 'succeeded':
        percent = 100
    elif total > 0:
        percent = min(99, int(processed * 100 / total))

    downloadable = task.status == 'succeeded' and bool(task.file_path)
    return {
        'id': task.id,
        'status': task.status,
        'created_by': task.created_by,
        'filters': _task_filters(task),
        'filter_summary': task.filter_summary or '全部告警',
        'total_count': total,
        'processed_count': processed,
        'missing_image_count': int(task.missing_image_count or 0),
        'progress_percent': percent,
        'file_name': task.file_name,
        'file_path': task.file_path,
        'file_url': public_export_url(task.file_path) if downloadable else None,
        'file_size': task.file_size,
        'error_message': task.error_message,
        'created_at': task.created_at.isoformat() if task.created_at else None,
        'started_at': task.started_at.isoformat() if task.started_at else None,
        'finished_at': task.finished_at.isoformat() if task.finished_at else None,
        'expires_at': task.expires_at.isoformat() if task.expires_at else None,
        'downloadable': downloadable,
    }


def public_export_url(relative_path: Optional[str]) -> Optional[str]:
    """Public nginx path for a completed export zip."""
    clean = str(relative_path or '').replace('\\', '/').lstrip('/')
    if not clean:
        return None
    return f'/media/exports/{quote(clean, safe="/")}'


def x_accel_redirect_path(relative_path: Optional[str]) -> Optional[str]:
    """Internal nginx path used after Flask authenticates a download."""
    public = public_export_url(relative_path)
    if not public:
        return None
    return '/internal' + public


def _export_root() -> Path:
    root = Path(EXPORT_SAVE_PATH)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def resolve_export_file(relative_path: Optional[str]) -> Optional[Path]:
    if not relative_path:
        return None
    root = _export_root()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def delete_export_files(task: AlertExportTask) -> None:
    resolved = resolve_export_file(getattr(task, 'file_path', None))
    if resolved and resolved.is_file():
        try:
            resolved.unlink()
        except OSError:
            logger.warning('Failed to delete export zip %s', resolved, exc_info=True)
        parent = resolved.parent
        root = _export_root()
        if parent != root and parent.is_dir() and not any(parent.iterdir()):
            shutil.rmtree(parent, ignore_errors=True)

    tmp_dir = _export_root() / 'tmp' / str(task.id)
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)


def find_active_export(username: str) -> Optional[AlertExportTask]:
    expire_stale_pending_tasks()
    return (
        AlertExportTask.select()
        .where(
            (AlertExportTask.created_by == username)
            & AlertExportTask.status.in_(ACTIVE_STATUSES)
        )
        .order_by(AlertExportTask.id.desc())
        .first()
    )


def expire_stale_pending_tasks(now: Optional[datetime] = None) -> int:
    """Fail pending tasks that were never claimed, so a dead jobs process cannot block retries."""
    now = now or _now()
    cutoff = now - timedelta(seconds=STALE_PENDING_SECONDS)
    stale = list(
        AlertExportTask.select().where(
            (AlertExportTask.status == 'pending')
            & (AlertExportTask.created_at < cutoff)
        )
    )
    if not stale:
        return 0
    updated = (
        AlertExportTask.update(
            status='failed',
            error_message='导出任务超时未开始，请确认后台 jobs 进程正在运行',
            locked_at=None,
            finished_at=now,
            expires_at=now + timedelta(days=EXPORT_RETENTION_DAYS),
        )
        .where(
            AlertExportTask.id.in_([task.id for task in stale])
            & (AlertExportTask.status == 'pending')
        )
        .execute()
    )
    return int(updated or 0)


def create_export_task(raw_filters: Optional[dict], *, username: str, is_admin: bool) -> AlertExportTask:
    if not username:
        raise ExportValidationError('未登录，无法导出', 401)

    active = find_active_export(username)
    if active is not None:
        raise ExportValidationError(
            f'已有导出任务 #{active.id} 正在进行，请到导出管理页面查看进度',
            409,
        )

    filters = parse_alert_filters(raw_filters)
    if not is_admin:
        filters['owner'] = username

    query = build_alert_query(filters)
    total = query.count()
    if total == 0:
        raise ExportValidationError('当前筛选条件下没有可导出的告警记录')
    if total > MAX_EXPORT_RECORDS:
        raise ExportValidationError(
            f'匹配 {total} 条，超过单次上限 {MAX_EXPORT_RECORDS} 条，请缩小筛选范围'
        )

    now = _now()
    return AlertExportTask.create(
        created_by=username,
        status='pending',
        filters_json=json.dumps(filters, ensure_ascii=False),
        filter_summary=build_filter_summary(filters),
        total_count=total,
        processed_count=0,
        missing_image_count=0,
        created_at=now,
    )


def _refresh_task(task: AlertExportTask) -> AlertExportTask:
    return AlertExportTask.get_by_id(task.id)


def _ensure_running(task: AlertExportTask) -> AlertExportTask:
    current = _refresh_task(task)
    if current.status != 'running':
        raise ExportCancelled()
    return current


def _touch_progress(
    task: AlertExportTask,
    *,
    processed_count: Optional[int] = None,
    missing_image_count: Optional[int] = None,
) -> AlertExportTask:
    now = _now()
    update = {AlertExportTask.locked_at: now}
    if processed_count is not None:
        update[AlertExportTask.processed_count] = processed_count
    if missing_image_count is not None:
        update[AlertExportTask.missing_image_count] = missing_image_count
    updated = (
        AlertExportTask.update(update)
        .where(
            (AlertExportTask.id == task.id)
            & (AlertExportTask.status == 'running')
        )
        .execute()
    )
    if not updated:
        raise ExportCancelled()
    return _refresh_task(task)


def _zip_image_name(alert_id: int, kind: str, source_path: str) -> str:
    suffix = Path(source_path).suffix.lower()
    if suffix not in {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff'}:
        suffix = '.jpg'
    return f'images/{alert_id}_{kind}{suffix}'


def _add_image(
    zf: zipfile.ZipFile,
    alert_id: int,
    kind: str,
    relative_path: Optional[str],
) -> tuple[str, bool]:
    if not relative_path:
        return '', False
    resolved = resolve_frame_media_path(relative_path)
    if resolved is None or not resolved.is_file():
        return '', True
    zip_name = _zip_image_name(alert_id, kind, str(resolved))
    zf.write(resolved, zip_name)
    return zip_name, False


def _related_or_none(alert, field_name: str):
    try:
        return getattr(alert, field_name, None)
    except Exception:
        return None


def _write_csv_row(writer: csv.DictWriter, alert, annotated: str, original: str) -> None:
    source = _related_or_none(alert, 'video_source')
    workflow = _related_or_none(alert, 'workflow')
    writer.writerow({
        'id': alert.id,
        'alert_time': alert.alert_time.isoformat() if alert.alert_time else '',
        'alert_type': alert.alert_type or '',
        'alert_level': alert.alert_level or '',
        'alert_message': alert.alert_message or '',
        'source_id': source.id if source else '',
        'source_name': source.name if source else '',
        'source_code': source.source_code if source else '',
        'workflow_id': workflow.id if workflow else '',
        'workflow_name': workflow.name if workflow else '',
        'detection_count': alert.detection_count if alert.detection_count is not None else '',
        'annotated_image': annotated,
        'original_image': original,
    })


def _write_readme(zf: zipfile.ZipFile, task: AlertExportTask, missing_image_count: int) -> None:
    created = task.created_at.strftime('%Y-%m-%d %H:%M:%S') if task.created_at else ''
    lines = [
        '告警记录导出',
        f'任务 ID: {task.id}',
        f'创建时间: {created}',
        f'创建人: {task.created_by}',
        f'筛选条件: {task.filter_summary or "全部告警"}',
        f'记录数: {task.total_count}',
        f'缺失图片: {missing_image_count}',
        '',
        'alerts.csv 为记录清单；images/ 下为对应标注图和原图。',
        '缺失的图片在 CSV 对应列留空。',
    ]
    zf.writestr('README.txt', '\n'.join(lines) + '\n')


def mark_task_running(task: AlertExportTask) -> AlertExportTask:
    now = _now()
    updated = (
        AlertExportTask.update(
            status='running',
            locked_at=now,
            started_at=now,
            error_message=None,
        )
        .where(
            (AlertExportTask.id == task.id)
            & (AlertExportTask.status == 'pending')
        )
        .execute()
    )
    if not updated:
        raise ExportValidationError('任务无法开始，状态已变化')
    return AlertExportTask.get_by_id(task.id)


def run_export_task(task: AlertExportTask) -> AlertExportTask:
    task = _ensure_running(task)
    filters = _task_filters(task)
    query = (
        build_alert_query(filters)
        .select(Alert, VideoSource, Workflow)
        .join(VideoSource)
        .switch(Alert)
        .join(Workflow, join_type=JOIN.LEFT_OUTER)
        .order_by(Alert.alert_time.desc(), Alert.id.desc())
    )

    stamp = (task.created_at or _now()).strftime('%Y%m%d_%H%M%S')
    file_name = f'alerts_export_{task.id}_{stamp}.zip'
    relative_path = f'{task.id}/{file_name}'

    tmp_dir = _export_root() / 'tmp' / str(task.id)
    final_dir = _export_root() / str(task.id)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_zip = tmp_dir / file_name

    processed = 0
    missing_image_count = 0

    try:
        with zipfile.ZipFile(tmp_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            csv_buffer = io.StringIO()
            writer = csv.DictWriter(csv_buffer, fieldnames=CSV_COLUMNS)
            writer.writeheader()

            for alert in query.iterator():
                task = _ensure_running(task)
                annotated, annotated_missing = _add_image(
                    zf, alert.id, 'annotated', getattr(alert, 'alert_image', None),
                )
                original, original_missing = _add_image(
                    zf, alert.id, 'original', getattr(alert, 'alert_image_ori', None),
                )
                missing_image_count += int(annotated_missing) + int(original_missing)
                _write_csv_row(writer, alert, annotated, original)
                processed += 1
                if processed == 1 or processed % PROGRESS_BATCH_SIZE == 0:
                    task = _touch_progress(
                        task,
                        processed_count=processed,
                        missing_image_count=missing_image_count,
                    )

            csv_bytes = ('\ufeff' + csv_buffer.getvalue()).encode('utf-8')
            zf.writestr('alerts.csv', csv_bytes)
            _write_readme(zf, task, missing_image_count)

        task = _ensure_running(task)
        final_dir.mkdir(parents=True, exist_ok=True)
        final_zip = final_dir / file_name
        if final_zip.exists():
            final_zip.unlink()
        shutil.move(str(tmp_zip), str(final_zip))
        file_size = final_zip.stat().st_size
        shutil.rmtree(tmp_dir, ignore_errors=True)

        now = _now()
        updated = (
            AlertExportTask.update(
                status='succeeded',
                processed_count=processed,
                missing_image_count=missing_image_count,
                file_name=file_name,
                file_path=relative_path,
                file_size=file_size,
                error_message=None,
                locked_at=None,
                finished_at=now,
                expires_at=now + timedelta(days=EXPORT_RETENTION_DAYS),
            )
            .where(
                (AlertExportTask.id == task.id)
                & (AlertExportTask.status == 'running')
            )
            .execute()
        )
        if not updated:
            delete_export_files(AlertExportTask(id=task.id, file_path=relative_path))
            raise ExportCancelled()
        return AlertExportTask.get_by_id(task.id)
    except ExportCancelled:
        delete_export_files(AlertExportTask(id=task.id, file_path=relative_path))
        raise
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def cancel_export_task(task: AlertExportTask) -> AlertExportTask:
    now = _now()
    updated = (
        AlertExportTask.update(
            status='cancelled',
            error_message='已取消',
            locked_at=None,
            finished_at=now,
        )
        .where(
            (AlertExportTask.id == task.id)
            & AlertExportTask.status.in_(ACTIVE_STATUSES)
        )
        .execute()
    )
    if not updated:
        current = AlertExportTask.get_by_id(task.id)
        if current.status in TERMINAL_STATUSES:
            raise ExportValidationError('任务已结束，无法取消')
        raise ExportValidationError('取消失败，请稍后重试')
    current = AlertExportTask.get_by_id(task.id)
    delete_export_files(current)
    return current


def delete_export_task(task: AlertExportTask) -> None:
    if task.status in ACTIVE_STATUSES:
        raise ExportValidationError('进行中的任务请先取消再删除')
    delete_export_files(task)
    task.delete_instance()


def cleanup_expired_exports(now: Optional[datetime] = None) -> int:
    now = now or _now()
    removed = 0
    expired = list(
        AlertExportTask.select().where(
            AlertExportTask.expires_at.is_null(False)
            & (AlertExportTask.expires_at <= now)
            & AlertExportTask.status.in_(TERMINAL_STATUSES)
        )
    )
    for task in expired:
        delete_export_files(task)
        task.delete_instance()
        removed += 1

    tmp_root = _export_root() / 'tmp'
    if tmp_root.is_dir():
        active_ids = {
            str(task.id)
            for task in AlertExportTask.select(AlertExportTask.id).where(
                AlertExportTask.status.in_(ACTIVE_STATUSES)
            )
        }
        for child in tmp_root.iterdir():
            if child.name not in active_ids:
                shutil.rmtree(child, ignore_errors=True)
    return removed


def _recover_stale_tasks(now: Optional[datetime] = None) -> None:
    now = now or _now()
    expire_stale_pending_tasks(now)
    cutoff = now - timedelta(seconds=LEASE_SECONDS)
    (
        AlertExportTask.update(
            status='pending',
            locked_at=None,
            started_at=None,
            processed_count=0,
            missing_image_count=0,
            file_name=None,
            file_path=None,
            file_size=None,
            error_message=None,
        )
        .where(
            (AlertExportTask.status == 'running')
            & (
                AlertExportTask.locked_at.is_null(True)
                | (AlertExportTask.locked_at < cutoff)
            )
        )
        .execute()
    )


def claim_next_export_task() -> Optional[AlertExportTask]:
    now = _now()
    _recover_stale_tasks(now)
    with db.atomic():
        task = (
            AlertExportTask.select()
            .where(AlertExportTask.status == 'pending')
            .order_by(AlertExportTask.id.asc())
            .first()
        )
        if task is None:
            return None
        updated = (
            AlertExportTask.update(
                status='running',
                locked_at=now,
                started_at=now,
                error_message=None,
            )
            .where(
                (AlertExportTask.id == task.id)
                & (AlertExportTask.status == 'pending')
            )
            .execute()
        )
        if not updated:
            return None
    return AlertExportTask.get_by_id(task.id)


def _fail_task(task: AlertExportTask, exc: Exception) -> None:
    now = _now()
    AlertExportTask.update(
        status='failed',
        error_message=str(exc)[:2000],
        locked_at=None,
        finished_at=now,
        expires_at=now + timedelta(days=EXPORT_RETENTION_DAYS),
    ).where(
        (AlertExportTask.id == task.id)
        & (AlertExportTask.status == 'running')
    ).execute()
    delete_export_files(task)


def process_claimed_task(task: AlertExportTask) -> AlertExportTask:
    try:
        with db.connection_context():
            return run_export_task(task)
    except ExportCancelled:
        current = AlertExportTask.get_by_id(task.id)
        delete_export_files(current)
        return current
    except Exception as exc:
        logger.exception('Alert export task %s failed', task.id)
        _fail_task(task, exc)
        return AlertExportTask.get_by_id(task.id)


def _slot_lock_path() -> Path:
    return _export_root() / '.export.lock'


def _try_acquire_slot():
    lock_path = _slot_lock_path()
    handle = open(lock_path, 'a+')
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _release_slot(handle) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


class AlertExportWorker:
    def __init__(self, *, poll_interval_seconds: float = POLL_INTERVAL_SECONDS):
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        os.makedirs(EXPORT_SAVE_PATH, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name='alert-export', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                cleanup_expired_exports()
                slot = _try_acquire_slot()
                if slot is None:
                    self._stop_event.wait(self.poll_interval_seconds)
                    continue
                try:
                    task = claim_next_export_task()
                    if task is None:
                        _release_slot(slot)
                        slot = None
                        self._stop_event.wait(self.poll_interval_seconds)
                        continue
                    process_claimed_task(task)
                finally:
                    _release_slot(slot)
            except Exception:
                logger.exception('Alert export worker loop failed')
                self._stop_event.wait(self.poll_interval_seconds)


def start_alert_export_worker() -> AlertExportWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = AlertExportWorker()
        _worker.start()
        return _worker


def stop_alert_export_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is not None:
            _worker.stop()
