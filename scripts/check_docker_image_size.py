#!/usr/bin/env python3
"""Fail CI when a locally loaded Docker image exceeds its role budget."""

from __future__ import annotations

import argparse
import json
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--max-mib", type=float, required=True)
    args = parser.parse_args()

    result = subprocess.run(
        ["docker", "image", "inspect", args.image, "--format", "{{json .Size}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    size_bytes = int(json.loads(result.stdout.strip()))
    size_mib = size_bytes / 1024 / 1024
    print(f"{args.image}: {size_mib:.1f} MiB (budget {args.max_mib:.1f} MiB)")
    if size_mib > args.max_mib:
        raise SystemExit(
            f"image size budget exceeded by {size_mib - args.max_mib:.1f} MiB"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
