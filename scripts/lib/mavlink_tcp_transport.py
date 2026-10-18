"""One socket writer: complete a frame or fail and retire that TCP session."""
import errno
import ipaddress
import select
import socket
import time


def connect_router(mavutil, url, source_system, source_component):
    """Bound connect, too: numeric IPv4 avoids unbounded DNS on the control loop."""
    if not url.startswith('tcp:'):
        raise ValueError('Router endpoint must be tcp:<numeric IPv4>:<port>')
    host, port = url[4:].rsplit(':', 1)
    ipaddress.IPv4Address(host)
    if not 1 <= int(port) <= 65535:
        raise ValueError('Invalid router port')

    class RouterTCP(mavutil.mavtcp):
        def do_connect(self):
            self.port = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self.port.settimeout(0.25)
                self.port.connect(self.destination_addr)
                self.port.setblocking(False)
                self.port.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                self.port.close()
                raise

    connection = RouterTCP(url[4:], source_system=source_system,
                           source_component=source_component, autoreconnect=False, retries=1)
    # Extensions (STATUSTEXT chunks/ACK target) are mandatory, even before FCU
    # heartbeat discovery. Do not let the first incoming v1 frame downgrade us.
    from pymavlink.dialects.v20 import ardupilotmega
    connection.mav = ardupilotmega.MAVLink(connection, srcSystem=source_system, srcComponent=source_component)
    connection.mav.robust_parsing = True
    connection.WIRE_PROTOCOL_VERSION = '2.0'
    connection.first_byte = False
    return connection


class MAVLinkTCPTransport:
    def __init__(self, port, counters):
        self.port = port
        self.counters = counters
        self.deadline = None
        self.last_write_at = None

    def begin_cycle(self, budget=0.03):
        # One budget for the entire batch, not 24 independent timeouts.
        self.deadline = time.monotonic() + budget

    def write(self, frame):
        self.counters['generated_frames'] += 1
        deadline = self.deadline if self.deadline is not None else time.monotonic() + 0.03
        offset = 0
        view = memoryview(frame)
        try:
            while offset < len(view):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('TCP frame write deadline')
                try:
                    written = self.port.send(view[offset:])
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                        raise
                    self.counters['would_block'] += 1
                    if not select.select([], [self.port], [], remaining)[1]:
                        raise TimeoutError('TCP backpressure deadline')
                    continue
                if not written:
                    raise ConnectionError('TCP zero write')
                if written < len(view) - offset:
                    self.counters['short_writes'] += 1
                offset += written
            self.counters['fully_written_frames'] += 1
            self.counters['fully_written_bytes'] += offset
            self.last_write_at = time.monotonic()
            return offset
        except (OSError, ValueError):
            self.counters['send_errors'] += 1
            self.port.close()  # Never put a new frame after an incomplete prefix.
            raise

    def recv(self, size):
        try:
            data = self.port.recv(size)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                return b''
            raise
        if not data:
            raise ConnectionError('TCP EOF')
        return data
