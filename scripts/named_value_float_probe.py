#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pyright: reportMissingImports=false
"""
NAMED_VALUE_FLOAT probe for real-hardware debugging.

Purpose:
  Distinguish whether short-name NAMED_VALUE_FLOAT frames are being dropped
  because of the current bridge-style encoding.

What it does:
  - Publishes raw MAVLink to /mavros/mavlink/to
  - Sends companion HEARTBEAT with sysid=2 compid=191
  - Runs a few phases with different NAMED_VALUE_FLOAT encodings

Recommended true-hardware usage:
  1. Start MAVROS and connect the Jetson to the FCU as usual
  2. Open QGC Analyze > MAVLink Inspector
  3. Run:
       rosrun usv_ros named_value_float_probe.py
  4. Watch whether msgid=251 appears during each phase

Interpretation:
  - bridge_short missing + bridge_10char visible + trimmed_short visible
      => root cause strongly points to short-name bridge encoding
  - all phases visible
      => root cause is likely further downstream than encoding
  - no phases visible
      => root cause is likely before QGC consumption, e.g. MAVROS/FCU/routing
"""

from __future__ import print_function

import argparse
import struct
import time

import rospy
from mavros_msgs.msg import Mavlink, State

TELEMETRY_RATE_HZ = 2.0
HEARTBEAT_RATE_HZ = 1.0

MAVLINK_MSG_ID_HEARTBEAT = 0
MAVLINK_MSG_ID_NAMED_VALUE_FLOAT = 251

MAV_TYPE_ONBOARD_CONTROLLER = 18
MAV_AUTOPILOT_INVALID = 8
MAV_STATE_ACTIVE = 4


class NamedValueFloatProbe(object):

    def __init__(self, args):
        rospy.init_node("named_value_float_probe", anonymous=False)

        self._sys_id = int(args.sysid)
        self._comp_id = int(args.compid)
        self._rate_hz = float(args.rate_hz)
        self._phase_seconds = float(args.phase_seconds)
        self._wait_timeout = float(args.wait_timeout)
        self._phases = self._build_phases(args.phases)

        self._seq = 0
        self._boot_time = time.time()
        self._last_heartbeat = 0.0
        self._mavros_connected = False

        self._pub = rospy.Publisher("/mavros/mavlink/to", Mavlink, queue_size=20)
        self._state_sub = rospy.Subscriber("/mavros/state", State, self._state_cb, queue_size=5)

        rospy.loginfo("NAMED_VALUE_FLOAT probe initialized")
        rospy.loginfo("  source ids: sysid=%d compid=%d", self._sys_id, self._comp_id)
        rospy.loginfo("  rate: %.2f Hz, phase_seconds: %.1f", self._rate_hz, self._phase_seconds)
        rospy.loginfo("  phases: %s", ", ".join([phase["id"] for phase in self._phases]))

    def _state_cb(self, msg):
        self._mavros_connected = bool(msg.connected)

    def _build_phases(self, requested):
        phase_map = {
            # Mirrors current bridge behavior for an actual panel field name.
            "bridge_short": {
                "id": "bridge_short",
                "label": "bridge-style short name",
                "encoding": "bridge",
                "name": "USV_VOLT",
                "value": 11.11,
                "notes": "Current bridge-style zero-padded short name; expected payload len 18",
            },
            # Control case: exactly 10 chars, same bridge encoding path.
            "bridge_10char": {
                "id": "bridge_10char",
                "label": "bridge-style 10-char name",
                "encoding": "bridge",
                "name": "ABCDEFGHIJ",
                "value": 22.22,
                "notes": "Control case; expected payload len 18",
            },
            # Same panel field, but with trailing zeros trimmed from the payload.
            "trimmed_short": {
                "id": "trimmed_short",
                "label": "trimmed short name",
                "encoding": "trimmed",
                "name": "USV_VOLT",
                "value": 33.33,
                "notes": "Short name without trailing NUL padding; expected payload len 16",
            },
        }

        selected = []
        for phase_id in requested:
            if phase_id not in phase_map:
                raise ValueError("unknown phase: %s" % phase_id)
            selected.append(phase_map[phase_id])
        return selected

    def _next_seq(self):
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFF
        return seq

    def _time_boot_ms(self):
        return int((time.time() - self._boot_time) * 1000) & 0xFFFFFFFF

    def _payload_to_uint64(self, payload):
        remainder = len(payload) % 8
        if remainder:
            payload += b"\x00" * (8 - remainder)
        out = []
        for i in range(0, len(payload), 8):
            out.append(struct.unpack_from("<Q", payload, i)[0])
        return out

    def _publish_raw(self, msgid, payload):
        msg = Mavlink()
        msg.header.stamp = rospy.Time.now()
        msg.framing_status = 1  # MAVLINK_FRAMING_OK
        msg.magic = 253         # MAVLink v2
        msg.len = len(payload)
        msg.incompat_flags = 0
        msg.compat_flags = 0
        msg.seq = self._next_seq()
        msg.sysid = self._sys_id
        msg.compid = self._comp_id
        msg.msgid = msgid
        msg.checksum = 0
        msg.payload64 = self._payload_to_uint64(payload)
        self._pub.publish(msg)

    def _send_heartbeat(self):
        payload = struct.pack(
            "<IBBBBB",
            0,                           # custom_mode
            MAV_TYPE_ONBOARD_CONTROLLER,
            MAV_AUTOPILOT_INVALID,
            0,                           # base_mode
            MAV_STATE_ACTIVE,
            3,                           # mavlink_version
        )
        self._publish_raw(MAVLINK_MSG_ID_HEARTBEAT, payload)

    def _build_named_value_payload(self, name, value, encoding):
        base = struct.pack("<If", self._time_boot_ms(), float(value))
        if encoding == "bridge":
            name_bytes = name.encode("ascii")[:10].ljust(10, b"\x00")
        elif encoding == "trimmed":
            name_bytes = name.encode("ascii")[:10]
        else:
            raise ValueError("unknown encoding: %s" % encoding)
        return base + name_bytes

    def _send_named_value_float(self, name, value, encoding):
        payload = self._build_named_value_payload(name, value, encoding)
        self._publish_raw(MAVLINK_MSG_ID_NAMED_VALUE_FLOAT, payload)
        return len(payload), payload.hex()

    def _wait_for_mavros(self):
        if self._mavros_connected:
            return True

        rospy.loginfo("Waiting for /mavros/state connected=true (timeout %.1fs)...", self._wait_timeout)
        deadline = time.time() + self._wait_timeout
        rate = rospy.Rate(5)
        while not rospy.is_shutdown() and time.time() < deadline:
            if self._mavros_connected:
                rospy.loginfo("MAVROS connected")
                return True
            rate.sleep()

        rospy.logwarn("MAVROS did not report connected=true before timeout")
        return False

    def run(self):
        self._wait_for_mavros()

        rate = rospy.Rate(self._rate_hz)
        for index, phase in enumerate(self._phases, 1):
            rospy.loginfo("=" * 72)
            rospy.loginfo("Phase %d/%d: %s", index, len(self._phases), phase["id"])
            rospy.loginfo("  label: %s", phase["label"])
            rospy.loginfo("  notes: %s", phase["notes"])
            rospy.loginfo("  name=%s value=%.2f encoding=%s",
                          phase["name"], phase["value"], phase["encoding"])
            rospy.loginfo("  QGC expectation: watch MAVLink Inspector for msgid=251 during this phase")
            rospy.loginfo("=" * 72)

            end_time = time.time() + self._phase_seconds
            while not rospy.is_shutdown() and time.time() < end_time:
                now = time.time()
                if now - self._last_heartbeat >= (1.0 / HEARTBEAT_RATE_HZ):
                    self._send_heartbeat()
                    self._last_heartbeat = now

                payload_len, payload_hex = self._send_named_value_float(
                    phase["name"], phase["value"], phase["encoding"]
                )
                rospy.loginfo_throttle(
                    2.0,
                    "phase=%s payload_len=%d payload_hex=%s",
                    phase["id"], payload_len, payload_hex
                )
                rate.sleep()

        rospy.loginfo("Probe finished")
        rospy.loginfo("Please report which phases produced msgid=251 in QGC Inspector:")
        for phase in self._phases:
            rospy.loginfo("  - %s", phase["id"])


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Phase-based NAMED_VALUE_FLOAT probe for MAVROS/FCU/QGC telemetry debugging."
    )
    parser.add_argument(
        "--phases",
        default="bridge_short,bridge_10char,trimmed_short",
        help="Comma-separated phase ids. Default: bridge_short,bridge_10char,trimmed_short",
    )
    parser.add_argument("--phase-seconds", type=float, default=10.0, help="Seconds per phase")
    parser.add_argument("--rate-hz", type=float, default=TELEMETRY_RATE_HZ, help="NAMED_VALUE_FLOAT send rate")
    parser.add_argument("--wait-timeout", type=float, default=15.0, help="Wait timeout for /mavros/state")
    parser.add_argument("--sysid", type=int, default=2, help="Companion source sysid")
    parser.add_argument("--compid", type=int, default=191, help="Companion source compid")

    args = parser.parse_args(rospy.myargv()[1:])
    args.phases = [item.strip() for item in args.phases.split(",") if item.strip()]
    return args


def main():
    try:
        args = _parse_args()
        probe = NamedValueFloatProbe(args)
        probe.run()
    except rospy.ROSInterruptException:
        pass
    except Exception as exc:
        rospy.logerr("named_value_float_probe failed: %s", str(exc))


if __name__ == "__main__":
    main()
