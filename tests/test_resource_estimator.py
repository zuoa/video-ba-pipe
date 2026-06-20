from app.core.resource_estimator import (
    ResourceSettings,
    SourceProfile,
    compressed_ringbuffer_size_bytes,
    estimate_resource_summary,
    estimate_source_resources,
    format_bytes,
)


def test_estimate_source_resources_uses_nv12_frame_size_and_analysis_fps_cap():
    estimate = estimate_source_resources(
        SourceProfile(name="cam-1", width=1920, height=1080, source_fps=25),
        ResourceSettings(
            pixel_format="nv12",
            analysis_target_fps=2,
            analysis_buffer_seconds=3,
            decoder_output_queue_size=2,
            recording_enabled=True,
            recording_fps=3,
            recording_buffer_duration=32,
            recording_compressed_max_bytes=512 * 1024,
        ),
    )

    frame_size = 1920 * 1080 * 3 // 2
    assert estimate.analysis_fps == 2
    assert estimate.frame_size_bytes == frame_size
    assert estimate.decoder_queue_bytes == frame_size * 2
    assert estimate.recording_buffer_bytes == compressed_ringbuffer_size_bytes(
        fps=3,
        duration_seconds=32,
        max_frame_bytes=512 * 1024,
    )
    assert estimate.total_runtime_buffer_bytes == (
        estimate.analysis_buffer_bytes
        + estimate.recording_buffer_bytes
        + estimate.decoder_queue_bytes
    )


def test_estimate_resource_summary_sums_sources():
    settings = ResourceSettings(
        pixel_format="nv12",
        analysis_target_fps=1,
        analysis_buffer_seconds=3,
        decoder_output_queue_size=1,
        recording_enabled=False,
        recording_fps=5,
        recording_buffer_duration=30,
        recording_compressed_max_bytes=512 * 1024,
    )

    summary = estimate_resource_summary(
        [
            SourceProfile(name="cam-1", width=640, height=480, source_fps=10),
            SourceProfile(name="cam-2", width=640, height=480, source_fps=10),
        ],
        settings,
    )

    assert len(summary.sources) == 2
    assert summary.total_recording_buffer_bytes == 0
    assert summary.total_runtime_buffer_bytes == sum(
        source.total_runtime_buffer_bytes for source in summary.sources
    )


def test_format_bytes_uses_binary_units():
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(1024 * 1024) == "1.00 MB"
