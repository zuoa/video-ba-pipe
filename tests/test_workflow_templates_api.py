from datetime import datetime

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
