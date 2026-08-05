"""Weekly time-schedule validation and evaluation helpers."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


WEEKDAY_KEYS = tuple(str(day) for day in range(1, 8))
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _minute_of_day(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def validate_weekly_schedule(schedule: Any) -> Tuple[bool, Optional[str]]:
    """Validate the persisted ISO-weekday schedule shape."""
    if not isinstance(schedule, dict):
        return False, "weeklySchedule 必须是对象"

    unknown_days = sorted(set(schedule) - set(WEEKDAY_KEYS))
    if unknown_days:
        return False, f"包含无效星期: {', '.join(unknown_days)}"

    missing_days = [day for day in WEEKDAY_KEYS if day not in schedule]
    if missing_days:
        return False, f"缺少星期配置: {', '.join(missing_days)}"

    period_count = 0
    for day in WEEKDAY_KEYS:
        periods = schedule.get(day)
        if not isinstance(periods, list):
            return False, f"星期 {day} 的时段必须是数组"

        for index, period in enumerate(periods, start=1):
            if not isinstance(period, dict):
                return False, f"星期 {day} 的第 {index} 个时段必须是对象"

            start = period.get("start")
            end = period.get("end")
            if not isinstance(start, str) or not TIME_PATTERN.fullmatch(start):
                return False, f"星期 {day} 的第 {index} 个开始时间格式无效"
            if not isinstance(end, str) or not TIME_PATTERN.fullmatch(end):
                return False, f"星期 {day} 的第 {index} 个结束时间格式无效"
            if _minute_of_day(start) > _minute_of_day(end):
                return False, f"星期 {day} 的第 {index} 个时段不可跨日，请拆成两段"
            period_count += 1

    if period_count == 0:
        return False, "至少需要配置一个启用时段"

    return True, None


def evaluate_weekly_schedule(
    schedule: Dict[str, Any],
    current_time: Optional[datetime] = None,
) -> Tuple[bool, Optional[Dict[str, str]]]:
    """Return whether the server-local minute is inside any inclusive period."""
    now = current_time or datetime.now().astimezone()
    weekday = str(now.isoweekday())
    current_minute = now.hour * 60 + now.minute

    for period in schedule.get(weekday, []):
        start = period["start"]
        end = period["end"]
        if _minute_of_day(start) <= current_minute <= _minute_of_day(end):
            return True, {"start": start, "end": end}

    return False, None


def validate_workflow_time_schedule_nodes(workflow_data: Any) -> Tuple[bool, Optional[str]]:
    """Validate every time-schedule node in a workflow payload."""
    if not isinstance(workflow_data, dict):
        return True, None

    for node in workflow_data.get("nodes", []):
        if not isinstance(node, dict) or node.get("type") != "time_schedule":
            continue
        data = node.get("data") or {}
        valid, error = validate_weekly_schedule(data.get("weeklySchedule"))
        if not valid:
            name = node.get("name") or node.get("id") or "时间启用区间"
            return False, f"时间节点 {name}: {error}"

    return True, None
