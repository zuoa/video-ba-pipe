import struct
from dataclasses import dataclass
from typing import Iterable, List

from app.core.frame_utils import get_frame_size_bytes, normalize_pixel_format


COMPRESSED_METADATA_FORMAT = '<QQQ?dd'
COMPRESSED_TIMESTAMP_FORMAT = '<d'
COMPRESSED_LENGTH_FORMAT = '<I'


@dataclass(frozen=True)
class SourceProfile:
    name: str
    width: int
    height: int
    source_fps: int


@dataclass(frozen=True)
class ResourceSettings:
    pixel_format: str
    analysis_target_fps: int
    analysis_buffer_seconds: int
    decoder_output_queue_size: int
    recording_enabled: bool
    recording_fps: int
    recording_buffer_duration: int
    recording_compressed_max_bytes: int


@dataclass(frozen=True)
class SourceResourceEstimate:
    name: str
    width: int
    height: int
    source_fps: int
    analysis_fps: int
    frame_size_bytes: int
    analysis_buffer_bytes: int
    decoder_queue_bytes: int
    recording_buffer_bytes: int

    @property
    def total_shared_memory_bytes(self) -> int:
        return self.analysis_buffer_bytes + self.recording_buffer_bytes

    @property
    def total_runtime_buffer_bytes(self) -> int:
        return self.total_shared_memory_bytes + self.decoder_queue_bytes


@dataclass(frozen=True)
class ResourceEstimateSummary:
    sources: List[SourceResourceEstimate]

    @property
    def total_analysis_buffer_bytes(self) -> int:
        return sum(source.analysis_buffer_bytes for source in self.sources)

    @property
    def total_recording_buffer_bytes(self) -> int:
        return sum(source.recording_buffer_bytes for source in self.sources)

    @property
    def total_decoder_queue_bytes(self) -> int:
        return sum(source.decoder_queue_bytes for source in self.sources)

    @property
    def total_shared_memory_bytes(self) -> int:
        return sum(source.total_shared_memory_bytes for source in self.sources)

    @property
    def total_runtime_buffer_bytes(self) -> int:
        return sum(source.total_runtime_buffer_bytes for source in self.sources)


def compressed_ringbuffer_size_bytes(
    fps: int,
    duration_seconds: int,
    max_frame_bytes: int,
) -> int:
    capacity = max(0, int(fps)) * max(0, int(duration_seconds))
    metadata_size = struct.calcsize(COMPRESSED_METADATA_FORMAT)
    timestamp_size = struct.calcsize(COMPRESSED_TIMESTAMP_FORMAT) * capacity
    length_size = struct.calcsize(COMPRESSED_LENGTH_FORMAT) * capacity
    return metadata_size + timestamp_size + length_size + int(max_frame_bytes) * capacity


def estimate_source_resources(
    profile: SourceProfile,
    settings: ResourceSettings,
) -> SourceResourceEstimate:
    pixel_format = normalize_pixel_format(settings.pixel_format)
    width = int(profile.width)
    height = int(profile.height)
    source_fps = max(1, int(profile.source_fps))
    analysis_fps = max(1, min(source_fps, int(settings.analysis_target_fps)))
    frame_size = get_frame_size_bytes(width, height, pixel_format)
    analysis_capacity = analysis_fps * max(0, int(settings.analysis_buffer_seconds))
    analysis_metadata_bytes = struct.calcsize('QQQ?dd') + 8 * analysis_capacity
    analysis_buffer_bytes = analysis_metadata_bytes + frame_size * analysis_capacity
    decoder_queue_bytes = frame_size * max(1, int(settings.decoder_output_queue_size))
    recording_buffer_bytes = 0
    if settings.recording_enabled:
        recording_buffer_bytes = compressed_ringbuffer_size_bytes(
            fps=settings.recording_fps,
            duration_seconds=settings.recording_buffer_duration,
            max_frame_bytes=settings.recording_compressed_max_bytes,
        )

    return SourceResourceEstimate(
        name=profile.name,
        width=width,
        height=height,
        source_fps=source_fps,
        analysis_fps=analysis_fps,
        frame_size_bytes=frame_size,
        analysis_buffer_bytes=analysis_buffer_bytes,
        decoder_queue_bytes=decoder_queue_bytes,
        recording_buffer_bytes=recording_buffer_bytes,
    )


def estimate_resource_summary(
    profiles: Iterable[SourceProfile],
    settings: ResourceSettings,
) -> ResourceEstimateSummary:
    return ResourceEstimateSummary(
        sources=[estimate_source_resources(profile, settings) for profile in profiles]
    )


def format_bytes(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"
