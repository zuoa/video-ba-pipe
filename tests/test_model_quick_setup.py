import json
from datetime import datetime

import pytest
from flask import Flask
from peewee import SqliteDatabase

from app.core.database_models import Algorithm, MLModel, SystemSetting, User, VideoSource, Workflow
from app.core import license_service
from app.web.api import models as models_api
from app.web.api.auth import generate_token


@pytest.fixture
def quick_setup_api(monkeypatch):
    test_db = SqliteDatabase(':memory:', pragmas={'foreign_keys': 1})
    bound_models = [User, VideoSource, Algorithm, MLModel, Workflow, SystemSetting]

    with test_db.bind_ctx(bound_models):
        test_db.connect()
        test_db.create_tables(bound_models)
        monkeypatch.setattr(models_api, 'db', test_db)
        monkeypatch.setattr(license_service, 'db', test_db)

        user = User.create(
            username='quick-admin',
            password_hash='unused',
            role='admin',
            created_at=datetime.now(),
        )
        app = Flask(__name__)
        app.config['TESTING'] = True
        models_api.register_models_api(app)
        token = generate_token(user.id, user.username, user.role)
        headers = {'Authorization': f'Bearer {token}'}

        yield app.test_client(), headers, user

        test_db.close()


def _model(name='园区人员检测', model_type='YOLO', framework='ultralytics', enabled=True):
    now = datetime.now()
    return MLModel.create(
        name=name,
        filename='detector.pt',
        file_path='/tmp/detector.pt',
        file_size=1024,
        model_type=model_type,
        framework=framework,
        version='v1.0',
        enabled=enabled,
        created_at=now,
        updated_at=now,
        uploaded_by='quick-admin',
    )


def test_quick_setup_creates_algorithm_and_three_node_template(quick_setup_api):
    client, headers, _user = quick_setup_api
    model = _model()

    preview = client.get(f'/api/models/{model.id}/quick-setup', headers=headers)
    assert preview.status_code == 200
    preview_data = preview.get_json()
    assert preview_data['eligible'] is True
    assert preview_data['script']['path'] == models_api.QUICK_SETUP_SCRIPT_PATH
    assert preview_data['defaults']['algorithm_name'] == '园区人员检测算法'

    response = client.post(
        f'/api/models/{model.id}/quick-setup',
        json={
            'algorithm_name': '人员检测算法',
            'template_name': '人员检测告警模板',
        },
        headers=headers,
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload['algorithm']['created'] is True
    assert payload['workflow_template']['created'] is True

    algorithm = Algorithm.get_by_id(payload['algorithm']['id'])
    assert algorithm.script_path == models_api.QUICK_SETUP_SCRIPT_PATH
    assert algorithm.config_dict['model_id'] == model.id
    assert algorithm.config_dict['backend'] == 'auto'
    assert algorithm.config_dict['confidence'] == 0.6
    assert algorithm.config_dict['nms_iou'] == 0.7
    assert algorithm.ext_config['runtime_timeout'] == 15
    assert algorithm.ext_config['memory_limit_mb'] == 256
    assert algorithm.ext_config['quick_create']['model_id'] == model.id

    template = Workflow.get_by_id(payload['workflow_template']['id'])
    assert template.is_template is True
    assert template.is_active is False
    assert template.video_source_id is None
    graph = template.data_dict
    assert [node['type'] for node in graph['nodes']] == ['source', 'algorithm', 'alert']
    assert graph['nodes'][0]['dataId'] is None
    assert graph['nodes'][1]['dataId'] == algorithm.id
    assert graph['nodes'][2]['data']['alertLevel'] == 'warning'
    assert graph['nodes'][2]['data']['alertMessage'] == '模型 园区人员检测 检测到目标'
    assert len(graph['connections']) == 2


@pytest.mark.parametrize(
    ('model_type', 'framework'),
    [
        ('YOLO', 'ultralytics'),
        ('ONNX', 'onnx'),
        ('RKNN', 'rknn'),
        ('RKNN', 'rknnlite'),
    ],
)
def test_quick_setup_supports_generic_detector_backends(
    quick_setup_api,
    model_type,
    framework,
):
    client, headers, _user = quick_setup_api
    model = _model(
        name=f'{model_type}-{framework}',
        model_type=model_type,
        framework=framework,
    )

    response = client.get(f'/api/models/{model.id}/quick-setup', headers=headers)
    assert response.status_code == 200
    assert response.get_json()['eligible'] is True


def test_quick_setup_reuses_resources_and_recreates_missing_template(quick_setup_api):
    client, headers, _user = quick_setup_api
    model = _model()

    first = client.post(f'/api/models/{model.id}/quick-setup', headers=headers)
    assert first.status_code == 201
    first_payload = first.get_json()

    repeated = client.post(f'/api/models/{model.id}/quick-setup', headers=headers)
    assert repeated.status_code == 200
    repeated_payload = repeated.get_json()
    assert repeated_payload['algorithm'] == {
        **first_payload['algorithm'],
        'created': False,
    }
    assert repeated_payload['workflow_template'] == {
        **first_payload['workflow_template'],
        'created': False,
    }

    Workflow.get_by_id(first_payload['workflow_template']['id']).delete_instance()
    repaired = client.post(f'/api/models/{model.id}/quick-setup', headers=headers)
    assert repaired.status_code == 201
    repaired_payload = repaired.get_json()
    assert repaired_payload['algorithm']['id'] == first_payload['algorithm']['id']
    assert repaired_payload['algorithm']['created'] is False
    assert repaired_payload['workflow_template']['created'] is True


def test_quick_setup_does_not_reuse_another_users_resources(quick_setup_api):
    client, first_headers, _user = quick_setup_api
    model = _model()
    first = client.post(f'/api/models/{model.id}/quick-setup', headers=first_headers)
    assert first.status_code == 201

    second_user = User.create(
        username='second-admin',
        password_hash='unused',
        role='admin',
        created_at=datetime.now(),
    )
    second_token = generate_token(second_user.id, second_user.username, second_user.role)
    second_headers = {'Authorization': f'Bearer {second_token}'}
    second = client.post(
        f'/api/models/{model.id}/quick-setup',
        json={
            'algorithm_name': '第二管理员算法',
            'template_name': '第二管理员模板',
        },
        headers=second_headers,
    )

    assert second.status_code == 201
    assert second.get_json()['algorithm']['id'] != first.get_json()['algorithm']['id']
    assert Algorithm.select().count() == 2
    assert Workflow.select().count() == 2


@pytest.mark.parametrize(
    ('model_type', 'framework', 'enabled', 'reason_fragment'),
    [
        ('PyTorch', 'pytorch', True, '模型类型'),
        ('YOLO', 'pytorch', True, '模型框架'),
        ('YOLO', 'ultralytics', False, '模型已禁用'),
    ],
)
def test_quick_setup_rejects_ineligible_models(
    quick_setup_api,
    model_type,
    framework,
    enabled,
    reason_fragment,
):
    client, headers, _user = quick_setup_api
    model = _model(
        name=f'{model_type}-{framework}-{enabled}',
        model_type=model_type,
        framework=framework,
        enabled=enabled,
    )

    preview = client.get(f'/api/models/{model.id}/quick-setup', headers=headers)
    assert preview.status_code == 200
    assert preview.get_json()['eligible'] is False
    assert reason_fragment in preview.get_json()['reason']

    response = client.post(f'/api/models/{model.id}/quick-setup', headers=headers)
    assert response.status_code == 400
    assert response.get_json()['code'] == 'quick_setup_ineligible'
    assert Algorithm.select().count() == 0
    assert Workflow.select().count() == 0


def test_quick_setup_name_conflict_does_not_create_partial_resources(quick_setup_api):
    client, headers, _user = quick_setup_api
    model = _model()
    Algorithm.create(
        name='园区人员检测算法',
        description='unrelated',
        script_path='templates/simple_yolo_detector.py',
        script_config=json.dumps({'model_id': model.id}),
        ext_config_json='{}',
        created_at=datetime.now(),
        updated_at=datetime.now(),
        created_by='quick-admin',
    )

    response = client.post(f'/api/models/{model.id}/quick-setup', headers=headers)
    assert response.status_code == 409
    assert response.get_json()['code'] == 'algorithm_name_conflict'
    assert Algorithm.select().count() == 1
    assert Workflow.select().count() == 0


def test_quick_setup_rolls_back_algorithm_when_template_creation_fails(
    quick_setup_api,
    monkeypatch,
):
    client, headers, _user = quick_setup_api
    model = _model()

    def fail_create(**_kwargs):
        raise RuntimeError('template storage failed')

    monkeypatch.setattr(Workflow, 'create', fail_create)
    response = client.post(f'/api/models/{model.id}/quick-setup', headers=headers)

    assert response.status_code == 500
    assert Algorithm.select().count() == 0
    assert Workflow.select().count() == 0
