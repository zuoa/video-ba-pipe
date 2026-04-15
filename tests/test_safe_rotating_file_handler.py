import importlib.util
import logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AJLOG_PATH = PROJECT_ROOT / "app" / "core" / "ajlog.py"
SPEC = importlib.util.spec_from_file_location("test_ajlog_module", AJLOG_PATH)
AJLOG_MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(AJLOG_MODULE)
SafeRotatingFileHandler = AJLOG_MODULE.SafeRotatingFileHandler


def test_safe_rotating_file_handler_rotates_by_size(tmp_path: Path):
    log_path = tmp_path / "logs" / "debug.log"
    handler = SafeRotatingFileHandler(
        filename=str(log_path),
        maxBytes=80,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("test.safe_rotating_file_handler")
    logger.setLevel(logging.INFO)
    logger.handlers = []
    logger.propagate = False
    logger.addHandler(handler)

    try:
        for idx in range(10):
            logger.info("line-%s-xxxxxxxxxxxxxxxxxxxx", idx)
    finally:
        handler.close()
        logger.removeHandler(handler)

    assert log_path.exists()
    assert (tmp_path / "logs" / "debug.log.1").exists()
    assert len(list((tmp_path / "logs").glob("debug.log*"))) <= 3
