from __future__ import annotations

import math
from datetime import datetime
from typing import Final, NamedTuple, Optional

from scripts.lib.lab_sim.coordinates import (
    Coordinate,
    LocalEnu,
    haversine_m,
    local_enu_to_wgs84,
    wgs84_to_local_enu,
)
from scripts.lib.lab_sim.survey_window import SurveyWindowConfig, SurveyWindowState

DEFAULT_START_LAT: Final = 25.314167
DEFAULT_START_LNG: Final = 110.412778
MIN_WHEEL_BASE_M: Final = 0.05
MIN_ARRIVAL_RADIUS_M: Final = 0.5
ZERO_OUTPUT_EPSILON: Final = 1e-6


class Waypoint(NamedTuple):
    seq: int
    coordinate: Coordinate


def _float_value(value, default=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _clamp(value, low, high):
    return max(low, min(high, value))


def _wrap180(deg):
    return (deg + 180.0) % 360.0 - 180.0


def _has_schema_coordinate(raw):
    return isinstance(raw, dict) and (
        raw.get("coordinate_schema_version") == 2
        or "wgs84" in raw
        or "gcj02" in raw
    )


def _schema_wgs84_coordinate(raw):
    if not _has_schema_coordinate(raw):
        return None
    wgs84 = raw.get("wgs84") if isinstance(raw, dict) else None
    if not isinstance(wgs84, dict):
        return None
    lat = _float_value(wgs84.get("lat"), None)
    lng = _float_value(wgs84.get("lng"), None)
    if lat is None or lng is None:
        return None
    alt = _float_value(wgs84.get("alt"), None)
    return Coordinate(lat, lng, alt)


class VesselSimulator(object):
    def __init__(self, config=None):
        self.configure(config or {})

    def configure(self, config):
        start = _schema_wgs84_coordinate(config.get("start"))
        if start is None and not _has_schema_coordinate(config.get("start")):
            start = Coordinate(
                _float_value(config.get("start_lat", DEFAULT_START_LAT), DEFAULT_START_LAT),
                _float_value(config.get("start_lng", DEFAULT_START_LNG), DEFAULT_START_LNG),
            )
        if start is None:
            start = Coordinate(DEFAULT_START_LAT, DEFAULT_START_LNG)
        self.start_lat = start.lat
        self.start_lng = start.lng
        self.start_heading_deg = _float_value(config.get("heading_deg", 0.0), 0.0) % 360.0
        self.max_speed_mps = max(0.0, _float_value(config.get("max_speed_mps", 1.0), 1.0))
        self.wheel_base_m = max(MIN_WHEEL_BASE_M, _float_value(config.get("wheel_base_m", 0.6), 0.6))
        self.real_propulsion_enabled = bool(config.get("real_propulsion_enabled", False))
        self.arrival_radius_m = max(
            MIN_ARRIVAL_RADIUS_M,
            _float_value(config.get("arrival_radius_m", 3.0), 3.0),
        )
        self.configure_survey_window(SurveyWindowConfig())
        self.reset()

    def configure_survey_window(self, config):
        self.survey_window = SurveyWindowState.from_config(config)

    def reset(self):
        self.lat = self.start_lat
        self.lng = self.start_lng
        self.heading_deg = self.start_heading_deg
        self.speed_mps = 0.0
        self.left_output = 0.0
        self.right_output = 0.0
        self.running = False
        self.waypoints = []
        self.target_idx = 0
        self._arrivals = []
        self._mission_completed = False
        self._waiting_sampling_done = False
        self.mission_active = False
        self.survey_window = self.survey_window.reset()
        self.track = [self._track_point()]
        return self.snapshot()

    def start(self):
        self.running = True
        return self.snapshot()

    def stop(self):
        self.running = False
        self.set_virtual_propulsion(0.0, 0.0)
        return self.snapshot()

    def set_virtual_propulsion(self, left, right):
        self.mission_active = False
        self.left_output = _clamp(_float_value(left, 0.0), -1.0, 1.0)
        self.right_output = _clamp(_float_value(right, 0.0), -1.0, 1.0)

    def load_mission(self, waypoints):
        self.waypoints = self._clean_waypoints(waypoints)
        self.target_idx = 0
        self._arrivals = []
        self._mission_completed = False
        self._waiting_sampling_done = False
        self.mission_active = False
        self.left_output = self.right_output = 0.0
        self.speed_mps = 0.0
        return self.snapshot()

    def start_mission(self, waypoints=None):
        if waypoints is not None:
            self.load_mission(waypoints)
        self.lat = self.start_lat
        self.lng = self.start_lng
        self.heading_deg = self.start_heading_deg
        self.speed_mps = 0.0
        self.left_output = self.right_output = 0.0
        self.target_idx = 0
        self._arrivals = []
        self._mission_completed = False
        self._waiting_sampling_done = False
        self.track = [self._track_point()]
        self.mission_active = len(self.waypoints) > 0
        self.running = True
        return self.snapshot()

    def complete_mission(self):
        self._waiting_sampling_done = False
        if self.waypoints and self.target_idx < len(self.waypoints):
            self.mission_active = True
            self.running = True
            return self.snapshot()
        self._mission_completed = bool(self.waypoints and self.target_idx >= len(self.waypoints))
        self.mission_active = False
        if self._mission_completed:
            self.running = False
        self.speed_mps = 0.0
        self.left_output = self.right_output = 0.0
        return self.snapshot()

    def set_mission(self, waypoints):
        self.load_mission(waypoints)
        return self.start_mission()

    def drain_arrivals(self):
        pending = list(getattr(self, "_arrivals", []))
        self._arrivals = []
        return pending

    def step(self, dt):
        elapsed_s = max(0.0, _float_value(dt, 0.0))
        self.survey_window = self.survey_window.clear_trigger()
        if self.mission_active:
            return self._step_mission(elapsed_s)
        if not self.running:
            self.running = abs(self.left_output) > ZERO_OUTPUT_EPSILON or abs(self.right_output) > ZERO_OUTPUT_EPSILON
        linear = self.max_speed_mps * (self.left_output + self.right_output) / 2.0 if self.running else 0.0
        yaw_rate = self.max_speed_mps * (self.right_output - self.left_output) / self.wheel_base_m if self.running else 0.0
        next_heading = (self.heading_deg + math.degrees(yaw_rate * elapsed_s)) % 360.0
        heading_rad = math.radians((self.heading_deg + math.degrees(yaw_rate * elapsed_s) / 2.0) % 360.0)
        self.heading_deg = next_heading
        self.speed_mps = abs(linear)
        distance = linear * elapsed_s
        self._move_enu(distance * math.sin(heading_rad), distance * math.cos(heading_rad))
        self._accumulate_survey(abs(distance), elapsed_s if self.running else 0.0)
        self.track.append(self._track_point())
        return self.snapshot()

    def _step_mission(self, dt):
        if not self.running or self._waiting_sampling_done:
            self.speed_mps = 0.0
            self.left_output = self.right_output = 0.0
            self.track.append(self._track_point())
            return self.snapshot()
        while self.target_idx < len(self.waypoints):
            target = self.waypoints[self.target_idx]
            if haversine_m(self._coordinate(), target.coordinate) > self.arrival_radius_m:
                break
            self.lat = target.coordinate.lat
            self.lng = target.coordinate.lng
            self._arrivals.append({"seq": target.seq, "lat": self.lat, "lng": self.lng})
            self.target_idx += 1
            self.mission_active = self.target_idx < len(self.waypoints)
            self._waiting_sampling_done = True
            self.speed_mps = 0.0
            self.left_output = self.right_output = 0.0
            self.track.append(self._track_point())
            return self.snapshot()
        if self.target_idx >= len(self.waypoints):
            self.mission_active = False
            self.speed_mps = 0.0
            self.left_output = self.right_output = 0.0
            self.track.append(self._track_point())
            return self.snapshot()
        return self._advance_toward_waypoint(dt, self.waypoints[self.target_idx])

    def _advance_toward_waypoint(self, dt, target):
        offset = wgs84_to_local_enu(target.coordinate, self._coordinate())
        dist = math.hypot(offset.north_m, offset.east_m)
        if dist <= ZERO_OUTPUT_EPSILON or dt <= 0.0 or self.max_speed_mps <= 0.0:
            self.speed_mps = 0.0
            self.left_output = self.right_output = 0.0
            self.track.append(self._track_point())
            return self.snapshot()
        bearing = math.degrees(math.atan2(offset.east_m, offset.north_m)) % 360.0
        heading_error = _wrap180(bearing - self.heading_deg)
        turn = _clamp(heading_error / 45.0, -1.0, 1.0)
        self.left_output = _clamp(1.0 - turn, -1.0, 1.0)
        self.right_output = _clamp(1.0 + turn, -1.0, 1.0)
        self.heading_deg = bearing
        distance = min(self.max_speed_mps * dt, dist)
        self.speed_mps = distance / dt
        self._move_enu(distance * math.sin(math.radians(bearing)), distance * math.cos(math.radians(bearing)))
        self._accumulate_survey(distance, dt)
        self.track.append(self._track_point())
        return self.snapshot()

    def _accumulate_survey(self, distance_m, time_s):
        self.survey_window = self.survey_window.accumulate(distance_m, time_s)

    def _clean_waypoints(self, waypoints):
        cleaned = []
        for wp in waypoints or []:
            coordinate = _schema_wgs84_coordinate(wp)
            if coordinate is None and isinstance(wp, dict) and not _has_schema_coordinate(wp):
                lat = _float_value(wp.get("lat"), None)
                lng = _float_value(wp.get("lng"), None)
                if lat is not None and lng is not None:
                    coordinate = Coordinate(lat, lng)
            if coordinate is None:
                continue
            seq = wp.get("seq") if isinstance(wp, dict) else None
            cleaned.append(Waypoint(int(seq) if seq is not None else len(cleaned), coordinate))
        return cleaned

    def _move_enu(self, east_m, north_m):
        moved = local_enu_to_wgs84(LocalEnu(east_m, north_m), self._coordinate())
        self.lat = moved.lat
        self.lng = moved.lng

    def _coordinate(self):
        return Coordinate(self.lat, self.lng)

    def _track_point(self):
        return {
            "timestamp": datetime.now().isoformat(),
            "lat": self.lat,
            "lng": self.lng,
            "heading_deg": self.heading_deg,
            "speed_mps": self.speed_mps,
        }

    def snapshot(self):
        target_seq = None
        if self.mission_active and self.target_idx < len(self.waypoints):
            target_seq = self.waypoints[self.target_idx].seq
        return {
            "timestamp": datetime.now().isoformat(),
            "running": bool(self.running),
            "lat": self.lat,
            "lng": self.lng,
            "heading_deg": self.heading_deg,
            "speed_mps": self.speed_mps,
            "track_count": len(self.track),
            "mission": {
                "active": bool(self.mission_active),
                "total": len(self.waypoints),
                "target_seq": target_seq,
                "reached_count": self.target_idx,
                "completed": bool(self._mission_completed),
                "waiting_sampling_done": bool(self._waiting_sampling_done),
            },
            "survey": self.survey_window.snapshot(),
            "virtual_propulsion": {
                "left": self.left_output,
                "right": self.right_output,
                "real_output_enabled": bool(self.real_propulsion_enabled),
            },
        }
