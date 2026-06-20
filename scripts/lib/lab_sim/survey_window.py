from __future__ import annotations

import math
from typing import NamedTuple


class SurveyWindowConfig(NamedTuple):
    distance_m: float = 0.0
    time_s: float = 0.0


class SurveyWindowState(NamedTuple):
    distance_window_m: float = 0.0
    time_window_s: float = 0.0
    distance_since_window_m: float = 0.0
    time_since_window_s: float = 0.0
    window_index: int = 0
    window_triggered: bool = False

    @classmethod
    def from_config(cls, config: SurveyWindowConfig) -> SurveyWindowState:
        return cls(_non_negative(config.distance_m), _non_negative(config.time_s))

    def reset(self) -> SurveyWindowState:
        return type(self)(self.distance_window_m, self.time_window_s)

    def clear_trigger(self) -> SurveyWindowState:
        return type(self)(
            self.distance_window_m,
            self.time_window_s,
            self.distance_since_window_m,
            self.time_since_window_s,
            self.window_index,
            False,
        )

    def accumulate(self, distance_m: float, time_s: float) -> SurveyWindowState:
        next_distance = self.distance_since_window_m + _non_negative(distance_m)
        next_time = self.time_since_window_s + _non_negative(time_s)
        distance_hit = self.distance_window_m > 0.0 and next_distance >= self.distance_window_m
        time_hit = self.time_window_s > 0.0 and next_time >= self.time_window_s
        if not (distance_hit or time_hit):
            return type(self)(self.distance_window_m, self.time_window_s, next_distance, next_time, self.window_index, False)
        if distance_hit:
            next_distance %= self.distance_window_m
        if time_hit:
            next_time %= self.time_window_s
        return type(self)(self.distance_window_m, self.time_window_s, next_distance, next_time, self.window_index + 1, True)

    def snapshot(self):
        return {
            "distance_window_m": self.distance_window_m,
            "time_window_s": self.time_window_s,
            "distance_since_window_m": self.distance_since_window_m,
            "time_since_window_s": self.time_since_window_s,
            "window_triggered": bool(self.window_triggered),
            "window_index": self.window_index,
        }


def _non_negative(value) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(parsed):
        return 0.0
    return max(0.0, parsed)
