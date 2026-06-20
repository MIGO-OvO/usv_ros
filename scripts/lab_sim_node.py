#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Laboratory navigation simulator for semi-hardware USV tests."""

from __future__ import print_function

import json
import threading
import time
from datetime import datetime

from scripts.lib.lab_sim.vessel_model import VesselSimulator as LabSimulator

try:
    import rospy
    from std_msgs.msg import String
    ROS_AVAILABLE = True
except ImportError:
    ROS_AVAILABLE = False

    class String(object):
        def __init__(self, data=""):
            self.data = data


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
        self._completed_status_emitted = False
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
                self._completed_status_emitted = False
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
                self._completed_status_emitted = False
                self.sim.load_mission(data.get("waypoints", []))
                if bool(data.get("start", False)):
                    self.sim.start_mission()
            elif cmd == "stop":
                self.sim.stop()
            elif cmd == "mission_complete":
                self.sim.complete_mission()
            elif cmd == "reset":
                self._completed_status_emitted = False
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
        snapshot = self.sim.snapshot()
        mission = snapshot.get("mission", {})
        if bool(mission.get("completed", False)):
            if self._completed_status_emitted:
                return
            self._completed_status_emitted = True
        self._status_pub.publish(String(json.dumps(snapshot, ensure_ascii=False)))

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
