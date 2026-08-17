from datetime import datetime

import pytest
from flask import Flask
from peewee import SqliteDatabase

from app.core.database_models import User, VideoSource, Workflow
from app.web.api import preview as preview_api
from app.web.api.auth import generate_token


@pytest.fixture
def preview_client(monkeypatch):
    test_db = SqliteDatabase(':memory:')
    models = [User, VideoSource, Workflow]
    with test_db.bind_ctx(models):
        test_db.connect()
        test_db.create_tables(models)
        user = User.create(
            username='preview-user',
            password_hash='unused',
            role='user',
            created_at=datetime.now(),
        )
        source = VideoSource.create(
            name='Lobby',
            source_code='lobby-1',
            source_url='rtsp://camera/live',
            created_by=user.username,
        )
        Workflow.create(
            name='preview-workflow',
            workflow_data='{}',
            is_active=True,
            is_template=False,
            video_source=source,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            created_by=user.username,
        )
        monkeypatch.setattr(preview_api, 'MEDIAMTX_ENABLED', True)
        app = Flask(__name__)
        app.config['TESTING'] = True
        preview_api.register_preview_api(app)
        token = generate_token(user.id, user.username, user.role)
        yield app.test_client(), {'Authorization': f'Bearer {token}'}, source
        test_db.close()


def test_ensure_retries_after_refreshing_availability(preview_client, monkeypatch):
    client, headers, source = preview_client
    registrations = iter([False, True])
    monkeypatch.setattr(
        preview_api.mediamtx_client,
        'register_path',
        lambda source_code, source_url: next(registrations),
    )
    monkeypatch.setattr(preview_api.mediamtx_client, 'is_available', lambda force=False: True)

    response = client.post(f'/api/preview/ensure/{source.id}', headers=headers)

    assert response.status_code == 200
    assert response.get_json()['success'] is True


def test_ensure_fails_when_registration_still_fails(preview_client, monkeypatch):
    client, headers, source = preview_client
    monkeypatch.setattr(
        preview_api.mediamtx_client,
        'register_path',
        lambda source_code, source_url: False,
    )
    monkeypatch.setattr(preview_api.mediamtx_client, 'is_available', lambda force=False: True)

    response = client.post(f'/api/preview/ensure/{source.id}', headers=headers)

    assert response.status_code == 502
    assert response.get_json()['success'] is False
