from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from typing import Mapping, Optional

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="milliseconds") + "Z"


def safe_id(value: object, fallback: str = "unknown") -> str:
    text = _SAFE_ID_RE.sub("_", str(value or fallback)).strip("._")
    return text or fallback


def _finite_float(value: object) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _trim(value: object, limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _gps_payload(position: Optional[Mapping[str, object]]) -> Optional[dict[str, object]]:
    if not isinstance(position, Mapping):
        return None
    wgs84 = position.get("wgs84")
    source = wgs84 if isinstance(wgs84, Mapping) else position
    lat = _finite_float(source.get("lat"))
    lng = _finite_float(source.get("lng"))
    if lat is None or lng is None:
        return None
    return {
        "lat": lat,
        "lng": lng,
        "alt": _finite_float(source.get("alt")),
        "received_at": _finite_float(position.get("received_at")),
    }


def normalize_gps_payload(position: Optional[Mapping[str, object]]) -> Optional[dict[str, object]]:
    return _gps_payload(position)


def make_sample_id(
    mission_id: object,
    mode: object,
    waypoint_seq: object = None,
    survey_index: object = None,
) -> str:
    suffix = "manual"
    waypoint = _finite_float(waypoint_seq)
    survey = _finite_float(survey_index)
    if waypoint is not None:
        suffix = "wp%03d" % int(waypoint)
    elif survey is not None:
        suffix = "survey%03d" % int(survey)
    elif mode:
        suffix = safe_id(mode)
    return "%s_%s_%d" % (safe_id(mission_id, "mission"), suffix, int(time.time() * 1000))


def default_manual_result() -> dict[str, object]:
    return {
        "status": "pending",
        "analyte": None,
        "concentration": None,
        "unit": None,
        "method": None,
        "operator": None,
        "recorded_at": None,
        "note": None,
    }


def default_processing() -> dict[str, object]:
    return {
        "status": "not_run",
        "algorithm": None,
        "version": None,
        "result": None,
        "error": None,
        "processed_at": None,
    }


def normalize_manual_result(payload: Optional[Mapping[str, object]]) -> dict[str, object]:
    payload = payload or {}
    concentration = payload.get("concentration")
    if concentration in ("", None):
        concentration_value = None
    else:
        concentration_value = _finite_float(concentration)
        if concentration_value is None:
            raise ValueError("concentration must be a finite number")
    result = default_manual_result()
    result.update(
        {
            "status": "recorded",
            "analyte": _trim(payload.get("analyte"), 100),
            "concentration": concentration_value,
            "unit": _trim(payload.get("unit"), 40) or "mg/L",
            "method": _trim(payload.get("method"), 100),
            "operator": _trim(payload.get("operator"), 100),
            "recorded_at": utc_now_iso(),
            "note": _trim(payload.get("note"), 1000),
        }
    )
    return result


def make_window(
    mission_id: object,
    context: Optional[Mapping[str, object]] = None,
    gps_latest: Optional[Mapping[str, object]] = None,
) -> dict[str, object]:
    context = context or {}
    mode = context.get("mode") or "manual"
    waypoint_seq = context.get("waypoint_seq")
    survey_index = context.get("survey_index")
    sample_id = context.get("sample_id") or make_sample_id(mission_id, mode, waypoint_seq, survey_index)
    gps = _gps_payload(gps_latest)
    now = utc_now_iso()
    return {
        "sample_id": safe_id(sample_id, "sample"),
        "mission_id": safe_id(mission_id, "mission"),
        "schema_version": 1,
        "mode": str(mode),
        "source": str(context.get("source") or mode),
        "state": "open",
        "waypoint_seq": int(waypoint_seq) if _finite_float(waypoint_seq) is not None else None,
        "mavlink_sample_id": int(context["mavlink_sample_id"]) if _finite_float(context.get("mavlink_sample_id")) is not None else None,
        "survey_index": int(survey_index) if _finite_float(survey_index) is not None else None,
        "route_ref": context.get("route_ref"),
        "start_time": now,
        "end_time": None,
        "duration_s": None,
        "gps_start": gps,
        "gps_end": None,
        "gps_latest": gps,
        "spectrometer": {},
        "manual_result": default_manual_result(),
        "processing": default_processing(),
    }


def normalize_raw_frame(
    payload: Mapping[str, object],
    received_at: Optional[float] = None,
    latest_voltage: Optional[Mapping[str, object]] = None,
) -> dict[str, object]:
    latest_voltage = latest_voltage or {}
    frame = {
        "received_at": received_at if received_at is not None else time.time(),
        "timestamp_ms": _finite_float(payload.get("timestamp_ms")),
        "tca_channel": int(payload["tca_channel"]) if _finite_float(payload.get("tca_channel")) is not None else None,
        "status": payload.get("status"),
        "raw_code": int(payload["raw_code"]) if _finite_float(payload.get("raw_code")) is not None else None,
        "voltage": _finite_float(payload.get("voltage", payload.get("sample_voltage"))),
        "valid": bool(payload.get("valid", False)),
        "i2c_error": bool(payload.get("i2c_error", False)),
        "not_configured": bool(payload.get("not_configured", False)),
        "saturated": bool(payload.get("saturated", False)),
        "absorbance": _finite_float(payload.get("absorbance", latest_voltage.get("absorbance"))),
        "baseline_set": bool(payload.get("baseline_set", latest_voltage.get("baseline_set", False))),
        "reference_voltage": _finite_float(payload.get("reference_voltage", latest_voltage.get("reference_voltage"))),
        "baseline_voltage": _finite_float(payload.get("baseline_voltage", latest_voltage.get("baseline_voltage"))),
    }
    return frame
