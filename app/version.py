import json
import os
from pathlib import Path


DEFAULT_VERSION = "unknown"


def get_app_version() -> str:
    env_version = os.environ.get("APP_VERSION")
    if env_version:
        return env_version

    repo_root = Path(__file__).resolve().parent.parent
    frontend_package_json = repo_root / "frontend" / "package.json"

    try:
        with frontend_package_json.open("r", encoding="utf-8") as f:
            package = json.load(f)
        version = package.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    except (OSError, ValueError, TypeError):
        pass

    return DEFAULT_VERSION
