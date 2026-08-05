import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.core.time_schedule import (
    evaluate_weekly_schedule,
    validate_weekly_schedule,
    validate_workflow_time_schedule_nodes,
)
from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_types import NodeContext, SourceNodeData, TimeScheduleNodeData


def _schedule(**overrides):
    schedule = {str(day): [] for day in range(1, 8)}
    schedule["1"] = [{"start": "08:30", "end": "12:00"}]
    schedule.update(overrides)
    return schedule


def _executor(schedule, connections):
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = 1
    executor.nodes = {
        "source": SourceNodeData(node_id="source"),
        "gate": TimeScheduleNodeData(node_id="gate", weekly_schedule=schedule),
        "child": NodeContext(node_id="child", node_type="algorithm"),
    }
    executor.execution_graph = defaultdict(list)
    for source, target in connections:
        executor.execution_graph[source].append({"target": target, "condition": None})
    executor._state_lock = threading.Lock()
    executor.node_results_cache = {}
    return executor


def test_schedule_uses_iso_weekday_and_inclusive_minute_boundaries():
    china_tz = timezone(timedelta(hours=8))
    schedule = _schedule()

    assert evaluate_weekly_schedule(schedule, datetime(2026, 8, 3, 8, 30, 0, tzinfo=china_tz))[0] is True
    assert evaluate_weekly_schedule(schedule, datetime(2026, 8, 3, 12, 0, 59, tzinfo=china_tz))[0] is True
    assert evaluate_weekly_schedule(schedule, datetime(2026, 8, 3, 12, 1, 0, tzinfo=china_tz))[0] is False
    assert evaluate_weekly_schedule(schedule, datetime(2026, 8, 4, 9, 0, 0, tzinfo=china_tz))[0] is False


def test_schedule_validation_rejects_cross_day_empty_and_invalid_shape():
    valid, error = validate_weekly_schedule(_schedule(**{"1": [{"start": "22:00", "end": "02:00"}]}))
    assert valid is False
    assert "不可跨日" in error

    valid, error = validate_weekly_schedule({str(day): [] for day in range(1, 8)})
    assert valid is False
    assert "至少需要" in error

    valid, error = validate_weekly_schedule(_schedule(**{"8": []}))
    assert valid is False
    assert "无效星期" in error


def test_workflow_schedule_validation_includes_node_name():
    valid, error = validate_workflow_time_schedule_nodes({
        "nodes": [{
            "id": "gate-1",
            "name": "夜间启用",
            "type": "time_schedule",
            "data": {"weeklySchedule": {str(day): [] for day in range(1, 8)}},
        }],
    })

    assert valid is False
    assert "夜间启用" in error


def test_disabled_gate_blocks_exclusive_descendants():
    executor = _executor(_schedule(), [("source", "gate"), ("gate", "child")])
    context = {"_time_schedule_now": datetime(2026, 8, 3, 7, 0).astimezone()}

    executor._prepare_time_schedule_gates(context)

    assert context["_time_schedule_results"]["gate"]["enabled"] is False
    assert context["_time_schedule_blocked_nodes"] == {"child"}


def test_merge_node_remains_available_through_alternate_path():
    executor = _executor(
        _schedule(),
        [("source", "gate"), ("gate", "child"), ("source", "child")],
    )
    context = {"_time_schedule_now": datetime(2026, 8, 3, 7, 0).astimezone()}

    executor._prepare_time_schedule_gates(context)

    assert context["_time_schedule_blocked_nodes"] == set()


def test_gate_handler_returns_none_when_disabled_and_records_result():
    executor = _executor(_schedule(), [("source", "gate"), ("gate", "child")])
    context = {"_time_schedule_now": datetime(2026, 8, 3, 7, 0).astimezone()}
    executor._prepare_time_schedule_gates(context)

    result = executor._handle_time_schedule_node("gate", context)

    assert result is None
    assert executor.node_results_cache["gate"]["enabled"] is False
