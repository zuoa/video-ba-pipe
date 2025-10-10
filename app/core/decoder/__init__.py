import threading
from queue import Full, Empty, Queue
from typing import Optional

import numpy as np

from app.core.decoder.async_dec import AsyncSoftwareDecoder
from app import logger
from app.core.decoder.base import FFmpegSoftwareDecoder, OpenCVDecoder, PyNvCodecDecoder, GStreamerNVDecoder, BaseDecoder, DecoderStatus
from app.core.decoder.nv import FFmpegNVDECDecoder
from app.core.decoder.vt import FFmpegVTDecoder


class DecoderFactory:
    """解码器工厂"""

    @staticmethod
    def create_decoder(
            decoder_type: str,
            decoder_id: int,
            **kwargs
    ) -> BaseDecoder:
        """
        创建解码器实例

        Args:
            decoder_type: 解码器类型
                - 'ffmpeg_nvdec' / 'nvdec': FFmpeg NVDEC硬件解码
                - 'ffmpeg_sw' / 'ffmpeg': FFmpeg软件解码
                - 'opencv': OpenCV软件解码（图片）
                - 'pynvcodec': PyNvCodec原生NVDEC
                - 'gstreamer': GStreamer NVDEC
            decoder_id: 解码器ID
            **kwargs: 其他参数

        Returns:
            解码器实例
        """
        decoders = {
            'ffmpeg_nvdec': FFmpegNVDECDecoder,
            'nvdec': FFmpegNVDECDecoder,
            'ffmpeg_sw': AsyncSoftwareDecoder, # <--- 更新为新的异步解码器
            'ffmpeg': AsyncSoftwareDecoder,    # <--- 更新为新的异步解码器
            'opencv': OpenCVDecoder,
            'pynvcodec': PyNvCodecDecoder,
            'gstreamer': GStreamerNVDecoder,
            'ffmpeg_videotoolbox': FFmpegVTDecoder,  # <--- 添加这一行
            'vtdec': FFmpegVTDecoder,  # <--- 添加这一行 (作为简称)
        }

        decoder_class = decoders.get(decoder_type.lower())
        if not decoder_class:
            raise ValueError(
                f"不支持的解码器类型: {decoder_type}\n"
                f"支持的类型: {list(decoders.keys())}"
            )

        decoder = decoder_class(decoder_id, **kwargs)
        decoder.initialize()
        return decoder



if __name__ == "__main__":
    print("=== NVDEC解码器修复版本 ===\n")

    # 示例1: FFmpeg硬件解码器
    print("示例1: FFmpeg NVDEC硬件解码器")
    try:
        with DecoderFactory.create_decoder(
                'ffmpeg_nvdec',
                decoder_id=0,
                input_format='h264',
                output_format='rgb24',
                width=1920,
                height=1080
        ) as decoder:
            test_data = b'\x00' * 1024
            frame = decoder.decode(test_data)
            stats = decoder.get_statistics()
            print(f"统计: {stats}\n")
    except Exception as e:
        print(f"错误: {e}\n")

    # 示例2: FFmpeg软件解码器
    print("示例2: FFmpeg软件解码器（CPU）")
    try:
        with DecoderFactory.create_decoder(
                'ffmpeg_sw',
                decoder_id=1,
                input_format='h264',
                output_format='rgb24',
                width=1920,
                height=1080,
                threads=4
        ) as decoder:
            print(f"软件解码器初始化成功\n")
    except Exception as e:
        print(f"错误: {e}\n")

    # 示例3: OpenCV解码器（图片）
    print("示例3: OpenCV软件解码器")
    try:
        decoder = DecoderFactory.create_decoder(
            'opencv',
            decoder_id=2,
            image_format='jpg',
            width=1920,
            height=1080
        )
        print("OpenCV解码器初始化成功")
        decoder.close()
    except Exception as e:
        print(f"错误: {e}\n")

    # 示例4: PyNvCodec解码器
    print("\n示例4: PyNvCodec解码器")
    try:
        decoder = DecoderFactory.create_decoder(
            'pynvcodec',
            decoder_id=3,
            codec='h264',
            device_id=0
        )
        decoder.close()
    except Exception as e:
        print(f"PyNvCodec不可用: {e}")

    # 性能对比说明
    print("\n" + "=" * 50)
    print("解码器选择建议:")
    print("=" * 50)
    print("📌 硬件解码（需要NVIDIA GPU）:")
    print("   - FFmpeg NVDEC: 易用，兼容性好")
    print("   - PyNvCodec: 性能最佳，延迟最低")
    print("   - GStreamer: 适合RTSP流")
    print("\n📌 软件解码（纯CPU）:")
    print("   - FFmpeg软解: 通用视频格式，多线程")
    print("   - OpenCV: 图片格式（JPEG/PNG等）")
    print("\n性能排序: PyNvCodec > FFmpeg NVDEC > FFmpeg软解 > OpenCV")