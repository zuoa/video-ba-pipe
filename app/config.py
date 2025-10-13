import os

from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# Database configuration
DB_PATH = os.getenv('DB_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data/db/ba.db'))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

FRAME_SAVE_PATH = os.getenv('FRAME_SAVE_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data/frames'))
os.makedirs(FRAME_SAVE_PATH, exist_ok=True)

VIDEO_SAVE_PATH = os.getenv('VIDEO_SAVE_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data/videos'))
os.makedirs(VIDEO_SAVE_PATH, exist_ok=True)



SNAPSHOT_ENABLED = os.getenv('SNAPSHOT_ENABLED', 'true').lower() in ('true', '1', 'yes')
SNAPSHOT_INTERVAL = int(os.getenv('SNAPSHOT_INTERVAL', '60'))  # in seconds
SNAPSHOT_SAVE_PATH = os.getenv('SNAPSHOT_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data/snapshots'))
os.makedirs(SNAPSHOT_SAVE_PATH, exist_ok=True)


# 极速解码模式，每次取最新的帧，扔掉所有老的帧
IS_EXTREME_DECODE_MODE = os.getenv('IS_Extreme_DECODE_MODE', 'true').lower() in ('true', '1', 'yes')


# ============ 视频录制配置 ============
# 预警录制功能开关
RECORDING_ENABLED = os.getenv('RECORDING_ENABLED', 'true').lower() in ('true', '1', 'yes')

# 录制预警前的时长（秒）
PRE_ALERT_DURATION = int(os.getenv('PRE_ALERT_DURATION', '10'))

# 录制预警后的时长（秒）
POST_ALERT_DURATION = int(os.getenv('POST_ALERT_DURATION', '10'))

# 录制视频的帧率
RECORDING_FPS = int(os.getenv('RECORDING_FPS', '5'))

# RingBuffer缓冲时长（秒）- 需要足够大以容纳PRE_ALERT_DURATION
RINGBUFFER_DURATION = int(os.getenv('RINGBUFFER_DURATION', '60'))

# ============ 告警抑制配置 ============
# 告警抑制时长（秒）- 同一任务的同一算法在此时间内不会重复预警
ALERT_SUPPRESSION_DURATION = int(os.getenv('ALERT_SUPPRESSION_DURATION', '20'))