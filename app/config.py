import os

from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(APP_DIR)
IN_DOCKER = os.path.exists('/.dockerenv')
LOCAL_DATA_ROOT = os.path.join(APP_DIR, 'data')
DOCKER_DATA_ROOT = '/data'


def _resolve_data_path(env_name: str, relative_path: str) -> str:
    env_value = os.getenv(env_name)
    if env_value:
        if os.path.isabs(env_value):
            return env_value
        return os.path.abspath(os.path.join(PROJECT_ROOT, env_value))

    data_root = DOCKER_DATA_ROOT if IN_DOCKER else LOCAL_DATA_ROOT
    return os.path.join(data_root, relative_path)


# Database configuration
DB_PATH = _resolve_data_path('DB_PATH', 'db/ba.db')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
DB_SQLITE_BUSY_TIMEOUT_MS = int(os.getenv('DB_SQLITE_BUSY_TIMEOUT_MS', '15000'))
DB_SQLITE_JOURNAL_MODE = os.getenv('DB_SQLITE_JOURNAL_MODE', 'wal').strip().lower()
DB_SQLITE_SYNCHRONOUS = os.getenv('DB_SQLITE_SYNCHRONOUS', 'normal').strip().lower()
DB_SQLITE_CACHE_SIZE_KB = int(os.getenv('DB_SQLITE_CACHE_SIZE_KB', '65536'))
DB_SQLITE_WAL_AUTOCHECKPOINT = int(os.getenv('DB_SQLITE_WAL_AUTOCHECKPOINT', '1000'))
DB_SQLITE_MMAP_SIZE = int(os.getenv('DB_SQLITE_MMAP_SIZE', str(128 * 1024 * 1024)))

FRAME_SAVE_PATH = _resolve_data_path('FRAME_SAVE_PATH', 'frames')
os.makedirs(FRAME_SAVE_PATH, exist_ok=True)

VIDEO_SAVE_PATH = _resolve_data_path('VIDEO_SAVE_PATH', 'videos')
os.makedirs(VIDEO_SAVE_PATH, exist_ok=True)

# Video source files storage path (uploaded video files for analysis)
VIDEO_SOURCE_PATH = _resolve_data_path('VIDEO_SOURCE_PATH', 'video_sources')
os.makedirs(VIDEO_SOURCE_PATH, exist_ok=True)


# Models storage path for uploaded AI model files
MODEL_SAVE_PATH = _resolve_data_path('MODEL_SAVE_PATH', 'models')
os.makedirs(MODEL_SAVE_PATH, exist_ok=True)

USER_SCRIPTS_ROOT = _resolve_data_path('USER_SCRIPTS_ROOT', 'user_scripts')
os.makedirs(USER_SCRIPTS_ROOT, exist_ok=True)

LOG_SAVE_PATH = _resolve_data_path('LOG_SAVE_PATH', 'logs')
os.makedirs(LOG_SAVE_PATH, exist_ok=True)

RUN_LOG_PATH = os.path.join(LOG_SAVE_PATH, 'run.log')
DEBUG_LOG_PATH = os.path.join(LOG_SAVE_PATH, 'debug.log')
WORKFLOW_LOG_PATH = os.path.join(LOG_SAVE_PATH, 'workflow.log')
WORKFLOW_DEBUG_LOG_PATH = os.path.join(LOG_SAVE_PATH, 'workflow_debug.log')

# ============ 检测结果调试日志 ============
# 输出算法检测结果到 logs/detection_results_YYYYMMDD.jsonl，便于排查不同部署环境输出差异
DETECTION_JSONL_LOG_ENABLED = os.getenv('DETECTION_JSONL_LOG_ENABLED', 'false').lower() in ('true', '1', 'yes')



SNAPSHOT_ENABLED = os.getenv('SNAPSHOT_ENABLED', 'true').lower() in ('true', '1', 'yes')
SNAPSHOT_INTERVAL = int(os.getenv('SNAPSHOT_INTERVAL', '60'))  # in seconds
SNAPSHOT_SAVE_PATH = _resolve_data_path('SNAPSHOT_PATH', 'snapshots')
os.makedirs(SNAPSHOT_SAVE_PATH, exist_ok=True)


# 极速解码模式，每次取最新的帧，扔掉所有老的帧
IS_EXTREME_DECODE_MODE = os.getenv('IS_EXTREME_DECODE_MODE', 'false').lower() in ('true', '1', 'yes')

# 默认视频解码器类型
# RK3588 推荐使用 rk_mpp，其他环境默认 ffmpeg_sw。
VIDEO_DECODER_TYPE = (os.getenv('VIDEO_DECODER_TYPE') or 'ffmpeg_sw').strip().lower() or 'ffmpeg_sw'

# 软件解码性能调优
# ffmpeg 软解默认限制为单线程，避免多路并发时每路自动拉满 CPU。
FFMPEG_SW_DECODER_THREADS = max(1, int(os.getenv('FFMPEG_SW_DECODER_THREADS', '1')))

# 解码输出队列大小（原始 RGB 帧）。队列越大，解码抖动越小，但内存占用会线性增加。
DECODER_OUTPUT_QUEUE_SIZE = max(1, int(os.getenv('DECODER_OUTPUT_QUEUE_SIZE', '5')))


# ============ 视频录制配置 ============
# 预警录制功能开关
RECORDING_ENABLED = os.getenv('RECORDING_ENABLED', 'true').lower() in ('true', '1', 'yes')

# 录制预警前的时长（秒）
PRE_ALERT_DURATION = int(os.getenv('PRE_ALERT_DURATION', '5'))

# 录制预警后的时长（秒）
POST_ALERT_DURATION = int(os.getenv('POST_ALERT_DURATION', '5'))

# 录制视频的帧率
RECORDING_FPS = int(os.getenv('RECORDING_FPS', '5'))

# 分析链路缓冲配置
# 分析工作流只消费最新帧，因此默认使用更小的 buffer 和更低的采样率。
ANALYSIS_TARGET_FPS = int(os.getenv('ANALYSIS_TARGET_FPS', '3'))
ANALYSIS_BUFFER_SECONDS = int(os.getenv('ANALYSIS_BUFFER_SECONDS', '5'))

# 录制链路缓冲配置
# 为兼容旧配置，未显式设置时仍回退到 RINGBUFFER_DURATION，但确保至少覆盖前后录制窗口。
_legacy_ringbuffer_duration = int(os.getenv('RINGBUFFER_DURATION', '30'))
RECORDING_BUFFER_DURATION = int(
    os.getenv(
        'RECORDING_BUFFER_DURATION',
        str(max(_legacy_ringbuffer_duration, PRE_ALERT_DURATION + POST_ALERT_DURATION + 2))
    )
)
RECORDING_JPEG_QUALITY = int(os.getenv('RECORDING_JPEG_QUALITY', '85'))
RECORDING_COMPRESSED_MAX_BYTES = int(os.getenv('RECORDING_COMPRESSED_MAX_BYTES', str(512 * 1024)))

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

# ============ VL 模型核验配置 ============
VL_MODEL_BASE_URL = os.getenv('VL_MODEL_BASE_URL', '').strip()
VL_MODEL_NAME = os.getenv('VL_MODEL_NAME', '').strip()
VL_MODEL_KEY = os.getenv('VL_MODEL_KEY', '').strip()
VL_MODEL_TIMEOUT_SECONDS = int(os.getenv('VL_MODEL_TIMEOUT_SECONDS', '30'))
VL_MODEL_PROMPT = os.getenv(
    'VL_MODEL_PROMPT',
    '你是视频告警复核助手。请基于图像内容和算法摘要判断当前场景是否应该触发告警。'
).strip()
