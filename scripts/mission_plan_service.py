#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build and upload controlled USV MAVROS missions from Web route drafts."""

from __future__ import print_function

import math


MAV_FRAME_GLOBAL_RELATIVE_ALT = 3
MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_NAV_SCRIPT_TIME = 42702
DEFAULT_SAMPLE_TIMEOUT_S = 255.0
MAX_SAMPLE_TIMEOUT_S = 255.0
MAX_WAYPOINTS = 200


def _finite_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _bool_value(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    return default


def _coord(raw, names):
    if not isinstance(raw, dict):
        return None
    for name in names:
        if name in raw:
            return _finite_float(raw.get(name))
    return None


def _sample_enabled(raw, seq, sampling_config):
    if isinstance(raw, dict) and "sample" in raw:
        return _bool_value(raw.get("sample"))
    if isinstance(raw, dict) and "sampling" in raw:
        return _bool_value(raw.get("sampling"))
    item = {}
    if isinstance(sampling_config, dict):
        item = sampling_config.get(str(seq), {}) or {}
    if isinstance(item, dict) and "enabled" in item:
        return _bool_value(item.get("enabled"))
    return False


def _sample_timeout(raw, default_timeout):
    source = None
    if isinstance(raw, dict):
        source = raw.get("sample_timeout_s", raw.get("timeout_s"))
    value = _finite_float(source)
    if value is None:
        value = default_timeout
    if value < 1.0 or value > MAX_SAMPLE_TIMEOUT_S:
        return None
    return float(int(round(value)))


def build_mission_plan(payload, sampling_config=None):
    """Validate a Web mission draft and return serializable MAVLink item specs."""
    data = payload if isinstance(payload, dict) else {}
    raw_waypoints = data.get("waypoints")
    errors = []
    items = []
    nav_waypoints = []
    script_count = 0
    default_timeout = _sample_timeout(
        {"sample_timeout_s": data.get("sample_timeout_s")},
        DEFAULT_SAMPLE_TIMEOUT_S,
    )
    if default_timeout is None:
        errors.append("sample_timeout_s must be within 1..255")
        default_timeout = DEFAULT_SAMPLE_TIMEOUT_S

    if not isinstance(raw_waypoints, list) or not raw_waypoints:
        errors.append("waypoints must be a non-empty list")
        raw_waypoints = []
    if len(raw_waypoints) > MAX_WAYPOINTS:
        errors.append("waypoints exceeds maximum %d" % MAX_WAYPOINTS)

    for seq, raw in enumerate(raw_waypoints[:MAX_WAYPOINTS]):
        lat = _coord(raw, ("lat", "latitude"))
        lng = _coord(raw, ("lng", "lon", "long", "longitude"))
        alt = _finite_float(raw.get("alt", raw.get("altitude", 0.0))) if isinstance(raw, dict) else None
        if alt is None:
            alt = 0.0
        if lat is None or lat < -90.0 or lat > 90.0:
            errors.append("waypoints[%d].lat must be within -90..90" % seq)
            continue
        if lng is None or lng < -180.0 or lng > 180.0:
            errors.append("waypoints[%d].lng must be within -180..180" % seq)
            continue

        nav_waypoints.append({"seq": seq, "lat": lat, "lng": lng, "alt": alt})
        items.append({
            "seq": len(items),
            "kind": "waypoint",
            "frame": MAV_FRAME_GLOBAL_RELATIVE_ALT,
            "command": MAV_CMD_NAV_WAYPOINT,
            "is_current": len(items) == 0,
            "autocontinue": True,
            "param1": 0.0,
            "param2": 0.0,
            "param3": 0.0,
            "param4": 0.0,
            "x_lat": lat,
            "y_long": lng,
            "z_alt": alt,
        })

        if _sample_enabled(raw, seq, sampling_config):
            timeout = _sample_timeout(raw, default_timeout)
            if timeout is None:
                errors.append("waypoints[%d].sample_timeout_s must be within 1..255" % seq)
                continue
            script_count += 1
            items.append({
                "seq": len(items),
                "kind": "sample",
                "frame": MAV_FRAME_GLOBAL_RELATIVE_ALT,
                "command": MAV_CMD_NAV_SCRIPT_TIME,
                "is_current": False,
                "autocontinue": True,
                "param1": 1.0,
                "param2": timeout,
                "param3": 0.0,
                "param4": 0.0,
                "x_lat": 0.0,
                "y_long": 0.0,
                "z_alt": 0.0,
            })

    return {
        "valid": not errors,
        "errors": errors,
        "items": items if not errors else [],
        "nav_waypoints": nav_waypoints if not errors else [],
        "mission_items": len(items) if not errors else 0,
        "nav_waypoint_count": len(nav_waypoints) if not errors else 0,
        "script_item_count": script_count if not errors else 0,
        "commands": [item["command"] for item in items] if not errors else [],
        "replace": _bool_value(data.get("replace"), True),
        "start_auto": False,
    }


def _make_waypoint(item, waypoint_class):
    waypoint = waypoint_class()
    for key in (
        "frame", "command", "is_current", "autocontinue",
        "param1", "param2", "param3", "param4", "x_lat", "y_long", "z_alt",
    ):
        setattr(waypoint, key, item[key])
    return waypoint


def _mavros_push_items(mission_items):
    """Prepend the ArduPilot home placeholder expected by MAVROS full pushes."""
    items = list(mission_items or [])
    if not items:
        return []
    placeholder = dict(items[0])
    placeholder["kind"] = "ardupilot_home_placeholder"
    placeholder["is_current"] = True
    push_items = [placeholder]
    for item in items:
        pushed = dict(item)
        pushed["is_current"] = False
        push_items.append(pushed)
    return push_items


def _response_ok(resp):
    return bool(getattr(resp, "success", False))


def _command_of(waypoint):
    return int(getattr(waypoint, "command", -1))


def _close_enough(expected, actual, tolerance=1e-7):
    actual_float = _finite_float(actual)
    return actual_float is not None and abs(float(expected) - actual_float) <= tolerance


def compare_mission_readback(expected_items, readback_waypoints):
    waypoints = list(readback_waypoints or [])
    if len(waypoints) == len(expected_items) + 1:
        waypoints = waypoints[1:]
    if len(expected_items) != len(waypoints):
        return False
    for item, waypoint in zip(expected_items, waypoints):
        if int(item["command"]) != _command_of(waypoint):
            return False
        if int(item["command"]) == MAV_CMD_NAV_WAYPOINT:
            if not _close_enough(item["x_lat"], getattr(waypoint, "x_lat", None)):
                return False
            if not _close_enough(item["y_long"], getattr(waypoint, "y_long", None)):
                return False
        if int(item["command"]) == MAV_CMD_NAV_SCRIPT_TIME:
            if not _close_enough(item["param1"], getattr(waypoint, "param1", None)):
                return False
            if not _close_enough(item["param2"], getattr(waypoint, "param2", None)):
                return False
    return True


def _load_ros_dependencies(rospy_module, waypoint_class, waypoint_list_class, service_classes):
    if rospy_module is None:
        import rospy as rospy_module
    if waypoint_class is None or waypoint_list_class is None:
        from mavros_msgs.msg import Waypoint, WaypointList
        waypoint_class = waypoint_class or Waypoint
        waypoint_list_class = waypoint_list_class or WaypointList
    if service_classes is None:
        from mavros_msgs.srv import WaypointClear, WaypointPull, WaypointPush
        service_classes = {"clear": WaypointClear, "pull": WaypointPull, "push": WaypointPush}
    return rospy_module, waypoint_class, waypoint_list_class, service_classes


def _ros_error_types(rospy_module):
    errors = []
    for name in ("ROSException", "ROSInterruptException", "ServiceException"):
        error_type = getattr(rospy_module, name, None)
        if isinstance(error_type, type) and issubclass(error_type, Exception):
            errors.append(error_type)
    return tuple(errors) or (RuntimeError,)


def upload_mission_plan(payload, sampling_config=None, rospy_module=None,
                        waypoint_class=None, waypoint_list_class=None, service_classes=None):
    plan = build_mission_plan(payload, sampling_config=sampling_config)
    if not plan["valid"]:
        return {"success": False, "message": "; ".join(plan["errors"]), "data": plan}

    try:
        rospy_module, waypoint_class, waypoint_list_class, service_classes = _load_ros_dependencies(
            rospy_module, waypoint_class, waypoint_list_class, service_classes
        )
        push_items = _mavros_push_items(plan["items"])
        mav_waypoints = [_make_waypoint(item, waypoint_class) for item in push_items]
        plan["mavros_home_placeholder"] = bool(push_items)
        plan["mavros_push_items"] = len(push_items)

        if plan["replace"]:
            rospy_module.wait_for_service("/mavros/mission/clear", timeout=5.0)
            clear_resp = rospy_module.ServiceProxy("/mavros/mission/clear", service_classes["clear"])()
            if not _response_ok(clear_resp):
                return {"success": False, "message": "mavros mission clear failed", "data": plan}

        rospy_module.wait_for_service("/mavros/mission/push", timeout=5.0)
        push_resp = rospy_module.ServiceProxy("/mavros/mission/push", service_classes["push"])(
            start_index=0,
            waypoints=mav_waypoints,
        )
        transferred = int(getattr(push_resp, "wp_transfered", 0) or 0)
        if not _response_ok(push_resp) or transferred != len(mav_waypoints):
            plan["transferred"] = transferred
            return {"success": False, "message": "mavros mission push failed", "data": plan}

        rospy_module.wait_for_service("/mavros/mission/pull", timeout=5.0)
        pull_resp = rospy_module.ServiceProxy("/mavros/mission/pull", service_classes["pull"])()
        received = int(getattr(pull_resp, "wp_received", 0) or 0)
        readback_msg = rospy_module.wait_for_message(
            "/mavros/mission/waypoints",
            waypoint_list_class,
            timeout=3.0,
        )
        readback_waypoints = list(getattr(readback_msg, "waypoints", []) or [])
        received_ok = received == len(readback_waypoints) and received >= len(plan["items"])
        verified = _response_ok(pull_resp) and received_ok and compare_mission_readback(
            plan["items"],
            readback_waypoints,
        )
    except _ros_error_types(rospy_module) as exc:
        return {"success": False, "message": "mavros mission upload error: %s" % exc, "data": plan}

    plan["transferred"] = transferred
    plan["received"] = received
    plan["verified"] = verified
    if not verified:
        return {"success": False, "message": "mavros mission readback mismatch", "data": plan}
    return {"success": True, "message": "mission uploaded and verified", "data": plan}
