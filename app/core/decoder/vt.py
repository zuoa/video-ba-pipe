from app import logger
from app.core.decoder.base import FFmpegBaseDecoder


class FFmpegVTDecoder(FFmpegBaseDecoder):
    """
    FFmpeg + VideoToolbox 硬件解码器 (适用于 macOS)
    """

    def _build_ffmpeg_command(self) -> list:
        """构建VideoToolbox硬件解码命令"""
        logger.info("构建 macOS VideoToolbox 硬件解码命令...")

        # VideoToolbox 通常不需要 '-hwaccel' 参数，直接指定解码器即可
        # 它会自动处理硬件加速
        return [
            'ffmpeg',
            # '-loglevel', 'error', # 在生产环境中可以打开，减少日志输出
            '-c:v', f'{self.input_format}_videotoolbox',
            '-i', 'pipe:0',
            '-f', 'rawvideo',
            '-pix_fmt', self.output_format,
            '-s', f'{self.width}x{self.height}',
            'pipe:1'
        ]
