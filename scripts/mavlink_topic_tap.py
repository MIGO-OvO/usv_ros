#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pyright: reportMissingImports=false
"""
Observe /mavros/mavlink/to and /mavros/mavlink/from to confirm whether a
companion source appears on the local ROS graph.

Usage:
  rosrun usv_ros mavlink_topic_tap.py --sysid 2 --compid 191
"""

from __future__ import print_function

import argparse
from collections import Counter

import rospy
from mavros_msgs.msg import Mavlink, State


class MavlinkTopicTap(object):

    def __init__(self, args):
        rospy.init_node("mavlink_topic_tap", anonymous=False)

        self._sysid = int(args.sysid)
        self._compid = int(args.compid)
        self._duration = float(args.duration)

        self._state = None
        self._to_counts = Counter()
        self._from_counts = Counter()
        self._to_hits = 0
        self._from_hits = 0

        self._state_sub = rospy.Subscriber("/mavros/state", State, self._state_cb, queue_size=5)
        self._to_sub = rospy.Subscriber("/mavros/mavlink/to", Mavlink, self._to_cb, queue_size=100)
        self._from_sub = rospy.Subscriber("/mavros/mavlink/from", Mavlink, self._from_cb, queue_size=100)

        rospy.loginfo("mavlink_topic_tap started")
        rospy.loginfo("  watch source: sysid=%d compid=%d", self._sysid, self._compid)
        rospy.loginfo("  duration: %.1f s", self._duration)

    def _state_cb(self, msg):
        self._state = msg

    def _key(self, msg):
        return (int(msg.sysid), int(msg.compid), int(msg.msgid))

    def _matches(self, msg):
        return int(msg.sysid) == self._sysid and int(msg.compid) == self._compid

    def _to_cb(self, msg):
        key = self._key(msg)
        self._to_counts[key] += 1
        if self._matches(msg):
            self._to_hits += 1
            rospy.loginfo_throttle(
                1.0,
                "TO hit: sysid=%d compid=%d msgid=%d len=%d checksum=%d seq=%d",
                msg.sysid, msg.compid, msg.msgid, msg.len, msg.checksum, msg.seq
            )

    def _from_cb(self, msg):
        key = self._key(msg)
        self._from_counts[key] += 1
        if self._matches(msg):
            self._from_hits += 1
            rospy.loginfo_throttle(
                1.0,
                "FROM hit: sysid=%d compid=%d msgid=%d len=%d checksum=%d seq=%d",
                msg.sysid, msg.compid, msg.msgid, msg.len, msg.checksum, msg.seq
            )

    def run(self):
        rate = rospy.Rate(1)
        deadline = rospy.Time.now() + rospy.Duration.from_sec(self._duration)
        while not rospy.is_shutdown() and rospy.Time.now() < deadline:
            if self._state is not None:
                rospy.loginfo_throttle(
                    5.0,
                    "MAVROS state: connected=%s mode=%s armed=%s",
                    self._state.connected, self._state.mode, self._state.armed
                )
            rate.sleep()

        rospy.loginfo("=" * 72)
        rospy.loginfo("mavlink_topic_tap summary")
        rospy.loginfo("  watch source: %d/%d", self._sysid, self._compid)
        if self._state is None:
            rospy.loginfo("  mavros_state: none")
        else:
            rospy.loginfo(
                "  mavros_state: connected=%s mode=%s armed=%s",
                self._state.connected, self._state.mode, self._state.armed
            )
        rospy.loginfo("  /mavros/mavlink/to matching hits: %d", self._to_hits)
        rospy.loginfo("  /mavros/mavlink/from matching hits: %d", self._from_hits)

        rospy.loginfo("  top /to sources:")
        for (sysid, compid, msgid), count in self._to_counts.most_common(10):
            rospy.loginfo("    %5d  %3d/%3d msgid=%d", count, sysid, compid, msgid)

        rospy.loginfo("  top /from sources:")
        for (sysid, compid, msgid), count in self._from_counts.most_common(10):
            rospy.loginfo("    %5d  %3d/%3d msgid=%d", count, sysid, compid, msgid)
        rospy.loginfo("=" * 72)


def _parse_args():
    parser = argparse.ArgumentParser(description="Observe MAVROS raw MAVLink topics for a target source id.")
    parser.add_argument("--sysid", type=int, default=2, help="Watched system id")
    parser.add_argument("--compid", type=int, default=191, help="Watched component id")
    parser.add_argument("--duration", type=float, default=30.0, help="Observe duration in seconds")
    return parser.parse_args(rospy.myargv()[1:])


def main():
    try:
        args = _parse_args()
        tap = MavlinkTopicTap(args)
        tap.run()
    except rospy.ROSInterruptException:
        pass
    except Exception as exc:
        rospy.logerr("mavlink_topic_tap failed: %s", str(exc))


if __name__ == "__main__":
    main()
