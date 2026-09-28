"""Read-only FCU evidence. Never substitutes for acquisition freeze_position().

Monotonic stamps are shared only between processes on the same Jetson boot.
Unix receive times are display metadata, not freshness clocks.
"""
import copy
import time

from scripts.lib.sample_recording.record import finite, freeze_position

PARAMETERS = ('GPS1_TYPE', 'GPS_AUTO_CONFIG', 'SERIAL3_PROTOCOL')
FIX_LABELS = {0: '未识别接收机', 1: '尚未定位', 2: '2D Fix', 3: '3D Fix',
              4: 'DGPS', 5: 'RTK Float', 6: 'RTK Fixed', 7: 'Static', 8: 'PPP'}
# FCU/MAVROS header.stamp and the Jetson wall clock are separate domains; a
# large offset is surfaced as a warning but never gates sampling admission.
HEADER_CLOCK_OFFSET_WARN_S = 2.0


def age(record, now):
    stamp = finite(record.get('received_monotonic'))
    return max(0.0, now - stamp) if stamp is not None and now >= stamp else None


def fresh(record, now, limit):
    value = age(record, now)
    return value is not None and value <= limit


def coordinates_valid(record):
    lat, lon = finite(record.get('latitude')), finite(record.get('longitude'))
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180


class GPSDiagnosticsCache:
    def __init__(self, system_id=1, component_id=1):
        self.system_id, self.component_id = system_id, component_id
        self.records = {}
        self.parameters = {}
        self.texts = []

    def ingest(self, msg, now=None, wall=None):
        if (msg.get_srcSystem(), msg.get_srcComponent()) != (self.system_id, self.component_id):
            return
        kind = msg.get_type()
        if kind not in ('HEARTBEAT', 'GPS_RAW_INT', 'GLOBAL_POSITION_INT', 'SYS_STATUS',
                        'STATUSTEXT', 'AUTOPILOT_VERSION', 'PARAM_VALUE'):
            return
        now = time.monotonic() if now is None else now
        wall = time.time() if wall is None else wall
        record = {'received_monotonic': now, 'received_at': wall}
        def scaled(name, divisor=1, unknown=None):
            value = finite(getattr(msg, name, None))
            return value / divisor if value is not None and value != unknown else None
        if kind == 'HEARTBEAT':
            # Ignore GCS/onboard-controller heartbeats even if misconfigured as 1/1.
            if getattr(msg, 'autopilot', 8) == 8:
                return
            record.update(system_id=self.system_id, component_id=self.component_id)
        elif kind in ('GPS_RAW_INT', 'GLOBAL_POSITION_INT'):
            record.update(latitude=scaled('lat', 1e7), longitude=scaled('lon', 1e7),
                          altitude=scaled('alt', 1000))
            if kind == 'GPS_RAW_INT':
                record.update(fix_type=scaled('fix_type'), satellites=scaled('satellites_visible', unknown=255),
                              hdop=scaled('eph', 100, 65535), vdop=scaled('epv', 100, 65535),
                              horizontal_accuracy_m=scaled('h_acc', 1000, 0),
                              vertical_accuracy_m=scaled('v_acc', 1000, 0))
        elif kind == 'PARAM_VALUE':
            name = msg.param_id
            if isinstance(name, bytes):
                name = name.decode('ascii', errors='replace')
            name = name.rstrip('\x00')
            if name in PARAMETERS:
                record['value'] = scaled('param_value')
                self.parameters[name] = record
            return
        elif kind == 'SYS_STATUS':
            for name in ('present', 'enabled', 'health'):
                value = getattr(msg, 'onboard_control_sensors_' + name, None)
                record['gps_' + name] = bool(value & 32) if value is not None else None
        elif kind == 'STATUSTEXT':
            text = msg.text.decode('utf-8', errors='replace') if isinstance(msg.text, bytes) else msg.text
            if not any(token in text.lower() for token in ('gps', 'gnss', 'ublox', 'u-blox', 'rtk')):
                return
            record.update(text=text.rstrip('\x00')[:200], severity=getattr(msg, 'severity', None))
            self.texts = (self.texts + [record])[-12:]
            return
        elif kind == 'AUTOPILOT_VERSION':
            version = getattr(msg, 'flight_sw_version', None)
            custom = getattr(msg, 'flight_custom_version', None)
            record.update(firmware_version=('%d.%d.%d (type %d)' %
                          (version >> 24, (version >> 16) & 255, (version >> 8) & 255, version & 255))
                          if version else None,
                          # ArduPilot sends the first 8 ASCII characters of fw_hash_str.
                          git_hash=bytes(custom).decode('ascii', errors='replace').rstrip('\x00')
                          if custom and any(custom) else None)
        self.records[kind] = record

    def snapshot(self):
        return copy.deepcopy({'records': self.records, 'parameters': self.parameters, 'statustext': self.texts})


def diagnose(evidence=None, position=None, mavros=None, max_age_s=2.0, now=None):
    now = time.monotonic() if now is None else now
    max_age_s = finite(max_age_s)
    max_age_s = max_age_s if max_age_s is not None and max_age_s > 0 else 2.0
    evidence, position, mavros = evidence or {}, position or {}, mavros or {}
    records = evidence.get('records', {})
    def section(kind):
        record = records.get(kind, {})
        return dict(record, available=bool(record), age_s=age(record, now),
                    stale=not fresh(record, now, max_age_s))
    raw, glob, hb = section('GPS_RAW_INT'), section('GLOBAL_POSITION_INT'), section('HEARTBEAT')
    raw['valid'] = (raw['available'] and not raw['stale'] and
                    (raw.get('fix_type') or 0) >= 3 and coordinates_valid(raw))
    raw['fix_label'] = FIX_LABELS.get(raw.get('fix_type'), 'Unknown')
    for key in ('fix_type', 'satellites', 'hdop', 'vdop', 'horizontal_accuracy_m',
                'vertical_accuracy_m', 'latitude', 'longitude', 'altitude', 'received_at'):
        raw.setdefault(key, None)
    for key in ('latitude', 'longitude', 'altitude', 'received_at'):
        glob.setdefault(key, None)
    valid, reason = False, 'gps_missing'
    if position:
        try:
            freeze_position(position, max_age_s)
            valid, reason = True, None
        except ValueError as exc:
            reason = str(exc)
    elapsed = age(position, now)
    clock_offset = finite(position.get('header_clock_offset_s'))
    if clock_offset is None:
        stamp = finite(position.get('gps_timestamp'))
        received_at = finite(position.get('received_at'))
        clock_offset = received_at - stamp if stamp is not None and received_at is not None else None
    navsat = {'available': bool(position), 'valid_navsat_fix': valid,
              'status': position.get('fix_status'), 'latitude': finite(position.get('lat')),
              'longitude': finite(position.get('lon')), 'altitude': finite(position.get('alt')),
              'age_s': elapsed,
              'gps_timestamp': finite(position.get('gps_timestamp')),
              'header_clock_offset_s': clock_offset,
              'received_at': position.get('received_at')}
    params = evidence.get('parameters', {})
    config = {key: params.get(name, {}).get('value') for key, name in
              (('gps1_type', 'GPS1_TYPE'), ('auto_config', 'GPS_AUTO_CONFIG'), ('serial_protocol', 'SERIAL3_PROTOCOL'))}
    config['parameters'] = {name: dict(params.get(name, {}), age_s=age(params.get(name, {}), now)) for name in PARAMETERS}
    # Configuration is refreshed periodically. Old values remain visible, not authoritative.
    config['fresh'] = fresh(params.get('GPS1_TYPE', {}), now, 60)
    heartbeat_valid = fresh(hb, now, 3.0)
    if not heartbeat_valid:
        state, severity, message = 'fcu_timeout', 'error', '飞控通信中断'
    elif config['fresh'] and config['gps1_type'] == 0:
        state, severity, message = 'disabled', 'neutral', 'GPS未启用'
    elif not raw['available']:
        state, severity, message = 'raw_missing', 'orange', 'GPS原始数据缺失 · 未收到 GPS_RAW_INT'
    elif raw['stale']:
        state, severity, message = 'stale', 'orange', 'GPS数据已过期'
    elif raw['fix_type'] == 0:
        state, severity, message = 'no_receiver', 'orange', 'GPS已启用但未识别接收机'
    elif (raw['fix_type'] or 0) < 3:
        state, severity, message = 'no_fix', 'warning', ('GPS 2D Fix · 尚未获得3D定位' if raw['fix_type'] == 2 else 'GPS已识别，尚未定位')
    elif not coordinates_valid(raw):
        state, severity, message = 'invalid_coordinates', 'error', 'GPS坐标无效'
    else:
        state, severity, message = 'fix_3d', 'success', 'GPS 3D FIX' + (' · ' + raw['fix_label'] if raw['fix_type'] > 3 else '')
    warnings = []
    if not raw['valid'] and (glob['available'] or navsat['available']):
        warnings.append('检测到融合/全局位置，但原始 GNSS 无有效3D Fix；当前经纬度可能来自 EKF/历史状态，不能证明有效采样坐标。')
    mavros_current = mavros.get('connected') if fresh(mavros, now, 3.0) else None
    if heartbeat_valid and mavros_current is not True:
        warnings.append('FCU连接正常 / MAVROS异常或状态已过期')
    if state == 'raw_missing':
        warnings.append('GPS已启用但无原始GPS数据' if config['fresh'] and config['gps1_type'] not in (None, 0) else 'GPS配置未知；缺帧不能单独证明接收机未识别。')
        warnings.append('检查 GPS 配置、接线及 MAVLink GPS_RAW_INT 消息流；本面板不修改飞控参数或消息速率。')
    if clock_offset is not None and abs(clock_offset) > HEADER_CLOCK_OFFSET_WARN_S:
        warnings.append('NavSatFix 时间戳与本机时钟偏差 %.1fs（跨时钟域信息仅作诊断，不影响采样准入）' % clock_offset)
    sys_status, version = section('SYS_STATUS'), section('AUTOPILOT_VERSION')
    for key in ('gps_present', 'gps_enabled', 'gps_health'):
        sys_status.setdefault(key, None)
    for key in ('firmware_version', 'git_hash'):
        version.setdefault(key, None)
    return {'schema_version': 1, 'overall': {'state': state, 'severity': severity, 'message': message},
            'fcu': {'heartbeat_valid': heartbeat_valid, 'system_id': hb.get('system_id'),
                    'component_id': hb.get('component_id'), 'heartbeat_age_s': hb['age_s'],
                    'heartbeat_received_at': hb.get('received_at'), 'mavros_connected': mavros_current,
                    'mavros_reported_connected': mavros.get('connected'), 'mavros_age_s': age(mavros, now)},
            'gps_config': config, 'gps_raw': raw, 'global_position': glob, 'navsat': navsat,
            'sampling_position': {'valid': valid, 'reason': reason, 'policy': 'strict_navsat_freeze_position'},
            'map_position_valid': valid and raw['valid'] and heartbeat_valid and state == 'fix_3d',
            'sys_status': sys_status, 'autopilot_version': version,
            'statustext': evidence.get('statustext', []), 'warnings': warnings,
            'freshness_threshold_s': max_age_s}
