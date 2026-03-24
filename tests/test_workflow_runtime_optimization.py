import threading

from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_runtime import build_workflow_signature, extract_source_id_from_workflow_data


class _FakeAlgorithmWithCleanup:
    def __init__(self):
        self.cleaned = False

    def cleanup(self):
        self.cleaned = True


class _FakeClosable:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_extract_source_id_from_workflow_data_returns_source_node_data_id():
    workflow_data = {
        "nodes": [
            {"id": "algo_1", "type": "algorithm", "dataId": 12},
            {"id": "source_1", "type": "source", "dataId": "7"},
        ]
    }

    assert extract_source_id_from_workflow_data(workflow_data) == 7


def test_build_workflow_signature_is_sorted_and_version_sensitive():
    workflow_b = type("Workflow", (), {"id": 9, "config_version": 4})()
    workflow_a = type("Workflow", (), {"id": 3, "config_version": 2})()

    signature = build_workflow_signature([workflow_b, workflow_a])

    assert signature == ((3, 2), (9, 4))


def test_workflow_executor_cleanup_releases_algorithms_and_buffers():
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = 1
    executor.running = True
    executor._cleaned_up = False
    executor.algorithms = {"algo_1": _FakeAlgorithmWithCleanup()}
    executor.video_source = None
    executor.video_recorder = None
    executor.buffer = _FakeClosable()
    executor.recording_buffer = _FakeClosable()
    executor.window_detector = None
    executor.nodes = {}
    executor.node_results_cache = {"algo_1": {"result": {"detections": []}}}
    executor.execution_results = {"algo_1": {"success": True}}
    executor.executed_nodes = ["algo_1"]
    executor._state_lock = threading.Lock()

    executor.cleanup()

    assert executor.running is False
    assert executor.algorithms == {}
    assert executor.buffer is None
    assert executor.recording_buffer is None
    assert executor.node_results_cache == {}
    assert executor.execution_results == {}
    assert executor.executed_nodes == []
