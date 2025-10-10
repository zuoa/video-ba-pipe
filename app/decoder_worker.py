import argparse
import os
import signal
from multiprocessing import resource_tracker

from app.core.decoder import DecoderFactory
from app.core.ringbuffer import VideoRingBuffer
from app.core.streamer import RTSPStreamer


def main(args):
    # 连接到由编排器创建的缓冲区
    buffer = VideoRingBuffer(name=args.buffer, create=False)

    shm_name = args.buffer if os.name == 'nt' else f"/{args.buffer}"
    resource_tracker.unregister(shm_name, 'shared_memory')

    # 初始化解码管道
    streamer = RTSPStreamer(rtsp_url=args.url)
    decoder = DecoderFactory.create_decoder(
        'ffmpeg_sw',
        decoder_id=401,
        width=1920,
        height=1080,
        input_format='h264',
        output_format='rgb24'
    )
    streamer.add_packet_handler(decoder.send_packet)
    streamer.start()

    def cleanup(s, f):
        streamer.stop()
        decoder.close()
        buffer.close()
        exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print(f"[DecoderWorker:{os.getpid()}] 开始解码 {args.url} -> {args.buffer}")
    while True:
        frame = decoder.get_frame(timeout=2.0)
        if frame is not None:
            buffer.write(frame)
        elif not streamer.is_running():
            break

    cleanup(None, None)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True, help="RTSP源地址")
    parser.add_argument('--buffer', required=True, help="共享内存缓冲区名称")
    args = parser.parse_args()
    main(args)
