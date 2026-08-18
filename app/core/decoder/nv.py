from app import logger
from app.core.decoder.async_dec import AsyncFFmpegDecoder
from app.core.video_probe import elementary_stream_muxer, ffmpeg_codec_name


class FFmpegNVDECDecoder(AsyncFFmpegDecoder):
    """
    FFmpeg + NVDEC 硬件解码器
    优点: 易于集成，支持多种格式，GPU硬件加速
    """

    def _build_ffmpeg_command(self) -> list:
        """构建NVDEC硬件解码命令"""
        input_format = self.config.get('input_format', 'h264')
        decoder_name = f'{ffmpeg_codec_name(input_format)}_cuvid'
        demuxer = elementary_stream_muxer(input_format)
        self.keyframes_only = bool(self.config.get('keyframes_only', False))
        logger.info(
            f"构建 FFmpeg NVDEC 硬件解码命令: "
            f"demuxer={demuxer}, decoder={decoder_name}, device={self.device_id}, "
            f"keyframes_only={self.keyframes_only}"
        )

        return [
            'ffmpeg',
            *(['-skip_frame', 'nokey'] if self.keyframes_only else []),
            '-fflags', '+genpts+discardcorrupt',
            '-f', demuxer,
            '-hwaccel', 'cuda',
            '-hwaccel_device', str(self.device_id),
            '-c:v', decoder_name,
            '-i', 'pipe:0',
            *self._output_fps_filter_args(),
            '-f', 'rawvideo',
            '-pix_fmt', self.output_format,
            '-s', f'{self.width}x{self.height}',
            'pipe:1'
        ]
