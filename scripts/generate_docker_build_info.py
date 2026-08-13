#!/usr/bin/env python3
"""Generate Git-based version metadata passed to application Docker builds."""

import argparse
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def read_git_version(repo_root: Path = REPO_ROOT) -> str:
    """Return YYYYMMDD-<7-char SHA> for the latest commit."""
    output = subprocess.check_output(
        ["git", "log", "-1", "--format=%cI%n%H"],
        cwd=repo_root,
        text=True,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    commit_time, commit_hash = output.strip().splitlines()
    commit_date = datetime.fromisoformat(commit_time).strftime("%Y%m%d")

    if len(commit_hash) < 7:
        raise ValueError("Git returned an invalid commit hash")
    return f"{commit_date}-{commit_hash[:7]}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--format",
        choices=("github", "shell"),
        default="github",
        help="output format (default: GitHub Actions output file)",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    build_time = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    values = {
        "app_version": read_git_version(),
        "build_time": build_time,
    }
    for key, value in values.items():
        rendered_value = shlex.quote(value) if args.format == "shell" else value
        print(f"{key}={rendered_value}")


if __name__ == "__main__":
    main()
