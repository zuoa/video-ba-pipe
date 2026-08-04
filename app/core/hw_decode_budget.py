"""硬件解码资源准入控制器。

按可用硬件资源自适应限制硬解码器并发数，资源探针按平台可插拔：
- Jetson/RK(CmaCapacityProbe)：解析 /proc/meminfo 的 CmaTotal/CmaFree，
  CMA 耗尽会触发 Host1x 硬失败，因此以 CMA 余量作为容量依据；
- X86+CUDA(NvmlCapacityProbe)：NVDEC 的瓶颈是解码引擎吞吐而非显存/会话数，
  耗尽表现为解码变慢而非硬失败，因此以 NVML 解码引擎利用率作为实时闸门，
  GPU 型号查表（NVDEC 引擎数 × 单引擎吞吐）仅提供初始估算，
  真实容量由利用率闭环收敛（估算偏保守时允许稳定升档越过初值）。

自适应反馈（失败降档 / 稳定升档）与软解兜底逻辑与平台无关，见 HwDecodeBudget。
"""

import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# 被视作硬件解码器的类型（与 app/core/decoder/__init__.py 的别名保持一致）
HW_DECODER_TYPES = {
    'jetson_gst', 'jetson', 'nvv4l2',
    'rk_mpp', 'rkmpp', 'ffmpeg_rkmpp',
    'ffmpeg_nvdec', 'nvdec',
}

# NVDEC(CUDA)解码器类型
NVDEC_DECODER_TYPES = {'ffmpeg_nvdec', 'nvdec'}

# 软解兜底使用的解码器类型
SW_FALLBACK_DECODER_TYPE = 'ffmpeg_sw'


def read_cma_info(meminfo_path: str = '/proc/meminfo'):
    """读取 CMA 总量/空闲量（MB）。非 Linux 或缺失时返回 (None, None)。"""
    try:
        total_kb = free_kb = None
        with open(meminfo_path, 'r') as f:
            for line in f:
                if line.startswith('CmaTotal:'):
                    total_kb = int(line.split()[1])
                elif line.startswith('CmaFree:'):
                    free_kb = int(line.split()[1])
        if total_kb is None:
            return None, None
        return total_kb // 1024, (free_kb or 0) // 1024
    except (OSError, ValueError, IndexError):
        return None, None


# ---------- NVDEC 容量估算（GPU 型号查表） ----------

# 单个 NVDEC 引擎的保守吞吐估算：1080p30 H.264 并发路数。
# 实测每引擎约 1000fps+(1080p H.264)，取保守值留余量；偏保守无副作用，
# 真实容量由解码利用率闸门 + 稳定升档收敛。
NVDEC_STREAMS_PER_ENGINE_1080P30 = 25

# GPU 型号 → NVDEC 引擎数。
# 数据来源：NVIDIA Video Encode and Decode GPU Support Matrix
# (https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new)
# 按正则顺序匹配，命中第一条即止；消费级/Quadro 默认单 NVDEC。
_GPU_NVDEC_ENGINES = [
    (r'\bh[12]00\b', 7),
    (r'\ba100\b', 5),
    (r'\ba30\b', 4),
    (r'\bl4\b', 4),
    (r'\bl40s?\b', 3),
    (r'\bt4\b', 2),
    (r'\ba10g?\b', 2),
    (r'\ba40\b', 2),
    (r'\ba2\b', 2),
    (r'\brtx a5[05]00\b', 2),
    (r'\brtx a6000\b', 2),
    (r'\bv100\b', 1),
    (r'\bp4\b', 1),
    (r'\bp40\b', 1),
    (r'\bp100\b', 1),
    (r'\brtx a[24]000\b', 1),
    (r'\ba16\b', 1),
    (r'\bgeforce\b', 1),
    (r'\bquadro\b', 1),
    (r'\brtx\b', 1),
]


def estimate_nvdec_engines(gpu_name: Optional[str]) -> Optional[int]:
    """按 GPU 型号估算 NVDEC 引擎数；无法识别时返回 None。"""
    if not gpu_name:
        return None
    normalized = gpu_name.lower()
    for pattern, engines in _GPU_NVDEC_ENGINES:
        if re.search(pattern, normalized):
            return engines
    return None


def estimate_nvdec_decode_streams(
    gpu_name: Optional[str],
    streams_per_engine: int = NVDEC_STREAMS_PER_ENGINE_1080P30,
) -> Optional[int]:
    """按 GPU 型号估算 NVDEC 并发解码路数（1080p30 H.264 基线）。"""
    engines = estimate_nvdec_engines(gpu_name)
    if engines is None:
        return None
    return engines * streams_per_engine


# ---------- 容量探针 ----------

class CmaCapacityProbe:
    """Jetson/RK 平台的 CMA 容量探针。

    容量发现：CmaTotal 减去预留后按每路估算划分；
    实时闸门：CmaFree 跌破单路需求时拒绝发放新槽位。
    """

    REFRESH_SECONDS = 30.0

    def __init__(
        self,
        *,
        meminfo_path='/proc/meminfo',
        per_instance_mb: int = 16,
        reserve_mb: int = 160,
        time_func=time.monotonic,
    ):
        # meminfo_path 支持 str 或返回 str 的 callable（便于宿主侧动态改写）
        self._meminfo_path = meminfo_path
        self.per_instance_mb = max(1, per_instance_mb)
        self.reserve_mb = max(0, reserve_mb)
        self._time = time_func
        self._last_free_mb = None
        self._last_read_at = float('-inf')  # 首次查询立即读取

    def _path(self) -> str:
        if callable(self._meminfo_path):
            return self._meminfo_path()
        return self._meminfo_path

    def describe(self) -> str:
        return 'cma'

    def discover_slots(self) -> Optional[int]:
        cma_total_mb, _ = read_cma_info(self._path())
        if not cma_total_mb:
            return None
        return (cma_total_mb - self.reserve_mb) // self.per_instance_mb

    def has_headroom(self, active_count: int) -> Optional[bool]:
        """True=有余量;False=拒绝;None=无数据不否决。首路放行以产生度量。"""
        if not active_count:
            return True
        now = self._time()
        if now - self._last_read_at >= self.REFRESH_SECONDS:
            self._last_read_at = now
            _, free_mb = read_cma_info(self._path())
            if free_mb is not None:
                self._last_free_mb = free_mb
        if self._last_free_mb is None:
            return None
        return self._last_free_mb >= self.per_instance_mb

    def upgrade_ceiling(self, discovered_slots: int, max_slots: int) -> int:
        """稳定升档上限:CMA 容量是实测硬上限,不允许越过。"""
        return discovered_slots

    def stats(self) -> dict:
        return {'cma_free_mb': self._last_free_mb}


class NvmlCapacityProbe:
    """X86 + CUDA(NVDEC)容量探针。

    - 初始估算：GPU 型号查表（NVDEC 引擎数 × 单引擎吞吐，1080p30 H.264 基线）;
    - 实时闸门：解码引擎利用率 >= 阈值，或空闲显存不足以再开一路时拒绝发放；
    - pynvml 不可用时整体退化：无初始估算（走保守默认）、不做闸门否决，
      且升档上限锁定为初始值（不在闸门失明状态下放大槽位）。
    """

    REFRESH_SECONDS = 5.0

    # 无法识别 GPU 型号时的保守初始路数
    UNKNOWN_GPU_INITIAL_STREAMS = 8

    # NVML 初始化失败后的重试间隔（秒），避免瞬时失败永久锁存
    NVML_RETRY_SECONDS = 60.0

    # NVML_ERROR_NOT_SUPPORTED 返回码(pynvml NVMLError.value)
    _NVML_ERROR_NOT_SUPPORTED = 3

    def __init__(
        self,
        *,
        gpu_index: int = 0,
        util_threshold: int = 85,
        vram_per_instance_mb: int = 128,
        vram_reserve_mb: int = 1024,
        initial_slots_override: int = 0,
        refresh_seconds: float = REFRESH_SECONDS,
        time_func=time.monotonic,
        nvml=None,
    ):
        self.gpu_index = gpu_index
        self.util_threshold = util_threshold
        self.vram_per_instance_mb = vram_per_instance_mb
        self.vram_reserve_mb = vram_reserve_mb
        self.initial_slots_override = initial_slots_override
        self.refresh_seconds = refresh_seconds
        self._time = time_func
        self._nvml = nvml  # 可注入假模块（测试）;None 时懒加载 pynvml
        self._handle = None
        self._nvml_unavailable_until = 0.0  # 初始化失败的重试冷却截止时刻
        self._gpu_name = None
        self._last_read_at = float('-inf')  # 首次查询立即读取
        self._decoder_util = None
        self._vram_free_mb = None
        self._decoder_util_unsupported = False

    def describe(self) -> str:
        return f'nvml(gpu{self.gpu_index}:{self._gpu_name or "unknown"})'

    def _ensure_nvml(self) -> bool:
        if self._handle is not None:
            return True
        now = self._time()
        if now < self._nvml_unavailable_until:
            return False
        try:
            if self._nvml is None:
                import pynvml
                self._nvml = pynvml
            self._nvml.nvmlInit()
            self._handle = self._nvml.nvmlDeviceGetHandleByIndex(self.gpu_index)
            raw_name = self._nvml.nvmlDeviceGetName(self._handle)
            self._gpu_name = (
                raw_name.decode('utf-8', 'replace')
                if isinstance(raw_name, bytes)
                else str(raw_name)
            )
            return True
        except Exception as exc:
            # 瞬时失败（驱动未就绪/NVML RPC 错误）按冷却重试，不永久锁存
            self._nvml_unavailable_until = now + self.NVML_RETRY_SECONDS
            logger.warning(
                f"NVML 初始化失败，{self.NVML_RETRY_SECONDS:.0f}s 后重试；"
                f"期间 NVDEC 容量探针退化为保守默认: {exc}"
            )
            return False

    def discover_slots(self) -> Optional[int]:
        if self.initial_slots_override > 0:
            return self.initial_slots_override
        if not self._ensure_nvml():
            return None
        streams = estimate_nvdec_decode_streams(self._gpu_name)
        if streams is None:
            logger.warning(
                f"未知 GPU 型号 {self._gpu_name!r}，NVDEC 初始槽位使用保守默认 "
                f"{self.UNKNOWN_GPU_INITIAL_STREAMS}（利用率闸门仍会生效）"
            )
            return self.UNKNOWN_GPU_INITIAL_STREAMS
        logger.info(
            f"GPU {self._gpu_name}: 估算 NVDEC 并发能力 {streams} 路"
            f"（1080p30 H.264 基线，运行时以解码利用率闸门为准）"
        )
        return streams

    @staticmethod
    def _is_not_supported_error(exc: Exception) -> bool:
        """判断异常是否表示"该指标不受支持"(vGPU/MIG/旧驱动)。"""
        if 'not supported' in str(exc).lower():
            return True
        # pynvml NVMLError 携带 NVML 返回码
        return getattr(exc, 'value', None) == NvmlCapacityProbe._NVML_ERROR_NOT_SUPPORTED

    def _refresh_metrics(self):
        now = self._time()
        if now - self._last_read_at < self.refresh_seconds:
            return
        self._last_read_at = now
        if not self._ensure_nvml():
            return
        handle = self._handle
        # 显存:free 属性缺失视为"无数据"(None,不否决)而非 0(永久硬拒绝)
        try:
            info = self._nvml.nvmlDeviceGetMemoryInfo(handle)
            free_bytes = getattr(info, 'free', None)
            self._vram_free_mb = (
                free_bytes / (1024.0 * 1024.0) if free_bytes is not None else None
            )
        except Exception:
            self._vram_free_mb = None
            # 显存查询失败通常意味着句柄失效（如外部 nvmlShutdown），下次重建
            self._handle = None
        # 解码利用率:两指标独立,单项失败不连带摧毁另一项闸门;
        # 失败时不置空句柄(可能只是指标不受支持),避免每周期重建 NVML
        if self._decoder_util_unsupported:
            return
        try:
            util, _period = self._nvml.nvmlDeviceGetDecoderUtilization(handle)
            self._decoder_util = int(util)
        except Exception as exc:
            self._decoder_util = None
            if self._is_not_supported_error(exc):
                self._decoder_util_unsupported = True
                logger.warning(
                    "NVDEC 解码利用率指标不受该平台支持，停用利用率闸门"
                    "（显存闸门仍生效）"
                )

    def has_headroom(self, active_count: int) -> Optional[bool]:
        """True=有余量;False=拒绝;None=无数据不否决。首路放行以产生度量。"""
        if not active_count:
            return True
        self._refresh_metrics()
        if self._decoder_util is None and self._vram_free_mb is None:
            return None
        if self._decoder_util is not None and self._decoder_util >= self.util_threshold:
            return False
        if (
            self._vram_free_mb is not None
            and self._vram_free_mb < self.vram_per_instance_mb + self.vram_reserve_mb
        ):
            return False
        return True

    def upgrade_ceiling(self, discovered_slots: int, max_slots: int) -> int:
        """稳定升档上限。

        初始值是型号表估算（软上限），闸门健康时允许升档越过它直抵 max_slots;
        但两种情况下初始值是硬上限:
        - 运维通过 HW_DECODE_NV_INITIAL_SLOTS 显式指定——尊重显式配置;
        - NVML 不可用——利用率/显存闸门处于失明状态,不允许无闸门放大槽位。
        """
        if self.initial_slots_override > 0:
            return discovered_slots
        if not self._ensure_nvml():
            return discovered_slots
        return max_slots

    def stats(self) -> dict:
        return {
            'gpu_name': self._gpu_name,
            'decoder_util_percent': self._decoder_util,
            'decoder_util_unsupported': self._decoder_util_unsupported,
            'vram_free_mb': (
                round(self._vram_free_mb, 1)
                if self._vram_free_mb is not None
                else None
            ),
        }


def build_capacity_probe(
    decoder_type: str,
    *,
    nv_gpu_index: int = 0,
    nv_util_threshold: int = 85,
    nv_vram_per_instance_mb: int = 128,
    nv_vram_reserve_mb: int = 1024,
    nv_initial_slots: int = 0,
    cma_meminfo_path: str = '/proc/meminfo',
    cma_per_instance_mb: int = 16,
    cma_reserve_mb: int = 160,
    time_func=time.monotonic,
):
    """按解码器类型构建容量探针:NVDEC → NVML,Jetson/RK/其他 → CMA。"""
    if (decoder_type or '').lower() in NVDEC_DECODER_TYPES:
        return NvmlCapacityProbe(
            gpu_index=nv_gpu_index,
            util_threshold=nv_util_threshold,
            vram_per_instance_mb=nv_vram_per_instance_mb,
            vram_reserve_mb=nv_vram_reserve_mb,
            initial_slots_override=nv_initial_slots,
            time_func=time_func,
        )
    return CmaCapacityProbe(
        meminfo_path=cma_meminfo_path,
        per_instance_mb=cma_per_instance_mb,
        reserve_mb=cma_reserve_mb,
        time_func=time_func,
    )


class HwDecodeBudget:
    """自适应硬解并发槽位预算器（账本 + 反馈，容量依据由探针提供）。"""

    # 兼容保留:CMA 探针的 CmaFree 重读间隔（秒）
    CMA_REFRESH_SECONDS = CmaCapacityProbe.REFRESH_SECONDS

    def __init__(
        self,
        *,
        enabled: bool = True,
        per_instance_mb: int = 16,
        reserve_mb: int = 160,
        min_slots: int = 1,
        max_slots: int = 32,
        sw_fallback_enabled: bool = True,
        sw_fallback_max: int = 0,
        stable_window_seconds: float = 600.0,
        meminfo_path: str = '/proc/meminfo',
        time_func=time.monotonic,
        probe=None,
    ):
        self.enabled = enabled
        self.per_instance_mb = max(1, per_instance_mb)
        self.reserve_mb = max(0, reserve_mb)
        self.min_slots = max(1, min_slots)
        self.max_slots = max(self.min_slots, max_slots)
        self.sw_fallback_enabled = sw_fallback_enabled
        self.sw_fallback_max = max(0, sw_fallback_max)
        self.stable_window_seconds = stable_window_seconds
        self._meminfo_path = meminfo_path
        self._time = time_func
        if probe is None:
            # 默认 CMA 探针（Jetson/RK 及旧行为）;callable 形式允许外部
            # 继续改写 self._meminfo_path（如单测）
            probe = CmaCapacityProbe(
                meminfo_path=lambda: self._meminfo_path,
                per_instance_mb=per_instance_mb,
                reserve_mb=reserve_mb,
                time_func=time_func,
            )
        self.probe = probe

        self._holders = set()          # 当前持有硬解槽位的 source_id
        self._acquired_at = {}         # source_id -> 获得槽位的时间（稳定窗口判定）
        self._sw_fallbacks = set()     # 当前处于软解兜底的 source_id
        self._last_upgrade_at = 0.0    # 上次升档时间（升档限速）

        self.discovered_slots = (
            self._discover_slots() if self.enabled else self.min_slots
        )
        self.effective_slots = self.discovered_slots
        logger.info(
            f"硬解预算器初始化: probe={self.probe.describe()}, "
            f"discovered_slots={self.discovered_slots}, "
            f"sw_fallback={'on' if sw_fallback_enabled else 'off'}"
        )

    # ---------- 容量发现 ----------

    def _discover_slots(self):
        """由探针估算初始槽位；无法估算时退化为保守默认。"""
        slots = self.probe.discover_slots()
        if not slots:
            fallback = max(self.min_slots, min(4, self.max_slots))
            logger.warning(
                f"容量探针 {self.probe.describe()} 无法估算硬解容量，"
                f"使用保守默认值: {fallback}"
            )
            return fallback
        clamped = max(self.min_slots, min(int(slots), self.max_slots))
        logger.info(
            f"容量探针 {self.probe.describe()} 估算硬解槽位: {clamped}"
            + (f"（原始估算 {slots}，已钳制）" if clamped != slots else "")
        )
        return clamped

    # ---------- 槽位账本 ----------

    @property
    def active_count(self):
        return len(self._holders)

    @property
    def sw_fallback_count(self):
        return len(self._sw_fallbacks)

    def has_capacity(self) -> bool:
        """是否还能发放新硬解槽位（计数 + 探针水位双重校验）。"""
        if not self.enabled:
            return True
        if len(self._holders) >= self.effective_slots:
            return False
        if self.probe.has_headroom(len(self._holders)) is False:
            return False
        return True

    def try_acquire(self, source_id: int) -> bool:
        """为指定源申请硬解槽位。已持有或预算器关闭时直接成功。"""
        if not self.enabled:
            return True
        if source_id in self._holders:
            return True
        if not self.has_capacity():
            return False
        self._holders.add(source_id)
        self._acquired_at[source_id] = self._time()
        self._sw_fallbacks.discard(source_id)
        logger.info(
            f"硬解槽位授予: source={source_id}, "
            f"占用 {len(self._holders)}/{self.effective_slots}"
        )
        return True

    def release(self, source_id: int):
        """释放源持有的槽位/软解标记（停止、崩溃、禁用时调用）。"""
        if source_id in self._holders:
            self._holders.discard(source_id)
            self._acquired_at.pop(source_id, None)
            logger.info(
                f"硬解槽位释放: source={source_id}, "
                f"占用 {len(self._holders)}/{self.effective_slots}"
            )
        self._sw_fallbacks.discard(source_id)

    def mark_sw_fallback(self, source_id: int) -> bool:
        """登记一路软解兜底；超出 sw_fallback_max 时拒绝（0=不限）。"""
        if source_id in self._sw_fallbacks:
            return True
        if self.sw_fallback_max and len(self._sw_fallbacks) >= self.sw_fallback_max:
            return False
        self._sw_fallbacks.add(source_id)
        return True

    def should_fallback_sw(self) -> bool:
        """预算拒绝后是否允许软解兜底。"""
        if not self.sw_fallback_enabled:
            return False
        if self.sw_fallback_max and len(self._sw_fallbacks) >= self.sw_fallback_max:
            return False
        return True

    # ---------- 自适应反馈 ----------

    def record_resource_failure(self, source_id: int = None):
        """硬解通道申请失败（Host1x/CMA 耗尽等）→ 降档并释放该源槽位。"""
        if source_id is not None:
            self.release(source_id)
        if self.effective_slots > self.min_slots:
            self.effective_slots -= 1
            logger.warning(
                f"硬解资源类失败，槽位降档为 {self.effective_slots}"
                f"（持有者 {len(self._holders)}）"
            )
        # 降档后若持有者超限，不抢占在跑的源，只是不再发新槽位

    def record_stable_success(self):
        """所有持有者稳定运行超过窗口 → 缓慢升档（每次 +1）。

        升档上限由探针决定:CMA 探针为实测的 discovered_slots;
        NVML 探针在闸门健康时允许越过型号表估算直抵 max_slots,
        但运维显式指定初始槽位或 NVML 失明时以初始值为硬上限。
        """
        ceiling = self.probe.upgrade_ceiling(self.discovered_slots, self.max_slots)
        if self.effective_slots >= ceiling:
            return
        if not self._holders:
            return
        now = self._time()
        if now - self._last_upgrade_at < self.stable_window_seconds:
            return
        oldest = min(self._acquired_at.values(), default=now)
        if now - oldest < self.stable_window_seconds:
            return
        self.effective_slots += 1
        self._last_upgrade_at = now
        logger.info(
            f"硬解运行稳定，槽位升档为 {self.effective_slots}（上限 {ceiling}）"
        )

    def get_stats(self) -> dict:
        stats = {
            'enabled': self.enabled,
            'probe': self.probe.describe(),
            'discovered_slots': self.discovered_slots,
            'effective_slots': self.effective_slots,
            'active_hw_decoders': len(self._holders),
            'sw_fallback_decoders': len(self._sw_fallbacks),
        }
        stats.update(self.probe.stats())
        # key 集合保持稳定:NVML 路径下也保留 cma_free_mb(None)
        stats.setdefault('cma_free_mb', None)
        return stats
