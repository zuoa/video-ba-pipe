from datetime import datetime
import json

import pytest
from flask import Flask
from peewee import IntegrityError, SqliteDatabase

from app.core.database_models import User, VideoSource, Workflow
from app.web.api import workflows as workflows_api
from app.web.api.auth import generate_token


@pytest.fixture
def workflow_api(monkeypatch):
    test_db = SqliteDatabase(':memory:', pragmas={'foreign_keys': 1})
    models = [User, VideoSource, Workflow]

    with test_db.bind_ctx(models):
        test_db.connect()
        test_db.create_tables(models)
        monkeypatch.setattr(workflows_api, 'db', test_db)

        user = User.create(
            username='template-owner',
            password_hash='unused',
            role='user',
            created_at=datetime.now(),
        )
        source = VideoSource.create(
            name='Lobby',
            source_code='lobby-001',
            source_url='rtsp://example/lobby',
            created_by=user.username,
        )

        app = Flask(__name__)
        app.config['TESTING'] = True
        workflows_api.register_workflows_api(app)
        token = generate_token(user.id, user.username, user.role)
        headers = {'Authorization': f'Bearer {token}'}

        yield app.test_client(), headers, source

        test_db.close()


def _create_template(client, headers):
    response = client.post(
        '/api/workflows',
        json={
            'name': '人员检测模板',
            'description': '可复用模板',
            'is_template': True,
            'is_active': True,
        },
        headers=headers,
    )
    assert response.status_code == 201
    return response.get_json()['id']


def test_template_is_unbound_inactive_and_cannot_activate(workflow_api):
    client, headers, _source = workflow_api
    template_id = _create_template(client, headers)

    template = Workflow.get_by_id(template_id)
    assert template.is_template is True
    assert template.is_active is False
    assert template.video_source_id is None
    assert template.data_dict['nodes'][0]['dataId'] is None

    response = client.post(f'/api/workflows/{template_id}/activate', headers=headers)
    assert response.status_code == 400
    assert response.get_json()['error'] == '编排模板不可激活'


def test_only_template_can_copy_and_provenance_is_recorded(workflow_api):
    client, headers, source = workflow_api
    template_id = _create_template(client, headers)

    response = client.post(
        f'/api/workflows/{template_id}/batch-copy',
        json={'source_ids': [source.id]},
        headers=headers,
    )
    assert response.status_code == 200
    copied_id = response.get_json()['created'][0]['workflow_id']

    copied = Workflow.get_by_id(copied_id)
    assert copied.is_template is False
    assert copied.source_template_id == template_id
    assert copied.video_source_id == source.id
    assert copied.is_active is False

    delete_response = client.delete(f'/api/workflows/{template_id}', headers=headers)
    assert delete_response.status_code == 409
    assert delete_response.get_json()['code'] == 'template_in_use'

    response = client.post(
        f'/api/workflows/{copied_id}/batch-copy',
        json={'source_ids': [source.id]},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.get_json()['error'] == '只有编排模板可以复制'


def test_batch_copy_can_activate_created_workflow(workflow_api):
    client, headers, source = workflow_api
    template_id = _create_template(client, headers)

    response = client.post(
        f'/api/workflows/{template_id}/batch-copy',
        json={'source_ids': [source.id], 'is_active': True},
        headers=headers,
    )

    assert response.status_code == 200
    created = response.get_json()['created'][0]
    assert created['is_active'] is True
    assert Workflow.get_by_id(created['workflow_id']).is_active is True


def test_batch_copy_rejects_non_boolean_activation_option(workflow_api):
    client, headers, source = workflow_api
    template_id = _create_template(client, headers)

    response = client.post(
        f'/api/workflows/{template_id}/batch-copy',
        json={'source_ids': [source.id], 'is_active': 'true'},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.get_json()['error'] == 'is_active 必须是布尔值'


def test_template_source_pair_is_unique(workflow_api):
    client, headers, source = workflow_api
    template_id = _create_template(client, headers)
    copy_url = f'/api/workflows/{template_id}/batch-copy'

    first = client.post(copy_url, json={'source_ids': [source.id]}, headers=headers)
    assert first.status_code == 200

    duplicate = client.post(copy_url, json={'source_ids': [source.id]}, headers=headers)
    assert duplicate.status_code == 409
    payload = duplicate.get_json()
    assert payload['errors'][0]['code'] == 'duplicate_template_source'
    assert payload['errors'][0]['existing_workflow_id'] is not None

    template = Workflow.get_by_id(template_id)
    with pytest.raises(IntegrityError):
        Workflow.create(
            name='数据库约束测试',
            description='',
            workflow_data=template.workflow_data,
            is_active=False,
            is_template=False,
            source_template=template,
            video_source=source,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            created_by='template-owner',
        )


def test_changing_derived_workflow_source_preserves_uniqueness(workflow_api):
    client, headers, source = workflow_api
    second_source = VideoSource.create(
        name='Warehouse',
        source_code='warehouse-001',
        source_url='rtsp://example/warehouse',
        created_by='template-owner',
    )
    template_id = _create_template(client, headers)
    response = client.post(
        f'/api/workflows/{template_id}/batch-copy',
        json={'source_ids': [source.id, second_source.id]},
        headers=headers,
    )
    assert response.status_code == 200
    copied_ids = [item['workflow_id'] for item in response.get_json()['created']]
    first_copy = Workflow.get_by_id(copied_ids[0])
    graph = first_copy.data_dict
    source_node = graph['nodes'][0]
    source_node['dataId'] = second_source.id
    source_node['videoSourceId'] = second_source.id

    conflict = client.put(
        f'/api/workflows/{first_copy.id}',
        json={'workflow_data': graph},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.get_json()['code'] == 'duplicate_template_source'


def _batch_config_graph(source_id):
    weekly_schedule = {
        str(day): [{'start': '00:00', 'end': '23:59'}]
        for day in range(1, 8)
    }
    return {
        'nodes': [
            {
                'id': 'source-node',
                'type': 'source',
                'name': '视频源',
                'dataId': source_id,
                'videoSourceId': source_id,
            },
            {
                'id': 'algorithm-node',
                'type': 'algorithm',
                'name': '人员检测',
                'dataId': 12,
                'config': {'confidence': 0.5, 'interval_seconds': 1},
            },
            {
                'id': 'alert-node',
                'type': 'alert',
                'name': '输出告警',
                'data': {
                    'triggerCondition': {'enable': False},
                    'suppression': {'enable': False},
                },
            },
            {
                'id': 'time-node',
                'type': 'time_schedule',
                'name': '启用时间',
                'data': {'weeklySchedule': weekly_schedule},
            },
        ],
        'connections': [],
    }


def _create_batch_config_workflow(source, *, name='批量配置测试'):
    return Workflow.create(
        name=name,
        description='',
        workflow_data=json.dumps(_batch_config_graph(source.id)),
        is_active=True,
        is_template=False,
        video_source=source,
        config_version=1,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        created_by='template-owner',
    )


def test_batch_config_preview_and_apply_are_field_scoped(workflow_api):
    client, headers, source = workflow_api
    workflow = _create_batch_config_workflow(source)
    payload = {
        'workflow_ids': [workflow.id],
        'expected_versions': {str(workflow.id): 1},
        'targets': [{
            'workflow_ids': [workflow.id],
            'node_id': 'algorithm-node',
            'node_type': 'algorithm',
            'changes': {'confidence': 0.7, 'interval_seconds': 2.5},
        }],
        'dry_run': True,
    }

    preview = client.post('/api/workflows/batch-config', json=payload, headers=headers)
    assert preview.status_code == 200
    assert preview.get_json()['summary'] == {
        'workflow_count': 1,
        'active_count': 1,
        'node_change_count': 1,
    }
    workflow = Workflow.get_by_id(workflow.id)
    assert workflow.config_version == 1
    assert workflow.data_dict['nodes'][1]['config']['confidence'] == 0.5

    payload['dry_run'] = False
    applied = client.post('/api/workflows/batch-config', json=payload, headers=headers)
    assert applied.status_code == 200
    workflow = Workflow.get_by_id(workflow.id)
    config = workflow.data_dict['nodes'][1]['config']
    assert config['confidence'] == 0.7
    assert config['confidence_override_enabled'] is True
    assert config['interval_seconds'] == 2.5
    assert workflow.data_dict['nodes'][0]['dataId'] == source.id
    assert workflow.config_version == 2


def test_batch_config_validation_failure_is_atomic(workflow_api):
    client, headers, source = workflow_api
    first = _create_batch_config_workflow(source, name='第一条')
    second_source = VideoSource.create(
        name='Second',
        source_code='second-001',
        source_url='rtsp://example/second',
        created_by='template-owner',
    )
    second = _create_batch_config_workflow(second_source, name='第二条')
    payload = {
        'workflow_ids': [first.id, second.id],
        'expected_versions': {str(first.id): 1, str(second.id): 1},
        'targets': [
            {
                'workflow_ids': [first.id],
                'node_id': 'algorithm-node',
                'node_type': 'algorithm',
                'changes': {'confidence': 0.9},
            },
            {
                'workflow_ids': [second.id],
                'node_id': 'missing-node',
                'node_type': 'algorithm',
                'changes': {'confidence': 0.9},
            },
        ],
        'dry_run': False,
    }

    response = client.post('/api/workflows/batch-config', json=payload, headers=headers)
    assert response.status_code == 400
    assert Workflow.get_by_id(first.id).data_dict['nodes'][1]['config']['confidence'] == 0.5
    assert Workflow.get_by_id(first.id).config_version == 1
    assert Workflow.get_by_id(second.id).config_version == 1


def test_batch_config_rejects_stale_version(workflow_api):
    client, headers, source = workflow_api
    workflow = _create_batch_config_workflow(source)
    payload = {
        'workflow_ids': [workflow.id],
        'expected_versions': {str(workflow.id): 0},
        'targets': [{
            'workflow_ids': [workflow.id],
            'node_id': 'alert-node',
            'node_type': 'alert',
            'changes': {'suppression': {'enable': True, 'seconds': 120}},
        }],
        'dry_run': False,
    }

    response = client.post('/api/workflows/batch-config', json=payload, headers=headers)
    assert response.status_code == 409
    assert response.get_json()['failures'][0]['code'] == 'version_conflict'
    assert Workflow.get_by_id(workflow.id).config_version == 1


def test_batch_config_updates_alert_and_weekly_schedule(workflow_api):
    client, headers, source = workflow_api
    workflow = _create_batch_config_workflow(source)
    weekday_schedule = {str(day): [] for day in range(1, 8)}
    weekday_schedule['1'] = [{'start': '08:00', 'end': '18:00'}]
    payload = {
        'workflow_ids': [workflow.id],
        'expected_versions': {str(workflow.id): 1},
        'targets': [
            {
                'workflow_ids': [workflow.id],
                'node_id': 'alert-node',
                'node_type': 'alert',
                'changes': {
                    'trigger_condition': {
                        'enable': True,
                        'window_size': 20,
                        'mode': 'count',
                        'threshold': 3,
                    },
                    'suppression': {'enable': True, 'seconds': 90},
                },
            },
            {
                'workflow_ids': [workflow.id],
                'node_id': 'time-node',
                'node_type': 'time_schedule',
                'changes': {'weekly_schedule': weekday_schedule},
            },
        ],
        'dry_run': False,
    }

    response = client.post('/api/workflows/batch-config', json=payload, headers=headers)
    assert response.status_code == 200
    nodes = {node['id']: node for node in Workflow.get_by_id(workflow.id).data_dict['nodes']}
    assert nodes['alert-node']['data']['triggerCondition']['threshold'] == 3
    assert nodes['alert-node']['data']['suppression']['seconds'] == 90
    assert nodes['time-node']['data']['weeklySchedule'] == weekday_schedule


def test_batch_config_concurrent_update_is_preserved_and_rolls_back_batch(
    workflow_api,
    monkeypatch,
):
    client, headers, source = workflow_api
    first = _create_batch_config_workflow(source, name='事务中的第一条')
    second_source = VideoSource.create(
        name='Concurrent',
        source_code='concurrent-001',
        source_url='rtsp://example/concurrent',
        created_by='template-owner',
    )
    second = _create_batch_config_workflow(second_source, name='被并发修改的第二条')
    original_apply = workflows_api.apply_batch_node_changes
    call_count = 0

    def apply_with_concurrent_update(*args, **kwargs):
        nonlocal call_count
        result = original_apply(*args, **kwargs)
        call_count += 1
        if call_count == 2:
            concurrent_data = second.data_dict
            concurrent_data['nodes'][1]['config']['confidence'] = 0.6
            (
                Workflow.update(
                    workflow_data=json.dumps(concurrent_data),
                    config_version=2,
                )
                .where(Workflow.id == second.id)
                .execute()
            )
        return result

    monkeypatch.setattr(
        workflows_api,
        'apply_batch_node_changes',
        apply_with_concurrent_update,
    )
    payload = {
        'workflow_ids': [first.id, second.id],
        'expected_versions': {str(first.id): 1, str(second.id): 1},
        'targets': [
            {
                'workflow_ids': [first.id],
                'node_id': 'algorithm-node',
                'node_type': 'algorithm',
                'changes': {'confidence': 0.9},
            },
            {
                'workflow_ids': [second.id],
                'node_id': 'algorithm-node',
                'node_type': 'algorithm',
                'changes': {'confidence': 0.9},
            },
        ],
        'dry_run': False,
    }

    response = client.post('/api/workflows/batch-config', json=payload, headers=headers)

    assert response.status_code == 409
    first_after = Workflow.get_by_id(first.id)
    second_after = Workflow.get_by_id(second.id)
    assert first_after.config_version == 1
    assert first_after.data_dict['nodes'][1]['config']['confidence'] == 0.5
    assert second_after.config_version == 2
    assert second_after.data_dict['nodes'][1]['config']['confidence'] == 0.6
