import threading

import numpy as np

from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_types import ExternalApiNodeData, create_node_data


class _FakeAsyncExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        return None


def test_create_node_data_supports_external_api_node():
    node = create_node_data(
        {
            'id': 'ext_1',
            'type': 'external_api',
            'dataId': '21',
            'config': {
                'interval_seconds': 2.5,
            },
        }
    )

    assert isinstance(node, ExternalApiNodeData)
    assert node.node_id == 'ext_1'
    assert node.data_id == 21
    assert node.interval_seconds == 2.5


def test_normalize_external_api_result_supports_nested_output_mapping():
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.external_api_datamap = {
        'ext_1': {'id': 9, 'name': 'Remote API'}
    }
    executor.external_api_configs = {
        'ext_1': {
            'execution_mode': 'sync',
            'output_mapping': {
                'has_detection_path': 'data.summary.hit',
                'detections_path': 'data.items',
                'metadata_path': 'data.meta',
                'label_color_path': 'style.color',
            },
            'label_color': '#1677ff',
        }
    }

    result = executor._normalize_external_api_result(
        'ext_1',
        {
            'status_code': 200,
            'body': {
                'data': {
                    'summary': {'hit': True},
                    'items': [{'box': [1, 2, 3, 4], 'confidence': 0.9}],
                    'meta': {'latency_ms': 42},
                },
                'style': {'color': '#00AA00'},
            },
        },
    )

    assert result['has_detection'] is True
    assert result['label_color'] == '#00AA00'
    assert len(result['result']['detections']) == 1
    assert result['result']['metadata']['latency_ms'] == 42
    assert result['result']['metadata']['external_api_status_code'] == 200


def test_handle_external_api_node_async_submit_is_non_blocking():
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = 1
    executor.video_source = None
    executor.connections = []
    executor.node_results_cache = {}
    executor.external_api_datamap = {
        'ext_1': {
            'id': 9,
            'name': 'Remote API',
            'enabled': True,
            'request_template': {},
        }
    }
    executor.external_api_configs = {
        'ext_1': {
            'execution_mode': 'async_submit',
            'include_image': False,
            'include_upstream_results': False,
            'payload_template': {},
            'output_mapping': {},
            'label_color': '#1677ff',
        }
    }
    executor._state_lock = threading.Lock()
    executor._async_submit_executor = _FakeAsyncExecutor()

    result = executor._handle_external_api_node(
        'ext_1',
        {
            'frame': np.zeros((8, 8, 3), dtype=np.uint8),
            'frame_bgr': np.zeros((8, 8, 3), dtype=np.uint8),
            'frame_timestamp': 123.0,
        },
    )

    assert result['has_detection'] is False
    assert result['result']['metadata']['submitted'] is True
    assert len(executor._async_submit_executor.calls) == 1
    assert executor.node_results_cache['ext_1']['result']['metadata']['execution_mode'] == 'async_submit'
