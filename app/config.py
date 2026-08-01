import os

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency in lightweight test envs
    def load_dotenv(*args, **kwargs):
        return False

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
_db_backend_env = (os.getenv('DB_BACKEND') or '').strip().lower()
DB_BACKEND = _db_backend_env or ('postgres' if IN_DOCKER else 'sqlite')
DB_PATH = _resolve_data_path('DB_PATH', 'db/ba.db')
if DB_BACKEND == 'sqlite':
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

_default_db_host = 'postgres' if IN_DOCKER else 'localhost'
DB_HOST = os.getenv('DB_HOST', _default_db_host).strip() or _default_db_host
DB_PORT = int(os.getenv('DB_PORT', '5432'))
DB_NAME = os.getenv('DB_NAME', 'video_ba_pipe').strip() or 'video_ba_pipe'
DB_USER = os.getenv('DB_USER', 'video_ba_pipe').strip() or 'video_ba_pipe'
DB_PASSWORD = os.getenv('DB_PASSWORD', 'video_ba_pipe')
DB_SSLMODE = os.getenv('DB_SSLMODE', 'prefer').strip().lower() or 'prefer'

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

# Hugging Face model import
HF_USE_MIRROR = os.getenv('HF_USE_MIRROR', 'false').lower() in ('true', '1', 'yes', 'on')
HF_MIRROR_ENDPOINT = (
    os.getenv('HF_MIRROR_ENDPOINT', 'https://hf-mirror.com').strip().rstrip('/')
    or 'https://hf-mirror.com'
)
HF_DOWNLOAD_TIMEOUT_SECONDS = max(1, int(os.getenv('HF_DOWNLOAD_TIMEOUT_SECONDS', '120')))

USER_SCRIPTS_ROOT = _resolve_data_path('USER_SCRIPTS_ROOT', 'user_scripts')
os.makedirs(USER_SCRIPTS_ROOT, exist_ok=True)

LOG_SAVE_PATH = _resolve_data_path('LOG_SAVE_PATH', 'logs')
os.makedirs(LOG_SAVE_PATH, exist_ok=True)

LOG_MAX_BYTES = int(os.getenv('LOG_MAX_BYTES', str(10 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv('LOG_BACKUP_COUNT', '3'))

RUN_LOG_PATH = os.path.join(LOG_SAVE_PATH, 'run.log')
DEBUG_LOG_PATH = os.path.join(LOG_SAVE_PATH, 'debug.log')
DECODER_LOG_PATH = os.path.join(LOG_SAVE_PATH, 'decoder.log')
DECODER_DEBUG_LOG_PATH = os.path.join(LOG_SAVE_PATH, 'decoder_debug.log')
WORKFLOW_LOG_PATH = os.path.join(LOG_SAVE_PATH, 'workflow.log')
WORKFLOW_DEBUG_LOG_PATH = os.path.join(LOG_SAVE_PATH, 'workflow_debug.log')

ALERT_IMAGE_CLEANUP_ENABLED = os.getenv('ALERT_IMAGE_CLEANUP_ENABLED', 'true').lower() in ('true', '1', 'yes')
ALERT_IMAGE_RETENTION_DAYS = max(0, int(os.getenv('ALERT_IMAGE_RETENTION_DAYS', '7')))
ALERT_VIDEO_RETENTION_DAYS = max(
    0,
    int(os.getenv('ALERT_VIDEO_RETENTION_DAYS', str(ALERT_IMAGE_RETENTION_DAYS))),
)
ALERT_RECORD_RETENTION_DAYS = max(0, int(os.getenv('ALERT_RECORD_RETENTION_DAYS', '30')))
WINDOW_DETECTION_RETENTION_HOURS = max(0, int(os.getenv('WINDOW_DETECTION_RETENTION_HOURS', '24')))
ALERT_IMAGE_MIN_FREE_GB = max(0.0, float(os.getenv('ALERT_IMAGE_MIN_FREE_GB', '10')))
ALERT_IMAGE_MAX_STORAGE_GB = max(1.0, float(os.getenv('ALERT_IMAGE_MAX_STORAGE_GB', '10')))
ALERT_VIDEO_MAX_STORAGE_GB = max(1.0, float(os.getenv('ALERT_VIDEO_MAX_STORAGE_GB', '20')))
MEDIA_CLEANUP_INTERVAL_SECONDS = max(30, int(os.getenv('MEDIA_CLEANUP_INTERVAL_SECONDS', '60')))

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
# RK3588 推荐使用 rk_mpp；Jetson 推荐使用 jetson_gst；其他环境默认 ffmpeg_sw。
VIDEO_DECODER_TYPE = (os.getenv('VIDEO_DECODER_TYPE') or 'ffmpeg_sw').strip().lower() or 'ffmpeg_sw'

# 运行时主帧格式。当前全链路默认使用 NV12，以降低共享内存与队列占用。
VIDEO_FRAME_PIXEL_FORMAT = (os.getenv('VIDEO_FRAME_PIXEL_FORMAT') or 'nv12').strip().lower() or 'nv12'

# 软件解码性能调优
# ffmpeg 软解默认限制为单线程，避免多路并发时每路自动拉满 CPU。
FFMPEG_SW_DECODER_THREADS = max(1, int(os.getenv('FFMPEG_SW_DECODER_THREADS', '1')))

# 软解默认只解关键帧以节省 CPU。如果已持续收到足量码流但仍无帧输出，
# decoder worker 会以专用退出码通知 orchestrator，仅将该视频源切换为全帧软解。
FFMPEG_SW_KEYFRAME_FALLBACK_SECONDS = max(
    1.0,
    float(os.getenv('FFMPEG_SW_KEYFRAME_FALLBACK_SECONDS', '10')),
)
FFMPEG_SW_KEYFRAME_FALLBACK_MIN_BYTES = max(
    1,
    int(os.getenv('FFMPEG_SW_KEYFRAME_FALLBACK_MIN_BYTES', str(256 * 1024))),
)

# 解码输出队列大小（运行时主帧格式，默认 NV12）。队列越大，解码抖动越小，但内存占用会线性增加。
DECODER_OUTPUT_QUEUE_SIZE = max(1, int(os.getenv('DECODER_OUTPUT_QUEUE_SIZE', '5')))

# ============ 硬解资源准入与重启退避 ============
# 硬解准入控制器总开关。启用后，硬解解码器按可用硬件资源（CMA）自适应发放并发槽位，
# 拿不到槽位的源自动软解兜底或等待，避免 NVDEC 资源耗尽导致的崩溃-重启风暴。
HW_DECODE_BUDGET_ENABLED = os.getenv('HW_DECODE_BUDGET_ENABLED', 'true').lower() in ('true', '1', 'yes')

# 每路硬解码器占用的 CMA 估算（MB）。Jetson Orin 上 1080p 以下 nvv4l2decoder 实测约 12MB。
HW_DECODE_CMA_PER_INSTANCE_MB = max(1, int(os.getenv('HW_DECODE_CMA_PER_INSTANCE_MB', '16')))

# 系统其他部分（GPU/显示/相机等）常驻占用的 CMA 预留（MB）。
HW_DECODE_CMA_RESERVE_MB = max(0, int(os.getenv('HW_DECODE_CMA_RESERVE_MB', '160')))

# 硬解并发槽位上下限（自适应结果会被钳制在该区间）。
HW_DECODE_MIN_SLOTS = max(1, int(os.getenv('HW_DECODE_MIN_SLOTS', '1')))
HW_DECODE_MAX_SLOTS = max(HW_DECODE_MIN_SLOTS, int(os.getenv('HW_DECODE_MAX_SLOTS', '32')))

# 硬解槽位不足时是否自动回退 ffmpeg 软解；HW_DECODE_SW_FALLBACK_MAX 限制软解兜底路数（0=不限）。
HW_DECODE_SW_FALLBACK_ENABLED = os.getenv('HW_DECODE_SW_FALLBACK_ENABLED', 'true').lower() in ('true', '1', 'yes')
HW_DECODE_SW_FALLBACK_MAX = max(0, int(os.getenv('HW_DECODE_SW_FALLBACK_MAX', '0')))

# 软解兜底源自动升级回硬解的检查间隔（秒）。
HW_DECODE_UPGRADE_INTERVAL_SECONDS = max(10, int(os.getenv('HW_DECODE_UPGRADE_INTERVAL_SECONDS', '60')))

# 解码器重启退避上限（秒）。初始退避按失败类别区分（流类 30s / 崩溃类 5s），指数增长到该上限。
SOURCE_RESTART_BACKOFF_MAX_SECONDS = max(30, int(os.getenv('SOURCE_RESTART_BACKOFF_MAX_SECONDS', '300')))

# 每个管理周期最多启动的视频源数量，防止批量启动时 ffprobe/硬解通道惊群。
SOURCE_MAX_CONCURRENT_STARTS = max(1, int(os.getenv('SOURCE_MAX_CONCURRENT_STARTS', '2')))

# 资源剖析日志。默认关闭；打开后会周期性输出关键帧拷贝、编码、workflow耗时。
RESOURCE_PROFILING_ENABLED = os.getenv('RESOURCE_PROFILING_ENABLED', 'false').lower() in ('true', '1', 'yes')
RESOURCE_PROFILE_LOG_INTERVAL_SECONDS = max(1.0, float(os.getenv('RESOURCE_PROFILE_LOG_INTERVAL_SECONDS', '30')))

# Source host 读取分析缓冲区时是否使用共享内存只读视图，避免复制最新帧。
# 注意：只读视图可能在 buffer 环绕后被新帧覆盖，只有在 workflow 处理耗时稳定小于 buffer 时长时才建议开启。
WORKFLOW_ZERO_COPY_FRAMES = os.getenv('WORKFLOW_ZERO_COPY_FRAMES', 'false').lower() in ('true', '1', 'yes')

# 实时 workflow 内同层节点并行 worker 数。0 表示保持当前串行行为。
SOURCE_HOST_WORKFLOW_NODE_WORKERS = max(0, int(os.getenv('SOURCE_HOST_WORKFLOW_NODE_WORKERS', '0')))

# 视频源轮转运行时保护参数。开关、路数和单批时长存储在 SystemSetting，支持在线修改。
SOURCE_ROTATION_STARTUP_TIMEOUT_SECONDS = max(
    10,
    int(os.getenv('SOURCE_ROTATION_STARTUP_TIMEOUT_SECONDS', '60')),
)
SOURCE_ROTATION_DRAIN_GRACE_SECONDS = max(
    5,
    int(os.getenv('SOURCE_ROTATION_DRAIN_GRACE_SECONDS', '15')),
)
SOURCE_ROTATION_CONFIG_REFRESH_SECONDS = max(
    1,
    int(os.getenv('SOURCE_ROTATION_CONFIG_REFRESH_SECONDS', '5')),
)


# ============ 视频录制配置 ============
# 预警录制功能开关
RECORDING_ENABLED = os.getenv('RECORDING_ENABLED', 'false').lower() in ('true', '1', 'yes')

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
