"""Calendar-based alert statistics shared by dashboard endpoints."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable


ALERT_PERIODS = ('hour', 'day', 'week', 'month', 'year')


@dataclass(frozen=True)
class AlertPeriodSpec:
    period: str
    start: datetime
    end: datetime
    bucket_starts: tuple[datetime, ...]
    labels: tuple[str, ...]


def normalize_alert_period(period: str | None) -> str:
    return period if period in ALERT_PERIODS else 'day'


def get_alert_period_spec(period: str | None, now: datetime | None = None) -> AlertPeriodSpec:
    """Return calendar bounds and display buckets for an alert period."""
    normalized = normalize_alert_period(period)
    current = now or datetime.now()

    if normalized == 'hour':
        start = current.replace(minute=0, second=0, microsecond=0)
        bucket_starts = tuple(start + timedelta(minutes=index * 5) for index in range(12))
        labels = tuple(item.strftime('%H:%M') for item in bucket_starts)
        end = start + timedelta(hours=1)
    elif normalized == 'day':
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        bucket_starts = tuple(start + timedelta(hours=index) for index in range(24))
        labels = tuple(item.strftime('%H:00') for item in bucket_starts)
        end = start + timedelta(days=1)
    elif normalized == 'week':
        start = (current - timedelta(days=current.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        bucket_starts = tuple(start + timedelta(days=index) for index in range(7))
        labels = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')
        end = start + timedelta(days=7)
    elif normalized == 'month':
        start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        days_in_month = monthrange(start.year, start.month)[1]
        bucket_starts = tuple(start + timedelta(days=index) for index in range(days_in_month))
        labels = tuple(f'{item.day}日' for item in bucket_starts)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    else:
        start = current.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        bucket_starts = tuple(start.replace(month=index) for index in range(1, 13))
        labels = tuple(f'{index}月' for index in range(1, 13))
        end = start.replace(year=start.year + 1)

    return AlertPeriodSpec(
        period=normalized,
        start=start,
        end=end,
        bucket_starts=bucket_starts,
        labels=labels,
    )


def build_alert_trend(
    period: str | None,
    alert_times: Iterable[datetime],
    now: datetime | None = None,
) -> dict:
    """Aggregate alert timestamps into the calendar buckets used by the dashboard."""
    current = now or datetime.now()
    spec = get_alert_period_spec(period, now=current)
    counts = [0] * len(spec.bucket_starts)

    for alert_time in alert_times:
        if alert_time < spec.start or alert_time >= spec.end:
            continue

        if spec.period == 'hour':
            index = int((alert_time - spec.start).total_seconds() // 300)
        elif spec.period == 'day':
            index = alert_time.hour
        elif spec.period in ('week', 'month'):
            index = (alert_time.date() - spec.start.date()).days
        else:
            index = alert_time.month - 1

        if 0 <= index < len(counts):
            counts[index] += 1

    return {
        'period': spec.period,
        'start': spec.start.isoformat(),
        'end': spec.end.isoformat(),
        'buckets': [
            {
                'start': bucket_start.isoformat(),
                'label': label,
                'count': count,
                'is_future': bucket_start > current,
            }
            for bucket_start, label, count in zip(spec.bucket_starts, spec.labels, counts)
        ],
    }
