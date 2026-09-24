"""Identical golden serial bytes through both production parsers, without ports."""
import importlib.util
import struct
import unittest
from pathlib import Path

from test_hardware_runtime_sync import _load_script


def spectro_frame(status):
    body = struct.pack('<BIBBif', 0xDD, 123456, 2, status, -654321, 1.2345)
    checksum = 0
    for byte in body:
        checksum ^= byte
    return b'\x55' + body + bytes([checksum, 0x0A])


class DetectorProtocolContractTests(unittest.TestCase):
    def test_ros_and_desktop_agree_on_real_test_and_corrupt_frames(self):
        desktop_path = Path(__file__).resolve().parents[3] / 'MotorControlApp_Pyside6/src/hardware/serial_reader.py'
        if not desktop_path.exists() or importlib.util.find_spec('PySide6') is None:
            self.skipTest('cross-repository comparison requires desktop checkout and PySide6')
        module, _, _ = _load_script('pump_golden_frames', 'scripts/pump_control_node.py')
        spec = importlib.util.spec_from_file_location('desktop_golden_frames', desktop_path)
        desktop = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(desktop)
        ros_reader = module.PumpSerialReader(None)
        desktop_reader = desktop.SerialReader(None)
        ros_packets, desktop_packets = [], []
        ros_reader.on_spectro_received = ros_packets.append
        desktop_reader.spectro_packet_received.connect(desktop_packets.append)
        invalid = bytearray(spectro_frame(1))
        invalid[-2] ^= 0x80
        stream = (b'ADS_OK:START\n' + spectro_frame(1) + bytes(invalid)
                  + spectro_frame(0x10) + spectro_frame(0x11) + spectro_frame(0x03))
        for offset in range(0, len(stream), 7):
            ros_reader._process_data(stream[offset:offset + 7])
            desktop_reader._process_data(stream[offset:offset + 7])
        self.assertEqual(len(ros_packets), 4)
        self.assertEqual(len(desktop_packets), 4)
        for ros, host in zip(ros_packets, desktop_packets):
            for field in ('timestamp_ms', 'tca_channel', 'status', 'raw_code', 'voltage', 'valid', 'simulated'):
                self.assertEqual(ros[field], host[field])
        self.assertEqual([p['valid'] for p in ros_packets], [True, False, False, False])


if __name__ == '__main__':
    unittest.main()
