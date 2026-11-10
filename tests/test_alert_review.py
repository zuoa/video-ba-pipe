"""Follow-up inspection uses fresh detection evidence and persists its outcome."""

from datetime import datetime, timedelta
from threading import RLock
from types import SimpleNamespace

import numpy as np
import pytest
from peewee import SqliteDatabase

from app.core import workflow_executor as executor_module
from app.core.database_models import Alert, AlertReview, VideoSource, Workflow
from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_validation import _validate_alert_reviews


@pytest.fixture
def review_case(tmp_path, monkeypatch):
    test_db = SqliteDatabase(str(tmp_path / 'reviews.db'))
    models = [VideoSource, Workflow, Alert, AlertReview]
    with test_db.bind_ctx(models):
        test_db.create_tables(models)
        source = VideoSource.create(name='门口', source_code='door', source_url='rtsp://example/door')
        workflow = Workflow.create(name='异常检测', video_source=source,
                                   created_at=datetime.now(), updated_at=datetime.now())
        alert = Alert.create(video_source=source, workflow=workflow,
                             alert_time=datetime.now(), alert_type='problem')
        engine = WorkflowExecutor.__new__(WorkflowExecutor)
        engine.workflow = workflow
        engine.workflow_id = workflow.id
        engine.video_source = source
        engine.connections = [{'from': 'detector', 'to': 'alert'}]
        engine.executed_nodes = ['detector']
        engine.node_results_cache = {'detector': {
            'has_detection': False, 'result': {'detections': [], 'metadata': {}}}}
        engine._state_lock = RLock()
        engine._get_storage_pressure = lambda: SimpleNamespace(allow_media=True)
        engine._find_upstream_roi = lambda _node: []
        engine._save_visualized_frame = lambda **kwargs: True
        monkeypatch.setattr(executor_module, 'FRAME_SAVE_PATH', str(tmp_path))
        monkeypatch.setattr(executor_module, 'save_frame', lambda *_args: None)
        monkeypatch.setattr('app.core.alert_delivery.enqueue_review_delivery', lambda _review: None)
        yield alert, engine
        test_db.close()


def test_review_clears_only_after_three_fresh_negative_samples(review_case):
    alert, engine = review_case
    review = AlertReview.create(alert=alert, workflow=engine.workflow,
                                video_source=engine.video_source, node_id='alert',
                                due_at=datetime.now() - timedelta(minutes=2),
                                started_at=datetime.now() - timedelta(seconds=61),
                                confirmation_started_at=datetime.now() - timedelta(seconds=61),
                                last_frame_timestamp=datetime.now().timestamp() - 5,
                                status='running', sample_count=2, confirmation_seconds=60)
    frame_time = datetime.now().timestamp()
    engine._process_due_reviews({'frame_timestamp': frame_time, 'frame': np.zeros((8, 8, 3), dtype=np.uint8)})
    review = AlertReview.get_by_id(review.id)
    assert review.status == 'cleared'
    assert review.sample_count == 3
    assert review.image and review.image_ori


def test_review_records_present_on_first_hit(review_case):
    alert, engine = review_case
    review = AlertReview.create(alert=alert, workflow=engine.workflow,
                                video_source=engine.video_source, node_id='alert',
                                due_at=datetime.now() - timedelta(minutes=2))
    engine.node_results_cache['detector']['has_detection'] = True
    engine._process_due_reviews({'frame_timestamp': datetime.now().timestamp(),
                                 'frame': np.zeros((8, 8, 3), dtype=np.uint8)})
    review = AlertReview.get_by_id(review.id)
    assert review.status == 'present'
    assert review.sample_count == 1


def test_algorithm_error_never_counts_as_recovery(review_case):
    alert, engine = review_case
    review = AlertReview.create(alert=alert, workflow=engine.workflow,
                                video_source=engine.video_source, node_id='alert',
                                due_at=datetime.now() - timedelta(minutes=2))
    engine.node_results_cache['detector']['result']['metadata']['error'] = 'model unavailable'
    engine._process_due_reviews({'frame_timestamp': datetime.now().timestamp(), 'frame': None})
    review = AlertReview.get_by_id(review.id)
    assert review.status == 'pending'
    assert review.sample_count == 0


def test_stale_cached_detection_never_counts_as_new_sample(review_case):
    alert, engine = review_case
    review = AlertReview.create(alert=alert, workflow=engine.workflow,
                                video_source=engine.video_source, node_id='alert',
                                due_at=datetime.now() - timedelta(minutes=2))
    prior = {'detector': id(engine.node_results_cache['detector'])}
    engine._process_due_reviews({'frame_timestamp': datetime.now().timestamp(), 'frame': None}, prior)
    review = AlertReview.get_by_id(review.id)
    assert review.status == 'pending'
    assert review.sample_count == 0


def test_review_never_uses_frames_from_reassigned_source(review_case):
    alert, engine = review_case
    review = AlertReview.create(alert=alert, workflow=engine.workflow,
                                video_source=engine.video_source, node_id='alert',
                                due_at=datetime.now() - timedelta(minutes=2))
    engine.video_source = VideoSource.create(name='另一入口', source_code='other', source_url='rtsp://example/other')
    engine._process_due_reviews({'frame_timestamp': datetime.now().timestamp(), 'frame': None})
    assert AlertReview.get_by_id(review.id).status == 'pending'


def test_review_gap_restarts_confirmation_without_changing_actual_start(review_case):
    alert, engine = review_case
    started = datetime.now() - timedelta(seconds=70)
    review = AlertReview.create(alert=alert, workflow=engine.workflow,
                                video_source=engine.video_source, node_id='alert',
                                due_at=datetime.now() - timedelta(minutes=2),
                                status='running', started_at=started,
                                confirmation_started_at=started,
                                last_frame_timestamp=datetime.now().timestamp() - 40,
                                sample_count=2, confirmation_seconds=60)
    engine._process_due_reviews({'frame_timestamp': datetime.now().timestamp(), 'frame': None})
    review = AlertReview.get_by_id(review.id)
    assert review.status == 'running'
    assert review.sample_count == 1
    assert review.started_at == started
    assert review.confirmation_started_at > started


@pytest.mark.parametrize('publish_to_mq', [False, True])
def test_review_message_respects_alert_output_setting_after_completion(
    review_case, monkeypatch, publish_to_mq,
):
    alert, engine = review_case
    review = AlertReview.create(alert=alert, workflow=engine.workflow,
                                video_source=engine.video_source, node_id='alert',
                                due_at=datetime.now() - timedelta(minutes=2),
                                publish_to_mq=publish_to_mq)
    engine.node_results_cache['detector']['has_detection'] = True
    delivered = []
    monkeypatch.setattr('app.core.alert_delivery.enqueue_review_delivery', delivered.append)
    engine._process_due_reviews({'frame_timestamp': datetime.now().timestamp(),
                                 'frame': np.zeros((8, 8, 3), dtype=np.uint8)})
    assert AlertReview.get_by_id(review.id).status == 'present'
    assert [item.id for item in delivered] == ([review.id] if publish_to_mq else [])


def test_review_message_waits_for_confirmation_window(review_case, monkeypatch):
    alert, engine = review_case
    review = AlertReview.create(alert=alert, workflow=engine.workflow,
                                video_source=engine.video_source, node_id='alert',
                                due_at=datetime.now() - timedelta(minutes=2),
                                publish_to_mq=True)
    delivered = []
    monkeypatch.setattr('app.core.alert_delivery.enqueue_review_delivery', delivered.append)
    engine._process_due_reviews({'frame_timestamp': datetime.now().timestamp(), 'frame': None})
    assert AlertReview.get_by_id(review.id).status == 'running'
    assert delivered == []


def test_review_configuration_rejects_missing_upstream():
    valid, error = _validate_alert_reviews({
        'nodes': [{'id': 'alert', 'type': 'alert', 'data': {
            'review': {'enabled': True, 'delay_seconds': 3600, 'confirmation_seconds': 60}}}],
        'connections': [],
    })
    assert not valid
    assert '上游' in error
