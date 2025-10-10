from app.core.decoder.base import FFmpegBaseDecoder


class FFmpegNVDECDecoder(FFmpegBaseDecoder):
    """
    FFmpeg + NVDEC 硬件解码器
    优点: 易于集成，支持多种格式，GPU硬件加速
    """

    def _build_ffmpeg_command(self) -> list:
        """构建NVDEC硬件解码命令"""
        return [
            'ffmpeg',
            '-hwaccel', 'cuda',
            '-hwaccel_device', str(self.device_id),
            '-c:v', f'{self.input_format}_cuvid',
            '-i', 'pipe:0',
            '-f', 'rawvideo',
            '-pix_fmt', self.output_format,
            '-s', f'{self.width}x{self.height}',
            'pipe:1'
        ]
