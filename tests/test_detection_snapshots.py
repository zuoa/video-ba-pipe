import threading
from types import MethodType

import numpy as np

import app.core.workflow_executor as workflow_executor_module
from app.core.workflow_executor import FrameExecutionContext, WorkflowExecutor


def _executor(workflow_id, frame_timestamp, label):
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = workflow_id
    executor._state_lock = threading.Lock()
    executor._latest_algorithm_results = {
        'algorithm-node': {
            'detections': [{'label': label}],
            'roi_mask': None,
            'label_color': '#00FF00',
            'roi_regions': [],
            'frame_timestamp': frame_timestamp,
        },
    }
    return executor


def _context(frame_timestamp):
    return FrameExecutionContext({
        'frame_rgb': np.zeros((4, 4, 3), dtype=np.uint8),
        'frame_timestamp': frame_timestamp,
    })


def test_snapshot_does_not_draw_cached_results_on_a_newer_frame(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow_executor_module, 'DETECTION_SNAPSHOT_ENABLED', True)
    monkeypatch.setattr(workflow_executor_module, 'DETECTION_SNAPSHOT_SAVE_PATH', str(tmp_path))
    monkeypatch.setattr(workflow_executor_module, 'DETECTION_SNAPSHOT_INTERVAL', 0)
    workflow_executor_module.DETECTION_SNAPSHOT_SOURCE_STATES.clear()
    executor = _executor(1, 10.0, 'stale')
    calls = []
    executor._save_visualized_frame = lambda *args, **kwargs: calls.append(args) or True

    executor._maybe_save_detection_snapshot(_context(11.0), 'camera-1')

    assert calls == []
    assert not (tmp_path / 'camera-1.jpg').exists()


def test_same_frame_results_are_aggregated_and_atomically_replaced(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow_executor_module, 'DETECTION_SNAPSHOT_ENABLED', True)
    monkeypatch.setattr(workflow_executor_module, 'DETECTION_SNAPSHOT_SAVE_PATH', str(tmp_path))
    monkeypatch.setattr(workflow_executor_module, 'DETECTION_SNAPSHOT_INTERVAL', 0)
    workflow_executor_module.DETECTION_SNAPSHOT_SOURCE_STATES.clear()
    later_id = _executor(2, 20.0, 'two')
    earlier_id = _executor(1, 20.0, 'one')
    rendered_labels = []

    def fake_save(self, frame, detections, save_path, **kwargs):
        rendered_labels.append([item['label'] for item in detections])
        with open(save_path, 'wb') as snapshot:
            snapshot.write(b'jpeg')
        return True

    later_id._save_visualized_frame = MethodType(fake_save, later_id)
    earlier_id._save_visualized_frame = MethodType(fake_save, earlier_id)

    later_id._maybe_save_detection_snapshot(_context(20.0), 'camera-1')
    earlier_id._maybe_save_detection_snapshot(_context(20.0), 'camera-1')

    assert rendered_labels[-1] == ['one', 'two']
    assert (tmp_path / 'camera-1.jpg').read_bytes() == b'jpeg'
    assert list(tmp_path.glob('*.tmp.jpg')) == []
