import threading

from app.core.numeric_window_detector import NumericWindowDetector
from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_types import ConditionNodeData, create_node_data


def _build_executor():
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = 1
    executor._state_lock = threading.Lock()
    executor.numeric_window_detector = NumericWindowDetector()
    executor.condition_diagnostics_cache = {}
    executor.connections = [{'from': 'algorithm-1', 'to': 'condition-1'}]
    executor.executed_nodes = ['algorithm-1']
    executor.node_results_cache = {}
    node = ConditionNodeData(
        node_id='condition-1',
        condition_kind='count_change',
        source_node_id='algorithm-1',
        labels=['person'],
        window_size=2,
        direction='increase',
        relative_threshold=0.5,
        absolute_threshold=2,
        confirmation_count=1,
    )
    executor.nodes = {'condition-1': node}
    return executor, node


def _set_result(executor, detections):
    executor.node_results_cache['algorithm-1'] = {
        'result': {'detections': detections, 'metadata': {'model': 'test'}},
        'label_color': '#ff0000',
    }


def test_count_change_condition_filters_labels_preserves_result_and_deduplicates():
    executor, node = _build_executor()
    _set_result(executor, [{'label': 'person'}, {'label': 'car'}])
    executor._handle_count_change_condition(node, {'frame_timestamp': 1})
    _set_result(executor, [{'label_name': 'PERSON'}])
    executor._handle_count_change_condition(node, {'frame_timestamp': 2})

    detections = [
        {'label': 'person'},
        {'label': 'person'},
        {'class_name': 'person'},
        {'label_name': 'PERSON'},
        {'label': 'car'},
    ]
    _set_result(executor, detections)
    context = {'frame_timestamp': 3}
    executor._handle_count_change_condition(node, context)

    assert context['has_detection'] is True
    assert context['result']['detections'] == detections
    metadata = executor.condition_diagnostics_cache['condition-1']
    assert metadata['current_count'] == 4
    assert metadata['baseline'] == 1
    assert metadata['triggered'] is True

    duplicate_context = {'frame_timestamp': 3}
    executor._handle_count_change_condition(node, duplicate_context)
    assert duplicate_context['has_detection'] is False
    assert duplicate_context['_skip_condition_routing'] is True
    assert executor._evaluate_condition('yes', duplicate_context) is False
    assert executor._evaluate_condition('no', duplicate_context) is False
    assert executor.condition_diagnostics_cache['condition-1']['duplicate_sample'] is True
    assert 'condition-1' not in executor.node_results_cache


def test_count_change_condition_does_not_sample_stale_upstream_cache():
    executor, node = _build_executor()
    _set_result(executor, [{'label': 'person'}])
    executor.executed_nodes = []

    context = {'frame_timestamp': 1}
    executor._handle_count_change_condition(node, context)

    assert context['has_detection'] is False
    assert context['_skip_condition_routing'] is True
    assert executor._evaluate_condition('no', context) is False
    metadata = executor.condition_diagnostics_cache['condition-1']
    assert metadata['sampled'] is False
    assert metadata['waiting_for_sample'] is True


def test_create_node_data_parses_count_change_configuration():
    node = create_node_data({
        'id': 'condition-1',
        'type': 'condition',
        'data': {
            'conditionKind': 'count_change',
            'sourceNodeId': 'algorithm-1',
            'labels': ['person'],
            'windowSize': 12,
            'direction': 'decrease',
            'relativeThreshold': 0.75,
            'absoluteThreshold': 4,
            'confirmationCount': 2,
        },
    })

    assert node.condition_kind == 'count_change'
    assert node.source_node_id == 'algorithm-1'
    assert node.labels == ['person']
    assert node.window_size == 12
    assert node.direction == 'decrease'
    assert node.relative_threshold == 0.75
    assert node.absolute_threshold == 4
    assert node.confirmation_count == 2
