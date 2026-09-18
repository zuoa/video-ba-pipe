import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.core.database_models import close_database_connection
from app.core import workflow_executor
from app import source_workflow_host


class _ThreadLocalDatabase:
    def __init__(self):
        self._state = threading.local()
        self._lock = threading.Lock()
        self.close_count = 0
        self.closed_event = threading.Event()

    def open_for_current_thread(self):
        self._state.closed = False

    def is_closed(self):
        return getattr(self._state, "closed", True)

    def close(self):
        self._state.closed = True
        with self._lock:
            self.close_count += 1
        self.closed_event.set()


def test_close_database_connection_does_not_open_closed_database():
    database = _ThreadLocalDatabase()

    assert close_database_connection(database) is False
    assert database.close_count == 0

    database.open_for_current_thread()
    assert close_database_connection(database) is True
    assert database.close_count == 1


@pytest.mark.parametrize("raises", [False, True])
def test_workflow_runner_releases_its_thread_connection(monkeypatch, raises):
    database = _ThreadLocalDatabase()
    executed = threading.Event()

    class _Executor:
        def run_once(self, *_args, **_kwargs):
            database.open_for_current_thread()
            executed.set()
            if raises:
                raise RuntimeError("frame failed")

        def stop(self):
            pass

        def cleanup(self):
            pass

    monkeypatch.setattr(source_workflow_host, "db", database)
    runner = source_workflow_host.WorkflowRunner(
        SimpleNamespace(id=7),
        _Executor(),
        max_consecutive_errors=1,
    )
    runner.start()
    runner.submit_frame(object(), 1.0)

    assert executed.wait(timeout=2)
    assert database.closed_event.wait(timeout=2)
    runner.stop()
    runner.join(timeout=2)

    assert not runner.is_alive()
    assert database.close_count == 1


def test_parallel_node_worker_releases_its_thread_connection(monkeypatch):
    database = _ThreadLocalDatabase()
    executor = workflow_executor.WorkflowExecutor.__new__(
        workflow_executor.WorkflowExecutor
    )
    executor._execute_level_node = (
        lambda _node_id, _context: database.open_for_current_thread()
    )
    monkeypatch.setattr(workflow_executor, "db", database)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(
            executor._execute_level_node_in_worker,
            "node-1",
            {},
        ).result(timeout=2)

    assert database.close_count == 1
