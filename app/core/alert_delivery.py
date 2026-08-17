"""Persistent asynchronous alert delivery for URL, inline and S3 media modes."""

from __future__ import annotations

import base64
import io
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from PIL import Image
from peewee import fn

from app.config import FRAME_SAVE_PATH
from app.core.database_models import Alert, AlertDeliveryTask, db
from app.core.message_queue_config import get_message_queue_config
from app.core.message_queue_publisher import publish_alert_to_mq
from app.core.node_identity import get_node_id
from app.core.object_storage import build_alert_object_key, upload_alert_image
from app.core.public_media_config import PublicMediaConfig, get_public_media_config
from app.core.rabbitmq_publisher import format_alert_message


logger = logging.getLogger(__name__)
_LEASE_SECONDS = 300


def enqueue_alert_delivery(alert: Alert, *, delivery_mode: Optional[str] = None) -> Optional[AlertDeliveryTask]:
    if not get_message_queue_config().enabled:
        return None
    config = get_public_media_config()
    now = datetime.now()
    task, _ = AlertDeliveryTask.get_or_create(
        alert=alert,
        event_type="alert.created",
        defaults={
            "delivery_mode": delivery_mode or config.delivery_mode,
            "status": "pending",
            "attempts": 0,
            "next_attempt_at": now,
            "created_at": now,
            "updated_at": now,
        },
    )
    return task


def get_delivery_stats() -> Dict[str, int]:
    counts = {"pending": 0, "processing": 0, "retrying": 0, "failed": 0}
    query = (
        AlertDeliveryTask.select(AlertDeliveryTask.status, fn.COUNT(AlertDeliveryTask.id).alias("count"))
        .where(AlertDeliveryTask.status.in_(tuple(counts)))
        .group_by(AlertDeliveryTask.status)
    )
    for row in query:
        counts[row.status] = int(row.count)
    return counts


def retry_failed_deliveries() -> int:
    now = datetime.now()
    return (
        AlertDeliveryTask.update(
            status="pending",
            attempts=0,
            next_attempt_at=now,
            locked_at=None,
            last_error=None,
            updated_at=now,
            completed_at=None,
        )
        .where(AlertDeliveryTask.status == "failed")
        .execute()
    )


def has_unfinished_delivery_for_path(relative_path: Optional[str]) -> bool:
    if not relative_path:
        return False
    return (
        AlertDeliveryTask.select(AlertDeliveryTask.id)
        .join(Alert)
        .where(
            (Alert.alert_image == relative_path)
            & AlertDeliveryTask.status.in_(("pending", "processing", "retrying"))
        )
        .exists()
    )


def _local_alert_image_path(alert: Alert) -> str:
    relative_path = str(alert.alert_image or "").replace("\\", "/").lstrip("/")
    if not relative_path:
        raise FileNotFoundError("告警没有可交付的标注图")
    base_path = os.path.abspath(FRAME_SAVE_PATH)
    full_path = os.path.abspath(os.path.join(base_path, relative_path))
    if not full_path.startswith(base_path + os.sep):
        raise ValueError("告警图片路径越界")
    if not os.path.isfile(full_path):
        raise FileNotFoundError(f"告警图片不存在: {relative_path}")
    return full_path


def _inline_image(config: PublicMediaConfig, alert: Alert) -> Dict[str, Any]:
    path = _local_alert_image_path(alert)
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((config.inline_max_edge, config.inline_max_edge), Image.Resampling.LANCZOS)
        quality = config.inline_jpeg_quality
        while True:
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            raw = output.getvalue()
            encoded = base64.b64encode(raw).decode("ascii")
            if len(encoded.encode("ascii")) <= config.inline_max_bytes:
                return {
                    "kind": "inline",
                    "content_type": "image/jpeg",
                    "encoding": "base64",
                    "size_bytes": len(raw),
                    "encoded_size_bytes": len(encoded),
                    "data": encoded,
                }
            if quality > 35:
                quality = max(35, quality - 10)
                continue
            width, height = image.size
            if max(width, height) <= 320:
                return {}
            image = image.resize(
                (max(1, int(width * 0.8)), max(1, int(height * 0.8))),
                Image.Resampling.LANCZOS,
            )


def _event_id(message: Dict[str, Any], event_type: str) -> str:
    return f"{message['external_alert_id']}:{event_type.replace('.', '-')}"


def _base_event(alert: Alert, *, event_type: str, delivery_mode: str) -> Dict[str, Any]:
    message = format_alert_message(alert)
    message.update({
        "event_id": _event_id(message, event_type),
        "event_type": event_type,
        "media_delivery_mode": delivery_mode,
    })
    return message


def _publish_or_raise(event: Dict[str, Any]) -> None:
    if not publish_alert_to_mq(event):
        raise RuntimeError("消息队列发布失败或已停用")


def _created_event(task: AlertDeliveryTask, alert: Alert, config: PublicMediaConfig) -> Dict[str, Any]:
    event = _base_event(alert, event_type="alert.created", delivery_mode=task.delivery_mode)
    if task.delivery_mode == "url":
        event["media"] = {
            "status": "ready" if event.get("alert_image_url") else "unavailable",
            "image": {"kind": "url", "url": event.get("alert_image_url")} if event.get("alert_image_url") else None,
        }
        return event

    # Local paths remain useful identifiers, but unreachable local URLs must not be advertised.
    event["alert_image_url"] = None
    event["alert_image_ori_url"] = None
    event["alert_video_url"] = None
    if task.delivery_mode == "inline":
        image = {}
        inline_error_code = "no_image"
        if alert.alert_image:
            try:
                image = _inline_image(config, alert)
                inline_error_code = "inline_too_large"
            except (OSError, ValueError) as exc:
                # Media failures must not suppress the alert itself. The local path may
                # exist in the record even when the write failed or the file is corrupt.
                inline_error_code = "inline_unreadable"
                logger.warning("内嵌告警图片不可读，降级为纯文字告警 alert=%s: %s", alert.id, exc)
        event["media"] = (
            {"status": "ready", "image": image}
            if image
            else {
                "status": "unavailable",
                "image": None,
                "error_code": inline_error_code,
            }
        )
    else:
        event["media"] = (
            {"status": "pending", "image": None}
            if alert.alert_image
            else {"status": "unavailable", "image": None, "error_code": "no_image"}
        )
    return event


def _media_ready_event(task: AlertDeliveryTask, alert: Alert, config: PublicMediaConfig) -> Dict[str, Any]:
    local_path = _local_alert_image_path(alert)
    base_message = format_alert_message(alert)
    occurred_at = alert.alert_time if isinstance(alert.alert_time, datetime) else datetime.now()
    object_key = build_alert_object_key(
        config,
        node_id=get_node_id(),
        external_alert_id=base_message["external_alert_id"],
        occurred_at=occurred_at,
    )
    uploaded = upload_alert_image(config, local_path=local_path, object_key=object_key)
    event = _base_event(alert, event_type="alert.media.ready", delivery_mode="object_storage")
    event["alert_image_url"] = uploaded.url
    event["alert_image_ori_url"] = None
    event["alert_video_url"] = None
    event["media"] = {
        "status": "ready",
        "image": {
            "kind": "url",
            "url": uploaded.url,
            "object_key": uploaded.object_key,
            "expires_at": uploaded.expires_at.isoformat(),
        },
    }
    return event


class AlertDeliveryWorker:
    def __init__(self, *, poll_interval_seconds: float = 1.0):
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._recover_stale_tasks(force=True)
        self._thread = threading.Thread(target=self._run, name="alert-delivery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _recover_stale_tasks(self, *, force: bool = False) -> None:
        now = datetime.now()
        predicate = AlertDeliveryTask.status == "processing"
        if not force:
            predicate &= (
                AlertDeliveryTask.locked_at.is_null(True)
                | (AlertDeliveryTask.locked_at < now - timedelta(seconds=_LEASE_SECONDS))
            )
        (
            AlertDeliveryTask.update(
                status="retrying", locked_at=None, next_attempt_at=now, updated_at=now
            )
            .where(predicate)
            .execute()
        )

    def _claim_next(self) -> Optional[AlertDeliveryTask]:
        now = datetime.now()
        with db.atomic():
            task = (
                AlertDeliveryTask.select()
                .where(
                    AlertDeliveryTask.status.in_(("pending", "retrying"))
                    & (AlertDeliveryTask.next_attempt_at <= now)
                )
                .order_by(AlertDeliveryTask.next_attempt_at, AlertDeliveryTask.id)
                .first()
            )
            if task is None:
                return None
            updated = (
                AlertDeliveryTask.update(status="processing", locked_at=now, updated_at=now)
                .where(
                    (AlertDeliveryTask.id == task.id)
                    & AlertDeliveryTask.status.in_(("pending", "retrying"))
                )
                .execute()
            )
            if not updated:
                return None
        return AlertDeliveryTask.get_by_id(task.id)

    def _complete(self, task: AlertDeliveryTask) -> None:
        now = datetime.now()
        task.status = "succeeded"
        task.locked_at = None
        task.last_error = None
        task.updated_at = now
        task.completed_at = now
        task.save()

    def _fail(self, task: AlertDeliveryTask, exc: Exception, config: PublicMediaConfig) -> None:
        now = datetime.now()
        attempts = task.attempts + 1
        task.attempts = attempts
        task.locked_at = None
        task.last_error = str(exc)[:2000]
        task.updated_at = now
        if attempts >= config.async_max_attempts:
            task.status = "failed"
            task.completed_at = now
            logger.error("告警异步投递最终失败 task=%s: %s", task.id, exc)
        else:
            delay = min(
                config.async_max_backoff_seconds,
                config.async_initial_backoff_seconds * (2 ** (attempts - 1)),
            )
            task.status = "retrying"
            task.next_attempt_at = now + timedelta(seconds=delay)
            logger.warning("告警异步投递待重试 task=%s attempt=%s: %s", task.id, attempts, exc)
        task.save()

    def _process(self, task: AlertDeliveryTask) -> None:
        alert = Alert.get_by_id(task.alert_id)
        config = get_public_media_config()
        if task.event_type == "alert.created":
            event = _created_event(task, alert, config)
            _publish_or_raise(event)
            if task.delivery_mode == "object_storage" and alert.alert_image:
                now = datetime.now()
                AlertDeliveryTask.get_or_create(
                    alert=alert,
                    event_type="alert.media.ready",
                    defaults={
                        "delivery_mode": "object_storage",
                        "status": "pending",
                        "attempts": 0,
                        "next_attempt_at": now,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
        elif task.event_type == "alert.media.ready":
            _publish_or_raise(_media_ready_event(task, alert, config))
        else:
            raise ValueError(f"不支持的投递事件类型: {task.event_type}")
        self._complete(task)

    def run_once(self) -> bool:
        # Recover tasks stranded by database errors without requiring a process restart.
        # A lease prevents another healthy worker from having its active task stolen.
        self._recover_stale_tasks()
        task = self._claim_next()
        if task is None:
            return False
        try:
            self._process(task)
        except Exception as exc:
            self._fail(task, exc, get_public_media_config())
        return True

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self.run_once():
                    continue
            except Exception as exc:  # pragma: no cover - defensive worker boundary
                logger.exception("告警异步投递 worker 异常: %s", exc)
            self._stop_event.wait(self.poll_interval_seconds)


alert_delivery_worker = AlertDeliveryWorker()
