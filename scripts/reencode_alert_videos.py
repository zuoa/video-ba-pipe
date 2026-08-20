#!/usr/bin/env python3
"""Re-encode existing alert mp4 files to browser-playable H.264.

Nginx can return 206 for MPEG-4 Part 2 (mp4v) files, but Chrome/Safari
cannot decode them. Walk VIDEO_SAVE_PATH and transcode those files in place.

Usage:
    python scripts/reencode_alert_videos.py
    python scripts/reencode_alert_videos.py /data/videos
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import VIDEO_SAVE_PATH
from app.core.video_recorder import ensure_browser_compatible_mp4, probe_mp4_video_codec


def iter_mp4_files(root: Path):
    for path in sorted(root.rglob('*.mp4')):
        if path.is_file() and '.browser.tmp.' not in path.name:
            yield path


def main() -> int:
    parser = argparse.ArgumentParser(description='将告警录像转码为浏览器可播放的 H.264')
    parser.add_argument(
        'root',
        nargs='?',
        default=VIDEO_SAVE_PATH,
        help='录像目录，默认 VIDEO_SAVE_PATH',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='只列出需要转码的文件，不写入',
    )
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f'目录不存在: {root}', file=sys.stderr)
        return 1

    converted = 0
    skipped = 0
    failed = 0
    for path in iter_mp4_files(root):
        codec = probe_mp4_video_codec(str(path))
        if codec == 'h264':
            skipped += 1
            continue
        size = os.path.getsize(path)
        print(f'{path.relative_to(root)} codec={codec or "unknown"} size={size}')
        if args.dry_run:
            converted += 1
            continue
        if ensure_browser_compatible_mp4(str(path)):
            converted += 1
            print(f'  -> h264')
        else:
            failed += 1
            print(f'  -> FAILED', file=sys.stderr)

    print(f'done converted={converted} skipped_h264={skipped} failed={failed}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
