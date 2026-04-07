#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pyright: reportMissingImports=false
"""
NAMED_VALUE_FLOAT probe for real-hardware debugging.

Purpose:
  Distinguish whether the current /mavros/mavlink/to publication path is
  failing because messages are not MAVLink-finalized before transmission.

What it does:
  - Publishes MAVLink to /mavros/mavlink/to
  - Sends companion HEARTBEAT with sysid=1 compid=240
  - Compares today's raw publication path against a finalized pymavlink path

Recommended true-hardware usage:
  1. Start MAVROS and connect the Jetson to the FCU as usual
  2. Open QGC Analyze > MAVLink Inspector
  3. Run:
       rosrun usv_ros named_value_float_probe.py
  4. Watch whether source 1/240 and msgid=251 appear during each phase

Interpretation:
  - bridge_short missing + finalized_short visible
      => root cause strongly points to missing MAVLink finalization/checksum
  - bridge_short missing + bridge_10char visible + trimmed_short visible
      => root cause strongly points to short-name bridge encoding
  - all phases visible
      => root cause is likely further downstream than encoding/finalization
  - no phases visible
      => root cause is likely before QGC consumption, e.g. MAVROS/FCU/routing
"""

from __future__ import print_function

import argparse
import struct
import time

import rospy
from mavros_msgs.msg import Mavlink, State

try:
    from pymavlink.dialects.v20 import common as mavlink2
except ImportError:
    mavlink2 = None

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
        self._run_forever = bool(args.run_forever)
        self._wait_timeout = float(args.wait_timeout)

        self._seq = 0
        self._boot_time = time.time()
        self._last_heartbeat = 0.0
        self._mavros_connected = False
        self._pymav = None
        if mavlink2 is not None:
            self._pymav = mavlink2.MAVLink(None, srcSystem=self._sys_id, srcComponent=self._comp_id)

        self._phases = self._build_phases(args.phases)

        self._pub = rospy.Publisher("/mavros/mavlink/to", Mavlink, queue_size=20)
        self._state_sub = rospy.Subscriber("/mavros/state", State, self._state_cb, queue_size=5)

        rospy.loginfo("NAMED_VALUE_FLOAT probe initialized")
        rospy.loginfo("  source ids: sysid=%d compid=%d", self._sys_id, self._comp_id)
        if self._run_forever:
            rospy.loginfo("  rate: %.2f Hz, phase_seconds: infinite(loop)", self._rate_hz)
        else:
            rospy.loginfo("  rate: %.2f Hz, phase_seconds: %.1f", self._rate_hz, self._phase_seconds)
        rospy.loginfo("  phases: %s", ", ".join([phase["id"] for phase in self._phases]))

    def _state_cb(self, msg):
        self._mavros_connected = bool(msg.connected)

    def _build_phases(self, requested):
        phase_map = {
            "bridge_short": {
                "id": "bridge_short",
                "label": "bridge-style short name",
                "tx_mode": "raw",
                "encoding": "bridge",
                "name": "USV_VOLT",
                "value": 11.11,
                "notes": "Current bridge-style zero-padded short name; expected payload len 18",
            },
            "bridge_10char": {
                "id": "bridge_10char",
                "label": "bridge-style 10-char name",
                "tx_mode": "raw",
                "encoding": "bridge",
                "name": "ABCDEFGHIJ",
                "value": 22.22,
                "notes": "Control case; expected payload len 18",
            },
            "trimmed_short": {
                "id": "trimmed_short",
                "label": "trimmed short name",
                "tx_mode": "raw",
                "encoding": "trimmed",
                "name": "USV_VOLT",
                "value": 33.33,
                "notes": "Short name without trailing NUL padding; expected payload len 16",
            },
            "finalized_short": {
                "id": "finalized_short",
                "label": "pymavlink finalized short name",
                "tx_mode": "finalized",
                "encoding": "pymavlink",
                "name": "USV_VOLT",
                "value": 44.44,
                "notes": "Valid MAVLink2 packet with computed checksum; expected packet len 28",
            },
        }

        selected = []
        for phase_id in requested:
            if phase_id not in phase_map:
                raise ValueError("unknown phase: %s" % phase_id)
            if phase_map[phase_id]["tx_mode"] == "finalized" and self._pymav is None:
                raise RuntimeError("phase %s requires pymavlink on the target ROS system" % phase_id)
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
        msg.framing_status = 1
        msg.magic = 253
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

    def _publish_packet(self, packet):
        if len(packet) < 12:
            raise ValueError("finalized packet too short")

        payload_len = packet[1]
        payload_start = 10
        payload_end = payload_start + payload_len

        msg = Mavlink()
        msg.header.stamp = rospy.Time.now()
        msg.framing_status = 1
        msg.magic = packet[0]
        msg.len = payload_len
        msg.incompat_flags = packet[2]
        msg.compat_flags = packet[3]
        msg.seq = packet[4]
        msg.sysid = packet[5]
        msg.compid = packet[6]
        msg.msgid = packet[7] | (packet[8] << 8) | (packet[9] << 16)
        msg.checksum = struct.unpack_from("<H", packet, payload_end)[0]
        msg.payload64 = self._payload_to_uint64(packet[payload_start:payload_end])
        self._pub.publish(msg)

    def _send_heartbeat(self):
        payload = struct.pack(
            "<IBBBBB",
            0,
            MAV_TYPE_ONBOARD_CONTROLLER,
            MAV_AUTOPILOT_INVALID,
            0,
            MAV_STATE_ACTIVE,
            3,
        )
        self._publish_raw(MAVLINK_MSG_ID_HEARTBEAT, payload)
        return len(payload), payload.hex()

    def _send_finalized_heartbeat(self):
        msg = self._pymav.heartbeat_encode(
            MAV_TYPE_ONBOARD_CONTROLLER,
            MAV_AUTOPILOT_INVALID,
            0,
            0,
            MAV_STATE_ACTIVE,
        )
        packet = msg.pack(self._pymav)
        self._publish_packet(packet)
        return len(packet), packet.hex()

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

    def _send_finalized_named_value_float(self, name, value):
        msg = self._pymav.named_value_float_encode(
            self._time_boot_ms(),
            name.encode("ascii")[:10],
            float(value),
        )
        packet = msg.pack(self._pymav)
        self._publish_packet(packet)
        return len(packet), packet.hex()

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
        cycle = 0
        while not rospy.is_shutdown():
            cycle += 1
            for index, phase in enumerate(self._phases, 1):
                rospy.loginfo("=" * 72)
                if self._run_forever:
                    rospy.loginfo("Cycle %d Phase %d/%d: %s", cycle, index, len(self._phases), phase["id"])
                else:
                    rospy.loginfo("Phase %d/%d: %s", index, len(self._phases), phase["id"])
                rospy.loginfo("  label: %s", phase["label"])
                rospy.loginfo("  notes: %s", phase["notes"])
                rospy.loginfo(
                    "  name=%s value=%.2f encoding=%s tx_mode=%s",
                    phase["name"], phase["value"], phase["encoding"], phase["tx_mode"]
                )
                rospy.loginfo(
                    "  QGC expectation: watch MAVLink Inspector for source %d/%d and msgid=251",
                    self._sys_id, self._comp_id
                )
                rospy.loginfo("=" * 72)

                if self._run_forever:
                    end_time = None
                else:
                    end_time = time.time() + self._phase_seconds

                while not rospy.is_shutdown() and (end_time is None or time.time() < end_time):
                    now = time.time()
                    if now - self._last_heartbeat >= (1.0 / HEARTBEAT_RATE_HZ):
                        if phase["tx_mode"] == "finalized":
                            self._send_finalized_heartbeat()
                        else:
                            self._send_heartbeat()
                        self._last_heartbeat = now

                    if phase["tx_mode"] == "finalized":
                        frame_len, frame_hex = self._send_finalized_named_value_float(
                            phase["name"], phase["value"]
                        )
                    else:
                        frame_len, frame_hex = self._send_named_value_float(
                            phase["name"], phase["value"], phase["encoding"]
                        )

                    rospy.loginfo_throttle(
                        2.0,
                        "phase=%s tx_mode=%s frame_len=%d frame_hex=%s",
                        phase["id"], phase["tx_mode"], frame_len, frame_hex
                    )
                    rate.sleep()

            if not self._run_forever:
                break

        rospy.loginfo("Probe finished")
        rospy.loginfo("Please report which phases produced source %d/%d and msgid=251 in QGC Inspector:",
                      self._sys_id, self._comp_id)
        for phase in self._phases:
            rospy.loginfo("  - %s", phase["id"])


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Phase-based NAMED_VALUE_FLOAT probe for MAVROS/FCU/QGC telemetry debugging."
    )
    parser.add_argument(
        "--phases",
        default="finalized_short",
        help="Comma-separated phase ids. Default: finalized_short",
    )
    parser.add_argument(
        "--phase-seconds",
        type=float,
        default=10.0,
        help="Seconds per phase when --run-forever is not set"
    )
    parser.add_argument(
        "--run-forever",
        action="store_true",
        default=True,
        help="Loop through phases continuously until Ctrl+C (default: enabled)"
    )
    parser.add_argument(
        "--run-once",
        dest="run_forever",
        action="store_false",
        help="Run configured phases once, honoring --phase-seconds"
    )
    parser.add_argument("--rate-hz", type=float, default=TELEMETRY_RATE_HZ, help="NAMED_VALUE_FLOAT send rate")
    parser.add_argument("--wait-timeout", type=float, default=15.0, help="Wait timeout for /mavros/state")
    parser.add_argument("--sysid", type=int, default=1, help="Companion source sysid")
    parser.add_argument("--compid", type=int, default=240, help="Companion source compid")

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
