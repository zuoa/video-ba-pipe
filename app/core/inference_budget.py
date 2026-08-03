"""Inference memory admission and OOM restart circuit breaking.

The Jetson GPU uses system RAM as unified memory, so GPU allocations are visible
through MemAvailable/cgroup accounting rather than nvidia-smi.  This module keeps
the policy independent from the orchestrator so it can be tested deterministically.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Optional, Set, Tuple


@dataclass(frozen=True)
class MemorySnapshot:
    total_mb: float
    available_mb: float
    swap_used_mb: float


def read_memory_snapshot(meminfo_path: str = "/proc/meminfo") -> Optional[MemorySnapshot]:
    values: Dict[str, int] = {}
    try:
        with open(meminfo_path, "r", encoding="utf-8") as handle:
            for line in handle:
                key, separator, value = line.partition(":")
                if not separator:
                    continue
                token = value.strip().split()[0]
                try:
                    values[key] = int(token)
                except (ValueError, IndexError):
                    continue
    except OSError:
        return None

    total_kb = values.get("MemTotal")
    available_kb = values.get("MemAvailable")
    if total_kb is None or available_kb is None:
        return None
    swap_used_kb = max(0, values.get("SwapTotal", 0) - values.get("SwapFree", 0))
    return MemorySnapshot(
        total_mb=total_kb / 1024.0,
        available_mb=available_kb / 1024.0,
        swap_used_mb=swap_used_kb / 1024.0,
    )


def _current_cgroup_dir() -> str:
    try:
        with open("/proc/self/cgroup", "r", encoding="utf-8") as handle:
            for line in handle:
                hierarchy, controllers, path = line.strip().split(":", 2)
                if hierarchy == "0" and controllers == "":
                    return os.path.join("/sys/fs/cgroup", path.lstrip("/"))
    except (OSError, ValueError):
        pass
    return "/sys/fs/cgroup"


def read_cgroup_oom_kill_count(events_path: Optional[str] = None) -> int:
    path = events_path or os.path.join(_current_cgroup_dir(), "memory.events")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.partition(" ")
                if key == "oom_kill":
                    return max(0, int(value.strip()))
    except (OSError, ValueError):
        return 0
    return 0


def read_process_memory_metrics(pid: int) -> Dict[str, float]:
    values: Dict[str, float] = {}
    try:
        with open(f"/proc/{int(pid)}/smaps_rollup", "r", encoding="utf-8") as handle:
            for line in handle:
                key, separator, value = line.partition(":")
                if not separator or key not in {
                    "Rss", "Pss", "Private_Clean", "Private_Dirty", "Swap"
                }:
                    continue
                values[key] = int(value.strip().split()[0]) / 1024.0
    except (OSError, ValueError, IndexError):
        return {}
    return {
        "rss_mb": values.get("Rss", 0.0),
        "pss_mb": values.get("Pss", 0.0),
        "private_mb": values.get("Private_Clean", 0.0)
        + values.get("Private_Dirty", 0.0),
        "swap_mb": values.get("Swap", 0.0),
    }


def read_process_pss_mb(pid: int) -> Optional[float]:
    metrics = read_process_memory_metrics(pid)
    return metrics.get("pss_mb") if metrics else None


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    reason: str
    available_mb: float = 0.0
    reserve_mb: float = 0.0
    estimated_increment_mb: float = 0.0
    new_model_ids: Tuple[int, ...] = ()
    local_model_ids: Tuple[int, ...] = ()


@dataclass
class SourceModelAllocation:
    shared_model_ids: Set[int] = field(default_factory=set)
    local_model_ids: Tuple[int, ...] = ()
    pending: bool = True


class InferenceAdmissionController:
    """Reserve RAM before a source host can introduce new model workers."""

    def __init__(
        self,
        *,
        enabled: bool,
        reserve_mb: int,
        reserve_percent: float,
        default_new_model_mb: int,
        margin_percent: float,
        memory_reader: Callable[[], Optional[MemorySnapshot]] = read_memory_snapshot,
    ):
        self.enabled = bool(enabled)
        self.reserve_mb = float(reserve_mb)
        self.reserve_percent = float(reserve_percent)
        self.default_new_model_mb = float(default_new_model_mb)
        self.margin_percent = float(margin_percent)
        self.memory_reader = memory_reader
        self.source_allocations: Dict[int, SourceModelAllocation] = {}
        self.observed_model_pss_mb: Dict[int, float] = {}

    @property
    def active_shared_model_ids(self) -> Set[int]:
        active: Set[int] = set()
        for allocation in self.source_allocations.values():
            active.update(allocation.shared_model_ids)
        return active

    def update_observed_model_pss(self, model_id: int, pss_mb: Optional[float]) -> None:
        if pss_mb is None or pss_mb <= 0:
            return
        previous = self.observed_model_pss_mb.get(int(model_id), 0.0)
        # Retain the high-water observation; admission must not learn an unsafe average.
        self.observed_model_pss_mb[int(model_id)] = max(previous, float(pss_mb))

    def evaluate(
        self,
        source_id: int,
        shared_model_ids: Iterable[int],
        *,
        local_model_ids: Iterable[int] = (),
        service_model_ids: Iterable[int] = (),
    ) -> AdmissionDecision:
        requested_shared = {
            int(value) for value in shared_model_ids if value is not None
        }
        requested_local = tuple(
            int(value) for value in local_model_ids if value is not None
        )
        if not self.enabled or (not requested_shared and not requested_local):
            return AdmissionDecision(True, "disabled_or_no_models")

        confirmed_service_models = {
            int(value) for value in service_model_ids if value is not None
        }
        reserved_shared_models = self.active_shared_model_ids - confirmed_service_models
        new_models = tuple(sorted(
            requested_shared
            - confirmed_service_models
            - self.active_shared_model_ids
        ))
        snapshot = self.memory_reader()
        if snapshot is None:
            return AdmissionDecision(
                False,
                "memory_metrics_unavailable",
                new_model_ids=new_models,
                local_model_ids=requested_local,
            )

        reserve = max(self.reserve_mb, snapshot.total_mb * self.reserve_percent / 100.0)
        margin = 1.0 + self.margin_percent / 100.0
        pending_shared_mb = sum(
            self.observed_model_pss_mb.get(model_id, self.default_new_model_mb) * margin
            for model_id in reserved_shared_models
        )
        pending_local_count = sum(
            len(allocation.local_model_ids)
            for allocation in self.source_allocations.values()
            if allocation.pending
        )
        pending_local_mb = pending_local_count * self.default_new_model_mb * margin
        requested_shared_mb = sum(
            self.observed_model_pss_mb.get(model_id, self.default_new_model_mb) * margin
            for model_id in new_models
        )
        # Local backends/direct YOLO load one private copy per occurrence and may
        # never appear in shared-service stats, so they are never deduplicated.
        requested_local_mb = len(requested_local) * self.default_new_model_mb * margin
        increment = (
            pending_shared_mb
            + pending_local_mb
            + requested_shared_mb
            + requested_local_mb
        )
        allowed = snapshot.available_mb - increment >= reserve
        reason = "admitted" if allowed else "insufficient_mem_available"
        return AdmissionDecision(
            allowed=allowed,
            reason=reason,
            available_mb=snapshot.available_mb,
            reserve_mb=reserve,
            estimated_increment_mb=increment,
            new_model_ids=new_models,
            local_model_ids=requested_local,
        )

    def commit(
        self,
        source_id: int,
        shared_model_ids: Iterable[int],
        *,
        local_model_ids: Iterable[int] = (),
    ) -> None:
        self.source_allocations[int(source_id)] = SourceModelAllocation(
            shared_model_ids={
                int(value) for value in shared_model_ids if value is not None
            },
            local_model_ids=tuple(
                int(value) for value in local_model_ids if value is not None
            ),
            pending=True,
        )

    def mark_source_ready(self, source_id: int) -> None:
        allocation = self.source_allocations.get(int(source_id))
        if allocation is not None:
            allocation.pending = False

    def release(self, source_id: int) -> None:
        self.source_allocations.pop(int(source_id), None)


@dataclass
class OomCircuitState:
    failures: int = 0
    next_retry_at: float = 0.0
    circuit_open_until: float = 0.0
    last_oom_at: Optional[float] = None


class OomCircuitBreaker:
    """Per-source circuit breaker plus a short global start gate after OOM."""

    def __init__(
        self,
        *,
        enabled: bool,
        failure_threshold: int,
        open_seconds: float,
        stable_reset_seconds: float,
        backoff_cap_seconds: float,
        time_func: Callable[[], float] = time.monotonic,
    ):
        self.enabled = bool(enabled)
        self.failure_threshold = max(1, int(failure_threshold))
        self.open_seconds = max(1.0, float(open_seconds))
        self.stable_reset_seconds = max(1.0, float(stable_reset_seconds))
        self.backoff_cap_seconds = max(15.0, float(backoff_cap_seconds))
        self.time_func = time_func
        self.states: Dict[int, OomCircuitState] = {}
        self.global_retry_at = 0.0

    def record_oom(self, source_id: int) -> OomCircuitState:
        now = self.time_func()
        state = self.states.setdefault(int(source_id), OomCircuitState())
        if state.last_oom_at is not None and now - state.last_oom_at >= self.stable_reset_seconds:
            state.failures = 0
        state.failures += 1
        state.last_oom_at = now
        backoff = min(15.0 * (2 ** max(0, state.failures - 1)), self.backoff_cap_seconds)
        state.next_retry_at = now + backoff
        self.global_retry_at = max(self.global_retry_at, now + min(backoff, 60.0))
        if state.failures >= self.failure_threshold:
            state.circuit_open_until = now + self.open_seconds
            state.next_retry_at = state.circuit_open_until
        return state

    def can_start(self, source_id: int) -> Tuple[bool, str, float]:
        if not self.enabled:
            return True, "disabled", 0.0
        now = self.time_func()
        if now < self.global_retry_at:
            return False, "global_oom_backoff", self.global_retry_at
        state = self.states.get(int(source_id))
        if state is None:
            return True, "closed", 0.0
        if state.last_oom_at is not None and now - state.last_oom_at >= self.stable_reset_seconds:
            self.states.pop(int(source_id), None)
            return True, "stable_reset", 0.0
        if now < state.circuit_open_until:
            return False, "circuit_open", state.circuit_open_until
        if now < state.next_retry_at:
            return False, "source_oom_backoff", state.next_retry_at
        return True, "half_open" if state.failures else "closed", 0.0

    def clear(self, source_id: int) -> None:
        self.states.pop(int(source_id), None)
