from app import logger
from app.core.decoder.async_dec import AsyncFFmpegDecoder


class FFmpegRKMPPDecoder(AsyncFFmpegDecoder):
    """
    FFmpeg + Rockchip MPP 硬件解码器
    依赖 ffmpeg 编译时启用 rkmpp（例如 h264_rkmpp / hevc_rkmpp）。
    """

    _DEMUXER_MAP = {
        'h264': 'h264',
        'h265': 'hevc',
        'hevc': 'hevc',
        'mjpeg': 'mjpeg',
    }

    _DECODER_MAP = {
        'h264': 'h264_rkmpp',
        'h265': 'hevc_rkmpp',
        'hevc': 'hevc_rkmpp',
        'mjpeg': 'mjpeg_rkmpp',
    }

    def _build_ffmpeg_command(self) -> list:
        input_format = (self.config.get('input_format', 'h264') or 'h264').lower()
        demuxer = self._DEMUXER_MAP.get(input_format, input_format)
        decoder = self._DECODER_MAP.get(input_format)

        if not decoder:
            raise ValueError(f'RKMPP 解码器暂不支持输入格式: {input_format}')

        logger.info(f"构建 RKMPP 硬件解码命令: demuxer={demuxer}, decoder={decoder}")

        return [
            'ffmpeg',
            '-skip_frame', 'nokey',
            '-fflags', '+genpts+discardcorrupt',
            '-f', demuxer,
            '-c:v', decoder,
            '-i', 'pipe:0',
            '-f', 'rawvideo',
            '-pix_fmt', self.config.get('output_format', 'rgb24'),
            '-s', f'{self.width}x{self.height}',
            'pipe:1'
        ]
