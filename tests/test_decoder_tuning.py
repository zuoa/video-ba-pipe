import logging

from app.core.decoder.async_dec import AsyncSoftwareDecoder
from app.core.decoder.base import BaseDecoder


class _DummyDecoder(BaseDecoder):
    def _initialize(self) -> bool:
        return True

    def send_packet(self, data: bytes):
        return None

    def get_frame(self, timeout=1.0):
        return None

    def _cleanup(self):
        return None


def test_base_decoder_output_queue_size_is_configurable():
    decoder = _DummyDecoder(
        decoder_id=1,
        width=320,
        height=240,
        output_queue_size=7,
    )

    assert decoder.output_queue.maxsize == 7


def test_async_software_decoder_builds_ffmpeg_command_with_thread_limit():
    decoder = AsyncSoftwareDecoder(
        decoder_id=1,
        width=960,
        height=540,
        input_format='h264',
        output_format='nv12',
        threads=2,
    )
    decoder.logger = logging.getLogger("test.decoder")

    command = decoder._build_ffmpeg_command()

    assert command[:5] == ['ffmpeg', '-threads', '2', '-skip_frame', 'nokey']
    assert '-f' in command
    assert 'rawvideo' in command
