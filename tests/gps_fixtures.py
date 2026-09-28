"""Valid hardware GPS fixtures for tests exercising downstream sampling logic."""
import time
import types


def gps_evidence():
    """Fresh direct-FCU evidence for map consumers (independent of NavSatFix)."""
    now = time.monotonic()
    return {'records': {
        'HEARTBEAT': {'system_id': 1, 'component_id': 1, 'received_monotonic': now},
        'GPS_RAW_INT': {'fix_type': 3, 'satellites': 14, 'latitude': 30., 'longitude': 120.,
                        'received_monotonic': now, 'received_at': time.time()},
    }}


def gps_position(lat=30.0, lon=120.0, age=0.0):
    """Fresh hardware fix aged ``age`` seconds on the Jetson-local receive clock.

    ``gps_timestamp`` mirrors the same age so the header.clock offset stays
    realistic; it remains diagnostic-only and never gates admission.
    """
    return dict(lat=lat, lon=lon, alt=4.5, fix_status=0,
                gps_timestamp=time.time() - age, received_at=time.time(),
                received_monotonic=time.monotonic() - age,
                header_clock_offset_s=age,
                position_source='gps')


def gps_message(lat=30.0, lon=120.0, age=0.0, status=0):
    stamp = time.time() - age
    return types.SimpleNamespace(latitude=lat, longitude=lon, altitude=4.5,
                                 header=types.SimpleNamespace(stamp=types.SimpleNamespace(to_sec=lambda: stamp)),
                                 status=types.SimpleNamespace(status=status))
