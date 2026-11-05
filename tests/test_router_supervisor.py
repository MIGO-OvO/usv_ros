"""Exercise real process exit/restart/stop under Linux without a flight controller."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(os.name == 'posix', 'Linux process supervision')
class RouterSupervisorTests(unittest.TestCase):
    def wait_for(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail('router supervisor deadline expired')

    def start(self, directory, code):
        supervisor = Path(__file__).resolve().parents[1] / 'scripts/mavlink-router-supervisor.sh'
        log = open(directory / 'log', 'w')
        self.addCleanup(log.close)
        process = subprocess.Popen(['bash', str(supervisor), sys.executable, '-c', code, str(directory)],
                                   stdout=log, stderr=subprocess.STDOUT)
        def cleanup():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
        self.addCleanup(cleanup)
        return process

    def test_router_exit_restarts_and_operator_stop_reaps_the_running_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            process = self.start(directory, '''
import os, pathlib, sys, time
root = pathlib.Path(sys.argv[1])
counter = root / 'starts'
count = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(count))
if count == 1:
    sys.exit(7)
(root / 'child').write_text(str(os.getpid()))
time.sleep(60)
''')
            self.wait_for(lambda: (directory / 'child').exists())
            self.assertEqual((directory / 'starts').read_text(), '2')
            child = int((directory / 'child').read_text())
            process.terminate()
            self.assertEqual(process.wait(timeout=3), 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(child, 0)

    def test_operator_stop_during_backoff_does_not_resurrect_router(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            process = self.start(directory, '''
import pathlib, sys
root = pathlib.Path(sys.argv[1])
with (root / 'starts').open('a') as stream:
    stream.write('started\\n')
sys.exit(1)
''')
            self.wait_for(lambda: (directory / 'log').exists() and
                          'retry in' in (directory / 'log').read_text())
            process.terminate()
            self.assertEqual(process.wait(timeout=3), 0)
            time.sleep(2.2)
            self.assertEqual((directory / 'starts').read_text(), 'started\n')


if __name__ == '__main__':
    unittest.main()
