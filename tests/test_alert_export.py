from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
import csv
import zipfile

import pytest
from flask import Flask
from peewee import SqliteDatabase

from app.core import alert_export as export_mod
from app.core import alert_media_cleaner
from app.core.alert_query import apply_alert_filters, build_alert_query, parse_alert_filters
from app.core.database_models import Alert, AlertExportTask, User, VideoSource, Workflow
from app.web.api.auth import generate_token
from app.web.api import alert_exports as export_api


@pytest.fixture
def export_env(tmp_path, monkeypatch):
    test_db = SqliteDatabase(':memory:', pragmas={'foreign_keys': 1})
    models = [User, VideoSource, Workflow, Alert, AlertExportTask]
    frames_dir = tmp_path / 'frames'
    exports_dir = tmp_path / 'exports'
    frames_dir.mkdir()
    exports_dir.mkdir()

    monkeypatch.setattr(export_mod, 'EXPORT_SAVE_PATH', str(exports_dir))
    monkeypatch.setattr(alert_media_cleaner, 'FRAME_SAVE_PATH', str(frames_dir))
    with test_db.bind_ctx(models):
        test_db.connect()
        test_db.create_tables(models)

        admin = User.create(
            username='admin',
            password_hash='unused',
            role='admin',
            created_at=datetime.now(),
        )
        operator = User.create(
            username='operator',
            password_hash='unused',
            role='user',
            created_at=datetime.now(),
        )
        source = VideoSource.create(
            name='东门',
            source_code='gate-east',
            source_url='rtsp://example/east',
            created_by=admin.username,
        )
        other_source = VideoSource.create(
            name='西门',
            source_code='gate-west',
            source_url='rtsp://example/west',
            created_by=operator.username,
        )
        template = Workflow.create(
            name='人员模板',
            is_template=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            created_by=admin.username,
        )
        workflow = Workflow.create(
            name='东门巡检',
            source_template=template,
            video_source=source,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            created_by=admin.username,
        )

        app = Flask(__name__)
        app.config['TESTING'] = True
        export_api.register_alert_exports_api(app)
        admin_headers = {'Authorization': f'Bearer {generate_token(admin.id, admin.username, admin.role)}'}
        operator_headers = {
            'Authorization': f'Bearer {generate_token(operator.id, operator.username, operator.role)}'
        }

        yield {
            'client': app.test_client(),
            'admin_headers': admin_headers,
            'operator_headers': operator_headers,
            'admin': admin,
            'operator': operator,
            'source': source,
            'other_source': other_source,
            'template': template,
            'workflow': workflow,
            'frames_dir': frames_dir,
            'exports_dir': exports_dir,
        }

        test_db.close()


def _write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'fake-image')


def _create_alert(source, *, workflow=None, alert_type='person', created_by='admin', minutes_ago=0, images=None):
    images = images or {}
    return Alert.create(
        video_source=source,
        workflow=workflow,
        alert_time=datetime.now() - timedelta(minutes=minutes_ago),
        alert_type=alert_type,
        alert_level='warning',
        alert_message='检测到人员',
        alert_image=images.get('annotated'),
        alert_image_ori=images.get('original'),
        created_by=created_by,
    )


def _run_created_task(task: AlertExportTask) -> AlertExportTask:
    running = export_mod.mark_task_running(task)
    return export_mod.run_export_task(running)


def test_parse_alert_filters_ignores_client_owner_and_invalid_time():
    filters = parse_alert_filters({
        'task_id': '12',
        'owner': 'attacker',
        'start_time': 'not-a-date',
        'end_time': '2026-08-19T12:00:00',
    })
    assert filters['source_id'] == 12
    assert 'owner' not in filters
    assert 'start_time' not in filters
    assert filters['end_time'] == '2026-08-19T12:00:00'


def test_iso_z_time_filter_matches_naive_local_alerts(export_env):
    from datetime import timezone

    keep = _create_alert(export_env['source'], minutes_ago=10)
    _create_alert(export_env['source'], minutes_ago=200)

    start = (datetime.now() - timedelta(hours=1)).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    end = datetime.now().astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    filters = parse_alert_filters({'start_time': start, 'end_time': end})
    assert build_alert_query(filters).count() == 1
    assert {alert.id for alert in build_alert_query(filters)} == {keep.id}


def test_build_alert_query_matches_list_filters(export_env):
    source = export_env['source']
    other_source = export_env['other_source']
    workflow = export_env['workflow']
    template = export_env['template']

    keep = _create_alert(source, workflow=workflow, alert_type='person', minutes_ago=10)
    _create_alert(other_source, alert_type='phone', created_by='operator', minutes_ago=10)
    _create_alert(source, workflow=workflow, alert_type='person', minutes_ago=200)

    filters = parse_alert_filters({
        'source_id': source.id,
        'workflow_id': workflow.id,
        'source_template_id': template.id,
        'alert_type': 'person',
        'start_time': (datetime.now() - timedelta(hours=1)).isoformat(),
        'end_time': datetime.now().isoformat(),
    })
    ids = {alert.id for alert in build_alert_query(filters)}
    assert ids == {keep.id}

    scoped = apply_alert_filters(Alert.select(), {'owner': 'operator'})
    assert {alert.created_by for alert in scoped} == {'operator'}


def test_export_zip_contains_csv_and_images(export_env):
    source = export_env['source']
    workflow = export_env['workflow']
    frames_dir = export_env['frames_dir']

    annotated = 'east/annotated.jpg'
    original = 'east/original.jpg'
    _write_image(frames_dir / annotated)
    _write_image(frames_dir / original)
    alert = _create_alert(
        source,
        workflow=workflow,
        images={'annotated': annotated, 'original': original},
    )

    task = export_mod.create_export_task({}, username='admin', is_admin=True)
    finished = _run_created_task(task)

    assert finished.status == 'succeeded'
    assert finished.total_count == 1
    assert finished.missing_image_count == 0
    zip_path = export_mod.resolve_export_file(finished.file_path)
    assert zip_path is not None and zip_path.is_file()

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert 'alerts.csv' in names
        assert 'README.txt' in names
        assert f'images/{alert.id}_annotated.jpg' in names
        assert f'images/{alert.id}_original.jpg' in names

        csv_text = zf.read('alerts.csv').decode('utf-8-sig')
        rows = list(csv.DictReader(csv_text.splitlines()))
        assert len(rows) == 1
        assert rows[0]['id'] == str(alert.id)
        assert rows[0]['source_name'] == '东门'
        assert rows[0]['workflow_name'] == '东门巡检'
        assert rows[0]['annotated_image'] == f'images/{alert.id}_annotated.jpg'
        assert rows[0]['original_image'] == f'images/{alert.id}_original.jpg'


def test_missing_images_do_not_fail_export(export_env):
    source = export_env['source']
    _create_alert(source, images={'annotated': 'missing/annotated.jpg'})

    task = export_mod.create_export_task({}, username='admin', is_admin=True)
    finished = _run_created_task(task)

    assert finished.status == 'succeeded'
    assert finished.missing_image_count == 1
    zip_path = export_mod.resolve_export_file(finished.file_path)
    with zipfile.ZipFile(zip_path) as zf:
        rows = list(csv.DictReader(zf.read('alerts.csv').decode('utf-8-sig').splitlines()))
        assert rows[0]['annotated_image'] == ''


def test_create_export_rejects_empty_and_over_limit(export_env, monkeypatch):
    with pytest.raises(export_mod.ExportValidationError, match='没有可导出'):
        export_mod.create_export_task({}, username='admin', is_admin=True)

    _create_alert(export_env['source'])
    monkeypatch.setattr(export_mod, 'MAX_EXPORT_RECORDS', 0)
    with pytest.raises(export_mod.ExportValidationError, match='超过单次上限'):
        export_mod.create_export_task({}, username='admin', is_admin=True)


def test_owner_scope_and_admin_visibility(export_env):
    source = export_env['source']
    other_source = export_env['other_source']
    _create_alert(source, created_by='admin')
    _create_alert(other_source, created_by='operator')

    admin_task = export_mod.create_export_task({}, username='admin', is_admin=True)
    operator_task = export_mod.create_export_task({}, username='operator', is_admin=False)
    assert admin_task.total_count == 2
    assert operator_task.total_count == 1


def test_public_export_url_helpers():
    assert export_mod.public_export_url('12/alerts_export_12_20260820.zip') == (
        '/media/exports/12/alerts_export_12_20260820.zip'
    )
    assert export_mod.x_accel_redirect_path('12/alerts_export_12_20260820.zip') == (
        '/internal/media/exports/12/alerts_export_12_20260820.zip'
    )
    assert export_mod.public_export_url('') is None
    assert export_mod.x_accel_redirect_path(None) is None


def test_create_export_api_returns_json_on_unexpected_error(export_env, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError('disk full')

    monkeypatch.setattr(export_api, 'create_export_task', boom)
    response = export_env['client'].post(
        '/api/alert-exports',
        json={},
        headers=export_env['admin_headers'],
    )
    assert response.status_code == 500
    assert response.get_json()['error'] == '创建导出任务失败: disk full'


def test_api_create_list_download_and_delete(export_env):
    source = export_env['source']
    frames_dir = export_env['frames_dir']
    _write_image(frames_dir / 'east/a.jpg')
    _write_image(frames_dir / 'east/o.jpg')
    _create_alert(source, images={'annotated': 'east/a.jpg', 'original': 'east/o.jpg'})

    client = export_env['client']
    headers = export_env['admin_headers']

    created = client.post('/api/alert-exports', json={'start_time': '2020-01-01T00:00:00.000Z'}, headers=headers)
    assert created.status_code == 201
    task_id = created.get_json()['id']
    assert created.get_json()['status'] == 'pending'

    duplicate = client.post('/api/alert-exports', json={}, headers=headers)
    assert duplicate.status_code == 409

    finished = _run_created_task(AlertExportTask.get_by_id(task_id))
    assert finished.status == 'succeeded'

    listed = client.get('/api/alert-exports', headers=headers)
    assert listed.status_code == 200
    assert listed.get_json()['pagination']['total'] == 1
    listed_task = listed.get_json()['data'][0]
    assert listed_task['downloadable'] is True
    assert listed_task['file_url'] == f'/media/exports/{finished.file_path}'

    download = client.get(f'/api/alert-exports/{task_id}/download', headers=headers)
    assert download.status_code == 200
    assert download.mimetype == 'application/zip'
    assert zipfile.is_zipfile(BytesIO(download.data))

    public = client.get(listed_task['file_url'])
    assert public.status_code == 200
    assert zipfile.is_zipfile(BytesIO(public.data))

    accel = client.get(
        f'/api/alert-exports/{task_id}/download',
        headers={**headers, 'X-Accel-Redirect-Enabled': 'true'},
    )
    assert accel.status_code == 200
    assert accel.headers['X-Accel-Redirect'] == f'/internal/media/exports/{finished.file_path}'
    assert accel.data == b''

    anonymous = client.get(f'/api/alert-exports/{task_id}/download')
    assert anonymous.status_code == 401

    deleted = client.delete(f'/api/alert-exports/{task_id}', headers=headers)
    assert deleted.status_code == 200
    assert AlertExportTask.select().count() == 0


def test_operator_cannot_access_admin_export(export_env):
    _create_alert(export_env['source'], created_by='admin')
    client = export_env['client']
    created = client.post('/api/alert-exports', json={}, headers=export_env['admin_headers'])
    task_id = created.get_json()['id']

    forbidden = client.get(f'/api/alert-exports/{task_id}', headers=export_env['operator_headers'])
    assert forbidden.status_code == 403

    listed = client.get('/api/alert-exports', headers=export_env['operator_headers'])
    assert listed.get_json()['pagination']['total'] == 0


def test_cancel_pending_and_running_export(export_env):
    _create_alert(export_env['source'])
    client = export_env['client']
    headers = export_env['admin_headers']
    created = client.post('/api/alert-exports', json={}, headers=headers)
    task_id = created.get_json()['id']

    cancelled = client.post(f'/api/alert-exports/{task_id}/cancel', headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.get_json()['status'] == 'cancelled'

    again = client.post(f'/api/alert-exports/{task_id}/cancel', headers=headers)
    assert again.status_code == 400


def test_download_rejects_path_traversal(export_env):
    _create_alert(export_env['source'])
    task = export_mod.create_export_task({}, username='admin', is_admin=True)
    finished = _run_created_task(task)
    finished.file_path = '../secret.zip'
    finished.save()

    client = export_env['client']
    response = client.get(
        f'/api/alert-exports/{finished.id}/download',
        headers=export_env['admin_headers'],
    )
    assert response.status_code == 404

    public = client.get('/media/exports/../secret.zip')
    assert public.status_code == 404


def test_stale_pending_task_does_not_block_new_export(export_env):
    _create_alert(export_env['source'])
    stuck = export_mod.create_export_task({}, username='admin', is_admin=True)
    AlertExportTask.update(
        created_at=datetime.now() - timedelta(seconds=export_mod.STALE_PENDING_SECONDS + 5)
    ).where(AlertExportTask.id == stuck.id).execute()

    replacement = export_mod.create_export_task({}, username='admin', is_admin=True)
    assert replacement.id != stuck.id
    assert AlertExportTask.get_by_id(stuck.id).status == 'failed'


def test_cleanup_expired_exports(export_env):
    _create_alert(export_env['source'])
    task = export_mod.create_export_task({}, username='admin', is_admin=True)
    finished = _run_created_task(task)
    zip_path = export_mod.resolve_export_file(finished.file_path)
    assert zip_path.is_file()

    AlertExportTask.update(expires_at=datetime.now() - timedelta(hours=1)).where(
        AlertExportTask.id == finished.id
    ).execute()
    removed = export_mod.cleanup_expired_exports()
    assert removed == 1
    assert not zip_path.exists()
    assert AlertExportTask.select().count() == 0
