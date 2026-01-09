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

# Video source files storage path (uploaded video files for analysis)
VIDEO_SOURCE_PATH = os.getenv('VIDEO_SOURCE_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data/video_sources'))
os.makedirs(VIDEO_SOURCE_PATH, exist_ok=True)


# Models storage path for uploaded AI model files
MODEL_SAVE_PATH = os.getenv('MODEL_SAVE_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data/models'))
os.makedirs(MODEL_SAVE_PATH, exist_ok=True)



SNAPSHOT_ENABLED = os.getenv('SNAPSHOT_ENABLED', 'true').lower() in ('true', '1', 'yes')
SNAPSHOT_INTERVAL = int(os.getenv('SNAPSHOT_INTERVAL', '60'))  # in seconds
SNAPSHOT_SAVE_PATH = os.getenv('SNAPSHOT_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data/snapshots'))
os.makedirs(SNAPSHOT_SAVE_PATH, exist_ok=True)


# 极速解码模式，每次取最新的帧，扔掉所有老的帧
IS_EXTREME_DECODE_MODE = os.getenv('IS_EXTREME_DECODE_MODE', 'false').lower() in ('true', '1', 'yes')


# ============ 视频录制配置 ============
# 预警录制功能开关
RECORDING_ENABLED = os.getenv('RECORDING_ENABLED', 'true').lower() in ('true', '1', 'yes')

# 录制预警前的时长（秒）
PRE_ALERT_DURATION = int(os.getenv('PRE_ALERT_DURATION', '5'))

# 录制预警后的时长（秒）
POST_ALERT_DURATION = int(os.getenv('POST_ALERT_DURATION', '5'))

# 录制视频的帧率
RECORDING_FPS = int(os.getenv('RECORDING_FPS', '5'))

# RingBuffer缓冲时长（秒）- 需要足够大以容纳PRE_ALERT_DURATION
RINGBUFFER_DURATION = int(os.getenv('RINGBUFFER_DURATION', '30'))

# ============ 告警抑制配置 ============
# 告警抑制时长（秒）- 同一任务的同一算法在此时间内不会重复预警
ALERT_SUPPRESSION_DURATION = int(os.getenv('ALERT_SUPPRESSION_DURATION', '10'))

# ============ RabbitMQ配置 ============
# RabbitMQ服务器地址
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', '10.0.4.15')
RABBITMQ_PORT = int(os.getenv('RABBITMQ_PORT', '5672'))
RABBITMQ_USER = os.getenv('RABBITMQ_USER', 'rabbit')
RABBITMQ_PASSWORD = os.getenv('RABBITMQ_PASSWORD', 'cv2025@)@%')
RABBITMQ_VHOST = os.getenv('RABBITMQ_VHOST', '/')

# 预警消息队列配置
RABBITMQ_ALERT_QUEUE = os.getenv('RABBITMQ_ALERT_QUEUE', 'video_alerts')
RABBITMQ_ALERT_EXCHANGE = os.getenv('RABBITMQ_ALERT_EXCHANGE', 'video_alerts')
RABBITMQ_ALERT_ROUTING_KEY = os.getenv('RABBITMQ_ALERT_ROUTING_KEY', 'alert')

# Topic模式配置
RABBITMQ_EXCHANGE_TYPE = os.getenv('RABBITMQ_EXCHANGE_TYPE', 'topic')  # topic 或 direct
RABBITMQ_ALERT_TOPIC_PATTERN = os.getenv('RABBITMQ_ALERT_TOPIC_PATTERN', 'video.alert.*')

# RabbitMQ连接超时设置（秒）
RABBITMQ_CONNECTION_TIMEOUT = int(os.getenv('RABBITMQ_CONNECTION_TIMEOUT', '30'))

# 是否启用RabbitMQ预警发布
RABBITMQ_ENABLED = os.getenv('RABBITMQ_ENABLED', 'true').lower() in ('true', '1', 'yes')

# ============ 健康监控配置 ============
# 是否启用健康监控
HEALTH_MONITOR_ENABLED = os.getenv('HEALTH_MONITOR_ENABLED', 'true').lower() in ('true', '1', 'yes')

# 无帧警告阈值（秒）- 超过此时间无帧输出则警告
NO_FRAME_WARNING_THRESHOLD = int(os.getenv('NO_FRAME_WARNING_THRESHOLD', '15'))

# 无帧严重阈值（秒）- 超过此时间无帧输出则判定为异常/重启
NO_FRAME_CRITICAL_THRESHOLD = int(os.getenv('NO_FRAME_CRITICAL_THRESHOLD', '30'))

# 低帧率比例 - 帧率低于期望帧率的此比例则告警
LOW_FPS_RATIO = float(os.getenv('LOW_FPS_RATIO', '0.5'))

# 帧率检查间隔（秒）
FPS_CHECK_INTERVAL = int(os.getenv('FPS_CHECK_INTERVAL', '10'))

# 高错误计数阈值 - 连续错误次数超过此值则告警
HIGH_ERROR_COUNT_THRESHOLD = int(os.getenv('HIGH_ERROR_COUNT_THRESHOLD', '10'))

# 最大连续错误次数 - 超过此次数则退出
MAX_CONSECUTIVE_ERRORS = int(os.getenv('MAX_CONSECUTIVE_ERRORS', '60'))

# 监控时间戳更新间隔（秒）- DecoderWorker定期更新last_write_time的间隔
MONITOR_UPDATE_INTERVAL = float(os.getenv('MONITOR_UPDATE_INTERVAL', '1.0'))