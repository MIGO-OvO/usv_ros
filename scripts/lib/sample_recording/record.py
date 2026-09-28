"""Shared acquisition contract. Coordinates are WGS84; times are Unix seconds.

The legacy FCU sample_id is a uint16 handshake token, NOT the record UUID.
Missing measurements/mission metadata stay null rather than being invented.
"""
import copy
import math
import time
import uuid


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def navsat_position(msg, ros_now=None):
    stamp = getattr(getattr(msg, 'header', None), 'stamp', None)
    timestamp = finite(stamp.to_sec()) if hasattr(stamp, 'to_sec') else None
    now = time.time()
    return {
        'lat': finite(getattr(msg, 'latitude', None)),
        'lon': finite(getattr(msg, 'longitude', None)),
        'alt': finite(getattr(msg, 'altitude', None)),
        'fix_status': getattr(getattr(msg, 'status', None), 'status', -1),
        'gps_timestamp': timestamp,
        'source_age_at_receive_s': (ros_now - timestamp
                                    if ros_now is not None and timestamp is not None else None),
        'received_at': now,
        'received_monotonic': time.monotonic(),
        'position_source': 'gps',
    }


def freeze_position(position, max_age_s=2.0, simulated=False):
    p = copy.deepcopy(position or {})
    lat, lon = finite(p.get('lat')), finite(p.get('lon', p.get('lng')))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('gps_invalid_coordinates')
    if simulated:
        age = 0.0
    else:
        if finite(p.get('fix_status')) is None or p['fix_status'] < 0:
            raise ValueError('gps_no_fix')
        stamp = finite(p.get('gps_timestamp'))
        received = finite(p.get('received_monotonic'))
        source_age = finite(p.get('source_age_at_receive_s'))
        if stamp is None or stamp <= 0 or received is None or source_age is None:
            raise ValueError('gps_missing_timestamp')
        elapsed = time.monotonic() - received
        age = source_age + elapsed
        limit = finite(max_age_s)
        limit = limit if limit is not None and limit > 0 else 2.0
        if source_age < 0 or elapsed < 0 or not 0 <= age <= limit:
            raise ValueError('gps_stale')
    p.update(lat=lat, lon=lon, lng=lon, position_age_s=age,
             position_source='lab_sim' if simulated else 'gps')
    return p


def bind_context(context, position, max_age_s=2.0, simulated=False):
    """Call once immediately before acquisition; never replace an existing fix."""
    if context.get('record_id'):
        return context
    snapshot = freeze_position(position, max_age_s, simulated)
    context.update(record_id=uuid.uuid4().hex, timestamp_start=time.time(),
                   gps_snapshot=snapshot, simulated=bool(simulated))
    return context


def sample_record(context, spectrometer=None, water_quality=None, timestamp_end=None):
    gps = context.get('gps_snapshot') or {}
    return {
        'record_schema_version': 1,
        'sample_id': context['record_id'],
        'timestamp_start': context['timestamp_start'],
        'timestamp_end': timestamp_end,
        'latitude': gps.get('lat'), 'longitude': gps.get('lon', gps.get('lng')),
        'altitude': gps.get('alt'), 'gps_timestamp': gps.get('gps_timestamp'),
        'position_age_s': gps.get('position_age_s'),
        'position_source': gps.get('position_source'),
        'waypoint_id': context.get('waypoint_seq'), 'mission_id': context.get('mission_id'),
        'mavlink_sample_id': context.get('sample_id') if context.get('source') == 'fcu' else None,
        'simulated': bool(context.get('simulated', False)),
        'spectrometer': copy.deepcopy(spectrometer or {}),
        'water_quality': copy.deepcopy(water_quality or {'concentration': None, 'unit': None}),
    }


def bind_web_context(context, position, max_age_s=2.0, require_gps=True):
    """Explicit per-request Web bench opt-out; other entry points remain strict."""
    if type(require_gps) is not bool:
        raise ValueError('require_gps must be a boolean')
    try:
        return bind_context(context, position, max_age_s)
    except ValueError as exc:
        if require_gps:
            raise
        context.update(record_id=uuid.uuid4().hex, timestamp_start=time.time(),
                       gps_snapshot={'position_source': 'web_no_gps'},
                       simulated=False, gps_required=False, gps_rejection_reason=str(exc))
        return context
