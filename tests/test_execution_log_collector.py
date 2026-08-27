from app.core.execution_log_collector import ExecutionLogCollector
from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_types import AlertNodeData


def test_direct_detection_branch_is_not_discarded_and_contains_target_details():
    collector = ExecutionLogCollector()
    collector.add_detection_result(
        "algorithm-1",
        [
            {"label_name": "person", "confidence": 0.91},
            {"label_name": "person", "confidence": 0.82},
            {"label_name": "phone", "confidence": 0.76},
        ],
        node_name="人员与手机检测",
    )

    message = collector.build_alert_message(format_type="detailed")

    assert "检测步骤 1: ✓ 命中" in message
    assert "人员与手机检测：命中 3 个目标" in message
    assert "person × 2" in message
    assert "phone × 1" in message
    assert "76.0%–91.0%" in message
    assert message != "无执行日志"


def test_detection_summary_includes_track_id_and_dwell():
    collector = ExecutionLogCollector()
    collector.add_detection_result(
        "algorithm-loiter",
        [
            {
                "label_name": "person",
                "confidence": 0.77,
                "track_id": 3,
                "attributes": {
                    "event": "loiter",
                    "event_label": "徘徊",
                    "dwell_seconds": 8.2,
                },
            }
        ],
        node_name="徘徊",
    )

    message = collector.build_alert_message(format_type="detailed")

    assert "徘徊：命中 1 个目标" in message
    assert "目标：person#3 徘徊 8s" in message
    assert "类别：" not in message
    assert "77.0%" in message


def test_hit_without_detection_boxes_is_rendered_as_hit():
    collector = ExecutionLogCollector()
    collector.add_detection_result(
        "external-api-1",
        [],
        node_name="远程行为识别",
        has_detection=True,
    )

    message = collector.build_alert_message(format_type="simple")

    assert "检测步骤 1: ✓ 命中" in message
    assert "远程行为识别：命中，但未返回目标明细" in message
    assert "未命中" not in message


def test_hit_without_detection_boxes_has_truthful_trigger_reason():
    message = WorkflowExecutor._format_direct_trigger_log(True, 0)

    assert "上游命中信号为真" in message
    assert "未返回目标明细" in message
    assert "上游有效结果 0 个" not in message


def test_detection_and_condition_are_rendered_as_trigger_chain():
    collector = ExecutionLogCollector()
    collector.add_detection_result(
        "algorithm-1",
        [{"label_name": "person", "confidence": 0.93}] * 2,
        node_name="人员检测",
    )
    collector.add_info(
        "condition-1",
        "条件判断: 2 >= 2 = ✓ 通过",
        metadata={
            "event_type": "condition",
            "condition_passed": True,
            "detection_count": 2,
            "target_count": 2,
        },
    )

    message = collector.build_alert_message(format_type="simple")

    assert "分支 1: ✓ 触发预警" in message
    assert "人员检测：命中 2 个目标" in message
    assert "2 >= 2 = ✓ 通过" in message


def test_default_placeholder_is_replaced_by_real_execution_details():
    collector = ExecutionLogCollector()
    collector.add_detection_result(
        "algorithm-1",
        [{"label_name": "person", "confidence": 0.88}],
        node_name="人员检测",
    )
    collector.add_info(
        "alert-1",
        "当前帧触发条件通过：上游有效结果 1 个，未启用时间窗口",
        metadata={"event_type": "trigger", "condition_passed": True},
    )
    alert_node = AlertNodeData(
        node_id="alert-1",
        alert_message="检测到目标",
        message_format="simple",
    )

    message = WorkflowExecutor._compose_alert_message(alert_node, collector)

    assert not message.startswith("检测到目标")
    assert "人员检测：命中 1 个目标" in message
    assert "当前帧触发条件通过" in message


def test_alert_message_only_contains_logs_from_its_upstream_branch():
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.connections = [
        {"from": "algorithm-a", "to": "alert-a"},
        {"from": "algorithm-b", "to": "alert-b"},
    ]
    collector = ExecutionLogCollector()
    collector.add_detection_result(
        "algorithm-a",
        [{"label_name": "person", "confidence": 0.92}],
        node_name="A 分支人员检测",
    )
    collector.add_detection_result(
        "algorithm-b",
        [{"label_name": "phone", "confidence": 0.89}],
        node_name="B 分支手机检测",
    )
    collector.add_info("alert-a", "A 分支触发条件通过", {"event_type": "trigger"})
    collector.add_info("alert-b", "B 分支触发条件通过", {"event_type": "trigger"})
    alert_node = AlertNodeData(
        node_id="alert-a",
        alert_message="检测到目标",
        message_format="simple",
    )

    message = executor._compose_alert_message(
        alert_node,
        collector,
        executor._get_alert_log_scope("alert-a"),
    )

    assert "A 分支人员检测" in message
    assert "A 分支触发条件通过" in message
    assert "B 分支手机检测" not in message
    assert "B 分支触发条件通过" not in message


def test_window_trigger_log_records_stats_and_rule():
    message = WorkflowExecutor._format_window_trigger_log(
        {"enable": True, "mode": "ratio", "threshold": 0.6, "window_size": 10},
        {
            "window_size": 10,
            "total_count": 8,
            "detection_count": 5,
            "detection_ratio": 0.625,
            "max_consecutive": 3,
        },
    )

    assert "10 秒内处理 8 帧" in message
    assert "命中 5 帧（62.5%）" in message
    assert "最大连续命中 3 帧" in message
    assert "命中比例 ≥ 60.0%" in message
