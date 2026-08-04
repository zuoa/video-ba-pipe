"""NVDEC(NVML)容量探针与 GPU 型号查表的单元测试。"""

from types import SimpleNamespace

import pytest

from app.core.hw_decode_budget import (
    HwDecodeBudget,
    NvmlCapacityProbe,
    build_capacity_probe,
    estimate_nvdec_decode_streams,
    estimate_nvdec_engines,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeNvml:
    """模拟 pynvml 的最小接口。"""

    def __init__(
        self,
        name='NVIDIA L4',
        util=10,
        vram_free_mb=8192,
        fail_init=False,
        fail_init_times=0,
        fail_decoder_util=False,
        memory_info_without_free=False,
    ):
        self.name = name
        self.util = util
        self.vram_free_mb = vram_free_mb
        self.fail_init = fail_init
        self.fail_init_times = fail_init_times
        self.fail_decoder_util = fail_decoder_util
        self.memory_info_without_free = memory_info_without_free
        self.init_count = 0
        self.memory_info_count = 0

    def nvmlInit(self):
        self.init_count += 1
        if self.fail_init or self.init_count <= self.fail_init_times:
            raise RuntimeError('NVML library not found')

    def nvmlDeviceGetHandleByIndex(self, index):
        return f'handle{index}'

    def nvmlDeviceGetName(self, handle):
        return self.name

    def nvmlDeviceGetDecoderUtilization(self, handle):
        if handle is None:
            raise RuntimeError('invalid handle')
        if self.fail_decoder_util:
            raise RuntimeError('Not Supported')
        return (self.util, 500000)

    def nvmlDeviceGetMemoryInfo(self, handle):
        self.memory_info_count += 1
        if handle is None:
            raise RuntimeError('invalid handle')
        if self.memory_info_without_free:
            return SimpleNamespace(total=16 * 1024**3)
        return SimpleNamespace(free=self.vram_free_mb * 1024 * 1024)


def _probe(clock=None, **overrides):
    kwargs = dict(
        gpu_index=0,
        util_threshold=85,
        vram_per_instance_mb=128,
        vram_reserve_mb=1024,
        time_func=(clock or FakeClock()),
        nvml=FakeNvml(),
    )
    kwargs.update(overrides)
    return NvmlCapacityProbe(**kwargs)


# ---------- GPU 型号查表 ----------

@pytest.mark.parametrize(
    ("gpu_name", "engines"),
    [
        ('NVIDIA H100 PCIe', 7),
        ('NVIDIA H200', 7),
        ('NVIDIA A100-SXM4-80GB', 5),
        ('NVIDIA A30', 4),
        ('NVIDIA L4', 4),
        ('NVIDIA L40', 3),
        ('NVIDIA L40S', 3),
        ('Tesla T4', 2),
        ('NVIDIA A10', 2),
        ('NVIDIA A10G', 2),
        ('NVIDIA A40', 2),
        ('NVIDIA A2', 2),
        ('NVIDIA RTX A5000', 2),
        ('NVIDIA RTX A5500', 2),
        ('NVIDIA RTX A6000', 2),
        ('Tesla V100-SXM2-32GB', 1),
        ('NVIDIA GeForce RTX 4090', 1),
        ('NVIDIA GeForce RTX 3060', 1),
        ('NVIDIA RTX A2000', 1),   # 不能误命中 A2
        ('NVIDIA RTX A4000', 1),
        ('Quadro RTX 8000', 1),
    ],
)
def test_estimate_nvdec_engines(gpu_name, engines):
    assert estimate_nvdec_engines(gpu_name) == engines


def test_estimate_unknown_gpu():
    assert estimate_nvdec_engines('Mystery Accelerator 9000') is None
    assert estimate_nvdec_engines(None) is None
    assert estimate_nvdec_engines('') is None
    assert estimate_nvdec_decode_streams('Mystery Accelerator 9000') is None


def test_estimate_streams_scales_with_engines():
    # T4 = 2 引擎 × 25 路 = 50
    assert estimate_nvdec_decode_streams('Tesla T4') == 50


# ---------- NVML 探针 ----------

def test_discover_slots_from_gpu_table():
    probe = _probe(nvml=FakeNvml(name='Tesla T4'))
    assert probe.discover_slots() == 50
    assert 'Tesla T4' in probe.describe()


def test_discover_slots_unknown_gpu_uses_conservative_default():
    probe = _probe(nvml=FakeNvml(name='Mystery GPU'))
    assert probe.discover_slots() == NvmlCapacityProbe.UNKNOWN_GPU_INITIAL_STREAMS


def test_discover_slots_override_wins():
    probe = _probe(initial_slots_override=12)
    assert probe.discover_slots() == 12


def test_discover_slots_nvml_unavailable():
    probe = _probe(nvml=FakeNvml(fail_init=True))
    assert probe.discover_slots() is None
    assert probe.has_headroom(3) is None  # 无数据不否决


def test_nvml_init_failure_retries_after_cooldown():
    clock = FakeClock()
    nvml = FakeNvml(fail_init_times=1)  # 仅第一次初始化失败
    probe = _probe(clock, nvml=nvml)
    assert probe.discover_slots() is None
    # 冷却期内不重试
    clock.advance(NvmlCapacityProbe.NVML_RETRY_SECONDS - 1)
    assert probe._ensure_nvml() is False
    assert nvml.init_count == 1
    # 冷却结束后重试成功,探针恢复
    clock.advance(2)
    assert probe.discover_slots() == 100  # L4 = 4 × 25
    assert nvml.init_count == 2


def test_decoder_util_not_supported_disables_only_util_gate():
    clock = FakeClock()
    nvml = FakeNvml(util=90, vram_free_mb=100, fail_decoder_util=True)
    probe = _probe(clock, nvml=nvml)
    # 'Not Supported' → 立即判定不受支持;显存闸门独立否决(100MB < 128+1024)
    assert probe.has_headroom(1) is False
    assert probe._decoder_util_unsupported is True
    assert probe._vram_free_mb == 100.0
    # 显存充足后放行:不受支持的利用率指标不再阻塞
    nvml.vram_free_mb = 8192
    clock.advance(NvmlCapacityProbe.REFRESH_SECONDS + 1)
    assert probe.has_headroom(1) is True
    # decoder-util 失败不置空句柄:不会每周期重建 NVML
    inits = nvml.init_count
    clock.advance(NvmlCapacityProbe.REFRESH_SECONDS + 1)
    probe.has_headroom(1)
    assert nvml.init_count == inits
    assert probe.stats()['decoder_util_unsupported'] is True


def test_decoder_util_transient_failure_does_not_latch():
    clock = FakeClock()
    nvml = FakeNvml(fail_decoder_util=True)
    # 非 Not Supported 类错误:仅本周期无数据,不判定为不受支持
    nvml.nvmlDeviceGetDecoderUtilization = lambda h: (_ for _ in ()).throw(
        RuntimeError('Unknown Error')
    )
    probe = _probe(clock, nvml=nvml)
    assert probe.has_headroom(1) is True  # 显存充足,利用率无数据不否决
    assert probe._decoder_util_unsupported is False
    assert probe._decoder_util is None
    # 下周期恢复
    nvml.nvmlDeviceGetDecoderUtilization = lambda h: (42, 500000)
    clock.advance(NvmlCapacityProbe.REFRESH_SECONDS + 1)
    probe.has_headroom(1)
    assert probe._decoder_util == 42


def test_vram_read_failure_rebuilds_handle_next_cycle():
    clock = FakeClock()
    nvml = FakeNvml()
    probe = _probe(clock, nvml=nvml)
    assert probe.has_headroom(1) is True
    # 模拟外部 nvmlShutdown 导致句柄失效:显存本周期无数据,
    # 但利用率指标(句柄无关的假实现)仍可继续否决/放行
    def boom(handle):
        raise RuntimeError('Unknown Error')
    nvml.nvmlDeviceGetMemoryInfo = boom
    clock.advance(NvmlCapacityProbe.REFRESH_SECONDS + 1)
    probe.has_headroom(1)
    assert probe._vram_free_mb is None
    assert probe._handle is None
    # 恢复后下个周期句柄重建、指标恢复
    nvml.nvmlDeviceGetMemoryInfo = lambda h: SimpleNamespace(free=8192 * 1024 * 1024)
    clock.advance(NvmlCapacityProbe.REFRESH_SECONDS + 1)
    assert probe.has_headroom(1) is True
    assert probe._vram_free_mb == 8192.0


def test_missing_free_attribute_is_no_data_not_zero():
    probe = _probe(nvml=FakeNvml(memory_info_without_free=True, util=10))
    # free 缺失 → None(不否决),而不是 0(永久硬拒绝)
    assert probe.has_headroom(1) is True
    assert probe._vram_free_mb is None


def test_headroom_first_holder_always_allowed():
    probe = _probe(nvml=FakeNvml(util=99, vram_free_mb=0))
    assert probe.has_headroom(0) is True


def test_headroom_rejects_on_decoder_util_saturation():
    clock = FakeClock()
    probe = _probe(clock, nvml=FakeNvml(util=90))
    assert probe.has_headroom(1) is False


def test_headroom_rejects_on_low_vram():
    # 空闲 1000MB < 每路 128 + 预留 1024
    probe = _probe(nvml=FakeNvml(util=10, vram_free_mb=1000))
    assert probe.has_headroom(1) is False


def test_headroom_allows_when_metrics_healthy():
    probe = _probe(nvml=FakeNvml(util=40, vram_free_mb=8192))
    assert probe.has_headroom(1) is True


def test_headroom_metrics_refresh_is_rate_limited():
    clock = FakeClock()
    nvml = FakeNvml(util=10)
    probe = _probe(clock, nvml=nvml)
    assert probe.has_headroom(1) is True
    # 未到刷新间隔,利用率上升也不生效
    nvml.util = 99
    clock.advance(1)
    assert probe.has_headroom(1) is True
    # 过刷新间隔后读到新值
    clock.advance(NvmlCapacityProbe.REFRESH_SECONDS)
    assert probe.has_headroom(1) is False


# ---------- 与预算器集成 ----------

def _nv_budget(clock=None, nvml=None, **overrides):
    kwargs = dict(
        enabled=True,
        min_slots=1,
        max_slots=32,
        probe=_probe(clock, nvml=nvml or FakeNvml(name='NVIDIA L4')),
        time_func=(clock or FakeClock()),
    )
    kwargs.update(overrides)
    return HwDecodeBudget(**kwargs)


def test_budget_discovers_slots_from_nvml_table():
    # L4 = 4 引擎 × 25 = 100,钳制到 max_slots=32
    budget = _nv_budget()
    assert budget.discovered_slots == 32


def test_budget_utilization_gate_blocks_before_count_limit():
    clock = FakeClock()
    nvml = FakeNvml(name='Tesla T4', util=10)
    budget = _nv_budget(clock, nvml=nvml, max_slots=64)  # 50 槽
    assert budget.try_acquire(1) is True
    # 解码引擎打满 → 即使计数远未达上限也拒绝
    nvml.util = 95
    clock.advance(NvmlCapacityProbe.REFRESH_SECONDS + 1)
    assert budget.try_acquire(2) is False
    assert budget.get_stats()['decoder_util_percent'] == 95


def test_budget_upgrade_beyond_estimate_allowed_when_probe_healthy():
    clock = FakeClock()
    budget = _nv_budget(clock, max_slots=10)
    budget.discovered_slots = budget.effective_slots = 4
    budget.try_acquire(1)
    clock.advance(601)
    budget.record_stable_success()
    # 闸门健康:升档上限是 max_slots 而非 discovered
    assert budget.effective_slots == 5


def test_budget_upgrade_ceiling_respects_initial_slots_override():
    clock = FakeClock()
    probe = _probe(clock, initial_slots_override=6)
    budget = _nv_budget(clock, probe=probe, max_slots=32)
    assert budget.discovered_slots == 6
    budget.try_acquire(1)
    clock.advance(601)
    budget.record_stable_success()
    # 运维显式指定的初始槽位是硬上限,不被稳定升档抹掉
    assert budget.effective_slots == 6


def test_budget_upgrade_ceiling_locked_when_nvml_unavailable():
    clock = FakeClock()
    nvml = FakeNvml(fail_init=True)
    probe = _probe(clock, nvml=nvml)
    budget = _nv_budget(clock, probe=probe, max_slots=32)
    assert budget.discovered_slots == 4  # 保守默认
    budget.try_acquire(1)
    clock.advance(601)
    budget.record_stable_success()
    # NVML 失明状态下不允许无闸门放大槽位
    assert budget.effective_slots == 4
    # NVML 恢复后解除锁定
    nvml.fail_init = False
    clock.advance(NvmlCapacityProbe.NVML_RETRY_SECONDS + 601)
    budget.record_stable_success()
    assert budget.effective_slots == 5


def test_budget_disabled_skips_probe_discovery():
    nvml = FakeNvml()
    budget = _nv_budget(probe=_probe(nvml=nvml), enabled=False)
    assert budget.discovered_slots == budget.min_slots
    assert nvml.init_count == 0  # 禁用时不做 NVML 初始化


def test_get_stats_always_has_cma_free_mb_key():
    budget = _nv_budget()
    stats = budget.get_stats()
    assert 'cma_free_mb' in stats
    assert stats['cma_free_mb'] is None  # NVML 路径下保留 key,值为 None
    assert stats['probe'].startswith('nvml(')


def test_budget_cma_probe_keeps_hard_ceiling(tmp_path):
    # 未注入探针时保持 CMA 行为:升档上限为 discovered_slots
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("CmaTotal: 262144 kB\nCmaFree: 123264 kB\n")
    clock = FakeClock()
    budget = HwDecodeBudget(
        enabled=True, min_slots=1, max_slots=32,
        meminfo_path=str(meminfo), time_func=clock,
    )
    assert budget.discovered_slots == 6
    budget.try_acquire(1)
    clock.advance(601)
    budget.record_stable_success()
    assert budget.effective_slots == 6  # 已到硬上限


# ---------- 探针工厂 ----------

def test_factory_selects_nvml_probe_for_nvdec():
    probe = build_capacity_probe('ffmpeg_nvdec')
    assert isinstance(probe, NvmlCapacityProbe)
    probe = build_capacity_probe('nvdec')
    assert isinstance(probe, NvmlCapacityProbe)


def test_factory_selects_cma_probe_for_others():
    from app.core.hw_decode_budget import CmaCapacityProbe
    assert isinstance(build_capacity_probe('jetson_gst'), CmaCapacityProbe)
    assert isinstance(build_capacity_probe('rk_mpp'), CmaCapacityProbe)
    assert isinstance(build_capacity_probe('ffmpeg_sw'), CmaCapacityProbe)


# ---------- 别名清单与解码器注册表一致性 ----------

def test_nvdec_decoder_types_in_sync_with_decoder_registry():
    """NVDEC_DECODER_TYPES 必须与注册表中映射到 FFmpegNVDECDecoder 的别名一致,
    漏改会导致 X86 CUDA 主机静默选错探针(回退 CMA → 4 槽位)。"""
    from app.core.decoder import DECODER_REGISTRY
    from app.core.decoder.nv import FFmpegNVDECDecoder
    from app.core.hw_decode_budget import NVDEC_DECODER_TYPES

    registry_nvdec_aliases = {
        alias for alias, cls in DECODER_REGISTRY.items()
        if cls is FFmpegNVDECDecoder
    }
    assert registry_nvdec_aliases == NVDEC_DECODER_TYPES
