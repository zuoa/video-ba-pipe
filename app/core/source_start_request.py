"""Durable, per-source manual start requests shared by the API and worker."""

import json
import uuid
from datetime import datetime

from app.core.database_models import SystemSetting


_KEY_PREFIX = 'manual_source_start:'


def _key(source_id: int) -> str:
    return f'{_KEY_PREFIX}{int(source_id)}'


def get_source_start_request(source_id: int) -> dict | None:
    setting = SystemSetting.get_or_none(SystemSetting.key == _key(source_id))
    if setting is None:
        return None
    try:
        return json.loads(setting.value)
    except (TypeError, ValueError):
        return None


def queue_source_start(source_id: int, requested_by: str) -> dict:
    request = {
        'request_id': uuid.uuid4().hex,
        'source_id': int(source_id),
        'status': 'pending',
        'requested_at': datetime.now().isoformat(),
        'requested_by': requested_by,
    }
    value = json.dumps(request, ensure_ascii=False)
    SystemSetting.insert(
        key=_key(source_id),
        value=value,
        description='手动启动视频源请求',
        updated_at=datetime.now(),
        updated_by=requested_by,
    ).on_conflict(
        conflict_target=[SystemSetting.key],
        update={
            SystemSetting.value: value,
            SystemSetting.updated_at: datetime.now(),
            SystemSetting.updated_by: requested_by,
        },
    ).execute()
    return request


def pending_source_starts() -> list[dict]:
    requests = []
    query = SystemSetting.select().where(
        SystemSetting.key.startswith(_KEY_PREFIX)
    ).order_by(SystemSetting.updated_at)
    for setting in query:
        try:
            request = json.loads(setting.value)
        except (TypeError, ValueError):
            continue
        if request.get('status') == 'pending':
            request['_stored_value'] = setting.value
            requests.append(request)
    return requests


def finish_source_start(request: dict, status: str, error: str | None = None) -> bool:
    """Update only the request observed by the worker; retain a newer click."""
    if status not in {'started', 'failed'}:
        raise ValueError(f'Invalid manual start status: {status}')
    result = {key: value for key, value in request.items() if not key.startswith('_')}
    result['status'] = status
    result['finished_at'] = datetime.now().isoformat()
    if error:
        result['error'] = error
    updated = SystemSetting.update(
        value=json.dumps(result, ensure_ascii=False),
        updated_at=datetime.now(),
    ).where(
        (SystemSetting.key == _key(request['source_id']))
        & (SystemSetting.value == request['_stored_value'])
    ).execute()
    return bool(updated)
