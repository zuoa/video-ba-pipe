import signal

from app.core.orchestrator import Orchestrator
from app.setup_database import setup_database

#
# if __name__ == "__main__":
#     # --- 1. 初始化 ---
#     RTSP_URL = "rtsp://admin:codvision120@192.168.201.120:554/Streaming/Channels/1"
#
#     TARGET_FPS = 3  # 我们希望每秒处理5帧
#     PROCESS_INTERVAL = 1.0 / TARGET_FPS  # 计算出每帧的处理间隔（秒）
#     last_process_time = 0
#     SAVE_DIR = './saved_frames'
#     os.makedirs(SAVE_DIR, exist_ok=True)
#
#
#     # 独立创建 Streamer
#     streamer = RTSPStreamer(rtsp_url=RTSP_URL)
#
#     # 直接通过工厂创建我们新的、自带异步功能的解码器
#     # 注意：这里不再需要 ThreadedDecoder 包装器了！
#     decoder = DecoderFactory.create_decoder(
#         'ffmpeg_sw',  # 或者 'ffmpeg_videotoolbox'
#         decoder_id=401,
#         width=1920,
#         height=1080,
#         input_format='h264',
#         output_format='rgb24'
#     )
#
#     # --- 2. 连接 ---
#     # 将解码器的 send_packet 方法作为回调
#     streamer.add_packet_handler(decoder.send_packet)
#     print("✅ Streamer 和 异步Decoder 已连接。")
#
#     try:
#         # --- 3. 启动 ---
#         # decoder.initialize() 已经在工厂里调用过了
#         streamer.start()
#
#         print("🚀 管道已启动，等待解码帧... (按 Ctrl+C 退出)")
#
#         frame_count = 0
#         start_time = time.time()
#
#         while streamer.is_running():
#             current_time = time.time()
#
#             # 检查是否到达了处理下一帧的时间
#             if current_time - last_process_time >= PROCESS_INTERVAL:
#                 last_process_time = current_time
#
#                 # 从解码器队列中获取最新的帧，丢弃中间帧
#                 frame = decoder.get_latest_frame()
#
#                 if frame is None:
#                     continue  # 如果没取到帧，就进入下一次循环
#
#                 frame_count += 1
#
#                 # --- 在这里对获取到的帧进行处理 ---
#                 logger.debug(f"[{time.strftime('%H:%M:%S')}] 已处理一帧, "
#                       f"尺寸: {frame.shape}, "
#                       f"队列积压: {decoder.output_queue.qsize()}")
#
#                 # 1. 将帧从 RGB 转换为 BGR
#                 bgr_frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
#
#                 # 2. 创建文件名 (例如：frame_00010.jpg)
#                 filename = os.path.join(SAVE_DIR, f"frame_{frame_count:05d}.jpg")
#
#                 # 3. 将 BGR 帧写入文件
#                 cv2.imwrite(filename, bgr_frame)
#
#                 print(f"✅ 已保存帧: {filename}")
#
#             else:
#                 # 还没到处理时间，短暂休眠，避免CPU空转
#                 time.sleep(0.005)
#
#     except KeyboardInterrupt:
#         print("\n用户中断。")
#     finally:
#         # --- 5. 清理 ---
#         print("正在关闭管道...")
#         streamer.stop()
#         decoder.close()
#         print("所有组件已安全关闭。")
if __name__ == "__main__":
    setup_database()
    orch = Orchestrator()
    signal.signal(signal.SIGINT, lambda s, f: orch.stop() or exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: orch.stop() or exit(0))
    orch.run()
