"""A ready fake detector, using the production raw/average/reference handlers."""
import time
from types import SimpleNamespace


def fresh_measurement(node):
    frame = {'valid': True, 'voltage': 1.2, 'timestamp_ms': int(time.monotonic() * 1000)}
    node._on_spectro_received(frame)
    node._emit_spectro_average([frame])


def prepare_measurement(node):
    if node.serial_conn is None:
        node.serial_conn = SimpleNamespace(is_open=True, close=lambda: None)
    node.spectro_config['enabled'] = True
    node._spectro_config_state = 'ready'
    node._spectro_i2c_verified = True
    acknowledge(node, 'ADSSTART')
    fresh_measurement(node)
    assert node._set_spectro_reference_voltage(1.3)[0]


def acknowledge(node, command):
    """Deliver an ACK to the actual active transaction, not an unsolicited line."""
    original = node.send_command
    reply = 'ADS_OK:START' if command == 'ADSSTART' else 'ADS_OK:STOP'
    node.send_command = lambda _command: node._on_text_received(reply) or True
    try:
        assert node._send_and_wait_text(command, (reply,), ('ADS_ERR:',))[0]
    finally:
        node.send_command = original
