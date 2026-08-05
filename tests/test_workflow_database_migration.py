import os
import subprocess
import sys
from pathlib import Path


def test_setup_keeps_deleted_workflow_source_null(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / 'workflow-source-backfill.db'
    script = """
import json
from datetime import datetime

from app.setup_database import setup_database
from app.core.database_models import db, VideoSource, Workflow

setup_database()
db.connect(reuse_if_open=True)
source = VideoSource.create(
    name='Deleted source',
    source_code='deleted-source',
    source_url='rtsp://example/deleted',
)
workflow = Workflow.create(
    name='Stale source workflow',
    workflow_data=json.dumps({
        'nodes': [{'id': 'source', 'type': 'source', 'dataId': source.id}],
        'connections': [],
    }),
    video_source=source,
    created_at=datetime.now(),
    updated_at=datetime.now(),
)
source.delete_instance()
assert Workflow.get_by_id(workflow.id).video_source_id is None
db.close()

setup_database()
db.connect(reuse_if_open=True)
assert Workflow.get_by_id(workflow.id).video_source_id is None
"""
    env = {
        **os.environ,
        'DB_BACKEND': 'sqlite',
        'DB_PATH': str(database_path),
        'PYTHONPATH': str(project_root),
    }

    result = subprocess.run(
        [sys.executable, '-c', script],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_setup_is_atomic_and_recovers_after_failure(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / 'atomic-schema-setup.db'
    script = """
from app import setup_database as setup_module
from app.core.database_models import Algorithm, User, db

original_ensure_default_admin_user = setup_module.ensure_default_admin_user

def fail_during_bootstrap():
    raise RuntimeError('injected schema bootstrap failure')

setup_module.ensure_default_admin_user = fail_during_bootstrap
try:
    setup_module.setup_database()
except RuntimeError as exc:
    assert str(exc) == 'injected schema bootstrap failure'
else:
    raise AssertionError('setup_database() did not propagate the bootstrap failure')

db.connect(reuse_if_open=True)
assert not db.table_exists(Algorithm._meta.table_name)
assert not db.table_exists(User._meta.table_name)
db.close()

try:
    setup_module.verify_database_schema()
except RuntimeError as exc:
    assert 'users' in str(exc)
else:
    raise AssertionError('verify_database_schema() accepted an incomplete schema')

setup_module.ensure_default_admin_user = original_ensure_default_admin_user
setup_module.setup_database()
setup_module.verify_database_schema()
db.connect(reuse_if_open=True)
assert db.table_exists(User._meta.table_name)
assert User.get(User.username == 'admin').role == 'admin'
db.close()
"""
    env = {
        **os.environ,
        'DB_BACKEND': 'sqlite',
        'DB_PATH': str(database_path),
        'PYTHONPATH': str(project_root),
    }

    result = subprocess.run(
        [sys.executable, '-c', script],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
