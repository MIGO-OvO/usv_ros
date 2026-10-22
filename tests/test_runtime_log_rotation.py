"""Linux test uses real logrotate and an open launcher-style append fd."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('rotate_runtime_logs',
        Path(__file__).resolve().parents[1] / 'scripts' / 'rotate_runtime_logs.py')
rotation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rotation)


@unittest.skipUnless(shutil.which('logrotate'), 'Linux logrotate required')
class RuntimeRotationTests(unittest.TestCase):
    def test_ros_log_rotation_keeps_append_descriptor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ros = root / 'ros'
            ros.mkdir()
            log = ros / 'node.log'
            with log.open('ab') as stream:
                stream.write(b'x' * (11 * 1024 * 1024))
                stream.flush()
                self.assertEqual(rotation.main(['--log-dir', folder, '--ros-log-dir', str(ros), '--rotate']), 0)
                stream.write(b'after\n')
            self.assertEqual(log.read_bytes(), b'after\n')
            self.assertTrue((ros / 'node.log.1').exists())

    def test_dry_run_and_append_descriptor_survive_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log = root / 'usv_system.log'
            evidence = root / 'evidence.txt'
            evidence.write_text('preserve')
            with log.open('ab') as stream:
                stream.write(b'x' * (11 * 1024 * 1024))
                stream.flush()
                size = log.stat().st_size
                self.assertEqual(rotation.main(['--log-dir', folder]), 0)
                self.assertEqual(log.stat().st_size, size)
                self.assertFalse((root / '.logrotate.state').exists())
                self.assertEqual(rotation.main(['--log-dir', folder, '--rotate']), 0)
                stream.write(b'after rotation\n')
                stream.flush()
            self.assertEqual(log.read_bytes(), b'after rotation\n')
            self.assertEqual((root / 'usv_system.log.1').stat().st_size, size)
            self.assertEqual(evidence.read_text(), 'preserve')


if __name__ == '__main__':
    unittest.main()
