#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Laboratory navigation simulator for semi-hardware USV tests."""

from __future__ import print_function

import json
import math
import threading
import time
from datetime import datetime

try:
    import rospy
    from std_msgs.msg import String
    ROS_AVAILABLE = True
except ImportError:
    ROS_AVAILABLE = False

    class String(object):
        def __init__(self, data=""):
            self.data = data


def _float_value(value, default=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _clamp(value, low, high):
    return max(low, min(high, value))


def _local_offset_m(lat0, lng0, lat1, lng1):
    """两经纬度点间的 (north_m, east_m) 平面近似偏移。"""
    north = (lat1 - lat0) * 110540.0
    cos_lat = max(0.01, math.cos(math.radians(lat0)))
    east = (lng1 - lng0) * 111320.0 * cos_lat
    return north, east


def _distance_m(lat0, lng0, lat1, lng1):
    north, east = _local_offset_m(lat0, lng0, lat1, lng1)
    return math.hypot(north, east)


def _wrap180(deg):
    """把角度归一化到 [-180, 180)。"""
    return (deg + 180.0) % 360.0 - 180.0


class LabSimulator(object):
    """Pure differential-drive state integrator used by the ROS lab sim node."""

    def __init__(self, config=None):
        self.configure(config or {})

    def configure(self, config):
        self.start_lat = _float_value(config.get("start_lat", 25.314167), 25.314167)
        self.start_lng = _float_value(config.get("start_lng", 110.412778), 110.412778)
        self.start_heading_deg = _float_value(config.get("heading_deg", 0.0), 0.0) % 360.0
        self.max_speed_mps = max(0.0, _float_value(config.get("max_speed_mps", 1.0), 1.0))
        self.wheel_base_m = max(0.05, _float_value(config.get("wheel_base_m", 0.6), 0.6))
        self.real_propulsion_enabled = bool(config.get("real_propulsion_enabled", False))
        # 到点判定半径(米): 船位距航点小于该值视为到达
        self.arrival_radius_m = max(0.5, _float_value(config.get("arrival_radius_m", 3.0), 3.0))
        self.reset()

    def reset(self):
        self.lat = self.start_lat
        self.lng = self.start_lng
        self.heading_deg = self.start_heading_deg
        self.speed_mps = 0.0
        self.left_output = 0.0
        self.right_output = 0.0
        self.running = False
        # 航线制导状态: 航点列表与当前目标索引
        self.waypoints = []
        self.target_idx = 0
        self._arrivals = []
        self.mission_active = False
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
        # 手动推进会接管, 退出航线制导
        self.mission_active = False
        self.left_output = _clamp(_float_value(left, 0.0), -1.0, 1.0)
        self.right_output = _clamp(_float_value(right, 0.0), -1.0, 1.0)

    def _clean_waypoints(self, waypoints):
        cleaned = []
        for i, wp in enumerate(waypoints or []):
            lat = _float_value((wp or {}).get("lat"), None) if isinstance(wp, dict) else None
            lng = _float_value((wp or {}).get("lng"), None) if isinstance(wp, dict) else None
            if lat is None or lng is None:
                continue
            seq = (wp.get("seq") if isinstance(wp, dict) else None)
            cleaned.append({"lat": lat, "lng": lng,
                            "seq": int(seq) if seq is not None else len(cleaned)})
        return cleaned

    def load_mission(self, waypoints):
        cleaned = self._clean_waypoints(waypoints)
        self.waypoints = cleaned
        self.target_idx = 0
        self._arrivals = []
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
        self.track = [self._track_point()]
        self.mission_active = len(self.waypoints) > 0
        self.running = True
        return self.snapshot()

    def set_mission(self, waypoints):
        self.load_mission(waypoints)
        return self.start_mission()

    def drain_arrivals(self):
        """取出并清空待发布的到达事件 (seq 列表)。"""
        pending = list(getattr(self, "_arrivals", []))
        self._arrivals = []
        return pending

    def _steer_toward_target(self):
        """差速制导: 朝当前目标航点转向并前进; 到点则推进索引并记录到达。"""
        if not self.waypoints or self.target_idx >= len(self.waypoints):
            self.mission_active = False
            self.left_output = self.right_output = 0.0
            return
        target = self.waypoints[self.target_idx]
        dist = _distance_m(self.lat, self.lng, target["lat"], target["lng"])
        if dist <= self.arrival_radius_m:
            if not hasattr(self, "_arrivals"):
                self._arrivals = []
            self._arrivals.append({"seq": target["seq"], "lat": self.lat, "lng": self.lng})
            self.target_idx += 1
            if self.target_idx >= len(self.waypoints):
                # 航线完成: 停在终点
                self.mission_active = False
                self.left_output = self.right_output = 0.0
                self.speed_mps = 0.0
                return
            target = self.waypoints[self.target_idx]
        north, east = _local_offset_m(self.lat, self.lng, target["lat"], target["lng"])
        bearing = math.degrees(math.atan2(east, north)) % 360.0
        error = _wrap180(bearing - self.heading_deg)
        turn = _clamp(error / 45.0, -1.0, 1.0)        # 45 度误差即满舵
        forward = _clamp(1.0 - abs(error) / 90.0, 0.15, 1.0)  # 偏差大时降速保留转向
        self.left_output = _clamp(forward - turn, -1.0, 1.0)
        self.right_output = _clamp(forward + turn, -1.0, 1.0)

    def step(self, dt):
        dt = max(0.0, _float_value(dt, 0.0))
        if self.mission_active:
            return self._step_mission(dt)
        linear = self.max_speed_mps * (self.left_output + self.right_output) / 2.0
        yaw_rate = self.max_speed_mps * (self.right_output - self.left_output) / self.wheel_base_m
        if not self.running:
            # Unit tests call step directly after setting propulsion; treat non-zero propulsion as active.
            self.running = abs(self.left_output) > 1e-6 or abs(self.right_output) > 1e-6
        if not self.running:
            linear = 0.0
            yaw_rate = 0.0
        next_heading = (self.heading_deg + math.degrees(yaw_rate * dt)) % 360.0
        heading_rad = math.radians((self.heading_deg + math.degrees(yaw_rate * dt) / 2.0) % 360.0)
        self.heading_deg = next_heading
        self.speed_mps = abs(linear)
        distance = linear * dt
        north_m = distance * math.cos(heading_rad)
        east_m = distance * math.sin(heading_rad)
        self.lat += north_m / 110540.0
        cos_lat = max(0.01, math.cos(math.radians(self.lat)))
        self.lng += east_m / (111320.0 * cos_lat)
        self.track.append(self._track_point())
        return self.snapshot()

    def _step_mission(self, dt):
        if not self.running:
            self.speed_mps = 0.0
            self.left_output = self.right_output = 0.0
            self.track.append(self._track_point())
            return self.snapshot()

        while self.target_idx < len(self.waypoints):
            target = self.waypoints[self.target_idx]
            dist = _distance_m(self.lat, self.lng, target["lat"], target["lng"])
            if dist > self.arrival_radius_m:
                break
            self.lat = target["lat"]
            self.lng = target["lng"]
            self._arrivals.append({"seq": target["seq"], "lat": self.lat, "lng": self.lng})
            self.target_idx += 1

        if self.target_idx >= len(self.waypoints):
            self.mission_active = False
            self.speed_mps = 0.0
            self.left_output = self.right_output = 0.0
            self.track.append(self._track_point())
            return self.snapshot()

        target = self.waypoints[self.target_idx]
        north, east = _local_offset_m(self.lat, self.lng, target["lat"], target["lng"])
        dist = math.hypot(north, east)
        if dist <= 1e-6 or dt <= 0.0 or self.max_speed_mps <= 0.0:
            self.speed_mps = 0.0
            self.left_output = self.right_output = 0.0
            self.track.append(self._track_point())
            return self.snapshot()

        bearing = math.degrees(math.atan2(east, north)) % 360.0
        heading_error = _wrap180(bearing - self.heading_deg)
        turn = _clamp(heading_error / 45.0, -1.0, 1.0)
        self.left_output = _clamp(1.0 - turn, -1.0, 1.0)
        self.right_output = _clamp(1.0 + turn, -1.0, 1.0)
        self.heading_deg = bearing

        distance = min(self.max_speed_mps * dt, dist)
        self.speed_mps = distance / dt
        self.lat += (distance * math.cos(math.radians(bearing))) / 110540.0
        cos_lat = max(0.01, math.cos(math.radians(self.lat)))
        self.lng += (distance * math.sin(math.radians(bearing))) / (111320.0 * cos_lat)
        self.track.append(self._track_point())
        return self.snapshot()

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
            target_seq = self.waypoints[self.target_idx]["seq"]
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
            },
            "virtual_propulsion": {
                "left": self.left_output,
                "right": self.right_output,
                "real_output_enabled": bool(self.real_propulsion_enabled),
            },
        }


class LabSimNode(object):
    def __init__(self):
        if not ROS_AVAILABLE:
            raise RuntimeError("ROS is required for LabSimNode")
        rospy.init_node("lab_sim_node", anonymous=False)
        config = {
            "start_lat": rospy.get_param("~start_lat", 25.314167),
            "start_lng": rospy.get_param("~start_lng", 110.412778),
            "heading_deg": rospy.get_param("~heading_deg", 0.0),
            "max_speed_mps": rospy.get_param("~max_speed_mps", 1.0),
            "wheel_base_m": rospy.get_param("~wheel_base_m", 0.6),
            "arrival_radius_m": rospy.get_param("~arrival_radius_m", 3.0),
            "real_propulsion_enabled": rospy.get_param("~real_propulsion_enabled", False),
        }
        self.sim = LabSimulator(config)
        self._lock = threading.Lock()
        self._last_step = time.time()
        self._status_pub = rospy.Publisher("/usv/lab_sim/status", String, queue_size=10, latch=True)
        # C2: 虚拟航点到达事件 (独立话题, 不污染 /mavros/mission/reached)
        self._reached_pub = rospy.Publisher("/usv/lab_sim/waypoint_reached", String, queue_size=10)
        rospy.Subscriber("/usv/lab_sim/command", String, self._command_cb, queue_size=10)

    def _command_cb(self, msg):
        try:
            data = json.loads(msg.data)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        cmd = str(data.get("cmd", "")).strip().lower()
        with self._lock:
            if cmd == "config":
                cfg = data.get("config", {})
                sim_cfg = cfg.get("sim", cfg) if isinstance(cfg, dict) else {}
                sim_cfg = dict(sim_cfg or {})
                sim_cfg["real_propulsion_enabled"] = bool((cfg or {}).get("real_propulsion_enabled", False))
                self.sim.configure(sim_cfg)
            elif cmd == "start":
                cfg = data.get("config", {})
                if isinstance(cfg, dict) and isinstance(cfg.get("sim"), dict):
                    sim_cfg = dict(cfg["sim"])
                    sim_cfg["real_propulsion_enabled"] = bool(cfg.get("real_propulsion_enabled", False))
                    self.sim.configure(sim_cfg)
                elif bool(data.get("reset_to_start", False)):
                    self.sim.reset()
                waypoints = data.get("waypoints")
                if isinstance(waypoints, list) and waypoints:
                    self.sim.start_mission(waypoints)
                else:
                    self.sim.start()
            elif cmd == "mission":
                self.sim.load_mission(data.get("waypoints", []))
                if bool(data.get("start", False)):
                    self.sim.start_mission()
            elif cmd == "stop":
                self.sim.stop()
            elif cmd == "reset":
                self.sim.reset()
            elif cmd == "propulsion":
                self.sim.set_virtual_propulsion(data.get("left", 0.0), data.get("right", 0.0))
                self.sim.start()
            arrivals = self.sim.drain_arrivals()
        self._publish_arrivals(arrivals)
        self._publish_status()

    def _publish_arrivals(self, arrivals):
        for item in arrivals or []:
            payload = {
                "seq": int(item.get("seq", 0)),
                "lat": item.get("lat"),
                "lng": item.get("lng"),
                "source": "lab_sim",
                "timestamp": datetime.now().isoformat(),
            }
            self._reached_pub.publish(String(json.dumps(payload, ensure_ascii=False)))

    def _publish_status(self):
        self._status_pub.publish(String(json.dumps(self.sim.snapshot(), ensure_ascii=False)))

    def run(self):
        rate = rospy.Rate(float(rospy.get_param("~rate", 5.0)))
        while not rospy.is_shutdown():
            now = time.time()
            with self._lock:
                self.sim.step(now - self._last_step)
                self._last_step = now
                arrivals = self.sim.drain_arrivals()
                self._publish_status()
            self._publish_arrivals(arrivals)
            rate.sleep()


if __name__ == "__main__":
    LabSimNode().run()
