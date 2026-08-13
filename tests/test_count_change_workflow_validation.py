from app.web.api.workflows import _validate_count_change_conditions


def _workflow(condition_data, source_type='algorithm', connected=True):
    return {
        'nodes': [
            {'id': 'source-1', 'type': source_type},
            {'id': 'condition-1', 'type': 'condition', 'data': condition_data},
        ],
        'connections': ([{'from': 'source-1', 'to': 'condition-1'}] if connected else []),
    }


def test_count_change_validation_accepts_valid_configuration():
    valid, error = _validate_count_change_conditions(_workflow({
        'conditionKind': 'count_change',
        'sourceNodeId': 'source-1',
        'labels': ['person'],
        'windowSize': 10,
        'direction': 'both',
        'relativeThreshold': 0.5,
        'absoluteThreshold': 3,
        'confirmationCount': 1,
    }))

    assert valid is True
    assert error is None


def test_count_change_validation_requires_connected_result_source():
    valid, error = _validate_count_change_conditions(_workflow({
        'conditionKind': 'count_change',
        'sourceNodeId': 'source-1',
    }, connected=False))

    assert valid is False
    assert '只能连接一个' in error


def test_count_change_validation_rejects_invalid_threshold():
    valid, error = _validate_count_change_conditions(_workflow({
        'conditionKind': 'count_change',
        'sourceNodeId': 'source-1',
        'relativeThreshold': 0,
    }))

    assert valid is False
    assert '相对阈值' in error


def test_count_change_validation_rejects_multiple_incoming_edges():
    workflow = _workflow({
        'conditionKind': 'count_change',
        'sourceNodeId': 'source-1',
    })
    workflow['nodes'].append({'id': 'source-2', 'type': 'function'})
    workflow['connections'].append({'from': 'source-2', 'to': 'condition-1'})

    valid, error = _validate_count_change_conditions(workflow)

    assert valid is False
    assert '只能连接一个' in error
