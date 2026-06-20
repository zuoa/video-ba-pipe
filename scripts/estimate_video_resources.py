#!/usr/bin/env python3
"""Estimate per-source buffer memory from current Video BA Pipe settings."""

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import (  # noqa: E402
    ANALYSIS_BUFFER_SECONDS,
    ANALYSIS_TARGET_FPS,
    DECODER_OUTPUT_QUEUE_SIZE,
    RECORDING_BUFFER_DURATION,
    RECORDING_COMPRESSED_MAX_BYTES,
    RECORDING_ENABLED,
    RECORDING_FPS,
    VIDEO_FRAME_PIXEL_FORMAT,
)
from app.core.resource_estimator import (  # noqa: E402
    ResourceSettings,
    SourceProfile,
    estimate_resource_summary,
    format_bytes,
)


def parse_source_spec(spec: str, default_fps: int) -> SourceProfile:
    name = spec
    if '=' in spec:
        name, spec = spec.split('=', 1)

    if ':' in spec:
        size_part, fps_part = spec.split(':', 1)
        fps = int(fps_part)
    else:
        size_part = spec
        fps = default_fps

    if 'x' not in size_part.lower():
        raise argparse.ArgumentTypeError(
            f"source spec must look like 1920x1080[:fps] or name=1920x1080[:fps], got {spec!r}"
        )

    width_part, height_part = size_part.lower().split('x', 1)
    return SourceProfile(
        name=name,
        width=int(width_part),
        height=int(height_part),
        source_fps=fps,
    )


def load_profiles_from_database() -> list[SourceProfile]:
    from app.core.database_models import VideoSource, db

    db.connect(reuse_if_open=True)
    try:
        return [
            SourceProfile(
                name=f"{source.id}:{source.source_code}",
                width=source.source_decode_width,
                height=source.source_decode_height,
                source_fps=source.source_fps,
            )
            for source in VideoSource.select().where(VideoSource.enabled == True)
        ]
    finally:
        if not db.is_closed():
            db.close()


def build_settings(args) -> ResourceSettings:
    return ResourceSettings(
        pixel_format=args.pixel_format,
        analysis_target_fps=args.analysis_fps,
        analysis_buffer_seconds=args.analysis_seconds,
        decoder_output_queue_size=args.decoder_queue_size,
        recording_enabled=args.recording_enabled,
        recording_fps=args.recording_fps,
        recording_buffer_duration=args.recording_seconds,
        recording_compressed_max_bytes=args.recording_max_frame_bytes,
    )


def print_summary(summary):
    headers = [
        "source",
        "size",
        "src_fps",
        "ana_fps",
        "frame",
        "analysis_shm",
        "recording_shm",
        "decoder_queue",
        "total_buffers",
    ]
    rows = []
    for source in summary.sources:
        rows.append([
            source.name,
            f"{source.width}x{source.height}",
            str(source.source_fps),
            str(source.analysis_fps),
            format_bytes(source.frame_size_bytes),
            format_bytes(source.analysis_buffer_bytes),
            format_bytes(source.recording_buffer_bytes),
            format_bytes(source.decoder_queue_bytes),
            format_bytes(source.total_runtime_buffer_bytes),
        ])

    widths = [
        max(len(headers[idx]), *(len(row[idx]) for row in rows))
        for idx in range(len(headers))
    ] if rows else [len(header) for header in headers]

    print("  ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)))

    print()
    print(f"sources: {len(summary.sources)}")
    print(f"analysis shm:   {format_bytes(summary.total_analysis_buffer_bytes)}")
    print(f"recording shm:  {format_bytes(summary.total_recording_buffer_bytes)}")
    print(f"decoder queues: {format_bytes(summary.total_decoder_queue_bytes)}")
    print(f"shared memory:  {format_bytes(summary.total_shared_memory_bytes)}")
    print(f"runtime total:  {format_bytes(summary.total_runtime_buffer_bytes)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--source',
        action='append',
        default=[],
        help='Source spec, e.g. 1920x1080:25 or lobby=1280x720:15. Repeatable.',
    )
    parser.add_argument(
        '--count',
        type=int,
        default=1,
        help='Repeat manual --source specs this many times when exactly one source is provided.',
    )
    parser.add_argument('--no-db', action='store_true', help='Do not read enabled sources from the database.')
    parser.add_argument('--pixel-format', default=VIDEO_FRAME_PIXEL_FORMAT)
    parser.add_argument('--analysis-fps', type=int, default=ANALYSIS_TARGET_FPS)
    parser.add_argument('--analysis-seconds', type=int, default=ANALYSIS_BUFFER_SECONDS)
    parser.add_argument('--decoder-queue-size', type=int, default=DECODER_OUTPUT_QUEUE_SIZE)
    parser.add_argument('--recording-enabled', action=argparse.BooleanOptionalAction, default=RECORDING_ENABLED)
    parser.add_argument('--recording-fps', type=int, default=RECORDING_FPS)
    parser.add_argument('--recording-seconds', type=int, default=RECORDING_BUFFER_DURATION)
    parser.add_argument('--recording-max-frame-bytes', type=int, default=RECORDING_COMPRESSED_MAX_BYTES)
    args = parser.parse_args()

    settings = build_settings(args)
    profiles = []

    if args.source:
        parsed = [parse_source_spec(spec, default_fps=args.analysis_fps) for spec in args.source]
        if len(parsed) == 1 and args.count > 1:
            base = parsed[0]
            profiles = [
                SourceProfile(
                    name=f"{base.name}#{idx + 1}",
                    width=base.width,
                    height=base.height,
                    source_fps=base.source_fps,
                )
                for idx in range(args.count)
            ]
        else:
            profiles = parsed
    elif not args.no_db:
        profiles = load_profiles_from_database()

    if not profiles:
        parser.error("no sources found; pass --source or configure enabled sources in the database")

    print_summary(estimate_resource_summary(profiles, settings))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
