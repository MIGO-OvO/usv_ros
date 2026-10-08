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
    """Legacy files explicitly containing offsets retain their saved zero points."""
    offsets = data.get('offsets', {})
    configured = data.get('configured_axes', list(offsets))
    result = {}
    for axis in configured:
        if axis not in AXES or axis not in offsets or isinstance(offsets[axis], bool):
            raise ValueError('Invalid saved relative zero for %s' % axis)
        value = float(offsets[axis])
        if not math.isfinite(value) or not 0 <= value < 360:
            raise ValueError('Relative zero for %s must be in [0, 360)' % axis)
        result[axis] = value
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


class PreflightError(Exception):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


class PreflightCancelled(Exception):
    pass


class AutomationPreflight:
    ACK_TIMEOUT = 2.0
    PID_TIMEOUT = 60.0
    ANGLE_TIMEOUT = 1.0

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
        self.error = None
        self.travel = None

    def snapshot(self):
        with self.lock:
            return dict(self.state, pending_motors=sorted(self.pending))

    def cancel(self):
        self.cancelled.set()
        self.changed.set()
        with self.lock:
            self.state.update(phase='cancelled', active=False)

    def stage(self, phase, **extra):
        with self.lock:
            self.state.update(phase=phase, **extra)
        self.publish()

    def _check(self):
        if self.cancelled.is_set():
            raise PreflightCancelled()
        self.check_active()
        with self.lock:
            if self.error:
                raise self.error

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
                self.state['travel_degrees'] = round(self.travel['degrees'], 2)
            except (KeyError, TypeError, ValueError) as exc:
                self.error = PreflightError('controller_fault', str(exc))
            self.changed.set()

    def pid_moves(self, moves, precision):
        with self.lock:
            self.expected = dict(moves)
            self.started = set()
            self.pending = set(moves)
        try:
            if moves:
                command = ''.join('%sE%sR%.3fP%g' % (axis, direction, delta, precision)
                                  for axis, (delta, direction) in moves.items())
                self.command(command)
                self._wait(lambda: not self.pending, self.PID_TIMEOUT,
                           'pid_timeout', 'Preflight PID completion timeout')
        finally:
            with self.lock:
                self.expected.clear()
                self.started.clear()
                self.pending.clear()

    def run(self, motors, zeros, policy, precision, injection):
        # Ordered firmware text processing drains previous command replies.
        self.command('PIDQUERY', 'PIDPARAM:')
        self.stage('homing', motors=motors, oil_axis=policy['oil_axis'])
        raw = self.angles(motors)
        moves = {}
        for axis in motors:
            delta = (zeros[axis] - raw[axis] + 180) % 360 - 180
            # Send even zero travel: require a real firmware completion for each pump.
            moves[axis] = (abs(delta), 'F' if delta >= 0 else 'B')
        self.pid_moves(moves, precision)
        self.stage('compensating')
        self.pid_moves({axis: (360.0, 'F') for axis in motors}, precision)
        if policy['separation_turns'] > 0:
            axis = policy['oil_axis']
            goal = policy['separation_turns'] * 360.0
            rpm = policy['separation_rpm']
            self.stage('separating', target_degrees=goal, travel_degrees=0.0)
            # Drain preceding angle/reply packets before taking the baseline.
            self.command('PIDQUERY', 'PIDPARAM:')
            def arm_travel():
                # The sender calls this under the control lock immediately
                # before writing. Waiting for that lock cannot count old travel.
                raw = self.angles([axis])
                with self.lock:
                    self.travel = {'axis': axis, 'angle': raw[axis], 'at': time.monotonic(),
                                   'degrees': 0.0, 'rpm': rpm}
            try:
                self.command('%sEFV%gJ%.3f' % (axis, rpm, goal), before_send=arm_travel)
                def completed():
                    self.angles([axis])  # Validate freshness even when the target has been reached.
                    if time.monotonic() - self.travel['at'] > self.ANGLE_TIMEOUT:
                        raise PreflightError('controller_fault', 'Oil pump angle feedback timeout')
                    return self.travel['degrees'] >= goal - precision
                self._wait(completed, goal / (rpm * 6.0) * 1.5 + 2.0,
                           'controller_fault', 'Oil pump did not complete separation travel')
                self.command('%sDFV0J0' % axis)
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
        self.stage('complete')
