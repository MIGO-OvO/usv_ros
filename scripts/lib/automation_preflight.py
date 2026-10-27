"""Bounded, feedback-driven preparation before AutomationEngine (Python 3.8)."""
import json
import math
import threading
import time

AXES = ('X', 'Y', 'Z', 'A')
DEFAULT_PREFLIGHT = {'oil_axis': None, 'separation_turns': 0.0, 'separation_rpm': 5.0}


def normalize_preflight(raw):
    if not isinstance(raw, dict):
        raise ValueError('preflight must be an object')
    config = dict(DEFAULT_PREFLIGHT, **raw)
    axis = config['oil_axis']
    if axis is not None and axis not in AXES:
        raise ValueError('preflight.oil_axis must be X/Y/Z/A or null')
    for key, minimum, maximum in (('separation_turns', 0.0, 10.0),
                                  ('separation_rpm', 0.1, 20.0)):
        value = config[key]
        if isinstance(value, bool):
            raise ValueError('preflight.%s must be a number' % key)
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError('preflight.%s must be a number' % key)
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError('preflight.%s must be in [%s, %s]' % (key, minimum, maximum))
        config[key] = value
    if config['separation_turns'] > 0 and axis is None:
        raise ValueError('Select the oil pump axis before enabling separation')
    if 0 < config['separation_turns'] < 0.01:
        raise ValueError('Positive separation_turns must be at least 0.01')
    return {key: config[key] for key in DEFAULT_PREFLIGHT}


def involved_axes(steps):
    return [axis for axis in AXES if any(
        isinstance(step.get(axis), dict) and step[axis].get('enable') == 'E'
        for step in steps)]


def calibrated_offsets(data):
    """Legacy zero values are ambiguous: the old writer persisted defaults too."""
    offsets = data.get('offsets', {})
    if not isinstance(offsets, dict):
        raise ValueError('Saved relative zero offsets must be an object')
    configured = data.get('configured_axes')
    if configured is not None and (not isinstance(configured, list) or
                                  any(axis not in AXES for axis in configured)):
        raise ValueError('configured_axes must be a list of X/Y/Z/A')
    result = {}
    for axis, raw in offsets.items():
        if axis not in AXES or isinstance(raw, bool):
            raise ValueError('Invalid saved relative zero for %s' % axis)
        value = float(raw)
        if not math.isfinite(value) or not 0 <= value < 360:
            raise ValueError('Relative zero for %s must be in [0, 360)' % axis)
        if (configured is not None and axis in configured) or (configured is None and value != 0):
            result[axis] = value
    if configured is not None and any(axis not in offsets for axis in configured):
        raise ValueError('Configured relative zero is missing its offset')
    return result


def load_zero_offsets(path, axes):
    if not axes:
        return {}
    try:
        with open(path, encoding='utf-8') as handle:
            offsets = calibrated_offsets(json.load(handle))
    except (OSError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError('Cannot read saved relative zero points: %s' % exc)
    missing = [axis for axis in axes if axis not in offsets]
    if missing:
        raise ValueError('Set relative zero points for: %s' % ', '.join(missing))
    return offsets


def relative_angles(raw, offsets):
    """Display only; absent zero points must never look like absolute zero."""
    return {axis: (float(value) - offsets[axis]) % 360 if axis in offsets else None
            for axis, value in raw.items() if axis in AXES}


class PreflightError(Exception):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


class PreflightCancelled(Exception):
    pass


class AutomationPreflight:
    # MT6701: 14-bit / 360deg (~0.022deg per count). Allow several counts
    # for quantization/noise, independent of the formal step PID setting.
    PID_PRECISION_DEG = 0.1
    ACK_TIMEOUT = 2.0
    PID_TIMEOUT = 610.0  # Firmware 600 s ceiling plus serial/result delivery margin.
    ANGLE_TIMEOUT = 1.0
    PROGRESS_TIMEOUT = 120.0
    PID_SETTLE_ZONE_DEG = 10.0
    SEPARATION_TOLERANCE_DEG = 0.5

    def __init__(self, send, check_active, angles, publish):
        self.send = send
        self.check_active = check_active
        self.angles = angles
        self.publish = publish
        self.cancelled = threading.Event()
        self.changed = threading.Event()
        self.lock = threading.RLock()
        self.state = {'active': True, 'phase': 'checking', 'motors': [],
                      'pending_motors': [], 'error': ''}
        self.expected = {}
        self.started = set()
        self.pending = set()
        self.ack = None
        self.ack_received = False
        self.ack_text = None
        self.error = None
        self.travel = None

    def snapshot(self):
        with self.lock:
            return dict(self.state, pending_motors=sorted(self.pending))

    def cancel(self):
        self.cancelled.set()
        self.changed.set()
        with self.lock:
            if self.state['phase'] != 'cancelled':
                self.state['interrupted_phase'] = self.state['phase']
            self.state.update(phase='cancelled', active=False)

    def stage(self, phase, **extra):
        with self.lock:
            self.state.update(phase=phase, **extra)
        self.publish()

    def _check(self):
        if self.cancelled.is_set():
            raise PreflightCancelled()
        # Mechanical preparation never consumes samples: the pumps have to home,
        # compensate 360 deg and separate oil exactly once even if the detector
        # has no fresh frame yet. The formal acquisition gate is applied at the
        # hand-off in run(), where measurement really is required.
        self.check_active(False)
        with self.lock:
            if self.error:
                raise self.error
            if self.travel:
                # 0xCC has no validity bit or source timestamp. Firmware can
                # repeatedly emit last_valid even with an invalid sensor.
                self.angles([self.travel['axis']], require_health=True)
                now = time.monotonic()
                if now - self.travel['at'] > self.ANGLE_TIMEOUT:
                    raise PreflightError('controller_fault', 'Oil pump angle feedback timeout')
                # PID may reverse or hold near the target while settling. The
                # move deadline and real PID_DONE still gate completion there.
                if abs(self.travel['target_degrees'] - self.travel['degrees']) <= self.PID_SETTLE_ZONE_DEG:
                    self.travel['settling'] = True
                if not self.travel.get('settling', False) and now - self.travel['progress_at'] > self.PROGRESS_TIMEOUT:
                    raise PreflightError('controller_fault', 'Oil pump has no verified forward progress')

    def _wait(self, ready, timeout, reason, message):
        deadline = time.monotonic() + timeout
        while True:
            self._check()
            with self.lock:
                if ready():
                    return
            if time.monotonic() >= deadline:
                raise PreflightError(reason, message)
            self.changed.wait(min(0.05, max(0.0, deadline - time.monotonic())))
            self.changed.clear()

    def command(self, command, ack='CMD_OK', before_send=None):
        self._check()
        with self.lock:
            self.ack, self.ack_received = ack, False
        try:
            sent = self.send(command, before_send) if before_send else self.send(command)
            if not sent:
                raise PreflightError('controller_fault', 'Preflight serial write failed')
            self._wait(lambda: self.ack_received, self.ACK_TIMEOUT,
                       'controller_fault', 'Preflight command ACK timeout')
            return self.ack_text
        finally:
            with self.lock:
                self.ack = None

    def notify_text(self, text):
        with self.lock:
            if not self.state['active'] or self.cancelled.is_set():
                return
            if self.ack is not None and (text == self.ack or
                    (self.ack == 'PIDPARAM:' and text.startswith(self.ack))):
                self.ack_received = True
                self.ack_text = text
            if text.startswith(('CMD_ERR:', 'BUSY:', 'PUMP_ERR:')):
                self.error = PreflightError('controller_fault', text)
            if text.startswith('PID_START:'):
                parts = text[len('PID_START:'):].split(',')
                axis = parts[0]
                if axis in self.expected:
                    values = dict(part.split('=', 1) for part in parts[1:] if '=' in part)
                    delta, direction = self.expected[axis]
                    try:
                        matches = (abs(float(values.get('delta', 'nan')) - delta) <= 0.051
                                   and values.get('dir') == direction)
                    except ValueError:
                        matches = False
                    if matches:
                        self.started.add(axis)
                    else:
                        self.error = PreflightError('controller_fault', 'Unexpected PID_START: ' + text)
            if text.startswith('PID_SETTLE:') and self.travel:
                axis = text[len('PID_SETTLE:'):].split(',')[0]
                if axis == self.travel['axis'] and axis in self.pending and axis in self.started:
                    self.travel['settling'] = True
            for prefix, reason in (('PID_DONE:', None), ('PID_TIMEOUT:', 'pid_timeout'),
                                   ('PID_FAIL:', 'pid_fail')):
                if text.startswith(prefix):
                    axis = text[len(prefix):].split(',')[0].split('=')[0].strip()
                    if axis in self.pending and axis in self.started:
                        if reason:
                            self.error = PreflightError(reason, text)
                        else:
                            self.pending.discard(axis)
            self.changed.set()

    def notify_angles(self, angles):
        with self.lock:
            if self.travel is None or not self.state['active']:
                return
            axis = self.travel['axis']
            now = time.monotonic()
            try:
                angle = float(angles[axis])
                gap = now - self.travel['at']
                if not math.isfinite(angle) or not 0 <= angle <= 360 or gap > self.ANGLE_TIMEOUT:
                    raise ValueError('Oil pump angle feedback is invalid or interrupted')
                delta = (angle - self.travel['angle'] + 180) % 360 - 180
                # Beyond this rate the single-turn stream cannot prove multi-turn travel.
                if abs(delta) > min(150.0, self.travel['rpm'] * 6 * gap + 5.0):
                    raise ValueError('Oil pump angle feedback jumped')
                self.travel['degrees'] += delta
                self.travel.update(angle=angle, at=now)
                if abs(self.travel['target_degrees'] - self.travel['degrees']) <= self.PID_SETTLE_ZONE_DEG:
                    self.travel['settling'] = True
                # Net forward progress must exceed noise/quantization. Repeated
                # frames or oscillation around one angle cannot renew this clock.
                threshold = self.PID_PRECISION_DEG
                if self.travel['degrees'] >= self.travel['progress_degrees'] + threshold:
                    self.travel.update(progress_degrees=self.travel['degrees'], progress_at=now)
                self.state['travel_degrees'] = round(self.travel['degrees'], 2)
            except (KeyError, TypeError, ValueError) as exc:
                self.error = PreflightError('controller_fault', str(exc))
            self.changed.set()

    def pid_moves(self, moves, precision, before_send=None, timeout=None):
        with self.lock:
            self.expected = dict(moves)
            self.started = set()
            self.pending = set(moves)
        try:
            if moves:
                command = ''.join('%sE%sR%.3fP%g' % (axis, direction, delta, precision)
                                  for axis, (delta, direction) in moves.items())
                self.command(command, before_send=before_send)
                self._wait(lambda: not self.pending, self.PID_TIMEOUT if timeout is None else timeout,
                           'pid_timeout', 'Preflight PID completion timeout')
        finally:
            with self.lock:
                self.expected.clear()
                self.started.clear()
                self.pending.clear()

    def home(self, motors, zeros, precision):
        self.stage('homing', motors=motors)
        raw = self.angles(motors)
        moves = {}
        for axis in motors:
            delta = (zeros[axis] - raw[axis] + 180) % 360 - 180
            # Send even zero travel: require a real firmware completion for each pump.
            moves[axis] = (abs(delta), 'F' if delta >= 0 else 'B')
        self.pid_moves(moves, precision)

    def run(self, motors, zeros, policy, precision, injection):
        # Ordered firmware text processing drains previous command replies.
        pid_reply = self.command('PIDQUERY', 'PIDPARAM:')
        if policy['separation_turns'] > 0:
            try:
                _, _, _, min_rpm, max_rpm = map(float, pid_reply[len('PIDPARAM:'):].split(','))
                if not (0.1 <= min_rpm <= 5 and 1 <= max_rpm <= 20 and min_rpm <= max_rpm):
                    raise ValueError('Invalid PID speed limits')
            except (TypeError, ValueError) as exc:
                raise PreflightError('configuration_failed', 'Cannot use detector PID limits: %s' % exc)
        if injection:
            # Managed acquisition owns injection from this point. Confirm OFF
            # before preparation even if a previous/manual output was left on.
            self.command('PUMP:OFF', 'PUMP_OK:OFF')
        self.stage('checking', oil_axis=policy['oil_axis'])
        self.home(motors, zeros, precision)
        self.stage('compensating')
        self.pid_moves({axis: (360.0, 'F') for axis in motors}, precision)
        if policy['separation_turns'] > 0:
            axis = policy['oil_axis']
            goal = policy['separation_turns'] * 360.0
            # R uses the device PID speed limits, not the legacy fixed-J rpm.
            # Firmware unwraps multi-turn travel and accepts up to 3600 degrees.
            tolerance = min(self.SEPARATION_TOLERANCE_DEG, goal * 0.05)
            self.stage('separating', target_degrees=goal, travel_degrees=0.0)
            def arm_travel():
                # Called under the control lock at the actual serial write.
                raw = self.angles([axis], require_health=True)
                with self.lock:
                    now = time.monotonic()
                    self.travel = {'axis': axis, 'angle': raw[axis], 'at': now,
                                   'degrees': 0.0, 'rpm': max_rpm,
                                   'target_degrees': goal, 'tolerance': tolerance,
                                   'settling': goal <= self.PID_SETTLE_ZONE_DEG,
                                   'progress_at': now, 'progress_degrees': 0.0}
            try:
                self.pid_moves({axis: (goal, 'F')}, precision,
                               before_send=arm_travel, timeout=self.PID_TIMEOUT)
                # PID_DONE is required above. Also allow the angle stream
                # up to one second to catch up with the final PID packet.
                self._wait(lambda: self.travel['degrees'] >= goal - tolerance,
                           self.ANGLE_TIMEOUT, 'controller_fault',
                           'Oil pump PID completed without verified separation travel')
            finally:
                with self.lock:
                    self.travel = None
        self.stage('injection')
        if injection:
            speed, lead = injection
            self.command('PUMP:SET:%d' % speed, 'PUMP_OK:SET=%d,ON' % speed)
            deadline = time.monotonic() + lead
            while time.monotonic() < deadline:
                self._check()
                self.changed.wait(min(0.05, max(0.0, deadline - time.monotonic())))
                self.changed.clear()
        self._check()
        # Last gate before formal acquisition: real data and a baseline for the
        # current session. Nothing above this point may depend on measurement.
        self.stage('verifying')
        self.check_active(True)
        self.stage('complete')
