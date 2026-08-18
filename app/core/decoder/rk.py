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
        output_fps = max(0.0, float(self.config.get('output_fps') or 0.0))
        self.keyframes_only = bool(self.config.get('keyframes_only', False))

        if not decoder:
            raise ValueError(f'RKMPP 解码器暂不支持输入格式: {input_format}')

        logger.info(
            f"构建 RKMPP 硬件解码命令: demuxer={demuxer}, "
            f"decoder={decoder}, keyframes_only={self.keyframes_only}, "
            f"output_fps={output_fps or 'unlimited'}"
        )
        if self.keyframes_only:
            logger.warning(
                "RKMPP 已开启仅关键帧解码；该模式可能在部分 RK3588 码流上产生多 GOP 固定延迟"
            )

        output_filters = []
        if output_fps > 0:
            fps_value = int(output_fps) if output_fps.is_integer() else output_fps
            output_filters = ['-vf', f'fps={fps_value}']

        return [
            'ffmpeg',
            *(['-skip_frame', 'nokey'] if self.keyframes_only else []),
            '-fflags', '+genpts+discardcorrupt',
            '-f', demuxer,
            '-c:v', decoder,
            '-i', 'pipe:0',
            *output_filters,
            '-f', 'rawvideo',
            '-pix_fmt', self.config.get('output_format', 'nv12'),
            '-s', f'{self.width}x{self.height}',
            'pipe:1'
        ]
