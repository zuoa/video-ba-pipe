#!/usr/bin/env python3
"""Generate the version metadata passed to application Docker builds."""

import argparse
import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VERSION_FILE = REPO_ROOT / "frontend" / "package.json"
VERSION_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]*\Z")


def read_base_version(version_file: Path) -> str:
    with version_file.open(encoding="utf-8") as file:
        version = json.load(file).get("version")

    if (
        not isinstance(version, str)
        or not version.strip()
        or not VERSION_PATTERN.fullmatch(version.strip())
    ):
        raise ValueError(f"Missing version in {version_file}")
    return version.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--version-file",
        type=Path,
        default=DEFAULT_VERSION_FILE,
        help="package.json used as the base application version",
    )
    parser.add_argument(
        "--format",
        choices=("github", "shell"),
        default="github",
        help="output format (default: GitHub Actions output file)",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    base_version = read_base_version(args.version_file)
    build_time = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    build_stamp = now.strftime("%Y%m%d.%H%M%S")

    values = {
        "app_version": f"{base_version}+{build_stamp}",
        "build_time": build_time,
    }
    for key, value in values.items():
        rendered_value = shlex.quote(value) if args.format == "shell" else value
        print(f"{key}={rendered_value}")


if __name__ == "__main__":
    main()
