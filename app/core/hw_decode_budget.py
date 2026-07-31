"""硬件解码资源准入控制器。

按可用硬件资源（Linux CMA）自适应限制硬解码器并发数：
- 容量发现：解析 /proc/meminfo 的 CmaTotal/CmaFree 估算初始槽位；
- 自适应反馈：硬解通道申请失败（资源类错误）自动降档，
  稳定运行一段时间后缓慢升档试探真实容量；
- 拿不到槽位的源由上层决定软解兜底或排队等待。

与路数无关：换设备、增减视频源都无需人工调参。
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

# 被视作硬件解码器的类型（与 app/core/decoder/__init__.py 的别名保持一致）
HW_DECODER_TYPES = {
    'jetson_gst', 'jetson', 'nvv4l2',
    'rk_mpp', 'rkmpp', 'ffmpeg_rkmpp',
    'ffmpeg_nvdec', 'nvdec',
}

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


class HwDecodeBudget:
    """自适应硬解并发槽位预算器（纯逻辑，不依赖 DB，方便单测）。"""

    # CmaFree 重读间隔（秒）
    CMA_REFRESH_SECONDS = 30.0

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

        self._holders = set()          # 当前持有硬解槽位的 source_id
        self._acquired_at = {}         # source_id -> 获得槽位的时间（稳定窗口判定）
        self._sw_fallbacks = set()     # 当前处于软解兜底的 source_id
        self._last_cma_free_mb = None
        self._last_cma_read_at = 0.0
        self._last_upgrade_at = 0.0    # 上次升档时间（升档限速）

        self.discovered_slots = self._discover_slots()
        self.effective_slots = self.discovered_slots
        logger.info(
            f"硬解预算器初始化: discovered_slots={self.discovered_slots}, "
            f"per_instance={self.per_instance_mb}MB, reserve={self.reserve_mb}MB, "
            f"sw_fallback={'on' if sw_fallback_enabled else 'off'}"
        )

    # ---------- 容量发现 ----------

    def _discover_slots(self):
        """按 CMA 总量估算硬解槽位数；无法读取时退化为 min/max 中位偏保守值。"""
        cma_total_mb, _ = read_cma_info(self._meminfo_path)
        if not cma_total_mb:
            # 非 Linux / 无 CMA 信息：给保守默认
            fallback = max(self.min_slots, min(4, self.max_slots))
            logger.warning(f"无法读取 CMA 信息，硬解槽位使用保守默认值: {fallback}")
            return fallback
        slots = (cma_total_mb - self.reserve_mb) // self.per_instance_mb
        slots = max(self.min_slots, min(int(slots), self.max_slots))
        logger.info(f"CMA 总量 {cma_total_mb}MB，估算硬解槽位: {slots}")
        return slots

    def _refresh_cma_free(self):
        """限频重读 CmaFree。"""
        now = self._time()
        if now - self._last_cma_read_at < self.CMA_REFRESH_SECONDS:
            return
        self._last_cma_read_at = now
        _, free_mb = read_cma_info(self._meminfo_path)
        if free_mb is not None:
            self._last_cma_free_mb = free_mb

    # ---------- 槽位账本 ----------

    @property
    def active_count(self):
        return len(self._holders)

    @property
    def sw_fallback_count(self):
        return len(self._sw_fallbacks)

    def has_capacity(self) -> bool:
        """是否还能发放新硬解槽位（计数 + CmaFree 双重校验）。"""
        if not self.enabled:
            return True
        if len(self._holders) >= self.effective_slots:
            return False
        self._refresh_cma_free()
        if (
            self._last_cma_free_mb is not None
            and self._holders  # 已有持有者在跑，新一路需要额外的 CMA
            and self._last_cma_free_mb < self.per_instance_mb
        ):
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
        """所有持有者稳定运行超过窗口 → 缓慢升档（每次 +1，上限 discovered）。"""
        if self.effective_slots >= self.discovered_slots:
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
        logger.info(f"硬解运行稳定，槽位升档为 {self.effective_slots}")

    def get_stats(self) -> dict:
        return {
            'enabled': self.enabled,
            'discovered_slots': self.discovered_slots,
            'effective_slots': self.effective_slots,
            'active_hw_decoders': len(self._holders),
            'sw_fallback_decoders': len(self._sw_fallbacks),
            'cma_free_mb': self._last_cma_free_mb,
        }
