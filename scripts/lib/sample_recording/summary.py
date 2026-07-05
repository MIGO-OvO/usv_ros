from __future__ import annotations

from typing import Iterable, Mapping


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

        timestamp_ms = self._number(frame.get("timestamp_ms"))
        if timestamp_ms is not None:
            if self.first_timestamp_ms is None:
                self.first_timestamp_ms = timestamp_ms
            self.last_timestamp_ms = timestamp_ms

    def extend(self, frames: Iterable[Mapping[str, object]]) -> None:
        for frame in frames:
            self.add_frame(frame)

    def to_dict(self, raw_file: str, duration_s=None) -> dict[str, object]:
        flags = set(self._flags)
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
            "quality_flags": sorted(flags),
        }
