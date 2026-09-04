from __future__ import annotations

from typing import Iterable, Mapping

from .models import MIN_RAW_RECORD_HZ


class SpectrometerSummaryBuilder(object):
    def __init__(self) -> None:
        self.frame_count = 0
        self.valid_count = 0
        self._voltage_sum = 0.0
        self._voltage_count = 0
        self._voltage_min = None
        self._voltage_max = None
        self._absorbance_sum = 0.0
        self._absorbance_count = 0
        self._absorbance_min = None
        self._absorbance_max = None
        self._raw_code_min = None
        self._raw_code_max = None
        self.first_timestamp_ms = None
        self.last_timestamp_ms = None
        self._timed_frame_count = 0
        self._flags = set()

    @staticmethod
    def _number(value: object):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number

    @staticmethod
    def _add_range(current_min, current_max, value: float):
        if current_min is None or value < current_min:
            current_min = value
        if current_max is None or value > current_max:
            current_max = value
        return current_min, current_max

    @classmethod
    def _frame_timestamp_ms(cls, frame: Mapping[str, object]):
        for key, multiplier in (
            ("source_timestamp_ms", 1.0),
            ("timestamp_ms", 1.0),
            ("received_at_ms", 1.0),
            ("received_at", 1000.0),
        ):
            timestamp = cls._number(frame.get(key))
            if timestamp is not None:
                return timestamp * multiplier
        return None

    def add_frame(self, frame: Mapping[str, object]) -> None:
        self.frame_count += 1
        if bool(frame.get("valid", False)):
            self.valid_count += 1
        for key in ("i2c_error", "saturated", "not_configured"):
            if bool(frame.get(key, False)):
                self._flags.add("%s_seen" % key)

        voltage = self._number(frame.get("voltage"))
        if voltage is not None:
            self._voltage_sum += voltage
            self._voltage_count += 1
            self._voltage_min, self._voltage_max = self._add_range(self._voltage_min, self._voltage_max, voltage)

        absorbance = self._number(frame.get("absorbance"))
        if absorbance is not None:
            self._absorbance_sum += absorbance
            self._absorbance_count += 1
            self._absorbance_min, self._absorbance_max = self._add_range(
                self._absorbance_min,
                self._absorbance_max,
                absorbance,
            )

        raw_code = self._number(frame.get("raw_code"))
        if raw_code is not None:
            self._raw_code_min, self._raw_code_max = self._add_range(self._raw_code_min, self._raw_code_max, raw_code)

        timestamp_ms = self._frame_timestamp_ms(frame)
        if timestamp_ms is not None:
            self._timed_frame_count += 1
            if self.first_timestamp_ms is None:
                self.first_timestamp_ms = timestamp_ms
            self.last_timestamp_ms = timestamp_ms

    def extend(self, frames: Iterable[Mapping[str, object]]) -> None:
        for frame in frames:
            self.add_frame(frame)

    def to_dict(self, raw_file: str, duration_s=None) -> dict[str, object]:
        flags = set(self._flags)
        observed_rate_hz = None
        if (
            self._timed_frame_count >= 2
            and self.first_timestamp_ms is not None
            and self.last_timestamp_ms is not None
            and self.last_timestamp_ms > self.first_timestamp_ms
        ):
            observed_rate_hz = (
                float(self._timed_frame_count - 1) * 1000.0 /
                (self.last_timestamp_ms - self.first_timestamp_ms)
            )
            if observed_rate_hz + 1e-9 < MIN_RAW_RECORD_HZ:
                flags.add("raw_rate_below_target")
        if self.frame_count == 0:
            flags.add("no_frames")
        if self.frame_count > 0 and self.valid_count == 0:
            flags.add("no_valid_frames")
        if self.frame_count > 0 and float(self.valid_count) / float(self.frame_count) < 0.8:
            flags.add("low_valid_ratio")
        if duration_s is not None and duration_s < 1.0:
            flags.add("short_duration")
        return {
            "raw_file": raw_file,
            "frame_count": self.frame_count,
            "valid_count": self.valid_count,
            "invalid_count": self.frame_count - self.valid_count,
            "voltage_mean": self._voltage_sum / self._voltage_count if self._voltage_count else None,
            "voltage_min": self._voltage_min,
            "voltage_max": self._voltage_max,
            "absorbance_mean": self._absorbance_sum / self._absorbance_count if self._absorbance_count else None,
            "absorbance_min": self._absorbance_min,
            "absorbance_max": self._absorbance_max,
            "raw_code_min": self._raw_code_min,
            "raw_code_max": self._raw_code_max,
            "first_timestamp_ms": self.first_timestamp_ms,
            "last_timestamp_ms": self.last_timestamp_ms,
            "target_rate_hz": MIN_RAW_RECORD_HZ,
            "observed_rate_hz": observed_rate_hz,
            "quality_flags": sorted(flags),
        }
