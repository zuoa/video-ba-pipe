"""硬解预算器与失败分类器的单元测试。"""

import pytest

from app.core.hw_decode_budget import HwDecodeBudget, read_cma_info
from app.core.orchestrator import classify_decoder_failure


MEMINFO_TEMPLATE = """MemTotal:       16056952 kB
MemFree:          804352 kB
CmaTotal:         {total_kb} kB
CmaFree:          {free_kb} kB
"""


def _write_meminfo(tmp_path, total_kb=262144, free_kb=123264):
    path = tmp_path / "meminfo"
    path.write_text(MEMINFO_TEMPLATE.format(total_kb=total_kb, free_kb=free_kb))
    return str(path)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _budget(tmp_path, clock=None, **overrides):
    kwargs = dict(
        enabled=True,
        per_instance_mb=16,
        reserve_mb=160,
        min_slots=1,
        max_slots=32,
        sw_fallback_enabled=True,
        sw_fallback_max=0,
        stable_window_seconds=600.0,
        meminfo_path=_write_meminfo(tmp_path),
        time_func=(clock or FakeClock()),
    )
    kwargs.update(overrides)
    return HwDecodeBudget(**kwargs)


# ---------- CMA 读取 ----------

def test_read_cma_info(tmp_path):
    total_mb, free_mb = read_cma_info(_write_meminfo(tmp_path, 262144, 123264))
    assert total_mb == 256
    assert free_mb == 120


def test_read_cma_info_missing(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text("MemTotal: 100 kB\n")
    assert read_cma_info(str(path)) == (None, None)
    assert read_cma_info(str(tmp_path / "nonexistent")) == (None, None)


# ---------- 容量发现 ----------

def test_discover_slots_from_cma(tmp_path):
    # (256 - 160) / 16 = 6
    budget = _budget(tmp_path)
    assert budget.discovered_slots == 6
    assert budget.effective_slots == 6


def test_discover_slots_clamped_by_max(tmp_path):
    budget = _budget(tmp_path, max_slots=3)
    assert budget.discovered_slots == 3


def test_discover_slots_fallback_without_cma(tmp_path):
    path = tmp_path / "meminfo_no_cma"
    path.write_text("MemTotal: 100 kB\n")
    budget = _budget(tmp_path, meminfo_path=str(path))
    assert budget.min_slots <= budget.discovered_slots <= 4


# ---------- 槽位账本 ----------

def test_acquire_up_to_effective_slots(tmp_path):
    budget = _budget(tmp_path)  # 6 槽
    for source_id in range(1, 7):
        assert budget.try_acquire(source_id) is True
    assert budget.try_acquire(7) is False
    assert budget.active_count == 6


def test_release_frees_slot(tmp_path):
    budget = _budget(tmp_path)
    for source_id in range(1, 7):
        budget.try_acquire(source_id)
    budget.release(3)
    assert budget.try_acquire(7) is True


def test_reacquire_same_source_is_idempotent(tmp_path):
    budget = _budget(tmp_path)
    assert budget.try_acquire(1) is True
    assert budget.try_acquire(1) is True
    assert budget.active_count == 1


def test_acquire_rejected_when_cma_free_low(tmp_path):
    clock = FakeClock()
    budget = _budget(tmp_path, clock)
    assert budget.try_acquire(1) is True
    # CmaFree 跌破单实例需求 → 即使计数未满也拒绝新授权
    budget._meminfo_path = _write_meminfo(tmp_path, 262144, 8 * 1024)
    clock.advance(HwDecodeBudget.CMA_REFRESH_SECONDS + 1)
    assert budget.try_acquire(2) is False


def test_disabled_budget_always_grants(tmp_path):
    budget = _budget(tmp_path, enabled=False)
    for source_id in range(1, 40):
        assert budget.try_acquire(source_id) is True


# ---------- 软解兜底决策 ----------

def test_sw_fallback_allowed_when_enabled(tmp_path):
    budget = _budget(tmp_path)
    assert budget.should_fallback_sw() is True
    assert budget.mark_sw_fallback(1) is True
    assert budget.sw_fallback_count == 1


def test_sw_fallback_disabled(tmp_path):
    budget = _budget(tmp_path, sw_fallback_enabled=False)
    assert budget.should_fallback_sw() is False


def test_sw_fallback_max_enforced(tmp_path):
    budget = _budget(tmp_path, sw_fallback_max=2)
    assert budget.mark_sw_fallback(1) is True
    assert budget.mark_sw_fallback(2) is True
    assert budget.mark_sw_fallback(3) is False
    assert budget.should_fallback_sw() is False
    budget.release(1)
    assert budget.mark_sw_fallback(3) is True


def test_acquire_clears_sw_fallback_mark(tmp_path):
    budget = _budget(tmp_path)
    budget.mark_sw_fallback(1)
    assert budget.try_acquire(1) is True
    assert budget.sw_fallback_count == 0


# ---------- 自适应升降档 ----------

def test_resource_failure_steps_down(tmp_path):
    budget = _budget(tmp_path)  # 6 槽
    budget.record_resource_failure(1)
    assert budget.effective_slots == 5
    budget.record_resource_failure(2)
    assert budget.effective_slots == 4


def test_resource_failure_never_below_min(tmp_path):
    budget = _budget(tmp_path, min_slots=2)
    for _ in range(10):
        budget.record_resource_failure()
    assert budget.effective_slots == 2


def test_stable_success_steps_up_slowly(tmp_path):
    clock = FakeClock()
    budget = _budget(tmp_path, clock)
    budget.record_resource_failure()
    assert budget.effective_slots == 5

    budget.try_acquire(1)
    # 稳定窗口未到,不升档
    clock.advance(599)
    budget.record_stable_success()
    assert budget.effective_slots == 5

    # 稳定窗口到达 → +1
    clock.advance(2)
    budget.record_stable_success()
    assert budget.effective_slots == 6

    # 已到 discovered 上限,不再升
    clock.advance(1000)
    budget.record_stable_success()
    assert budget.effective_slots == 6


def test_no_upgrade_without_holders(tmp_path):
    clock = FakeClock()
    budget = _budget(tmp_path, clock)
    budget.record_resource_failure()
    clock.advance(1000)
    budget.record_stable_success()
    assert budget.effective_slots == 5


# ---------- 退避序列 ----------

def test_stream_backoff_grows_exponentially_and_caps():
    from app.core.orchestrator import Orchestrator
    seen = [Orchestrator._compute_backoff_seconds('stream', n) for n in (1, 2, 3, 4, 10)]
    # 30 / 60 / 120 / 240 / 300(封顶),含 ±20% 抖动
    assert 24 <= seen[0] <= 36
    assert 48 <= seen[1] <= 72
    assert 96 <= seen[2] <= 144
    assert 192 <= seen[3] <= 288
    assert 240 <= seen[4] <= 360


def test_crash_backoff_caps_at_120():
    from app.core.orchestrator import Orchestrator
    for n in range(1, 20):
        assert Orchestrator._compute_backoff_seconds('crash', n) <= 144  # 120 * 1.2


def test_classify_then_backoff_integration():
    """404 场景(stream)第一次退避应明显长于崩溃场景基线。"""
    from app.core.orchestrator import Orchestrator
    assert (
        Orchestrator._compute_backoff_seconds('stream', 1)
        > Orchestrator._compute_backoff_seconds('crash', 1)
    )


# ---------- 失败分类器 ----------
@pytest.mark.parametrize(
    ("exit_code", "tail", "uptime", "expected"),
    [
        # NVDEC 资源耗尽:错误模式命中
        (-6, ["InitNVDEC: Host1x channel open failed"], 5.0, "resource"),
        (-6, ["NVMMLITE_NVVIDEODEC, video_parser_parse Unsupported Codec"], 3.0, "resource"),
        (1, ["Maybe be due to not enough memory or failing driver"], 10.0, "resource"),
        (-6, ["NVMEDIA: NvMMDecNvVideoCreateParser failed"], 2.0, "resource"),
        # 原生崩溃:信号退出但无资源模式
        (-6, ["corrupted size vs. prev_size in fastbins"], 100.0, "crash"),
        (-11, ["Segmentation fault"], 50.0, "crash"),
        # 上游流问题
        (0, ["method DESCRIBE failed: 404 Not Found"], 8.0, "stream"),
        (1, ["Connection refused"], 30.0, "stream"),
        (1, ["rtsp://x: Server returned 404 Not Found"], 5.0, "stream"),
        # 启动后很快干净退出 = 流问题（如上游立即 EOF）
        (0, [], 10.0, "stream"),
        # 其余:长时间运行后干净退出、无有效信息
        (0, [], 3600.0, "clean"),
        (1, ["some random error"], 300.0, "clean"),
        (None, [], 0.0, "clean"),
    ],
)
def test_classify_decoder_failure(exit_code, tail, uptime, expected):
    assert classify_decoder_failure(exit_code, tail, uptime) == expected
