from datetime import datetime
import json

import pytest
from flask import Flask
from peewee import SqliteDatabase

from app.core.database_models import ApiKey, SystemSetting, User, VideoSource, Workflow
from app.core import license_service
from app.core.orchestrator import Orchestrator
from app.core.workflow_runtime import build_template_workflow_data
from app.web.api.auth import generate_token
from app.web.api import public_api


@pytest.fixture
def public_api_client(monkeypatch):
    test_db = SqliteDatabase(':memory:', pragmas={'foreign_keys': 1})
    models = [User, ApiKey, VideoSource, Workflow, SystemSetting]

    with test_db.bind_ctx(models):
        test_db.connect()
        test_db.create_tables(models)
        monkeypatch.setattr(public_api, 'db', test_db)
        monkeypatch.setattr(license_service, 'db', test_db)
        monkeypatch.setattr(
            public_api.mediamtx_client,
            'register_path',
            lambda source_code, source_url: None,
        )

        admin = User.create(
            username='admin',
            password_hash='unused',
            role='admin',
            created_at=datetime.now(),
        )
        app = Flask(__name__)
        app.config['TESTING'] = True
        public_api.register_public_api(app)
        token = generate_token(admin.id, admin.username, admin.role)
        admin_headers = {'Authorization': f'Bearer {token}'}

        yield app.test_client(), admin_headers

        test_db.close()


def _create_managed_key(client, admin_headers, name='生产集成'):
    response = client.post(
        '/api/system/api-keys',
        json={'name': name},
        headers=admin_headers,
    )
    assert response.status_code == 201
    return response.get_json()['key']


@pytest.mark.parametrize(
    ('endpoint', 'download_name', 'content_marker'),
    [
        (
            '/api/system/openapi/spec',
            'video-ba-pipe-openapi.yaml',
            b'openapi: 3.0.3',
        ),
        (
            '/api/system/openapi/guide',
            'video-ba-pipe-api-usage.md',
            '开放 API'.encode('utf-8'),
        ),
    ],
)
def test_openapi_documents_can_be_downloaded(
    public_api_client,
    endpoint,
    download_name,
    content_marker,
):
    client, admin_headers = public_api_client

    response = client.get(endpoint, headers=admin_headers)

    assert response.status_code == 200
    assert download_name in response.headers['Content-Disposition']
    assert content_marker in response.data


def test_managed_api_key_is_only_returned_in_plaintext_once(public_api_client):
    client, admin_headers = public_api_client
    created = _create_managed_key(client, admin_headers)

    assert created['key'].startswith('vbp_')
    stored = ApiKey.get_by_id(created['id'])
    assert stored.key_hash != created['key']
    assert created['key'] not in stored.key_hash

    listed = client.get('/api/system/api-keys', headers=admin_headers)
    assert listed.status_code == 200
    listed_key = listed.get_json()['keys'][0]
    assert 'key' not in listed_key
    assert listed_key['key_prefix'] == created['key'][:12]


def test_disabled_api_key_is_rejected(public_api_client):
    client, admin_headers = public_api_client
    created = _create_managed_key(client, admin_headers)
    public_headers = {'X-API-Key': created['key']}

    assert client.get('/openapi/v1/workflow-templates', headers=public_headers).status_code == 200

    disabled = client.patch(
        f"/api/system/api-keys/{created['id']}",
        json={'enabled': False},
        headers=admin_headers,
    )
    assert disabled.status_code == 200
    rejected = client.get('/openapi/v1/workflow-templates', headers=public_headers)
    assert rejected.status_code == 401
    assert rejected.get_json()['code'] == 'invalid_api_key'


def test_video_source_create_edit_and_url_update(public_api_client):
    client, admin_headers = public_api_client
    created_key = _create_managed_key(client, admin_headers)
    headers = {'X-API-Key': created_key['key']}

    created = client.post(
        '/openapi/v1/video-sources',
        json={
            'source_code': 'camera-001',
            'name': '东门摄像头',
            'source_url': 'rtsp://camera/old',
            'source_fps': 12,
            'decode_keyframes_only': True,
        },
        headers=headers,
    )
    assert created.status_code == 201
    source = VideoSource.get(VideoSource.source_code == 'camera-001')
    assert source.created_by == 'api-integration'
    assert source.decode_keyframes_only is True

    decode_updated = client.patch(
        '/openapi/v1/video-sources/camera-001',
        json={'decode_keyframes_only': None},
        headers=headers,
    )
    assert decode_updated.status_code == 200
    assert VideoSource.get_by_id(source.id).decode_keyframes_only is None

    forbidden = client.patch(
        '/openapi/v1/video-sources/camera-001',
        json={'source_url': 'rtsp://camera/new'},
        headers=headers,
    )
    assert forbidden.status_code == 400
    assert forbidden.get_json()['code'] == 'field_not_allowed'

    source.status = 'RUNNING'
    source.source_codec = 'h264'
    source.save()
    updated = client.put(
        '/openapi/v1/video-sources/camera-001/source-url',
        json={'source_url': 'rtsp://camera/new'},
        headers=headers,
    )
    assert updated.status_code == 202
    assert updated.get_json()['data']['reload_scheduled'] is True
    source = VideoSource.get_by_id(source.id)
    assert source.source_url == 'rtsp://camera/new'
    assert source.source_codec == 'unknown'

    unchanged = client.put(
        '/openapi/v1/video-sources/camera-001/source-url',
        json={'source_url': 'rtsp://camera/new'},
        headers=headers,
    )
    assert unchanged.status_code == 200
    assert unchanged.get_json()['data']['changed'] is False


@pytest.mark.parametrize('source_code', ['building/camera', 'camera 001', '摄像头-001'])
def test_video_source_rejects_source_codes_unsafe_for_route_paths(
    public_api_client,
    source_code,
):
    client, admin_headers = public_api_client
    created_key = _create_managed_key(client, admin_headers)
    response = client.post(
        '/openapi/v1/video-sources',
        json={
            'source_code': source_code,
            'name': '无效编码测试',
            'source_url': 'rtsp://camera/stream',
        },
        headers={'X-API-Key': created_key['key']},
    )

    assert response.status_code == 400
    assert response.get_json()['code'] == 'invalid_field'
    assert VideoSource.select().count() == 0


@pytest.mark.parametrize('field_name', [
    'source_decode_width',
    'source_decode_height',
    'source_fps',
])
def test_video_source_rejects_fractional_decoder_parameters(
    public_api_client,
    field_name,
):
    client, admin_headers = public_api_client
    created_key = _create_managed_key(client, admin_headers)
    response = client.post(
        '/openapi/v1/video-sources',
        json={
            'source_code': f'fractional-{field_name}',
            'name': '小数参数测试',
            'source_url': 'rtsp://camera/stream',
            field_name: 12.9,
        },
        headers={'X-API-Key': created_key['key']},
    )

    assert response.status_code == 400
    assert response.get_json()['code'] == 'invalid_field'
    assert VideoSource.select().count() == 0


def test_activation_is_idempotent_and_workflows_can_be_filtered(public_api_client):
    client, admin_headers = public_api_client
    created_key = _create_managed_key(client, admin_headers)
    headers = {'X-API-Key': created_key['key']}
    source = VideoSource.create(
        name='Lobby',
        source_code='lobby-001',
        source_url='rtsp://example/lobby',
        created_by='api-integration',
    )
    now = datetime.now()
    template = Workflow.create(
        name='人员检测模板',
        description='模板',
        workflow_data=json.dumps(build_template_workflow_data()),
        is_template=True,
        is_active=False,
        created_at=now,
        updated_at=now,
        created_by='admin',
    )
    payload = {
        'source_code': source.source_code,
        'template_workflow_id': template.id,
    }

    for invalid_template_id in (True, 1.9, '1.9'):
        invalid = client.post(
            '/openapi/v1/workflow-activations',
            json={**payload, 'template_workflow_id': invalid_template_id},
            headers=headers,
        )
        assert invalid.status_code == 400
        assert invalid.get_json()['code'] == 'invalid_field'
    assert Workflow.select().where(Workflow.is_template == False).count() == 0

    first = client.post('/openapi/v1/workflow-activations', json=payload, headers=headers)
    second = client.post('/openapi/v1/workflow-activations', json=payload, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.get_json()['data']['workflow_id'] == second.get_json()['data']['workflow_id']
    assert second.get_json()['data']['created'] is False

    listed = client.get(
        f'/openapi/v1/workflows?source_code={source.source_code}',
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.get_json()['data']['total'] == 1

    workflow_id = first.get_json()['data']['workflow_id']
    deactivated = client.post(
        f'/openapi/v1/workflows/{workflow_id}/deactivate', headers=headers
    )
    assert deactivated.status_code == 200
    assert Workflow.get_by_id(workflow_id).is_active is False


def test_decoder_source_signature_detects_url_and_runtime_changes():
    source = type(
        'Source',
        (),
        {
            'id': 7,
            'source_code': 'camera-007',
            'source_url': 'rtsp://camera/old',
            'source_decode_width': 960,
            'source_decode_height': 540,
            'source_fps': 10,
            'source_codec': 'h264',
            'decode_keyframes_only': None,
        },
    )()
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.decode_keyframes_only = False
    signature = orchestrator._runtime_source_config_signature(source)
    orchestrator.running_processes = {
        source.id: {'source_config_signature': signature}
    }

    assert orchestrator._source_config_requires_reload(source) is False
    source.source_url = 'rtsp://camera/new'
    assert orchestrator._source_config_requires_reload(source) is True
    source.source_url = 'rtsp://camera/old'
    source.source_fps = 15
    assert orchestrator._source_config_requires_reload(source) is True
    source.source_fps = 10
    source.decode_keyframes_only = True
    assert orchestrator._source_config_requires_reload(source) is True
