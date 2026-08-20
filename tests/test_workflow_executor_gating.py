import threading
import time
from collections import defaultdict

import numpy as np

from app.core.execution_log_collector import ExecutionLogCollector
from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_types import (
    AlertNodeData,
    AlgorithmNodeData,
    ConditionNodeData,
    SourceNodeData,
    create_node_data,
)


BOX = {'box': [1, 1, 10, 10], 'confidence': 0.9, 'label_name': 'banner'}
OCR_DET = {'box': [2, 2, 8, 8], 'confidence': 0.95, 'text': '安全', 'label_name': '安全'}


class _CountingAlgo:
    def __init__(self, detections=None, metadata=None, skip=False):
        self.call_count = 0
        self._detections = list(detections or [])
        self._metadata = dict(metadata or {})
        self._skip = skip

    def process(self, frame, roi_regions, upstream_results=None):
        self.call_count += 1
        if self._skip:
            return {
                'detections': [],
                'metadata': {
                    'execution_state': 'skipped',
                    'reason_code': 'upstream_empty',
                    'skipped': True,
                    'ocr_checked': False,
                },
            }
        return {
            'detections': [dict(item) for item in self._detections],
            'metadata': dict(self._metadata),
        }


def _conn(src, dst, condition=None):
    return {'from': src, 'to': dst, 'from_node_id': src, 'to_node_id': dst, 'condition': condition}


def _algo_node(node_id, interval_seconds=0):
    return AlgorithmNodeData(
        node_id=node_id,
        interval_seconds=interval_seconds,
        config={'interval_seconds': interval_seconds},
    )


def _stub_executor(nodes, connections, algorithms=None):
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = 1
    executor.test_mode = False
    executor._state_lock = threading.Lock()
    executor.nodes = nodes
    executor.connections = connections
    executor.execution_graph = defaultdict(list)
    executor._build_execution_graph()
    executor.node_results_cache = {}
    executor.condition_diagnostics_cache = {}
    executor.execution_results = {}
    executor.executed_nodes = []
    executor.skipped_nodes = set()
    executor._in_progress_nodes = set()
    executor._node_completion_events = {}
    executor.node_last_exec_time = {node_id: 0 for node_id in nodes}
    executor.algorithms = {}
    executor.algorithm_configs = {}
    executor.algorithm_datamap = {}
    executor.algorithm_roi_configs = {}
    executor.external_api_configs = {}
    executor._latest_algorithm_results = {}
    executor.workflow_data = {
        'nodes': [{'id': node_id, 'type': node.node_type, 'config': getattr(node, 'config', {}) or {}}
                  for node_id, node in nodes.items()]
    }
    executor.alert_calls = []
    executor.condition_calls = []

    algorithms = algorithms or {}
    for node_id, algo in algorithms.items():
        _attach_algo(executor, node_id, algo)

    orig_condition = executor._handle_condition_node

    def _spy_condition(node_id, context):
        executor.condition_calls.append(node_id)
        return orig_condition(node_id, context)

    def _record_alert(node_id, context):
        executor.alert_calls.append({
            'node_id': node_id,
            'has_detection': context.get('has_detection'),
            'detections': list((context.get('result') or {}).get('detections') or []),
            'result': dict(context.get('result') or {}),
        })
        return context

    executor._handle_condition_node = _spy_condition
    executor.node_handlers = {
        'source': executor._handle_source_node,
        'algorithm': executor._handle_algorithm_node,
        'condition': _spy_condition,
        'time_schedule': executor._handle_time_schedule_node,
        'output': _record_alert,
        'roi_draw': executor._handle_roi_draw_node,
        'roi': executor._handle_roi_draw_node,
        'alert': _record_alert,
        'function': executor._handle_function_node,
        'external_api': executor._handle_external_api_node,
        'webhook': executor._handle_webhook_node,
    }
    return executor


def _attach_algo(executor, node_id, algo, algorithm_type=None):
    if algorithm_type is None:
        algorithm_type = 'ocr' if node_id.startswith('ocr') else 'script'
    executor.algorithms[node_id] = algo
    executor.algorithm_configs[node_id] = {'algorithm_id': 1}
    executor.algorithm_datamap[node_id] = {
        'name': node_id,
        'algorithm_type': algorithm_type,
        'label_color': '#FF0000',
        'interval_seconds': 0,
    }


def _frame_context(**extra):
    context = {
        'frame_nv12': np.zeros((8, 8, 3), dtype=np.uint8),
        'frame': np.zeros((8, 8, 3), dtype=np.uint8),
        'frame_timestamp': 1.0,
        'roi_regions': [],
        'log_collector': ExecutionLogCollector(),
    }
    context.update(extra)
    return context


def _run(executor, extra_context=None):
    context = _frame_context(**(extra_context or {}))
    with executor._state_lock:
        executor.executed_nodes.clear()
        executor.skipped_nodes.clear()
        executor.execution_results.clear()
        executor._in_progress_nodes.clear()
        executor._node_completion_events.clear()
    executor._execute_by_topology_levels(executor=None, context=context)
    return context


def _skip_cache(executor, node_id):
    return executor.node_results_cache.get(node_id) or {}


def _banner_graph(edge_condition, yolo_dets=None, ocr_dets=None, yolo_interval=0, ocr_interval=0):
    yolo = _CountingAlgo(detections=yolo_dets or [])
    ocr = _CountingAlgo(
        detections=ocr_dets or [],
        metadata={'ocr_checked': True, 'full_text': '安全'} if ocr_dets else {},
    )
    nodes = {
        'source': SourceNodeData(node_id='source'),
        'yolo': _algo_node('yolo', yolo_interval),
        'ocr': _algo_node('ocr', ocr_interval),
        'alert': AlertNodeData(node_id='alert'),
    }
    connections = [
        _conn('source', 'yolo'),
        _conn('yolo', 'ocr', edge_condition),
        _conn('ocr', 'alert'),
    ]
    executor = _stub_executor(nodes, connections, {'yolo': yolo, 'ocr': ocr})
    return executor, yolo, ocr


def test_empty_yolo_detected_edge_skips_ocr_process():
    executor, yolo, ocr = _banner_graph('detected', yolo_dets=[])
    _run(executor)

    assert yolo.call_count == 1
    assert ocr.call_count == 0
    sentinel = _skip_cache(executor, 'ocr')
    assert sentinel.get('skipped') is True
    metadata = (sentinel.get('result') or {}).get('metadata') or {}
    assert metadata.get('execution_state') == 'skipped'
    assert metadata.get('ocr_checked') is not True
    assert metadata.get('reason_code') == 'upstream_empty'
    assert 'ocr' in executor.skipped_nodes
    assert 'ocr' not in executor.executed_nodes


def test_yolo_with_boxes_detected_edge_runs_ocr_once():
    executor, yolo, ocr = _banner_graph('detected', yolo_dets=[BOX], ocr_interval=0)
    _run(executor)

    assert yolo.call_count == 1
    assert ocr.call_count == 1
    assert 'ocr' in executor.executed_nodes
    assert 'ocr' not in executor.skipped_nodes
    cached = _skip_cache(executor, 'ocr')
    assert cached.get('skipped') is not True


def test_null_edge_yolo_interval_skip_still_runs_ocr():
    executor, yolo, ocr = _banner_graph(None, yolo_dets=[BOX], yolo_interval=1, ocr_interval=0)
    executor.node_last_exec_time['yolo'] = time.time()
    _run(executor)

    assert yolo.call_count == 0
    assert ocr.call_count == 1
    assert 'ocr' not in executor.skipped_nodes
    assert 'ocr' in executor.executed_nodes


def test_not_detected_edge_runs_only_when_empty():
    with_boxes, yolo_hit, x_hit = _banner_graph('not_detected', yolo_dets=[BOX])
    _run(with_boxes)
    assert yolo_hit.call_count == 1
    assert x_hit.call_count == 0
    assert (_skip_cache(with_boxes, 'ocr').get('result') or {}).get('metadata', {}).get('reason_code') == 'gate_failed'

    empty, yolo_miss, x_miss = _banner_graph('not_detected', yolo_dets=[])
    _run(empty)
    assert yolo_miss.call_count == 1
    assert x_miss.call_count == 1


def test_condition_true_edge_skips_ocr_without_boxes():
    yolo = _CountingAlgo(detections=[])
    ocr = _CountingAlgo()
    nodes = {
        'source': SourceNodeData(node_id='source'),
        'yolo': _algo_node('yolo'),
        'cond': create_node_data({
            'id': 'cond',
            'type': 'condition',
            'data': {'targetCount': 1, 'comparisonType': '>='},
        }),
        'ocr': _algo_node('ocr'),
    }
    connections = [
        _conn('source', 'yolo'),
        _conn('yolo', 'cond'),
        _conn('cond', 'ocr', 'true'),
    ]
    executor = _stub_executor(nodes, connections, {'yolo': yolo, 'ocr': ocr})
    _run(executor)

    assert ocr.call_count == 0
    sentinel = _skip_cache(executor, 'ocr')
    assert sentinel.get('skipped') is True
    assert (sentinel.get('result') or {}).get('metadata', {}).get('reason_code') == 'gate_failed'


def test_mixed_incoming_or_null_edge_runs_ocr():
    # A --null--> OCR + B --detected--> OCR is per-edge OR: A firing runs OCR.
    algo_a = _CountingAlgo(detections=[BOX])
    algo_b = _CountingAlgo(detections=[])
    ocr = _CountingAlgo()
    nodes = {
        'source': SourceNodeData(node_id='source'),
        'a': _algo_node('a'),
        'b': _algo_node('b'),
        'ocr': _algo_node('ocr'),
    }
    connections = [
        _conn('source', 'a'),
        _conn('source', 'b'),
        _conn('a', 'ocr'),
        _conn('b', 'ocr', 'detected'),
    ]
    executor = _stub_executor(nodes, connections, {'a': algo_a, 'b': algo_b, 'ocr': ocr})
    _run(executor)

    assert ocr.call_count == 1
    assert 'ocr' in executor.executed_nodes


def test_same_frame_branch_and_topology_keep_cache():
    executor, yolo, ocr = _banner_graph(None, yolo_dets=[BOX], ocr_dets=[OCR_DET], ocr_interval=1)
    _run(executor)

    assert ocr.call_count == 1
    cached = executor.node_results_cache.get('ocr')
    assert cached is not None
    assert cached.get('has_detection') is True
    assert cached.get('result', {}).get('detections')


def test_interval_zero_dual_path_does_not_double_run():
    executor, yolo, ocr = _banner_graph(None, yolo_dets=[BOX], yolo_interval=0, ocr_interval=0)
    _run(executor)

    assert ocr.call_count == 1


def test_alert_fires_once_per_frame():
    executor, yolo, ocr = _banner_graph(None, yolo_dets=[BOX], ocr_dets=[OCR_DET], ocr_interval=0)
    _run(executor)

    assert len(executor.alert_calls) == 1


def test_yolo_interval_skip_gated_ocr_does_not_use_last_good():
    executor, yolo, ocr = _banner_graph('detected', yolo_dets=[BOX], yolo_interval=1, ocr_interval=0)
    executor.node_results_cache['ocr'] = {
        'has_detection': True,
        'result': {
            'detections': [dict(OCR_DET)],
            'metadata': {'ocr_checked': True, 'full_text': '旧字'},
        },
    }
    executor.node_last_exec_time['yolo'] = time.time()
    _run(executor)

    assert yolo.call_count == 0
    assert ocr.call_count == 0
    sentinel = _skip_cache(executor, 'ocr')
    assert sentinel.get('skipped') is True
    assert sentinel['result']['detections'] == []
    assert sentinel['result']['metadata'].get('reason_code') == 'upstream_not_executed'
    assert sentinel['result']['metadata'].get('full_text') != '旧字'


def test_yolo_interval_skip_count_eq_zero_does_not_fire_alert():
    yolo = _CountingAlgo(detections=[])
    nodes = {
        'source': SourceNodeData(node_id='source'),
        'yolo': _algo_node('yolo', interval_seconds=1),
        'cond': create_node_data({
            'id': 'cond',
            'type': 'condition',
            'data': {'targetCount': 0, 'comparisonType': '=='},
        }),
        'alert': AlertNodeData(node_id='alert'),
    }
    connections = [
        _conn('source', 'yolo'),
        _conn('yolo', 'cond'),
        _conn('cond', 'alert', 'true'),
    ]
    executor = _stub_executor(nodes, connections, {'yolo': yolo})
    executor.node_last_exec_time['yolo'] = time.time()
    _run(executor)

    assert yolo.call_count == 0
    assert executor.alert_calls == []
    assert 'cond' in executor.executed_nodes


def test_skip_sentinel_ocr_text_condition_never_passes():
    node = ConditionNodeData(
        node_id='cond',
        condition_kind='ocr_text',
        source_node_id='ocr',
        text_operator='contains',
        pattern_type='keywords',
        keywords=['安全'],
        keyword_logic='any',
    )
    upstream = {
        'ocr': {
            'detections': [],
            'metadata': {
                'execution_state': 'skipped',
                'reason_code': 'upstream_empty',
                'ocr_checked': False,
                'skipped': True,
            },
        }
    }
    passed, _, _ = WorkflowExecutor._evaluate_ocr_text_condition(node, upstream)
    assert passed is False

    node.text_operator = 'not_contains'
    passed, _, _ = WorkflowExecutor._evaluate_ocr_text_condition(node, upstream)
    assert passed is False


def test_time_schedule_blocked_gated_node_writes_no_skip_sentinel():
    executor, _yolo, ocr = _banner_graph('detected', yolo_dets=[])
    context = _frame_context(_time_schedule_blocked_nodes={'ocr'})
    executor._execute_level_node('ocr', context)

    assert ocr.call_count == 0
    assert 'ocr' not in executor.skipped_nodes
    assert 'ocr' not in executor.node_results_cache
    assert 'ocr' not in executor.execution_results


def test_ocr_text_condition_preserves_boxes_for_alert():
    ocr = _CountingAlgo(
        detections=[OCR_DET],
        metadata={'ocr_checked': True, 'full_text': '安全'},
    )
    nodes = {
        'source': SourceNodeData(node_id='source'),
        'ocr': _algo_node('ocr'),
        'cond': create_node_data({
            'id': 'cond',
            'type': 'condition',
            'data': {
                'conditionKind': 'ocr_text',
                'sourceNodeId': 'ocr',
                'textOperator': 'contains',
                'patternType': 'keywords',
                'keywords': ['安全'],
                'keywordLogic': 'any',
            },
        }),
        'alert': AlertNodeData(node_id='alert'),
    }
    connections = [
        _conn('source', 'ocr'),
        _conn('ocr', 'cond'),
        _conn('cond', 'alert', 'true'),
    ]
    executor = _stub_executor(nodes, connections, {'ocr': ocr})
    _run(executor)

    assert len(executor.alert_calls) == 1
    detections = executor.alert_calls[0]['detections']
    assert detections
    assert detections[0].get('text') == '安全'
    cond_cache = _skip_cache(executor, 'cond')
    assert cond_cache.get('result', {}).get('detections') == []
    assert executor.alert_calls[0]['result'].get('detections') != []


def test_ocr_text_condition_interval_does_not_double_route():
    ocr = _CountingAlgo(
        detections=[OCR_DET],
        metadata={'ocr_checked': True, 'full_text': '安全'},
    )
    nodes = {
        'source': SourceNodeData(node_id='source'),
        'ocr': _algo_node('ocr', interval_seconds=1),
        'cond': create_node_data({
            'id': 'cond',
            'type': 'condition',
            'data': {
                'conditionKind': 'ocr_text',
                'sourceNodeId': 'ocr',
                'textOperator': 'contains',
                'patternType': 'keywords',
                'keywords': ['安全'],
                'keywordLogic': 'any',
            },
        }),
        'alert': AlertNodeData(node_id='alert'),
    }
    connections = [
        _conn('source', 'ocr'),
        _conn('ocr', 'cond'),
        _conn('cond', 'alert', 'true'),
    ]
    executor = _stub_executor(nodes, connections, {'ocr': ocr})
    _run(executor)

    assert ocr.call_count == 1
    assert executor.condition_calls == ['cond']
    assert len(executor.alert_calls) == 1


def test_plugin_skip_rolls_back_last_exec_and_second_call_keeps_cache():
    ocr = _CountingAlgo(skip=True)
    nodes = {
        'ocr': _algo_node('ocr', interval_seconds=1),
    }
    executor = _stub_executor(nodes, [], {'ocr': ocr})
    executor.node_last_exec_time = {}
    context = _frame_context()

    first = executor._execute_node('ocr', context)
    assert ocr.call_count == 1
    assert 'ocr' not in executor.node_last_exec_time
    assert 'ocr' in executor.skipped_nodes
    assert 'ocr' not in executor.executed_nodes
    assert executor._is_skip_result('ocr', first)
    cached = executor.node_results_cache['ocr']
    assert cached is not None

    second = executor._execute_node('ocr', context)
    assert ocr.call_count == 1
    assert 'ocr' in executor.node_results_cache
    assert executor.node_results_cache['ocr'].get('skipped') or (
        (executor.node_results_cache['ocr'].get('result') or {}).get('metadata') or {}
    ).get('execution_state') == 'skipped'
    assert second is not executor.node_results_cache['ocr']


def test_skip_sentinel_appears_in_collected_results_without_failing():
    executor, _yolo, ocr = _banner_graph('detected', yolo_dets=[])
    context = _run(executor)

    final_result = executor._collect_execution_results(context)
    skip_nodes = [item for item in final_result['nodes'] if item.get('node_id') == 'ocr']
    assert skip_nodes
    skip_node = skip_nodes[0]
    assert skip_node['success'] is True
    assert skip_node['skipped'] is True
    assert skip_node['data']['execution_state'] == 'skipped'
    assert skip_node['data']['reason_code'] == 'upstream_empty'
    assert skip_node['data']['detection_count'] == 0
    assert final_result['success'] is True
    assert ocr.call_count == 0


def test_has_gated_incoming_is_opt_in():
    executor, _, _ = _banner_graph('detected')
    assert executor._has_gated_incoming('ocr') is True
    assert executor._has_gated_incoming('yolo') is False

    empty, _, _ = _banner_graph(None)
    assert empty._has_gated_incoming('ocr') is False


def test_plugin_skip_ocr_empty_edge_does_not_skip_condition():
    ocr = _CountingAlgo(skip=True)
    nodes = {
        'source': SourceNodeData(node_id='source'),
        'ocr': _algo_node('ocr'),
        'cond': create_node_data({
            'id': 'cond',
            'type': 'condition',
            'data': {
                'conditionKind': 'ocr_text',
                'sourceNodeId': 'ocr',
                'textOperator': 'contains',
                'patternType': 'keywords',
                'keywords': ['安全'],
                'keywordLogic': 'any',
            },
        }),
        'alert': AlertNodeData(node_id='alert'),
    }
    connections = [
        _conn('source', 'ocr'),
        _conn('ocr', 'cond'),
        _conn('cond', 'alert', 'true'),
    ]
    executor = _stub_executor(nodes, connections, {'ocr': ocr})
    context = _run(executor)

    assert ocr.call_count == 1
    assert 'ocr' in executor.skipped_nodes
    assert 'cond' in executor.executed_nodes
    assert 'cond' not in executor.skipped_nodes
    cached = _skip_cache(executor, 'cond')
    assert 'frame_nv12' not in cached
    assert 'log_collector' not in cached
    assert cached.get('node_id') == 'cond'
    assert cached.get('result', {}).get('detections') == []
    assert executor._is_skip_result('cond', context) is False
    assert executor.alert_calls == []


def test_plugin_skip_ocr_empty_edge_count_condition_stays_executed():
    ocr = _CountingAlgo(skip=True)
    nodes = {
        'source': SourceNodeData(node_id='source'),
        'ocr': _algo_node('ocr'),
        'cond': create_node_data({
            'id': 'cond',
            'type': 'condition',
            'data': {'targetCount': 0, 'comparisonType': '=='},
        }),
        'alert': AlertNodeData(node_id='alert'),
    }
    connections = [
        _conn('source', 'ocr'),
        _conn('ocr', 'cond'),
        _conn('cond', 'alert', 'true'),
    ]
    executor = _stub_executor(nodes, connections, {'ocr': ocr})
    _run(executor)

    assert 'ocr' in executor.skipped_nodes
    assert 'cond' in executor.executed_nodes
    assert 'cond' not in executor.skipped_nodes
    cached = _skip_cache(executor, 'cond')
    assert 'frame_nv12' not in cached
    assert cached.get('result', {}).get('detections') == []


def test_is_skip_result_ignores_live_context_payload():
    executor, _, _ = _banner_graph('detected')
    live_context = _frame_context()
    live_context['result'] = {
        'detections': [],
        'metadata': {'execution_state': 'skipped', 'ocr_checked': False},
    }
    live_context['has_detection'] = False
    assert executor._is_skip_result('ocr', live_context) is False

    wrapper = {
        'node_id': 'ocr',
        'skipped': False,
        'has_detection': False,
        'result': {
            'detections': [],
            'metadata': {'execution_state': 'skipped', 'ocr_checked': False},
        },
    }
    assert executor._is_skip_result('ocr', wrapper) is True
    wrapper['skipped'] = True
    wrapper.pop('node_id', None)
    assert executor._is_skip_result('ocr', wrapper) is True


def test_parallel_dual_path_process_runs_once():
    started = threading.Event()
    release = threading.Event()
    call_lock = threading.Lock()

    class _BlockingAlgo(_CountingAlgo):
        def process(self, frame, roi_regions, upstream_results=None):
            with call_lock:
                self.call_count += 1
            started.set()
            assert release.wait(timeout=2)
            return {
                'detections': [dict(BOX)],
                'metadata': {},
            }

    algo = _BlockingAlgo(detections=[BOX])
    nodes = {'ocr': _algo_node('ocr', interval_seconds=0)}
    executor = _stub_executor(nodes, [], {'ocr': algo})
    results = [None, None]
    barrier = threading.Barrier(2)

    def _run_node(index):
        barrier.wait()
        results[index] = executor._execute_node('ocr', _frame_context())

    threads = [
        threading.Thread(target=_run_node, args=(0,)),
        threading.Thread(target=_run_node, args=(1,)),
    ]
    for thread in threads:
        thread.start()
    assert started.wait(timeout=2)
    release.set()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert algo.call_count == 1
    assert 'ocr' in executor.executed_nodes
    assert results[0] is not None and results[1] is not None
    assert results[0].get('has_detection') is True
    assert results[1].get('has_detection') is True
