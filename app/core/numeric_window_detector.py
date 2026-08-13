"""有状态的数值窗口变化检测。"""

from collections import deque
from dataclasses import dataclass, field
from statistics import median
from threading import RLock
from typing import Deque, Dict, Hashable, Optional, Tuple


@dataclass
class _NumericWindowState:
    signature: Tuple
    history: Deque[float]
    last_sample_id: Optional[Hashable] = None
    pending_direction: Optional[str] = None
    confirmation_streak: int = 0
    recovery_streak: int = 0
    latched: bool = False
    latched_direction: Optional[str] = None
    last_result: Optional[dict] = field(default=None)


class NumericWindowDetector:
    """按 key 隔离历史窗口，并对单个数值样本执行骤变检测。"""

    VALID_DIRECTIONS = {'increase', 'decrease', 'both'}

    def __init__(self):
        self._states: Dict[Hashable, _NumericWindowState] = {}
        self._lock = RLock()

    def clear(self, key: Optional[Hashable] = None):
        with self._lock:
            if key is None:
                self._states.clear()
            else:
                self._states.pop(key, None)

    def evaluate(
        self,
        key: Hashable,
        sample_id: Hashable,
        value: float,
        *,
        window_size: int,
        direction: str,
        relative_threshold: float,
        absolute_threshold: float,
        confirmation_count: int,
    ) -> dict:
        window_size = max(1, int(window_size))
        confirmation_count = max(1, int(confirmation_count))
        direction = direction if direction in self.VALID_DIRECTIONS else 'both'
        relative_threshold = max(0.0, float(relative_threshold))
        absolute_threshold = max(0.0, float(absolute_threshold))
        value = float(value)
        signature = (
            window_size,
            direction,
            relative_threshold,
            absolute_threshold,
            confirmation_count,
        )

        with self._lock:
            state = self._states.get(key)
            if state is None or state.signature != signature:
                state = _NumericWindowState(
                    signature=signature,
                    history=deque(maxlen=window_size),
                )
                self._states[key] = state

            if state.last_sample_id == sample_id:
                duplicate = dict(state.last_result or {})
                duplicate.update({
                    'sampled': False,
                    'duplicate_sample': True,
                    'emitted': False,
                })
                return duplicate

            state.last_sample_id = sample_id
            history_count = len(state.history)
            if history_count < window_size:
                state.history.append(value)
                result = {
                    'sampled': True,
                    'duplicate_sample': False,
                    'warmed_up': False,
                    'warmup_count': len(state.history),
                    'window_size': window_size,
                    'current_count': value,
                    'baseline': None,
                    'delta': None,
                    'relative_change': None,
                    'change_direction': None,
                    'candidate': False,
                    'abnormal': state.latched,
                    'triggered': False,
                    'emitted': False,
                    'confirmation_progress': 0,
                    'confirmation_count': confirmation_count,
                    'armed': not state.latched,
                }
                state.last_result = result
                return dict(result)

            baseline = float(median(state.history))
            delta = value - baseline
            relative_change = abs(delta) / max(abs(baseline), 1.0)
            change_direction = 'increase' if delta > 0 else 'decrease' if delta < 0 else None
            direction_matches = (
                change_direction is not None
                and (direction == 'both' or direction == change_direction)
            )
            candidate = bool(
                direction_matches
                and abs(delta) >= absolute_threshold
                and relative_change >= relative_threshold
            )

            triggered = False
            if candidate:
                state.recovery_streak = 0
                if not state.latched:
                    if state.pending_direction == change_direction:
                        state.confirmation_streak += 1
                    else:
                        state.pending_direction = change_direction
                        state.confirmation_streak = 1
                    if state.confirmation_streak >= confirmation_count:
                        state.latched = True
                        state.latched_direction = change_direction
                        state.confirmation_streak = confirmation_count
                        triggered = True
            else:
                state.pending_direction = None
                state.confirmation_streak = 0
                if state.latched:
                    state.recovery_streak += 1
                    if state.recovery_streak >= confirmation_count:
                        state.latched = False
                        state.latched_direction = None
                        state.recovery_streak = 0

            result = {
                'sampled': True,
                'duplicate_sample': False,
                'warmed_up': True,
                'warmup_count': window_size,
                'window_size': window_size,
                'current_count': value,
                'baseline': baseline,
                'delta': delta,
                'relative_change': relative_change,
                'change_direction': change_direction,
                'candidate': candidate,
                'abnormal': state.latched,
                'triggered': triggered,
                'emitted': triggered,
                'confirmation_progress': state.confirmation_streak,
                'confirmation_count': confirmation_count,
                'armed': not state.latched,
                'latched_direction': state.latched_direction,
            }
            # 确认中的异常样本不能反过来抬高/压低自己的历史基线，否则在
            # confirmation_count 较大时，持续变化可能在确认前被窗口吞掉。
            # 触发后允许新样本进入窗口，使基线逐步适应新的稳定水平。
            if not candidate or state.latched:
                state.history.append(value)
            state.last_result = result
            return dict(result)
