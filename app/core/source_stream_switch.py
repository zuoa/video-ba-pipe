"""Persist deferred video-source URL switches shared by the API and worker."""

import json
from datetime import datetime

from app.core.database_models import SystemSetting
from app.core.video_probe import normalize_video_codec


_KEY_PREFIX = 'deferred_source_stream_switch:'


def _key(source_id: int) -> str:
    return f'{_KEY_PREFIX}{int(source_id)}'


def get_deferred_stream_switch(source_id: int) -> dict | None:
    setting = SystemSetting.get_or_none(SystemSetting.key == _key(source_id))
    if setting is None:
        return None
    try:
        value = json.loads(setting.value)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def get_matching_deferred_stream_switch(source) -> dict | None:
    """Return a marker only when it still targets the source's stored URL."""
    marker = get_deferred_stream_switch(source.id)
    if marker is None or marker.get('pending_url') != source.source_url:
        return None
    active_url = str(marker.get('active_url') or '').strip()
    if not active_url:
        return None
    return marker


def defer_stream_switch(
    source_id: int,
    *,
    active_url: str,
    active_codec: str | None,
    pending_url: str,
    requested_by: str,
) -> dict:
    """Keep the original active URL when a pending URL is edited repeatedly."""
    existing = get_deferred_stream_switch(source_id)
    if (
        existing
        and existing.get('active_url')
        and existing.get('pending_url') == active_url
    ):
        active_url = existing['active_url']
        active_codec = existing.get('active_codec', active_codec)

    now = datetime.now()
    marker = {
        'source_id': int(source_id),
        'active_url': active_url,
        'active_codec': normalize_video_codec(
            active_codec,
            allow_unknown=True,
        ),
        'pending_url': pending_url,
        'requested_at': now.isoformat(),
        'requested_by': requested_by,
    }
    value = json.dumps(marker, ensure_ascii=False)
    SystemSetting.insert(
        key=_key(source_id),
        value=value,
        description='视频源地址失效后切换',
        updated_at=now,
        updated_by=requested_by,
    ).on_conflict(
        conflict_target=[SystemSetting.key],
        update={
            SystemSetting.value: value,
            SystemSetting.updated_at: now,
            SystemSetting.updated_by: requested_by,
        },
    ).execute()
    return marker


def clear_deferred_stream_switch(source_id: int) -> bool:
    deleted = SystemSetting.delete().where(
        SystemSetting.key == _key(source_id)
    ).execute()
    return bool(deleted)
