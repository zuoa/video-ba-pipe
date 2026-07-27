import json
import subprocess


class VideoCodecProbeError(RuntimeError):
    pass


_CODEC_ALIASES = {
    "h264": "h264",
    "avc": "h264",
    "avc1": "h264",
    "h265": "h265",
    "hevc": "h265",
    "hev1": "h265",
    "hvc1": "h265",
    "mjpeg": "mjpeg",
    "jpeg": "mjpeg",
}


def normalize_video_codec(codec, *, allow_unknown: bool = False) -> str:
    normalized = str(codec or "").strip().lower()
    if allow_unknown and normalized in {"", "auto", "unknown"}:
        return "unknown"
    resolved = _CODEC_ALIASES.get(normalized)
    if resolved is None:
        raise ValueError(f"Unsupported video codec: {codec or 'unknown'}")
    return resolved


def elementary_stream_muxer(codec: str) -> str:
    normalized = normalize_video_codec(codec)
    return {
        "h264": "h264",
        "h265": "hevc",
        "mjpeg": "mjpeg",
    }[normalized]


def ffmpeg_codec_name(codec: str) -> str:
    normalized = normalize_video_codec(codec)
    return "hevc" if normalized == "h265" else normalized


def probe_video_codec(
    source: str,
    *,
    transport: str = "tcp",
    timeout_seconds: float = 15.0,
) -> str:
    command = ["ffprobe", "-v", "error"]
    if source.lower().startswith(("rtsp://", "rtsps://")):
        command.extend(["-rtsp_transport", transport])
    command.extend(
        [
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "json",
            source,
        ]
    )

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise VideoCodecProbeError("ffprobe is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoCodecProbeError(
            f"video codec probe timed out after {timeout_seconds:g}s"
        ) from exc

    if result.returncode != 0:
        raise VideoCodecProbeError(
            f"ffprobe failed while detecting the video codec (exit={result.returncode})"
        )

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise VideoCodecProbeError("ffprobe returned invalid JSON") from exc

    streams = payload.get("streams") or []
    if not streams:
        raise VideoCodecProbeError("ffprobe did not find a video stream")
    try:
        return normalize_video_codec(streams[0].get("codec_name"))
    except ValueError as exc:
        raise VideoCodecProbeError(str(exc)) from exc
