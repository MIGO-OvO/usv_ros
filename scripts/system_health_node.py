#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publish Jetson, detector, and ROS node health as one JSON topic."""

from __future__ import print_function

import json
import os
import time
from datetime import datetime, timezone

import rospy
from std_msgs.msg import String

try:
    import rosnode
except Exception:
    rosnode = None


EXPECTED_NODES = [
    "/pump_control_node",
    "/web_config_server",
    "/mavlink_trigger_node",
    "/usv_mavlink_bridge",
    "/mavros",
]


class SystemHealthCollector(object):
    def __init__(self, expected_nodes=None, stale_after_s=3.0):
        self.expected_nodes = list(expected_nodes or EXPECTED_NODES)
        self.stale_after_s = float(stale_after_s)
        self._last_cpu_sample = None
        self._detector_health = {}
        self._detector_received_at = 0.0

    def _now(self):
        return time.time()

    def detector_health_cb(self, msg):
        try:
            data = json.loads(msg.data)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        self._detector_health = data
        self._detector_received_at = self._now()

    def read_cpu_percent(self):
        sample = self._read_proc_stat()
        if sample is None:
            return None
        if self._last_cpu_sample is None:
            self._last_cpu_sample = sample
            return 0.0
        prev_total, prev_idle = self._last_cpu_sample
        total, idle = sample
        self._last_cpu_sample = sample
        total_delta = total - prev_total
        idle_delta = idle - prev_idle
        if total_delta <= 0:
            return 0.0
        return round(max(0.0, min(100.0, (1.0 - float(idle_delta) / float(total_delta)) * 100.0)), 1)

    def _read_proc_stat(self):
        try:
            with open("/proc/stat", "r") as fh:
                fields = fh.readline().split()
        except Exception:
            return None
        if len(fields) < 5 or fields[0] != "cpu":
            return None
        values = [int(v) for v in fields[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle

    def read_memory(self):
        mem = {}
        try:
            with open("/proc/meminfo", "r") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 2:
                        mem[parts[0].rstrip(":")] = float(parts[1])
        except Exception:
            return {
                "memory_total_mb": None,
                "memory_available_mb": None,
                "memory_used_mb": None,
                "memory_percent": None,
            }
        total_kb = mem.get("MemTotal", 0.0)
        available_kb = mem.get("MemAvailable", mem.get("MemFree", 0.0))
        used_kb = max(0.0, total_kb - available_kb)
        return {
            "memory_total_mb": round(total_kb / 1024.0, 1) if total_kb else None,
            "memory_available_mb": round(available_kb / 1024.0, 1) if total_kb else None,
            "memory_used_mb": round(used_kb / 1024.0, 1) if total_kb else None,
            "memory_percent": round((used_kb / total_kb) * 100.0, 1) if total_kb else None,
        }

    def read_temperature_c(self):
        values = []
        base = "/sys/class/thermal"
        try:
            names = os.listdir(base)
        except Exception:
            return None
        for name in names:
            if not name.startswith("thermal_zone"):
                continue
            path = os.path.join(base, name, "temp")
            try:
                with open(path, "r") as fh:
                    raw = float(fh.read().strip())
            except Exception:
                continue
            if raw > 1000:
                raw = raw / 1000.0
            if -40.0 <= raw <= 125.0:
                values.append(raw)
        return round(max(values), 1) if values else None

    def read_uptime_s(self):
        try:
            with open("/proc/uptime", "r") as fh:
                return round(float(fh.readline().split()[0]), 1)
        except Exception:
            return None

    def read_ros_nodes(self):
        if rosnode is None:
            return [{"name": name, "alive": False} for name in self.expected_nodes]
        try:
            node_names = set(rosnode.get_node_names())
        except Exception:
            node_names = set()
        return [{"name": name, "alive": name in node_names} for name in self.expected_nodes]

    def collect(self):
        jetson = self._collect_jetson()
        detector = self._collect_detector()
        nodes = self.read_ros_nodes()
        health = self._evaluate_health(jetson, detector, nodes)
        return {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "jetson": jetson,
            "detector": detector,
            "ros_nodes": nodes,
            "health": health,
        }

    def _collect_jetson(self):
        memory = self.read_memory()
        jetson = {
            "cpu_percent": self.read_cpu_percent(),
            "temperature_c": self.read_temperature_c(),
            "uptime_s": self.read_uptime_s(),
        }
        jetson.update(memory)
        return jetson

    def _collect_detector(self):
        data = dict(self._detector_health)
        online = bool(data) and (self._now() - self._detector_received_at) <= self.stale_after_s
        heap_free = data.get("heap_free")
        heap_total = data.get("heap_total")
        if data.get("heap_percent_free") is None and heap_total:
            try:
                data["heap_percent_free"] = round((float(heap_free) / float(heap_total)) * 100.0, 1)
            except Exception:
                data["heap_percent_free"] = None
        data["online"] = online
        data["age_s"] = round(self._now() - self._detector_received_at, 1) if self._detector_received_at else None
        return data

    def _evaluate_health(self, jetson, detector, nodes):
        code = 0
        reasons = []
        if not detector.get("online", False):
            code = max(code, 1)
            reasons.append("detector offline")
        missing_nodes = [n["name"] for n in nodes if not n.get("alive")]
        if missing_nodes:
            code = max(code, 1)
            reasons.append("missing ros nodes: " + ",".join(missing_nodes))

        for key, warn, err, label in (
            ("temperature_c", 75.0, 85.0, "jetson temp"),
            ("cpu_percent", 85.0, 95.0, "jetson cpu"),
            ("memory_percent", 85.0, 95.0, "jetson memory"),
        ):
            value = jetson.get(key)
            if value is None:
                continue
            if value >= err:
                code = max(code, 2)
                reasons.append("%s high" % label)
            elif value >= warn:
                code = max(code, 1)
                reasons.append("%s warm" % label)

        detector_temp = detector.get("temperature_c")
        if detector_temp is not None:
            if detector_temp >= 85.0:
                code = max(code, 2)
                reasons.append("detector temp high")
            elif detector_temp >= 75.0:
                code = max(code, 1)
                reasons.append("detector temp warm")
        detector_heap = detector.get("heap_percent_free")
        if detector_heap is not None and detector_heap < 10.0:
            code = max(code, 2)
            reasons.append("detector heap low")
        elif detector_heap is not None and detector_heap < 20.0:
            code = max(code, 1)
            reasons.append("detector heap warn")

        return {
            "code": code,
            "level": "error" if code >= 2 else ("warn" if code == 1 else "ok"),
            "summary": "; ".join(reasons) if reasons else "ok",
        }


class SystemHealthNode(object):
    def __init__(self):
        rospy.init_node("system_health_node", anonymous=False)
        self.rate_hz = float(rospy.get_param("~rate_hz", 1.0))
        self.collector = SystemHealthCollector(
            expected_nodes=rospy.get_param("~expected_nodes", EXPECTED_NODES),
            stale_after_s=float(rospy.get_param("~detector_stale_after_s", 3.0)),
        )
        self.pub = rospy.Publisher("/usv/system_health", String, queue_size=5)
        rospy.Subscriber("/usv/detector_health", String, self.collector.detector_health_cb, queue_size=5)

    def run(self):
        rate = rospy.Rate(self.rate_hz)
        while not rospy.is_shutdown():
            msg = String()
            msg.data = json.dumps(self.collector.collect(), ensure_ascii=False)
            self.pub.publish(msg)
            rate.sleep()


def main():
    try:
        SystemHealthNode().run()
    except rospy.ROSInterruptException:
        pass


if __name__ == "__main__":
    main()
