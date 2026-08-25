from app import jobs


class _FakeEvent:
    def __init__(self):
        self.wait_calls = 0

    def set(self):
        pass

    def wait(self):
        self.wait_calls += 1
        return True


class _FakeCleaner:
    def __init__(self, calls):
        self.calls = calls

    def run_startup_filesystem_cleanup(self):
        self.calls.append("cleanup-once")

    def start(self):
        self.calls.append("cleaner-start")

    def stop(self):
        self.calls.append("cleaner-stop")


class _FakeDelivery:
    def __init__(self, calls):
        self.calls = calls

    def start(self):
        self.calls.append("delivery-start")

    def stop(self):
        self.calls.append("delivery-stop")


class _FakeDatabase:
    def __init__(self, calls):
        self.calls = calls

    def is_closed(self):
        return False

    def close(self):
        self.calls.append("db-close")


def test_jobs_process_owns_background_worker_lifecycle(monkeypatch):
    calls = []
    event = _FakeEvent()

    monkeypatch.setattr(jobs.threading, "Event", lambda: event)
    monkeypatch.setattr(jobs.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(jobs, "verify_database_schema", lambda: calls.append("verify"))
    monkeypatch.setattr(jobs, "AlertMediaCleaner", lambda: _FakeCleaner(calls))
    monkeypatch.setattr(jobs, "alert_delivery_worker", _FakeDelivery(calls))
    monkeypatch.setattr(jobs, "db", _FakeDatabase(calls))
    monkeypatch.setattr(
        jobs, "start_alert_export_worker", lambda: calls.append("export-start")
    )
    monkeypatch.setattr(
        jobs, "stop_alert_export_worker", lambda: calls.append("export-stop")
    )
    monkeypatch.setattr(
        jobs, "start_face_import_worker", lambda: calls.append("face-start")
    )
    monkeypatch.setattr(
        jobs, "stop_face_import_worker", lambda: calls.append("face-stop")
    )

    jobs.run_jobs()

    assert event.wait_calls == 1
    assert calls == [
        "verify",
        "cleanup-once",
        "export-start",
        "face-start",
        "delivery-start",
        "cleaner-start",
        "cleaner-stop",
        "delivery-stop",
        "face-stop",
        "export-stop",
        "db-close",
    ]
