import numpy as np

from app.core.workflow_executor import WorkflowExecutor


class _FakeAlgorithm:
    def __init__(self, result):
        self._result = result

    def process(self, frame, roi_regions, upstream_results=None):
        return dict(self._result)


def _build_executor(node_config=None, node_extra=None, result=None):
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    node_dict = {
        "id": "algo_1",
        "type": "algorithm",
        "config": node_config or {},
    }
    if node_extra:
        node_dict.update(node_extra)

    executor.workflow_id = 1
    executor.workflow_data = {"nodes": [node_dict]}
    executor.algorithms = {
        "algo_1": _FakeAlgorithm(result or {"detections": []})
    }
    executor.algorithm_roi_configs = {}
    executor.algorithm_configs = {
        "algo_1": {"algorithm_id": 101}
    }
    executor.algorithm_datamap = {
        "algo_1": {"name": "fake", "label_color": "#FF0000"}
    }
    return executor


def test_process_algorithm_filters_detections_by_config_confidence():
    executor = _build_executor(
        node_config={"confidence": 0.7},
        result={
            "detections": [
                {"box": [1, 1, 5, 5], "confidence": 0.95, "label_name": "person"},
                {"box": [10, 10, 20, 20], "confidence": 0.22, "label_name": "person"},
            ],
            "metadata": {},
        },
    )

    result = executor._process_algorithm(
        node_id="algo_1",
        frame=np.zeros((32, 32, 3), dtype=np.uint8),
        frame_timestamp=0.0,
        roi_regions=None,
        upstream_results={},
    )

    detections = result["result"]["detections"]
    metadata = result["result"]["metadata"]

    assert len(detections) == 1
    assert detections[0]["confidence"] == 0.95
    assert metadata["confidence_threshold"] == 0.7
    assert metadata["confidence_filtered_count"] == 1


def test_process_algorithm_supports_legacy_top_level_confidence():
    executor = _build_executor(
        node_config={},
        node_extra={"confidence": 0.8},
        result={
            "detections": [
                {"box": [1, 1, 5, 5], "confidence": 0.81, "label_name": "person"},
                {"box": [10, 10, 20, 20], "confidence": 0.79, "label_name": "person"},
            ]
        },
    )

    result = executor._process_algorithm(
        node_id="algo_1",
        frame=np.zeros((32, 32, 3), dtype=np.uint8),
        frame_timestamp=0.0,
        roi_regions=None,
        upstream_results={},
    )

    detections = result["result"]["detections"]

    assert len(detections) == 1
    assert detections[0]["confidence"] == 0.81


def test_process_algorithm_result_does_not_retain_frame_payload():
    executor = _build_executor(
        node_config={"confidence": 0.5},
        result={
            "detections": [
                {"box": [1, 1, 5, 5], "confidence": 0.9, "label_name": "person"},
            ]
        },
    )

    result = executor._process_algorithm(
        node_id="algo_1",
        frame=np.zeros((32, 32, 3), dtype=np.uint8),
        frame_timestamp=123.0,
        roi_regions=None,
        upstream_results={},
    )

    assert "frame" not in result
    assert "frame_timestamp" not in result


def test_process_algorithm_filters_stage_boxes_by_config_confidence():
    executor = _build_executor(
        node_config={"confidence": 0.7},
        result={
            "detections": [
                {
                    "box": [1, 1, 20, 20],
                    "confidence": 0.91,
                    "label_name": "phone",
                    "stages": [
                        {"box": [2, 2, 8, 8], "confidence": 0.85, "label_name": "stage-a"},
                        {"box": [10, 10, 18, 18], "confidence": 0.31, "label_name": "stage-b"},
                    ],
                }
            ],
            "metadata": {},
        },
    )

    result = executor._process_algorithm(
        node_id="algo_1",
        frame=np.zeros((32, 32, 3), dtype=np.uint8),
        frame_timestamp=0.0,
        roi_regions=None,
        upstream_results={},
    )

    detections = result["result"]["detections"]
    metadata = result["result"]["metadata"]

    assert len(detections) == 1
    assert len(detections[0]["stages"]) == 1
    assert detections[0]["stages"][0]["confidence"] == 0.85
    assert metadata["stage_confidence_filtered_count"] == 1


def test_sync_single_model_confidence_uses_max_threshold_without_explicit_override():
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = 1

    config = {
        "confidence": 0.21,
        "models": [
            {"model_id": 1, "confidence": 0.73, "class": 0}
        ]
    }

    synced, threshold = executor._sync_single_model_confidence(config)

    assert threshold == 0.73
    assert synced["models"][0]["confidence"] == 0.73
    assert synced["confidence"] == 0.73
    assert config["models"][0]["confidence"] == 0.73


def test_sync_single_model_confidence_prefers_explicit_override_threshold():
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = 1

    config = {
        "confidence": 0.21,
        "confidence_override_enabled": True,
        "models": [
            {"model_id": 1, "confidence": 0.73, "class": 0}
        ]
    }

    synced, threshold = executor._sync_single_model_confidence(config)

    assert threshold == 0.21
    assert synced["models"][0]["confidence"] == 0.21
    assert synced["confidence"] == 0.21


def test_sync_single_model_confidence_override_falls_back_to_model_threshold():
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = 1

    config = {
        "confidence_override_enabled": True,
        "models": [
            {"model_id": 1, "confidence": 0.73, "class": 0}
        ]
    }

    synced, threshold = executor._sync_single_model_confidence(config)

    assert threshold == 0.73
    assert synced["models"][0]["confidence"] == 0.73
    assert synced["confidence"] == 0.73


def test_sync_single_model_confidence_falls_back_to_model_threshold():
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = 1

    config = {
        "models": [
            {"model_id": 1, "confidence": 0.73, "class": 0}
        ]
    }

    synced, threshold = executor._sync_single_model_confidence(config)

    assert threshold == 0.73
    assert synced["models"][0]["confidence"] == 0.73
    assert synced["confidence"] == 0.73


def test_get_algorithm_confidence_threshold_prefers_runtime_effective_threshold():
    executor = _build_executor(node_config={"confidence": 0.2})
    executor.algorithm_configs["algo_1"]["effective_confidence_threshold"] = 0.5

    assert executor._get_algorithm_confidence_threshold("algo_1") == 0.5
