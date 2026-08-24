from datetime import datetime

import pytest

from app.core.alert_stats import build_alert_trend, get_alert_period_spec


@pytest.mark.parametrize(
    ('period', 'expected_start', 'expected_end', 'bucket_count'),
    [
        ('hour', datetime(2026, 8, 24, 14), datetime(2026, 8, 24, 15), 12),
        ('day', datetime(2026, 8, 24), datetime(2026, 8, 25), 24),
        ('week', datetime(2026, 8, 24), datetime(2026, 8, 31), 7),
        ('month', datetime(2026, 8, 1), datetime(2026, 9, 1), 31),
        ('year', datetime(2026, 1, 1), datetime(2027, 1, 1), 12),
    ],
)
def test_alert_period_spec_uses_calendar_ranges(period, expected_start, expected_end, bucket_count):
    spec = get_alert_period_spec(period, now=datetime(2026, 8, 24, 14, 37, 25))

    assert spec.start == expected_start
    assert spec.end == expected_end
    assert len(spec.bucket_starts) == bucket_count
    assert len(spec.labels) == bucket_count


@pytest.mark.parametrize(
    ('period', 'alert_time', 'bucket_index'),
    [
        ('hour', datetime(2026, 8, 24, 14, 17), 3),
        ('day', datetime(2026, 8, 24, 14, 17), 14),
        ('week', datetime(2026, 8, 27, 14, 17), 3),
        ('month', datetime(2026, 8, 24, 14, 17), 23),
        ('year', datetime(2026, 8, 24, 14, 17), 7),
    ],
)
def test_alert_trend_places_alert_in_expected_bucket(period, alert_time, bucket_index):
    result = build_alert_trend(
        period,
        [alert_time],
        now=datetime(2026, 8, 24, 14, 37, 25),
    )

    assert result['buckets'][bucket_index]['count'] == 1
    assert sum(bucket['count'] for bucket in result['buckets']) == 1


def test_alert_trend_excludes_values_outside_selected_period():
    result = build_alert_trend(
        'hour',
        [
            datetime(2026, 8, 24, 13, 59, 59),
            datetime(2026, 8, 24, 14, 0),
            datetime(2026, 8, 24, 14, 59, 59),
            datetime(2026, 8, 24, 15, 0),
        ],
        now=datetime(2026, 8, 24, 14, 37, 25),
    )

    assert sum(bucket['count'] for bucket in result['buckets']) == 2


def test_invalid_period_falls_back_to_day():
    result = build_alert_trend('quarter', [], now=datetime(2026, 8, 24, 14, 37, 25))

    assert result['period'] == 'day'
    assert len(result['buckets']) == 24


def test_alert_trend_marks_future_buckets_without_removing_them():
    result = build_alert_trend('week', [], now=datetime(2026, 8, 26, 14, 37, 25))

    assert [bucket['is_future'] for bucket in result['buckets']] == [
        False,
        False,
        False,
        True,
        True,
        True,
        True,
    ]
