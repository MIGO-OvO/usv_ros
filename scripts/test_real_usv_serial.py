#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pyright: reportMissingImports=false
"""
真机直连飞控串口的 USV MAVLink 测试脚本。

目的：绕过 /mavros/mavlink/to，直接用 pymavlink 向飞控串口发送
NAMED_VALUE_FLOAT，验证 Jetson/Nano -> FCU -> telemetry radio -> QGC 是否可通。

用法示例：
  python3 src/usv_ros/scripts/test_real_usv_serial.py
  python3 src/usv_ros/scripts/test_real_usv_serial.py --device /dev/ttyTHS1 --baud 921600
  python3 src/usv_ros/scripts/test_real_usv_serial.py --sysid 1 --compid 0 --rate 2
"""

from __future__ import print_function

import argparse
import sys
import time

from pymavlink import mavutil

DEFAULT_DEVICE = "/dev/ttyTHS1"
DEFAULT_BAUD = 921600
DEFAULT_RATE_HZ = 2.0
DEFAULT_SYSID = 1
DEFAULT_COMPID = 0
HEARTBEAT_RATE_HZ = 1.0


class RealUSVSerialTester(object):

    def __init__(self, args):
        self._device = args.device
        self._baud = int(args.baud)
        self._rate_hz = float(args.rate)
        self._sysid = int(args.sysid)
        self._compid = int(args.compid)
        self._wait_heartbeat = bool(args.wait_heartbeat)
        self._last_hb = 0.0
        self._pkt = 0

        print("=" * 72)
        print("Real USV serial MAVLink tester")
        print("  device : %s" % self._device)
        print("  baud   : %d" % self._baud)
        print("  rate   : %.2f Hz" % self._rate_hz)
        print("  source : %d/%d" % (self._sysid, self._compid))
        print("=" * 72)

        self._conn = mavutil.mavlink_connection(
            self._device,
            baud=self._baud,
            source_system=self._sysid,
            source_component=self._compid,
            autoreconnect=True,
            force_connected=True,
        )

    def connect(self):
        if not self._wait_heartbeat:
            print("skip wait_heartbeat; start sending immediately")
            return

        print("waiting heartbeat from FCU ...")
        hb = self._conn.wait_heartbeat(timeout=10)
        if hb is None:
            print("WARN: no heartbeat within 10s, continue sending anyway")
            return

        print(
            "heartbeat ok: target_system=%s target_component=%s" %
            (self._conn.target_system, self._conn.target_component)
        )

    def _send_heartbeat(self):
        self._conn.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
            0,
            0,
            mavutil.mavlink.MAV_STATE_ACTIVE,
        )

    def _send_payload(self):
        t = int(time.time() * 1000) & 0xFFFFFFFF
        self._pkt = (self._pkt + 1) % 65536

        self._conn.mav.named_value_float_send(t, b"USV_VOLT\x00\x00", 12.56)
        self._conn.mav.named_value_float_send(t, b"USV_ABS\x00\x00\x00", 0.432)
        self._conn.mav.named_value_float_send(t, b"PUMP_X\x00\x00\x00\x00", 45.0)
        self._conn.mav.named_value_float_send(t, b"PUMP_Y\x00\x00\x00\x00", 90.0)
        self._conn.mav.named_value_float_send(t, b"PUMP_Z\x00\x00\x00\x00", 0.0)
        self._conn.mav.named_value_float_send(t, b"PUMP_A\x00\x00\x00\x00", 30.0)
        self._conn.mav.named_value_float_send(t, b"USV_STAT\x00\x00", 1.0)
        self._conn.mav.named_value_float_send(t, b"USV_PKT\x00\x00\x00", float(self._pkt))

    def run(self):
        self.connect()
        period = 1.0 / self._rate_hz if self._rate_hz > 0 else 0.5
        print("start sending test payload; press Ctrl+C to stop")

        while True:
            now = time.time()
            if now - self._last_hb >= (1.0 / HEARTBEAT_RATE_HZ):
                self._send_heartbeat()
                self._last_hb = now

            self._send_payload()
            print("tx pkt=%d t=%d" % (self._pkt, int(now)), end="\r")
            sys.stdout.flush()
            time.sleep(period)


def _parse_args():
    parser = argparse.ArgumentParser(description="Direct serial MAVLink tester for real USV FCU.")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="FCU serial device path")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="FCU serial baudrate")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE_HZ, help="Payload send rate in Hz")
    parser.add_argument("--sysid", type=int, default=DEFAULT_SYSID, help="Source system id")
    parser.add_argument("--compid", type=int, default=DEFAULT_COMPID, help="Source component id")
    parser.add_argument(
        "--no-wait-heartbeat",
        dest="wait_heartbeat",
        action="store_false",
        help="Do not wait heartbeat before sending"
    )
    parser.set_defaults(wait_heartbeat=True)
    return parser.parse_args()


def main():
    try:
        RealUSVSerialTester(_parse_args()).run()
    except KeyboardInterrupt:
        print("\nstop requested")
    except Exception as exc:
        print("tester failed: %s" % str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
