"""Trusted filesystem helpers for encrypted face-event snapshots.

User scripts are intentionally forbidden from opening files directly. This
module owns the fixed biometric-data root and performs atomic encrypted writes
so the built-in face workflow can persist snapshots without broad filesystem
access.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from app import logger
from app.config import FACE_EVENT_PATH
from app.core.face_crypto import encrypt_biometric


_SAFE_TRACK_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_track_id(track_id) -> str:
    value = _SAFE_TRACK_ID.sub("_", str(track_id or "track"))[:64]
    return value or "track"


def write_encrypted_face_event_snapshot(
    jpeg_bytes: bytes,
    track_id,
    *,
    occurred_at: Optional[datetime] = None,
) -> str:
    if not jpeg_bytes:
        raise ValueError("人脸事件抓拍不能为空")
    now = occurred_at or datetime.now()
    relative = Path(now.strftime("%Y%m%d")) / (
        f"{now.strftime('%H%M%S%f')}-{_safe_track_id(track_id)}.face"
    )
    target = Path(FACE_EVENT_PATH) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = encrypt_biometric(jpeg_bytes, purpose="face-event-snapshot")
    file_descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(file_descriptor, "wb") as handle:
            file_descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
    except Exception:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        try:
            os.remove(temporary_path)
        except FileNotFoundError:
            pass
        raise
    return relative.as_posix()


def remove_face_event_snapshot(snapshot_path: Optional[str]) -> bool:
    """Best-effort removal constrained to the configured face-event root."""
    if not snapshot_path:
        return True
    base = Path(FACE_EVENT_PATH).resolve()
    candidate = (base / str(snapshot_path)).resolve()
    if candidate != base and base not in candidate.parents:
        logger.warning("拒绝删除越界的人脸事件抓拍: %s", snapshot_path)
        return False
    try:
        candidate.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.warning("删除人脸事件抓拍失败 %s: %s", candidate, exc)
        return False
