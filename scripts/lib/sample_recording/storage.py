from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Mapping, Optional

from .models import make_window, normalize_gps_payload, normalize_manual_result, safe_id
from .summary import SpectrometerSummaryBuilder


class SampleRecordingStorage(object):
    def __init__(self, missions_dir: str) -> None:
        self.missions_dir = os.path.abspath(os.path.expanduser(missions_dir))
        self.raw_root = os.path.join(self.missions_dir, "raw")
        self._builders = {}

    def _raw_relpath(self, mission_id: object, sample_id: object) -> str:
        mission = safe_id(mission_id, "mission")
        sample = safe_id(sample_id, "sample")
        if mission != str(mission_id) or sample != str(sample_id):
            raise ValueError("unsafe mission_id or sample_id")
        return os.path.join("raw", mission, sample + ".jsonl").replace(os.sep, "/")

    def _raw_abspath(self, relpath: str) -> str:
        path = os.path.abspath(os.path.join(self.missions_dir, relpath))
        raw_root = os.path.abspath(self.raw_root)
        if path != raw_root and not path.startswith(raw_root + os.sep):
            raise ValueError("raw path escapes missions raw directory")
        return path

    @staticmethod
    def _sample_windows(mission_data: dict[str, object]) -> list[dict[str, object]]:
        mission_data["sample_windows_schema_version"] = 1
        windows = mission_data.setdefault("sample_windows", [])
        if not isinstance(windows, list):
            mission_data["sample_windows"] = []
            windows = mission_data["sample_windows"]
        return windows

    @staticmethod
    def _find_window(mission_data: Mapping[str, object], sample_id: object):
        for window in mission_data.get("sample_windows", []) if isinstance(mission_data, Mapping) else []:
            if isinstance(window, dict) and str(window.get("sample_id")) == str(sample_id):
                return window
        return None

    def start_window(
        self,
        mission_data: dict[str, object],
        context: Optional[Mapping[str, object]] = None,
        gps_latest: Optional[Mapping[str, object]] = None,
    ) -> dict[str, object]:
        mission_id = mission_data.get("mission_id")
        window = make_window(mission_id, context, gps_latest)
        raw_file = self._raw_relpath(window["mission_id"], window["sample_id"])
        window["spectrometer"] = SpectrometerSummaryBuilder().to_dict(raw_file)
        self._sample_windows(mission_data).append(window)
        self._builders[window["sample_id"]] = SpectrometerSummaryBuilder()
        return window

    def append_raw_frame(self, window: dict[str, object], frame: Mapping[str, object]) -> None:
        sample_id = window.get("sample_id")
        raw_file = window.get("spectrometer", {}).get("raw_file") if isinstance(window.get("spectrometer"), dict) else None
        if not raw_file:
            raise ValueError("sample window has no raw_file")
        path = self._raw_abspath(str(raw_file))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(dict(frame), ensure_ascii=False, separators=(",", ":")) + "\n")
        builder = self._builders.setdefault(sample_id, SpectrometerSummaryBuilder())
        builder.add_frame(frame)
        window["spectrometer"] = builder.to_dict(str(raw_file), self._duration_s(window))

    @staticmethod
    def _duration_s(window: Mapping[str, object]):
        start = window.get("start_time")
        end = window.get("end_time")
        if not start or not end:
            return None
        try:
            return (datetime.fromisoformat(str(end).replace("Z", "")) - datetime.fromisoformat(str(start).replace("Z", ""))).total_seconds()
        except ValueError:
            return None

    def close_window(
        self,
        mission_data: dict[str, object],
        window: dict[str, object],
        gps_latest: Optional[Mapping[str, object]] = None,
    ) -> dict[str, object]:
        window["state"] = "closed"
        window["end_time"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="milliseconds") + "Z"
        duration = self._duration_s(window)
        window["duration_s"] = duration
        gps = normalize_gps_payload(gps_latest)
        window["gps_end"] = gps
        window["gps_latest"] = gps or window.get("gps_latest")
        raw_file = window.get("spectrometer", {}).get("raw_file") if isinstance(window.get("spectrometer"), dict) else None
        builder = self._builders.pop(window.get("sample_id"), None)
        if builder is None:
            builder = SpectrometerSummaryBuilder()
            if raw_file:
                path = self._raw_abspath(str(raw_file))
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as file_obj:
                        for line in file_obj:
                            if line.strip():
                                builder.add_frame(json.loads(line))
        window["spectrometer"] = builder.to_dict(str(raw_file or ""), duration)
        stored = self._find_window(mission_data, window.get("sample_id"))
        if stored is not None and stored is not window:
            stored.update(window)
        return window

    def list_windows(self, mission_data: Mapping[str, object]) -> list[dict[str, object]]:
        return [
            dict(window)
            for window in mission_data.get("sample_windows", [])
            if isinstance(window, dict)
        ]

    def read_raw_frames(self, mission_id: object, sample_id: object, limit: Optional[int] = None, offset: int = 0) -> list[dict[str, object]]:
        limit = 2000 if limit is None else max(0, min(int(limit), 20000))
        offset = max(0, int(offset))
        frames = []
        for index, frame in enumerate(self.iter_raw_frames(mission_id, sample_id)):
            if index < offset:
                continue
            if len(frames) >= limit:
                break
            frames.append(frame)
        return frames

    def iter_raw_frames(self, mission_id: object, sample_id: object):
        path = self._raw_abspath(self._raw_relpath(mission_id, sample_id))
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as file_obj:
            for line in file_obj:
                if line.strip():
                    yield json.loads(line)

    @staticmethod
    def _frame_time_ms(frame: Mapping[str, object]):
        for key, multiplier in (("received_at_ms", 1.0), ("received_at", 1000.0), ("source_timestamp_ms", 1.0), ("timestamp_ms", 1.0)):
            try:
                value = float(frame.get(key)) * multiplier
                if value == value and value not in (float("inf"), float("-inf")):
                    return value
            except (TypeError, ValueError):
                pass
        return None

    def read_raw_series(
        self,
        mission_id: object,
        sample_id: object,
        from_ms: Optional[float] = None,
        to_ms: Optional[float] = None,
        max_points: int = 2000,
    ) -> dict[str, object]:
        path = self._raw_abspath(self._raw_relpath(mission_id, sample_id))
        max_points = max(4, min(int(max_points), 20000))
        if not os.path.exists(path):
            return {"raw_count": 0, "returned_count": 0, "from_ms": from_ms, "to_ms": to_ms, "covered": True, "samples": []}

        raw_count = 0
        first_time = None
        last_time = None
        small = []
        with open(path, "r", encoding="utf-8") as file_obj:
            for line in file_obj:
                if not line.strip():
                    continue
                frame = json.loads(line)
                timestamp = self._frame_time_ms(frame)
                if timestamp is None or (from_ms is not None and timestamp < from_ms) or (to_ms is not None and timestamp > to_ms):
                    continue
                raw_count += 1
                first_time = timestamp if first_time is None else min(first_time, timestamp)
                last_time = timestamp if last_time is None else max(last_time, timestamp)
                if len(small) <= max_points:
                    small.append((raw_count - 1, timestamp, frame))

        if raw_count <= max_points:
            samples = [frame for _, _, frame in small]
        else:
            bucket_count = max(1, (max_points - 2) // 2)
            span = max(1.0, last_time - first_time)
            buckets = {}
            first = last = None
            selected_index = 0
            with open(path, "r", encoding="utf-8") as file_obj:
                for line in file_obj:
                    if not line.strip():
                        continue
                    frame = json.loads(line)
                    timestamp = self._frame_time_ms(frame)
                    if timestamp is None or (from_ms is not None and timestamp < from_ms) or (to_ms is not None and timestamp > to_ms):
                        continue
                    entry = (selected_index, frame)
                    first = first or entry
                    last = entry
                    selected_index += 1
                    voltage = frame.get("voltage")
                    try:
                        voltage = float(voltage)
                    except (TypeError, ValueError):
                        continue
                    bucket = min(bucket_count - 1, int((timestamp - first_time) * bucket_count / span))
                    current = buckets.get(bucket)
                    if current is None:
                        buckets[bucket] = [entry, entry, voltage, voltage]
                    else:
                        if voltage < current[2]:
                            current[0], current[2] = entry, voltage
                        if voltage > current[3]:
                            current[1], current[3] = entry, voltage
            selected = {entry[0]: entry[1] for entry in (first, last) if entry is not None}
            for minimum, maximum, _, _ in buckets.values():
                selected[minimum[0]] = minimum[1]
                selected[maximum[0]] = maximum[1]
            samples = [selected[index] for index in sorted(selected)]

        return {
            "raw_count": raw_count,
            "returned_count": len(samples),
            "from_ms": first_time if from_ms is None else from_ms,
            "to_ms": last_time if to_ms is None else to_ms,
            "covered": len(samples) == raw_count,
            "method": "minmax",
            "samples": samples,
        }

    def update_manual_result(
        self,
        mission_data: dict[str, object],
        sample_id: object,
        payload: Optional[Mapping[str, object]],
    ):
        window = self._find_window(mission_data, sample_id)
        if window is None:
            return None
        window["manual_result"] = normalize_manual_result(payload)
        return window
