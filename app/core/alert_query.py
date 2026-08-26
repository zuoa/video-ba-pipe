"""Shared alert list/export filter parsing and query construction."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from app.core.database_models import Alert, Workflow, VideoSource


def _as_int(value: Any) -> Optional[int]:
    if value is None or value is False:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


FILTER_KEYS = (
    'source_id',
    'workflow_id',
    'source_template_id',
    'alert_type',
    'start_time',
    'end_time',
)


def _first_value(raw: Mapping[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        if key not in raw:
            continue
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
            return value
        return str(value)
    return None


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # Alert.alert_time is stored as naive local time. Frontend toISOString()
    # is UTC; comparing aware vs naive raises on some drivers and shifts filters.
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def parse_alert_filters(raw: Optional[Mapping[str, Any]]) -> dict:
    """Parse request args / JSON into a persisted filter dict.

    Client-supplied owner is ignored; the API layer must set it.
    """
    raw = raw or {}
    filters: dict[str, Any] = {}

    source_id = _as_int(_first_value(raw, 'source_id', 'task_id'))
    if source_id is not None:
        filters['source_id'] = source_id

    workflow_id = _as_int(_first_value(raw, 'workflow_id'))
    if workflow_id is not None:
        filters['workflow_id'] = workflow_id

    source_template_id = _as_int(_first_value(raw, 'source_template_id'))
    if source_template_id is not None:
        filters['source_template_id'] = source_template_id

    alert_type = _first_value(raw, 'alert_type')
    if alert_type:
        filters['alert_type'] = alert_type

    start_dt = parse_iso_datetime(_first_value(raw, 'start_time'))
    if start_dt is not None:
        filters['start_time'] = start_dt.isoformat(timespec='seconds')

    end_dt = parse_iso_datetime(_first_value(raw, 'end_time'))
    if end_dt is not None:
        filters['end_time'] = end_dt.isoformat(timespec='seconds')

    return filters


def apply_alert_filters(query, filters: Optional[Mapping[str, Any]]):
    filters = filters or {}

    source_id = _as_int(filters.get('source_id'))
    if source_id is not None:
        query = query.where(Alert.video_source == source_id)

    workflow_id = _as_int(filters.get('workflow_id'))
    if workflow_id is not None:
        query = query.where(Alert.workflow == workflow_id)

    source_template_id = _as_int(filters.get('source_template_id'))
    if source_template_id is not None:
        derived_workflow_ids = Workflow.select(Workflow.id).where(
            Workflow.source_template == source_template_id
        )
        query = query.where(Alert.workflow.in_(derived_workflow_ids))

    alert_type = filters.get('alert_type')
    if alert_type:
        query = query.where(Alert.alert_type == alert_type)

    start_dt = parse_iso_datetime(filters.get('start_time'))
    if start_dt is not None:
        query = query.where(Alert.alert_time >= start_dt)

    end_dt = parse_iso_datetime(filters.get('end_time'))
    if end_dt is not None:
        query = query.where(Alert.alert_time <= end_dt)

    owner = filters.get('owner')
    if owner:
        query = query.where(Alert.created_by == owner)

    return query


def build_alert_query(filters: Optional[Mapping[str, Any]]):
    return apply_alert_filters(Alert.select(), filters)


def build_filter_summary(filters: Optional[Mapping[str, Any]]) -> str:
    filters = filters or {}
    parts: list[str] = []

    source_id = filters.get('source_id')
    if source_id:
        try:
            source = VideoSource.get_by_id(int(source_id))
            parts.append(f'视频源 {source.name}')
        except (TypeError, ValueError, VideoSource.DoesNotExist):
            parts.append(f'视频源 #{source_id}')

    workflow_id = filters.get('workflow_id')
    if workflow_id:
        try:
            workflow = Workflow.get_by_id(int(workflow_id))
            parts.append(f'流程编排 {workflow.name}')
        except (TypeError, ValueError, Workflow.DoesNotExist):
            parts.append(f'流程编排 #{workflow_id}')

    source_template_id = filters.get('source_template_id')
    if source_template_id:
        try:
            template = Workflow.get_by_id(int(source_template_id))
            parts.append(f'编排模板 {template.name}')
        except (TypeError, ValueError, Workflow.DoesNotExist):
            parts.append(f'编排模板 #{source_template_id}')

    alert_type = filters.get('alert_type')
    if alert_type:
        parts.append(f'类型 {alert_type}')

    start_time = filters.get('start_time')
    end_time = filters.get('end_time')
    if start_time or end_time:
        start_label = _display_datetime(start_time) if start_time else '不限'
        end_label = _display_datetime(end_time) if end_time else '不限'
        parts.append(f'{start_label} ~ {end_label}')

    return ' · '.join(parts) if parts else '全部告警'


def _display_datetime(value: str) -> str:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return value
    return parsed.strftime('%Y-%m-%d %H:%M:%S')
