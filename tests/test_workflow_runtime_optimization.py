import threading

from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_runtime import (
    build_workflow_signature,
    extract_source_id_from_workflow_data,
    normalize_source_node_fields,
    validate_single_source_node,
    workflow_configs_equivalent,
)


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


def test_extract_source_id_from_workflow_data_supports_legacy_source_shapes():
    workflow_data = {
        "nodes": [
            {
                "id": "source_1",
                "type": "videoSource",
                "data": {
                    "type": "videoSource",
                    "videoSourceId": "7",
                },
            }
        ]
    }

    assert extract_source_id_from_workflow_data(workflow_data) == 7


def test_validate_single_source_node_rejects_multiple_sources():
    workflow_data = {
        "nodes": [
            {"id": "source_1", "type": "source", "dataId": 1},
            {"id": "source_2", "type": "source", "dataId": 2},
        ]
    }

    is_valid, error_message = validate_single_source_node(workflow_data)

    assert is_valid is False
    assert "只允许包含一个视频源节点" in error_message


def test_validate_single_source_node_accepts_source_id_from_nested_legacy_data():
    workflow_data = {
        "nodes": [
            {
                "id": "source_1",
                "data": {
                    "type": "videoSource",
                    "dataId": "9",
                },
            }
        ]
    }

    is_valid, error_message = validate_single_source_node(workflow_data)

    assert is_valid is True
    assert error_message == ""


def test_normalize_source_node_fields_syncs_runtime_and_display_fields():
    workflow_data = {
        "nodes": [
            {
                "id": "source_1",
                "type": "videoSource",
                "videoSourceId": 3,
                "videoSourceName": "old-name",
                "videoSourceCode": "old-code",
                "data": {
                    "type": "videoSource",
                    "videoSourceId": 3,
                    "videoSourceName": "old-name",
                    "videoSourceCode": "old-code",
                },
            }
        ]
    }
    source = type("VideoSource", (), {"id": 7, "name": "Lobby Cam", "source_code": "cam-007"})()

    normalized = normalize_source_node_fields(workflow_data, source)

    source_node = normalized["nodes"][0]
    assert source_node["type"] == "source"
    assert source_node["dataId"] == 7
    assert source_node["videoSourceId"] == 7
    assert source_node["videoSourceName"] == "Lobby Cam"
    assert source_node["videoSourceCode"] == "cam-007"
    assert source_node["data"]["type"] == "source"
    assert source_node["data"]["dataId"] == 7
    assert source_node["data"]["videoSourceId"] == 7
    assert source_node["data"]["videoSourceName"] == "Lobby Cam"
    assert source_node["data"]["videoSourceCode"] == "cam-007"
    assert workflow_data["nodes"][0]["type"] == "videoSource"
    assert workflow_data["nodes"][0]["videoSourceId"] == 3


def test_workflow_configs_equivalent_ignores_source_display_fields_and_legacy_shape():
    existing = {
        "nodes": [
            {
                "id": "source_1",
                "type": "videoSource",
                "data": {
                    "type": "videoSource",
                    "videoSourceId": "7",
                    "videoSourceName": "Old Name",
                    "videoSourceCode": "old-code",
                },
            },
            {"id": "algo_1", "type": "algorithm", "dataId": 12},
        ],
        "connections": [{"source": "source_1", "target": "algo_1"}],
    }
    updated = {
        "nodes": [
            {
                "id": "source_1",
                "type": "source",
                "dataId": 7,
                "videoSourceId": 7,
                "videoSourceName": "New Name",
                "videoSourceCode": "new-code",
                "data": {
                    "type": "source",
                    "dataId": 7,
                    "videoSourceId": 7,
                    "videoSourceName": "New Name",
                    "videoSourceCode": "new-code",
                },
            },
            {"id": "algo_1", "type": "algorithm", "dataId": 12},
        ],
        "connections": [{"source": "source_1", "target": "algo_1"}],
    }

    assert workflow_configs_equivalent(existing, updated) is True


def test_workflow_configs_equivalent_detects_runtime_source_change():
    existing = {
        "nodes": [
            {"id": "source_1", "type": "source", "dataId": 7},
        ]
    }
    updated = {
        "nodes": [
            {"id": "source_1", "type": "source", "dataId": 8},
        ]
    }

    assert workflow_configs_equivalent(existing, updated) is False


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
