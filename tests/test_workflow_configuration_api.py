from copy import deepcopy
from datetime import datetime, timezone
import json
import threading
from unittest.mock import Mock

from flask import Flask
from peewee import SqliteDatabase
import pytest

from app.core.database_models import Algorithm, ApiKey, VideoSource, Workflow
from app.core.orchestrator import Orchestrator
from app.core.workflow_configuration import apply_entry_configuration
from app.core import workflow_configuration_service as service
from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_runtime import build_workflow_signature
from app.core.workflow_types import create_node_data
from app.web.api import public_api


def schedule():
    return {str(day): [{'start': '08:00', 'end': '18:00'}] for day in range(1, 8)}


def regions():
    return [{'name': '入口', 'mode': 'post_filter', 'anchor': 'bottom_center',
             'polygon': [{'x': 0.1, 'y': 0.1}, {'x': 0.9, 'y': 0.1}, {'x': 0.5, 'y': 0.9}]}]


def graph(source_id):
    return {
        'nodes': [
            {'id': 'source', 'type': 'source', 'dataId': source_id, 'x': 80, 'y': 160},
            {'id': 'algorithm', 'type': 'algorithm', 'dataId': 12,
             'config': {'confidence': 0.5, 'interval_seconds': 1, 'runtime_timeout': 30}},
            {'id': 'alert', 'type': 'alert', 'data': {'suppression': {'enable': False}}},
        ],
        'connections': [
            {'id': 'edge-1', 'from': 'source', 'from_node_id': 'source',
             'to': 'algorithm', 'to_node_id': 'algorithm', 'to_port': 'image',
             'condition': None, 'label': '主分支'},
            {'id': 'edge-2', 'from': 'source', 'to': 'alert', 'to_port': 'input', 'condition': None},
        ],
    }


@pytest.fixture
def configuration_api(monkeypatch):
    database = SqliteDatabase(':memory:', pragmas={'foreign_keys': 1})
    models = [Algorithm, ApiKey, VideoSource, Workflow]
    with database.bind_ctx(models):
        database.connect()
        database.create_tables(models)
        monkeypatch.setattr(public_api, 'db', database)
        ApiKey.create(name='integration', key_prefix='vbp_test',
                      key_hash=public_api._hash_api_key('vbp_test'), created_at=datetime.now())
        source = VideoSource.create(name='Camera', source_code='cam-config', source_url='rtsp://example/test')
        workflow = Workflow.create(created_at=datetime.now(), updated_at=datetime.now(), name='运行编排', video_source=source, is_active=True,
                                   workflow_data=json.dumps(graph(source.id)))
        app = Flask(__name__)
        app.config['TESTING'] = True
        public_api.register_public_api(app)
        yield app.test_client(), {'X-API-Key': 'vbp_test'}, workflow
        database.close()


def put(client, headers, workflow_id, capability, value, version=1, **extra):
    field = 'weekly_schedule' if capability == 'time-schedule' else 'roi_regions'
    return client.put(f'/openapi/v1/workflows/{workflow_id}/configuration/{capability}',
                      headers=headers, json={'expected_version': version, field: value, **extra})


def test_discovery_reports_missing_capabilities_and_common_parameters(configuration_api):
    client, headers, workflow = configuration_api
    response = client.get(f'/openapi/v1/workflows/{workflow.id}/configuration', headers=headers)
    assert response.status_code == 200
    result = response.json['data']
    assert result['config_version'] == 1
    assert result['time_schedule']['status'] == result['roi_draw']['status'] == 'missing'
    assert result['common_parameters'][0] == {
        'node_id': 'algorithm', 'node_type': 'algorithm', 'name': 'algorithm',
        'allowed_fields': ['confidence', 'interval_seconds'],
        'config': {'confidence': 0.5, 'interval_seconds': 1},
    }
    assert 'runtime_timeout' not in json.dumps(result)


@pytest.mark.parametrize('order', [('time-schedule', 'roi'), ('roi', 'time-schedule')])
def test_missing_components_insert_in_fixed_order_and_preserve_branches(configuration_api, order):
    client, headers, workflow = configuration_api
    original = workflow.data_dict
    for version, capability in enumerate(order, 1):
        value = schedule() if capability == 'time-schedule' else regions()
        preview = put(client, headers, workflow.id, capability, value, version)
        assert preview.status_code == 200
        assert preview.json['data']['dry_run'] is True
        assert preview.json['data']['change']['action'] == 'created'
        assert Workflow.get_by_id(workflow.id).config_version == version
        saved = put(client, headers, workflow.id, capability, value, version, dry_run=False)
        assert saved.status_code == 200
        assert saved.json['data']['config_version'] == version + 1
    stored = Workflow.get_by_id(workflow.id)
    nodes = {node['type']: node for node in stored.data_dict['nodes']}
    links = stored.data_dict['connections']
    time_id, roi_id = nodes['time_schedule']['id'], nodes['roi']['id']
    assert {(edge['from'], edge['to']) for edge in links} == {
        ('source', time_id), (time_id, roi_id), (roi_id, 'algorithm'), (roi_id, 'alert'),
    }
    for edge in original['connections']:
        updated = next(item for item in links if item['id'] == edge['id'])
        for key in ('to', 'to_port', 'condition', 'label'):
            assert updated.get(key) == edge.get(key)
    for edge in links:
        assert edge.get('from_node_id', edge['from']) == edge['from']
        assert edge.get('to_node_id', edge['to']) == edge['to']
    result = client.get(f'/openapi/v1/workflows/{workflow.id}/configuration', headers=headers).json['data']
    assert result['time_schedule']['status'] == result['roi_draw']['status'] == 'configured'
    assert result['time_schedule']['weekly_schedule'] == schedule()
    assert result['roi_draw']['roi_regions'] == regions()
    # Generated nodes are consumable by the existing executor and ROI discovery.
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = workflow.id
    executor.nodes = {node['id']: create_node_data(node) for node in stored.data_dict['nodes']}
    executor.connections = links
    executor._state_lock = threading.Lock()
    executor.node_results_cache = {}
    assert executor._find_upstream_roi('algorithm') == regions()
    assert executor._find_upstream_roi('alert') == regions()
    context = {'_time_schedule_now': datetime(2026, 10, 5, 9, tzinfo=timezone.utc)}
    assert executor._handle_time_schedule_node(time_id, context) is context
    context['_time_schedule_now'] = datetime(2026, 10, 5, 23, tzinfo=timezone.utc)
    assert executor._handle_time_schedule_node(time_id, context) is None


def test_updates_and_clears_roi_without_recreating_node(configuration_api):
    client, headers, workflow = configuration_api
    first = put(client, headers, workflow.id, 'roi', regions(), dry_run=False)
    node_id = first.json['data']['change']['node_id']
    cleared = put(client, headers, workflow.id, 'roi', [], 2, dry_run=False)
    assert cleared.status_code == 200
    assert cleared.json['data']['change']['action'] == 'updated'
    assert cleared.json['data']['change']['node_id'] == node_id
    assert cleared.json['data']['change']['connections'] == []
    graph_data = Workflow.get_by_id(workflow.id).data_dict
    assert len(graph_data['nodes']) == 4
    assert next(node for node in graph_data['nodes'] if node['id'] == node_id)['data']['roiRegions'] == []


def test_empty_roi_does_not_create_or_bump_version(configuration_api):
    client, headers, workflow = configuration_api
    response = put(client, headers, workflow.id, 'roi', [], dry_run=False)
    assert response.status_code == 200
    assert response.json['data']['change']['action'] == 'unchanged'
    assert Workflow.get_by_id(workflow.id).config_version == 1
    assert Workflow.get_by_id(workflow.id).data_dict == workflow.data_dict


@pytest.mark.parametrize('case', ['branch', 'duplicate', 'reverse', 'conditional', 'bypass'])
def test_ambiguous_existing_components_report_conflict_without_writes(configuration_api, case):
    client, headers, workflow = configuration_api
    data = workflow.data_dict
    time_node = {'id': 'time', 'type': 'time_schedule', 'data': {'weeklySchedule': schedule()}}
    data['nodes'].append(time_node)
    if case in ('branch', 'duplicate'):
        data['connections'][0]['to'] = data['connections'][0]['to_node_id'] = 'time'
        data['connections'].append({'from': 'time', 'to': 'algorithm'})
        if case == 'duplicate':
            data['nodes'].append({**time_node, 'id': 'time-2'})
    elif case == 'reverse':
        data['nodes'].append({'id': 'roi', 'type': 'roi_draw', 'data': {'roiRegions': regions()}})
        data['connections'] = [{'from': 'source', 'to': 'roi'}, {'from': 'roi', 'to': 'time'},
                               {'from': 'time', 'to': 'algorithm'}]
    else:
        data['connections'] = [{'from': 'source', 'to': 'time',
                                'condition': 'detected' if case == 'conditional' else None},
                               {'from': 'time', 'to': 'algorithm'}]
        if case == 'bypass':
            data['connections'].append({'from': 'alert', 'to': 'time'})
    Workflow.update(workflow_data=json.dumps(data)).where(Workflow.id == workflow.id).execute()
    view = client.get(f'/openapi/v1/workflows/{workflow.id}/configuration', headers=headers)
    assert view.json['data']['time_schedule']['status'] == 'conflict'
    response = put(client, headers, workflow.id, 'roi', regions(), dry_run=False)
    assert response.status_code == 409
    assert response.json['code'] == 'entry_component_conflict'
    assert Workflow.get_by_id(workflow.id).data_dict == data
    assert Workflow.get_by_id(workflow.id).config_version == 1


@pytest.mark.parametrize('case', ['no_downstream', 'cycle', 'dangling', 'alias_mismatch', 'duplicate_id'])
def test_invalid_graph_is_rejected_without_insertion(configuration_api, case):
    client, headers, workflow = configuration_api
    data = workflow.data_dict
    if case == 'no_downstream':
        data['connections'] = []
    elif case == 'cycle':
        data['connections'].append({'from': 'algorithm', 'to': 'source'})
    elif case == 'dangling':
        data['connections'].append({'from': 'source', 'to': 'missing'})
    elif case == 'alias_mismatch':
        data['connections'][0]['from_node_id'] = 'alert'
    else:
        data['nodes'].append(deepcopy(data['nodes'][1]))
    Workflow.update(workflow_data=json.dumps(data)).where(Workflow.id == workflow.id).execute()
    response = put(client, headers, workflow.id, 'roi', regions(), dry_run=False)
    assert response.status_code == 400
    assert response.json['code'] == 'invalid_workflow_structure'
    assert Workflow.get_by_id(workflow.id).data_dict == data


@pytest.mark.parametrize('bad', [None, {}, [1], [{'name': '', 'mode': 'post_filter', 'polygon': []}],
    [{'name': 'x', 'mode': 'unknown', 'polygon': []}],
    [{'name': 'x', 'mode': 'post_filter', 'polygon': [{'x': 0, 'y': 0}] * 3}],
    [{'name': 'x', 'mode': 'post_filter', 'polygon': [{'x': 0, 'y': 0}, {'x': 0.5, 'y': 0.5}, {'x': 1, 'y': 1}]}],
])
def test_invalid_roi_is_rejected(configuration_api, bad):
    client, headers, workflow = configuration_api
    response = put(client, headers, workflow.id, 'roi', bad, dry_run=False)
    assert response.status_code == 400
    assert Workflow.get_by_id(workflow.id).config_version == 1


@pytest.mark.parametrize('axis_value', [-0.1, 1.1, True, '0.5', float('nan'), float('inf')])
def test_roi_coordinates_are_finite_normalized_numbers(configuration_api, axis_value):
    client, headers, workflow = configuration_api
    value = regions()
    value[0]['polygon'][0]['x'] = axis_value
    assert put(client, headers, workflow.id, 'roi', value, dry_run=False).status_code == 400


@pytest.mark.parametrize('mode', ['pre_mask', 'crop_infer', 'post_filter'])
def test_supported_roi_modes(configuration_api, mode):
    client, headers, workflow = configuration_api
    value = regions()
    value[0]['mode'] = mode
    assert put(client, headers, workflow.id, 'roi', value).status_code == 200


@pytest.mark.parametrize('value', [None, {}, {str(day): [] for day in range(1, 8)},
    {**schedule(), '1': [{'start': '22:00', 'end': '02:00'}]},
    {**schedule(), '1': [{'start': '24:00', 'end': '25:00'}]},
])
def test_invalid_schedule_is_rejected(configuration_api, value):
    client, headers, workflow = configuration_api
    response = put(client, headers, workflow.id, 'time-schedule', value, dry_run=False)
    assert response.status_code == 400
    assert Workflow.get_by_id(workflow.id).config_version == 1


@pytest.mark.parametrize('capability', ['roi', 'time-schedule'])
def test_versions_and_strict_request_types(configuration_api, capability):
    client, headers, workflow = configuration_api
    value = regions() if capability == 'roi' else schedule()
    assert put(client, headers, workflow.id, capability, value, 0).status_code == 409
    for version in (True, 1.5, '1', None):
        assert put(client, headers, workflow.id, capability, value, version).status_code == 400
    assert put(client, headers, workflow.id, capability, value, dry_run='false').status_code == 400
    assert put(client, headers, workflow.id, capability, value, unexpected=1).status_code == 400
    path = f'/openapi/v1/workflows/{workflow.id}/configuration/{capability}'
    assert client.put(path, json=[], headers=headers).status_code == 400
    assert client.put(path, data='{', content_type='application/json', headers=headers).status_code == 400


def test_configuration_auth_missing_workflows_and_templates(configuration_api):
    client, headers, workflow = configuration_api
    path = f'/openapi/v1/workflows/{workflow.id}/configuration'
    assert client.get(path).status_code == 401
    assert client.get(path, headers={'X-API-Key': 'bad'}).status_code == 401
    assert client.get('/openapi/v1/workflows/999/configuration', headers=headers).status_code == 404
    Workflow.update(is_template=True, video_source=None).where(Workflow.id == workflow.id).execute()
    assert put(client, headers, workflow.id, 'roi', regions(), dry_run=False).status_code == 400
    assert Workflow.get_by_id(workflow.id).config_version == 1


def batch_payload(workflow_ids, changes=None):
    return {
        'workflow_ids': workflow_ids, 'expected_versions': {str(key): 1 for key in workflow_ids},
        'targets': [{'workflow_ids': workflow_ids, 'node_id': 'algorithm', 'node_type': 'algorithm',
                     'changes': changes or {'confidence': 0.8}}],
    }


def test_public_batch_preview_and_atomic_field_scoped_apply(configuration_api):
    client, headers, workflow = configuration_api
    second = Workflow.create(created_at=datetime.now(), updated_at=datetime.now(), name='second', video_source=workflow.video_source_id,
                             workflow_data=workflow.workflow_data)
    payload = batch_payload([workflow.id, second.id])
    path = '/openapi/v1/workflows/batch-config'
    preview = client.post(path, json=payload, headers=headers)
    assert preview.status_code == 200
    assert preview.json['data']['summary']['workflow_count'] == 2
    assert Workflow.get_by_id(workflow.id).config_version == 1
    payload['dry_run'] = False
    saved = client.post(path, json=payload, headers=headers)
    assert saved.status_code == 200
    assert saved.json['data']['config_versions'] == {str(workflow.id): 2, str(second.id): 2}
    for workflow_id in (workflow.id, second.id):
        stored = Workflow.get_by_id(workflow_id)
        assert stored.data_dict['nodes'][1]['config'] == {
            'confidence': 0.8, 'interval_seconds': 1, 'runtime_timeout': 30, 'confidence_override_enabled': True,
        }


@pytest.mark.parametrize('case', ['stale', 'missing', 'invalid_field', 'duplicate', 'nan', 'template'])
def test_public_batch_failures_write_nothing(configuration_api, case):
    client, headers, workflow = configuration_api
    second = Workflow.create(created_at=datetime.now(), updated_at=datetime.now(), name='second', video_source=workflow.video_source_id,
                             workflow_data=workflow.workflow_data)
    payload = batch_payload([workflow.id, second.id])
    expected_status = 400
    if case == 'stale':
        payload['expected_versions'][str(second.id)] = 0
        expected_status = 409
    elif case == 'missing':
        second.delete_instance()
        expected_status = 404
    elif case == 'template':
        Workflow.update(is_template=True, video_source=None).where(Workflow.id == second.id).execute()
    elif case == 'invalid_field':
        payload['targets'][0]['changes'] = {'runtime_timeout': 30}
    elif case == 'nan':
        payload['targets'][0]['changes'] = {'confidence': float('nan')}
    else:
        payload['targets'].append(deepcopy(payload['targets'][0]))
    payload['dry_run'] = False
    response = client.post('/openapi/v1/workflows/batch-config', json=payload, headers=headers)
    assert response.status_code == expected_status
    assert Workflow.get_by_id(workflow.id).config_version == 1
    assert Workflow.get_by_id(workflow.id).data_dict['nodes'][1]['config']['confidence'] == 0.5


def test_public_batch_late_conflict_rolls_back_all_changes(configuration_api, monkeypatch):
    client, headers, workflow = configuration_api
    second = Workflow.create(created_at=datetime.now(), updated_at=datetime.now(), name='second', video_source=workflow.video_source_id,
                             workflow_data=workflow.workflow_data)
    original = service.validate_workflow_configuration

    def concurrent_validator(data):
        Workflow.update(config_version=2).where(Workflow.id == second.id).execute()
        return original(data)

    # Service defaults are explicit arguments; intercept persistence after preparation.
    original_save = service.save_configurations

    def save_after_concurrent_update(*args, **kwargs):
        concurrent_validator(workflow.data_dict)
        return original_save(*args, **kwargs)

    monkeypatch.setattr(service, 'save_configurations', save_after_concurrent_update)
    payload = {**batch_payload([workflow.id, second.id]), 'dry_run': False}
    response = client.post('/openapi/v1/workflows/batch-config', json=payload, headers=headers)
    assert response.status_code == 409
    assert Workflow.get_by_id(workflow.id).config_version == 1
    assert Workflow.get_by_id(second.id).config_version == 2
    assert Workflow.get_by_id(second.id).data_dict['nodes'][1]['config']['confidence'] == 0.5


def test_entry_late_conflict_does_not_overwrite_concurrent_changes(configuration_api, monkeypatch):
    client, headers, workflow = configuration_api
    original = public_api.apply_entry_configuration

    def concurrent_patch(*args, **kwargs):
        result = original(*args, **kwargs)
        Workflow.update(config_version=2, description='concurrent').where(Workflow.id == workflow.id).execute()
        return result

    monkeypatch.setattr(public_api, 'apply_entry_configuration', concurrent_patch)
    response = put(client, headers, workflow.id, 'roi', regions(), dry_run=False)
    assert response.status_code == 409
    stored = Workflow.get_by_id(workflow.id)
    assert stored.description == 'concurrent'
    assert stored.data_dict == workflow.data_dict


def test_saved_version_causes_existing_source_host_to_reload(configuration_api):
    client, headers, workflow = configuration_api
    VideoSource.update(status='RUNNING').where(VideoSource.id == workflow.video_source_id).execute()
    orchestrator = Orchestrator.__new__(Orchestrator)
    source_id = workflow.video_source_id
    orchestrator.license_entitlements = {'algorithm_ids': None}
    orchestrator.desired_source_ids = {source_id}
    orchestrator.workflow_host_signatures = {source_id: build_workflow_signature([workflow])}
    orchestrator.workflow_hosts = {source_id: {'process': object()}}
    orchestrator._build_active_workflow_groups = lambda: {source_id: [Workflow.get_by_id(workflow.id)]}
    orchestrator._workflow_start_allowed = Mock(return_value=(True, [], []))
    orchestrator._stop_source_host = Mock()
    orchestrator._start_source_host = Mock()
    orchestrator._mark_rotation_batch_starting = Mock()
    orchestrator._get_process_exit_code = Mock(return_value=None)
    orchestrator.manage_workflows()
    orchestrator._stop_source_host.assert_not_called()
    assert put(client, headers, workflow.id, 'roi', regions(), dry_run=False).status_code == 200
    orchestrator.manage_workflows()
    orchestrator._stop_source_host.assert_called_once_with(source_id)
    assert orchestrator._start_source_host.call_args.args[0] == source_id
    assert orchestrator._start_source_host.call_args.args[1][0].config_version == 2


def test_discovery_and_insert_support_legacy_endpoint_aliases():
    data = graph(1)
    data['connections'] = [{'from_node_id': 'source', 'to_node_id': 'algorithm', 'toPort': 'image'}]
    updated, _change = apply_entry_configuration(data, 'roi_draw', regions())
    assert updated['connections'][0]['to_node_id'] == 'algorithm'
    assert updated['connections'][0]['toPort'] == 'image'
    assert updated['connections'][0]['to'] == 'algorithm'
    assert updated['connections'][0]['to_port'] == 'image'
    assert data['connections'][0]['from_node_id'] == 'source'


@pytest.mark.parametrize('stored_type', ['roi', 'roi_draw'])
def test_existing_roi_node_is_updated_without_creating_a_duplicate(configuration_api, stored_type):
    client, headers, workflow = configuration_api
    data = workflow.data_dict
    data['nodes'].append({'id': 'legacy-roi', 'type': stored_type, 'data': {'roi_regions': regions()}})
    data['connections'] = [{'from': 'source', 'to': 'legacy-roi'}, {'from': 'legacy-roi', 'to': 'algorithm'}]
    Workflow.update(workflow_data=json.dumps(data)).where(Workflow.id == workflow.id).execute()
    response = put(client, headers, workflow.id, 'roi', regions(), dry_run=False)
    assert response.status_code == 200
    assert response.json['data']['change']['action'] == 'updated'
    assert response.json['data']['change']['node_id'] == 'legacy-roi'
    assert len(Workflow.get_by_id(workflow.id).data_dict['nodes']) == len(data['nodes'])


@pytest.mark.parametrize('bad_field,bad_value', [('anchor', 'invalid'), ('anchor', []),
    ('mode', {}), ('name', 42), ('unexpected', True)])
def test_roi_rejects_invalid_metadata(configuration_api, bad_field, bad_value):
    client, headers, workflow = configuration_api
    value = regions()
    value[0][bad_field] = bad_value
    response = put(client, headers, workflow.id, 'roi', value, dry_run=False)
    assert response.status_code == 400
    assert response.json['details'][0]['field'] == 'roi_regions[0]'
    assert response.json['details'][0]['workflow_id'] == workflow.id


def test_multiple_roi_regions_and_inactive_workflow_persist(configuration_api):
    client, headers, workflow = configuration_api
    Workflow.update(is_active=False).where(Workflow.id == workflow.id).execute()
    value = regions() + [{**regions()[0], 'name': '出口', 'mode': 'pre_mask'}]
    response = put(client, headers, workflow.id, 'roi', value, dry_run=False)
    assert response.status_code == 200
    stored = Workflow.get_by_id(workflow.id)
    assert stored.is_active is False
    roi = next(node for node in stored.data_dict['nodes'] if node['type'] == 'roi')
    assert create_node_data(roi).roi_regions == value


def test_repeated_schedule_update_keeps_graph_and_other_data(configuration_api):
    client, headers, workflow = configuration_api
    response = put(client, headers, workflow.id, 'time-schedule', schedule(), dry_run=False)
    node_id = response.json['data']['change']['node_id']
    data = Workflow.get_by_id(workflow.id).data_dict
    next(node for node in data['nodes'] if node['id'] == node_id)['data']['custom'] = 'keep'
    Workflow.update(workflow_data=json.dumps(data)).where(Workflow.id == workflow.id).execute()
    value = schedule()
    value['7'] = []
    response = put(client, headers, workflow.id, 'time-schedule', value, 2, dry_run=False)
    assert response.status_code == 200
    assert response.json['data']['change']['action'] == 'updated'
    stored = Workflow.get_by_id(workflow.id)
    assert stored.data_dict['connections'] == data['connections']
    assert next(node for node in stored.data_dict['nodes'] if node['id'] == node_id)['data'] == {
        'weeklySchedule': value, 'custom': 'keep',
    }


def test_public_batch_changes_alert_and_schedule_once_per_workflow(configuration_api):
    client, headers, workflow = configuration_api
    result = put(client, headers, workflow.id, 'time-schedule', schedule(), dry_run=False)
    node_id = result.json['data']['change']['node_id']
    value = schedule()
    value['7'] = []
    payload = {
        'workflow_ids': [workflow.id], 'expected_versions': {str(workflow.id): 2}, 'dry_run': False,
        'targets': [
            {'workflow_ids': [workflow.id], 'node_id': node_id, 'node_type': 'time_schedule',
             'changes': {'weekly_schedule': value}},
            {'workflow_ids': [workflow.id], 'node_id': 'alert', 'node_type': 'alert',
             'changes': {'trigger_condition': {'enable': True, 'mode': 'ratio', 'window_size': 10, 'threshold': 0.6},
                         'suppression': {'enable': True, 'seconds': 60}}},
        ],
    }
    response = client.post('/openapi/v1/workflows/batch-config', json=payload, headers=headers)
    assert response.status_code == 200
    stored = Workflow.get_by_id(workflow.id)
    assert stored.config_version == 3
    assert next(node for node in stored.data_dict['nodes'] if node['id'] == node_id)['data']['weeklySchedule'] == value
    alert = next(node for node in stored.data_dict['nodes'] if node['id'] == 'alert')
    assert alert['data']['triggerCondition']['threshold'] == 0.6
    assert alert['data']['suppression'] == {'enable': True, 'seconds': 60}


def test_invalid_stored_node_data_returns_structured_error(configuration_api):
    client, headers, workflow = configuration_api
    data = workflow.data_dict
    data['nodes'][1]['config'] = []
    Workflow.update(workflow_data=json.dumps(data)).where(Workflow.id == workflow.id).execute()
    response = client.get(f'/openapi/v1/workflows/{workflow.id}/configuration', headers=headers)
    assert response.status_code == 400
    assert response.json['code'] == 'invalid_workflow_structure'
