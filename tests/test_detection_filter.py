import threading

import numpy as np
import pytest

from app.core.detection_filter import (
    DetectionFilterValidationError,
    filter_detections_by_size,
    normalize_detection_filter_config,
    validate_workflow_detection_filter_nodes,
)
from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_types import DetectionFilterNodeData, create_node_data


def test_absolute_filter_is_inclusive_and_drops_invalid_boxes():
    exact = {"box": [0, 0, 20, 40], "label": "exact"}
    larger_alias = {"bbox": [0, 0, 20, 41], "label": "larger"}
    too_small = {"xyxy": [0, 0, 20, 39], "label": "small"}
    missing = {"label": "semantic"}
    inverted = {"box": [20, 20, 10, 10], "label": "invalid"}

    filtered, stats = filter_detections_by_size(
        [exact, larger_alias, too_small, missing, inverted],
        {"dimension": "height", "unit": "pixel", "comparison": "gte", "threshold": 40},
        frame_width=1920,
        frame_height=1080,
    )

    assert filtered == [exact, larger_alias]
    assert stats == {
        "dimension": "height",
        "unit": "pixel",
        "comparison": "gte",
        "threshold": 40.0,
        "input_count": 5,
        "output_count": 2,
        "filtered_count": 3,
        "invalid_box_count": 2,
    }


def test_ratio_filter_uses_matching_frame_dimension_and_inclusive_maximum():
    detections = [
        {"box": [0, 0, 100, 50], "label": "half"},
        {"box": [0, 0, 101, 50], "label": "over"},
    ]

    filtered, stats = filter_detections_by_size(
        detections,
        {"dimension": "width", "unit": "ratio", "comparison": "lte", "threshold": 0.5},
        frame_width=200,
        frame_height=1000,
    )

    assert [item["label"] for item in filtered] == ["half"]
    assert stats["output_count"] == 1


@pytest.mark.parametrize(
    "config",
    [
        {"dimension": "area"},
        {"unit": "percent"},
        {"comparison": "gt"},
        {"threshold": -1},
        {"unit": "ratio", "threshold": 1.01},
        {"threshold": float("inf")},
        {"threshold": True},
    ],
)
def test_invalid_filter_config_is_rejected(config):
    with pytest.raises(DetectionFilterValidationError):
        normalize_detection_filter_config(config)


def test_workflow_validation_requires_one_supported_upstream():
    workflow = {
        "nodes": [
            {"id": "algo", "type": "algorithm"},
            {
                "id": "filter",
                "type": "detection_filter",
                "config": {
                    "dimension": "height",
                    "unit": "pixel",
                    "comparison": "gte",
                    "threshold": 40,
                },
            },
        ],
        "connections": [{"from": "algo", "to": "filter"}],
    }

    assert validate_workflow_detection_filter_nodes(workflow) == (True, None)

    workflow["connections"] = []
    valid, message = validate_workflow_detection_filter_nodes(workflow)
    assert valid is False
    assert "必须且只能连接一个" in message

    workflow["connections"] = [{"from": "algo", "to": "filter"}]
    workflow["nodes"][1]["config"].pop("threshold")
    valid, message = validate_workflow_detection_filter_nodes(workflow)
    assert valid is False
    assert "缺少完整" in message

    workflow["nodes"][0]["type"] = "source"
    workflow["nodes"][1]["config"]["threshold"] = 40
    workflow["connections"] = [{"from": "algo", "to": "filter"}]
    valid, message = validate_workflow_detection_filter_nodes(workflow)
    assert valid is False
    assert "上游必须是检测结果节点" in message


def test_create_node_data_normalizes_frontend_filter_type():
    node = create_node_data({
        "id": "filter",
        "type": "detectionFilter",
        "config": {"dimension": "width", "unit": "pixel", "comparison": "lte", "threshold": 80},
    })

    assert isinstance(node, DetectionFilterNodeData)
    assert node.node_type == "detection_filter"
    assert node.config["threshold"] == 80


def test_executor_filter_nodes_chain_and_preserve_metadata():
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = 99
    executor.test_mode = False
    executor._state_lock = threading.Lock()
    executor.connections = [
        {"from": "algo", "to": "min-height"},
        {"from": "min-height", "to": "max-width"},
    ]
    executor.nodes = {
        "min-height": DetectionFilterNodeData(
            node_id="min-height",
            config={"dimension": "height", "unit": "pixel", "comparison": "gte", "threshold": 40},
        ),
        "max-width": DetectionFilterNodeData(
            node_id="max-width",
            config={"dimension": "width", "unit": "pixel", "comparison": "lte", "threshold": 60},
        ),
    }
    detections = [
        {"box": [0, 0, 60, 40], "label": "keep"},
        {"box": [0, 0, 61, 40], "label": "wide"},
        {"box": [0, 0, 30, 39], "label": "short"},
    ]
    executor.node_results_cache = {
        "algo": {
            "node_id": "algo",
            "has_detection": True,
            "result": {"detections": detections, "metadata": {"model": "test"}, "custom": "kept"},
            "label_color": "#123456",
            "upstream_node_id": "algo",
            "roi_mask": None,
        }
    }
    context = {"frame": np.zeros((100, 200, 3), dtype=np.uint8)}

    first = executor._handle_detection_filter_node("min-height", context)
    second = executor._handle_detection_filter_node("max-width", context)

    assert [item["label"] for item in first["result"]["detections"]] == ["keep", "wide"]
    assert [item["label"] for item in second["result"]["detections"]] == ["keep"]
    assert second["has_detection"] is True
    assert second["result"]["custom"] == "kept"
    assert second["result"]["metadata"]["model"] == "test"
    assert [entry["node_id"] for entry in second["result"]["metadata"]["detection_filters"]] == [
        "min-height",
        "max-width",
    ]
