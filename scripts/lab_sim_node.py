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


class LabSimulator(object):
    """Pure differential-drive state integrator used by the ROS lab sim node."""

    def __init__(self, config=None):
        self.configure(config or {})

    def configure(self, config):
        self.start_lat = _float_value(config.get("start_lat", 30.0), 30.0)
        self.start_lng = _float_value(config.get("start_lng", 120.0), 120.0)
        self.start_heading_deg = _float_value(config.get("heading_deg", 0.0), 0.0) % 360.0
        self.max_speed_mps = max(0.0, _float_value(config.get("max_speed_mps", 1.0), 1.0))
        self.wheel_base_m = max(0.05, _float_value(config.get("wheel_base_m", 0.6), 0.6))
        self.real_propulsion_enabled = bool(config.get("real_propulsion_enabled", False))
        self.reset()

    def reset(self):
        self.lat = self.start_lat
        self.lng = self.start_lng
        self.heading_deg = self.start_heading_deg
        self.speed_mps = 0.0
        self.left_output = 0.0
        self.right_output = 0.0
        self.running = False
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
        self.left_output = _clamp(_float_value(left, 0.0), -1.0, 1.0)
        self.right_output = _clamp(_float_value(right, 0.0), -1.0, 1.0)

    def step(self, dt):
        dt = max(0.0, _float_value(dt, 0.0))
        linear = self.max_speed_mps * (self.left_output + self.right_output) / 2.0
        yaw_rate = self.max_speed_mps * (self.right_output - self.left_output) / self.wheel_base_m
        if not self.running:
            # Unit tests call step directly after setting propulsion; treat non-zero propulsion as active.
            self.running = abs(self.left_output) > 1e-6 or abs(self.right_output) > 1e-6
        if not self.running:
            linear = 0.0
            yaw_rate = 0.0
        heading_rad = math.radians(self.heading_deg)
        self.heading_deg = (self.heading_deg + math.degrees(yaw_rate * dt)) % 360.0
        self.speed_mps = abs(linear)
        distance = linear * dt
        north_m = distance * math.cos(heading_rad)
        east_m = distance * math.sin(heading_rad)
        self.lat += north_m / 110540.0
        cos_lat = max(0.01, math.cos(math.radians(self.lat)))
        self.lng += east_m / (111320.0 * cos_lat)
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
        return {
            "timestamp": datetime.now().isoformat(),
            "running": bool(self.running),
            "lat": self.lat,
            "lng": self.lng,
            "heading_deg": self.heading_deg,
            "speed_mps": self.speed_mps,
            "track_count": len(self.track),
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
            "start_lat": rospy.get_param("~start_lat", 30.0),
            "start_lng": rospy.get_param("~start_lng", 120.0),
            "heading_deg": rospy.get_param("~heading_deg", 0.0),
            "max_speed_mps": rospy.get_param("~max_speed_mps", 1.0),
            "wheel_base_m": rospy.get_param("~wheel_base_m", 0.6),
            "real_propulsion_enabled": rospy.get_param("~real_propulsion_enabled", False),
        }
        self.sim = LabSimulator(config)
        self._lock = threading.Lock()
        self._last_step = time.time()
        self._status_pub = rospy.Publisher("/usv/lab_sim/status", String, queue_size=10, latch=True)
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
                self.sim.start()
            elif cmd == "stop":
                self.sim.stop()
            elif cmd == "reset":
                self.sim.reset()
            elif cmd == "propulsion":
                self.sim.set_virtual_propulsion(data.get("left", 0.0), data.get("right", 0.0))
                self.sim.start()
        self._publish_status()

    def _publish_status(self):
        self._status_pub.publish(String(json.dumps(self.sim.snapshot(), ensure_ascii=False)))

    def run(self):
        rate = rospy.Rate(float(rospy.get_param("~rate", 5.0)))
        while not rospy.is_shutdown():
            now = time.time()
            with self._lock:
                self.sim.step(now - self._last_step)
                self._last_step = now
                self._publish_status()
            rate.sleep()


if __name__ == "__main__":
    LabSimNode().run()
