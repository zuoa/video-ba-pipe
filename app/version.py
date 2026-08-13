import json
import os
import subprocess
from datetime import datetime
from pathlib import Path


DEFAULT_VERSION = "unknown"


def get_git_version(repo_root: Path) -> str | None:
    """Return YYYYMMDD-<7-char SHA> for the latest Git commit, if available."""
    try:
        output = subprocess.check_output(
            ["git", "log", "-1", "--format=%cI%n%H"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        commit_time, commit_hash = output.strip().splitlines()
        commit_date = datetime.fromisoformat(commit_time).strftime("%Y%m%d")
        if len(commit_hash) < 7:
            return None
        return f"{commit_date}-{commit_hash[:7]}"
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def get_app_version() -> str:
    env_version = os.environ.get("APP_VERSION")
    if env_version:
        return env_version

    repo_root = Path(__file__).resolve().parent.parent
    git_version = get_git_version(repo_root)
    if git_version:
        return git_version

    # Source archives and minimal runtime images may contain neither .git nor an
    # injected APP_VERSION. Keep package.json as a compatibility fallback.
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
