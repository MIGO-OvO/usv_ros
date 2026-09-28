"""Valid hardware GPS fixtures for tests exercising downstream sampling logic."""
import time
import types


def gps_position(lat=30.0, lon=120.0, age=0.0):
    return dict(lat=lat, lon=lon, alt=4.5, fix_status=0,
                gps_timestamp=time.time() - age, received_at=time.time(),
                received_monotonic=time.monotonic(), source_age_at_receive_s=age,
                position_source='gps')


def gps_message(lat=30.0, lon=120.0, age=0.0, status=0):
    stamp = time.time() - age
    return types.SimpleNamespace(latitude=lat, longitude=lon, altitude=4.5,
                                 header=types.SimpleNamespace(stamp=types.SimpleNamespace(to_sec=lambda: stamp)),
                                 status=types.SimpleNamespace(status=status))
