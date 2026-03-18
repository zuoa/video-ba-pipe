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
