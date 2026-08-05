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
