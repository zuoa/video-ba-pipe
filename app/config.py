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

# Offline license verification. Release builds replace this bundled public key
# with the vendor's Ed25519 public key; private signing material never ships.
LICENSE_PUBLIC_KEY_PATH = os.getenv(
    'LICENSE_PUBLIC_KEY_PATH',
    os.path.join(APP_DIR, 'license_public_key.pem'),
).strip() or os.path.join(APP_DIR, 'license_public_key.pem')

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

# ============ 最新检测帧快照（视频源预览） ============
# 与上面的原始快照不同：此处保存的是「带算法检测框 + ROI」的标注帧，
# 供视频源管理页「最新检测帧」按钮查看。由 workflow 执行器周期性写入，
# 无活跃 workflow/检测时回退显示原始快照。
DETECTION_SNAPSHOT_ENABLED = os.getenv('DETECTION_SNAPSHOT_ENABLED', 'true').lower() in ('true', '1', 'yes')
DETECTION_SNAPSHOT_INTERVAL = float(os.getenv('DETECTION_SNAPSHOT_INTERVAL', '5'))  # 秒，比原始快照更勤
DETECTION_SNAPSHOT_SAVE_PATH = _resolve_data_path('DETECTION_SNAPSHOT_PATH', 'detection_snapshots')
os.makedirs(DETECTION_SNAPSHOT_SAVE_PATH, exist_ok=True)


# 已弃用：分析链路现在固定只消费最新解码帧，不再由该开关控制。
IS_EXTREME_DECODE_MODE = os.getenv('IS_EXTREME_DECODE_MODE', 'false').lower() in ('true', '1', 'yes')

# 是否只解码关键帧。默认关闭；视频源可通过 decode_keyframes_only 覆盖。
DECODE_KEYFRAMES_ONLY = os.getenv('DECODE_KEYFRAMES_ONLY', 'false').lower() in ('true', '1', 'yes', 'on')

# 默认视频解码器类型
# RK3588 推荐使用 rk_mpp；Jetson 推荐使用 jetson_gst；其他环境默认 ffmpeg_sw。
VIDEO_DECODER_TYPE = (os.getenv('VIDEO_DECODER_TYPE') or 'ffmpeg_sw').strip().lower() or 'ffmpeg_sw'

# 运行时主帧格式。当前全链路默认使用 NV12，以降低共享内存与队列占用。
VIDEO_FRAME_PIXEL_FORMAT = (os.getenv('VIDEO_FRAME_PIXEL_FORMAT') or 'nv12').strip().lower() or 'nv12'

# 软件解码性能调优
# ffmpeg 软解默认限制为单线程，避免多路并发时每路自动拉满 CPU。
FFMPEG_SW_DECODER_THREADS = max(1, int(os.getenv('FFMPEG_SW_DECODER_THREADS', '1')))

# 如果主动开启关键帧软解后持续收到足量码流但仍无帧输出，decoder worker
# 会以专用退出码通知 orchestrator，仅将该视频源切换为全帧软解。
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
# 硬解准入控制器总开关。启用后，硬解解码器按可用硬件资源自适应发放并发槽位
# （Jetson/RK 依据 CMA 余量；X86+CUDA 依据 NVDEC 解码引擎利用率 + GPU 型号查表估算），
# 拿不到槽位的源自动软解兜底或等待，避免硬解资源耗尽导致的崩溃-重启风暴。
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

# ---- X86 + CUDA(NVDEC)容量探针 ----
# NVDEC 解码引擎利用率闸门（%）。达到该值后不再发放新硬解槽位，已运行的路数不受影响。
HW_DECODE_NV_UTIL_THRESHOLD = max(50, min(99, int(os.getenv('HW_DECODE_NV_UTIL_THRESHOLD', '85'))))

# 每路 NVDEC 解码器占用的显存估算（MB），以及必须为推理等其他负载保留的空闲显存（MB）。
HW_DECODE_NV_VRAM_PER_INSTANCE_MB = max(16, int(os.getenv('HW_DECODE_NV_VRAM_PER_INSTANCE_MB', '128')))
HW_DECODE_NV_VRAM_RESERVE_MB = max(0, int(os.getenv('HW_DECODE_NV_VRAM_RESERVE_MB', '1024')))

# NVDEC 解码使用的 GPU 序号（与 ffmpeg -hwaccel_device 对应）。
HW_DECODE_NV_GPU_INDEX = max(0, int(os.getenv('HW_DECODE_NV_GPU_INDEX', '0')))

# NVDEC 初始槽位估算覆盖值（0=按 GPU 型号查表自动估算；>0 时跳过查表）。
HW_DECODE_NV_INITIAL_SLOTS = max(0, int(os.getenv('HW_DECODE_NV_INITIAL_SLOTS', '0')))

# 解码器重启退避上限（秒）。初始退避按失败类别区分（流类 30s / 崩溃类 5s），指数增长到该上限。
SOURCE_RESTART_BACKOFF_MAX_SECONDS = max(30, int(os.getenv('SOURCE_RESTART_BACKOFF_MAX_SECONDS', '300')))

# 每个管理周期最多启动的视频源数量，防止批量启动时 ffprobe/硬解通道惊群。
SOURCE_MAX_CONCURRENT_STARTS = max(1, int(os.getenv('SOURCE_MAX_CONCURRENT_STARTS', '2')))

# ============ 推理内存保护与共享模型服务 ============
# Source host 被全局 OOM killer 以 SIGKILL 终止后，禁止编排器立即原地重启，
# 否则会形成“加载模型 -> OOM -> 重启 -> 再加载”的放大循环。
OOM_CIRCUIT_BREAKER_ENABLED = os.getenv(
    'OOM_CIRCUIT_BREAKER_ENABLED', 'true'
).lower() in ('true', '1', 'yes', 'on')
OOM_CIRCUIT_FAILURE_THRESHOLD = max(
    1, int(os.getenv('OOM_CIRCUIT_FAILURE_THRESHOLD', '3'))
)
OOM_CIRCUIT_OPEN_SECONDS = max(
    30, int(os.getenv('OOM_CIRCUIT_OPEN_SECONDS', '600'))
)
OOM_CIRCUIT_STABLE_RESET_SECONDS = max(
    60, int(os.getenv('OOM_CIRCUIT_STABLE_RESET_SECONDS', '600'))
)
OOM_RESTART_BACKOFF_MAX_SECONDS = max(
    30, int(os.getenv('OOM_RESTART_BACKOFF_MAX_SECONDS', '300'))
)

# Ultralytics 模型共享服务的首次启动/数据库不可用回退值。正常运行后由
# SystemSetting 中的推理资源配置接管。服务使用 Unix socket + POSIX shared
# memory 在 source host 间共享模型。
SHARED_INFERENCE_ENABLED = os.getenv(
    'SHARED_INFERENCE_ENABLED', 'false'
).lower() in ('true', '1', 'yes', 'on')
SHARED_INFERENCE_SOCKET_PATH = os.getenv(
    'SHARED_INFERENCE_SOCKET_PATH', '/tmp/video-ba-pipe-inference.sock'
).strip() or '/tmp/video-ba-pipe-inference.sock'
SHARED_INFERENCE_QUEUE_SIZE = max(
    1, int(os.getenv('SHARED_INFERENCE_QUEUE_SIZE', '2'))
)
SHARED_INFERENCE_BATCH_MAX_SIZE = max(
    1, int(os.getenv('SHARED_INFERENCE_BATCH_MAX_SIZE', '4'))
)
SHARED_INFERENCE_BATCH_WAIT_MS = max(
    0.0, float(os.getenv('SHARED_INFERENCE_BATCH_WAIT_MS', '5'))
)
SHARED_INFERENCE_REQUEST_TIMEOUT_SECONDS = max(
    1.0, float(os.getenv('SHARED_INFERENCE_REQUEST_TIMEOUT_SECONDS', '30'))
)
# 模型子进程首次 import PyTorch、加载权重并完成 CUDA warm-up，通常明显慢于
# 稳态单帧推理。启动等待必须与请求超时分开，否则冷启动会被误报成
# inference_timeout，客户端随即释放该帧的共享内存。
SHARED_INFERENCE_STARTUP_TIMEOUT_SECONDS = max(
    1.0, float(os.getenv('SHARED_INFERENCE_STARTUP_TIMEOUT_SECONDS', '180'))
)
SHARED_INFERENCE_IDLE_SECONDS = max(
    10, int(os.getenv('SHARED_INFERENCE_IDLE_SECONDS', '120'))
)

# API forwards all interactive algorithm tests to this worker-local HTTP service.
# The port is exposed only on the Compose network, never on the host.
ALGORITHM_TEST_WORKER_HOST = (
    os.getenv('ALGORITHM_TEST_WORKER_HOST', '127.0.0.1').strip() or '127.0.0.1'
)
ALGORITHM_TEST_WORKER_PORT = max(
    1, min(65535, int(os.getenv('ALGORITHM_TEST_WORKER_PORT', '5010')))
)
ALGORITHM_TEST_WORKER_URL = (
    os.getenv(
        'ALGORITHM_TEST_WORKER_URL',
        'http://127.0.0.1:5010',
    ).strip().rstrip('/')
)
ALGORITHM_TEST_WORKER_TOKEN = (
    os.getenv('ALGORITHM_TEST_WORKER_TOKEN', '').strip()
    or 'video-ba-pipe-internal-test-v1'
)
ALGORITHM_TEST_QUEUE_SIZE = max(
    0, int(os.getenv('ALGORITHM_TEST_QUEUE_SIZE', '2'))
)
ALGORITHM_TEST_TIMEOUT_SECONDS = max(
    1.0, float(os.getenv('ALGORITHM_TEST_TIMEOUT_SECONDS', '180'))
)
ALGORITHM_TEST_MAX_IMAGE_BYTES = max(
    1024 * 1024, int(os.getenv('ALGORITHM_TEST_MAX_IMAGE_BYTES', str(20 * 1024 * 1024)))
)

# 推理准入只把 RAM 作为容量；Swap 不计入可用容量。新模型尚无实测数据时，
# 使用保守默认增量，待共享服务产生 PSS 样本后改用观测值。
INFERENCE_ADMISSION_ENABLED = os.getenv(
    'INFERENCE_ADMISSION_ENABLED', 'false'
).lower() in ('true', '1', 'yes', 'on')
INFERENCE_SYSTEM_RESERVE_MB = max(
    256, int(os.getenv('INFERENCE_SYSTEM_RESERVE_MB', '2048'))
)
INFERENCE_SYSTEM_RESERVE_PERCENT = min(
    50.0,
    max(0.0, float(os.getenv('INFERENCE_SYSTEM_RESERVE_PERCENT', '15'))),
)
INFERENCE_NEW_MODEL_DEFAULT_MB = max(
    128, int(os.getenv('INFERENCE_NEW_MODEL_DEFAULT_MB', '1024'))
)
INFERENCE_MODEL_MEMORY_MARGIN_PERCENT = min(
    100.0,
    max(0.0, float(os.getenv('INFERENCE_MODEL_MEMORY_MARGIN_PERCENT', '25'))),
)

# 资源剖析日志。默认关闭；打开后会周期性输出关键帧拷贝、编码、workflow耗时。
RESOURCE_PROFILING_ENABLED = os.getenv('RESOURCE_PROFILING_ENABLED', 'false').lower() in ('true', '1', 'yes')
RESOURCE_PROFILE_LOG_INTERVAL_SECONDS = max(1.0, float(os.getenv('RESOURCE_PROFILE_LOG_INTERVAL_SECONDS', '30')))

# 实时帧热路径诊断日志。默认关闭，避免多路视频把逐节点、逐推理日志
# 同时写入控制台和轮转文件；排障时可临时开启。
WORKFLOW_FRAME_LOGS_ENABLED = os.getenv(
    'WORKFLOW_FRAME_LOGS_ENABLED', 'false'
).lower() in ('true', '1', 'yes', 'on')

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

# ============ 平台节点身份（集群 / MQ 来源标识）============
# 当前实例的唯一编码。集群或多盒子部署时必须保证全局唯一（如 box-01、edge-sh-03）。
# 留空时按以下优先级解析（见 app/core/node_identity.py）：
#   1) 持久化文件 NODE_ID_FILE；
#   2) 首次启动优先使用 MAC 地址并写入持久化文件；
#   3) MAC 不可用时自动生成 UUID，文件不可写时最终回退 hostname。
# 推荐在 .env 中显式设置 NODE_ID，便于集群可读与可追溯。
NODE_ID = (os.getenv('NODE_ID') or '').strip()
NODE_ID_FILE = _resolve_data_path('NODE_ID_FILE', 'node_id.json')

# ============ MediaMTX / WebRTC 实时预览 ============
# 通过部署 MediaMTX 作为「按需拉流」中继，前端用 WebRTC(WHEP) 直接看实时画面。
# 仅当 MEDIAMTX_ENABLED=true 且 MediaMTX 服务可达时生效；其余情况所有调用安全降级为 no-op。
# 注意：MediaMTX 不参与检测流水线，worker/decoder 仍直连摄像机拉流；
#       MediaMTX 只在有 WebRTC 观众时另起一路拉流(sourceOnDemand)，观众离开自动断开。
MEDIAMTX_ENABLED = os.getenv('MEDIAMTX_ENABLED', 'false').lower() in ('true', '1', 'yes')
# 后端容器内访问 MediaMTX REST API 的地址（docker 网络内通常为服务名 mediamtx）
MEDIAMTX_API_HOST = os.getenv('MEDIAMTX_API_HOST', 'mediamtx')
MEDIAMTX_API_PORT = int(os.getenv('MEDIAMTX_API_PORT', '9997'))
# Control API 专用凭据。MediaMTX 默认只允许 localhost 调用 API，容器间访问必须认证。
MEDIAMTX_API_USER = os.getenv('MEDIAMTX_API_USER', 'video-ba-api')
MEDIAMTX_API_PASSWORD = os.getenv('MEDIAMTX_API_PASSWORD', 'video-ba-api-change-me')
MEDIAMTX_RTSP_PORT = int(os.getenv('MEDIAMTX_RTSP_PORT', '8554'))
# 浏览器侧连接 WebRTC 的端口（docker host 映射端口）
MEDIAMTX_WEBRTC_PORT = int(os.getenv('MEDIAMTX_WEBRTC_PORT', '8889'))
# 浏览器侧访问的主机名/IP；为空时前端用页面所在 hostname 兜底
MEDIAMTX_WEBRTC_PUBLIC_HOST = os.getenv('MEDIAMTX_WEBRTC_PUBLIC_HOST', '')
# 逗号分隔的额外 ICE 主机，注入 MediaMTX(MTX_WEBRTC_ADDITIONALHOSTS)，让候选对浏览器可达
MEDIAMTX_WEBRTC_ADDITIONAL_HOSTS = os.getenv('MEDIAMTX_WEBRTC_ADDITIONAL_HOSTS', '')

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
