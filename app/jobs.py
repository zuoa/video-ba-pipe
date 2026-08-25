"""Run database-backed background jobs in one dedicated process."""

from __future__ import annotations

import signal
import threading

from app import logger
from app.core.alert_delivery import alert_delivery_worker
from app.core.alert_export import start_alert_export_worker, stop_alert_export_worker
from app.core.alert_media_cleaner import AlertMediaCleaner
from app.core.database_models import db
from app.setup_database import verify_database_schema
from app.web.api.faces import start_face_import_worker, stop_face_import_worker


def run_jobs() -> None:
    """Start singleton background workers and block until the process is stopped."""
    stop_event = threading.Event()

    def request_stop(signum, _frame):
        logger.info("Jobs process received signal %s", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    verify_database_schema()
    media_cleaner = AlertMediaCleaner()
    # Prefer persisted storage settings; the cleaner falls back to environment
    # defaults only when the database is unavailable.
    media_cleaner.run_startup_filesystem_cleanup()

    started = []
    try:
        start_alert_export_worker()
        started.append(stop_alert_export_worker)
        start_face_import_worker()
        started.append(stop_face_import_worker)
        alert_delivery_worker.start()
        started.append(alert_delivery_worker.stop)
        media_cleaner.start()
        started.append(media_cleaner.stop)
        logger.info("Dedicated jobs process started")
        stop_event.wait()
    finally:
        for stop_worker in reversed(started):
            try:
                stop_worker()
            except Exception:
                logger.exception("Failed to stop background worker")
        if not db.is_closed():
            db.close()
        logger.info("Dedicated jobs process stopped")


if __name__ == "__main__":
    run_jobs()
