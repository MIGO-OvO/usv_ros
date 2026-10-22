"""Run on Linux: python3 -m unittest discover -s tests -p test_usv_logs.py."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import uuid

if sys.platform != 'linux':
    raise unittest.SkipTest('Linux /proc and fcntl required')

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import usv_logs as logs


class LogTests(unittest.TestCase):
    def test_cli_alias_and_path_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = Path(logs.__file__).with_name('usvctl.sh')
            alias = root / 'usvlogs'
            alias.symlink_to(script)
            result = subprocess.run(['bash', str(alias), '--help'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('maintain', result.stdout)
            runtime = root / 'runtime'
            runtime.mkdir()
            link = root / 'link'
            link.symlink_to(runtime)
            with self.assertRaises(SystemExit):
                logs.main(['--runtime-dir', str(link), 'clean', '--apply'])
            self.assertFalse((runtime / '.logrotate.state').exists())

    def test_bundle_recent_tail_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime, ros, output = [root / n for n in ('runtime', 'ros', 'bundles')]
            runtime.mkdir()
            ros.mkdir()
            (runtime / 'usv_system.log').write_bytes(b'x' * (3 * logs.MIB))
            for index in range(5):
                session = ros / str(uuid.uuid4())
                session.mkdir()
                (session / 'node.log').write_text(str(index))
                os.utime(session, (index, index))
            (runtime / 'secret.log').write_text('not a launcher log')
            (runtime / 'roscore.log').symlink_to(runtime / 'secret.log')
            target = logs.bundle(runtime, ros, output, 'test', 3)
            with tarfile.open(target) as archive:
                manifest = json.load(archive.extractfile('manifest.json'))
                self.assertEqual(len(manifest), 4)
                self.assertEqual(manifest[0]['packed_bytes'], 2 * logs.MIB)
                self.assertTrue(manifest[0]['tail_only'])
                self.assertEqual([archive.extractfile(m['path']).read().decode()
                                  for m in manifest[1:]], ['4', '3', '2'])
            with self.assertRaises(FileExistsError):
                logs.bundle(runtime, ros, output, 'test', 3)
            with self.assertRaises(ValueError):
                logs.bundle(runtime, ros, output, '../escape', 3)

    def test_bundle_total_cap_rotation_tail_and_disk_full(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime, ros, output = [root / n for n in ('runtime', 'ros', 'bundles')]
            runtime.mkdir()
            session = ros / str(uuid.uuid4())
            session.mkdir(parents=True)
            (runtime / 'roscore.log.1').write_bytes(b'previous')
            for index in range(33):
                (session / ('node-%s.log' % index)).write_bytes(b'x' * 2 * logs.MIB)
            target = logs.bundle(runtime, ros, output, 'capped', 3)
            with tarfile.open(target) as archive:
                manifest = json.load(archive.extractfile('manifest.json'))
                self.assertEqual(sum(m['packed_bytes'] for m in manifest), 64 * logs.MIB)
                self.assertEqual(archive.extractfile('runtime/roscore.log.1').read(), b'previous')
                self.assertTrue(any(m['packed_bytes'] == 0 for m in manifest))
            usage = logs.shutil.disk_usage(str(output))
            with patch.object(logs.shutil, 'disk_usage', return_value=usage._replace(free=0)):
                with self.assertRaises(ValueError):
                    logs.bundle(runtime, ros, output, 'no-space', 3)
            self.assertFalse((output / 'no-space.tar.gz').exists())

    def test_cleanup_preview_budget_active_and_outside_link(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ros, output = root / 'ros', root / 'bundles'
            ros.mkdir()
            output.mkdir()
            outside = root / 'evidence'
            outside.write_text('keep')
            sessions = []
            for index in range(5):
                session = ros / str(uuid.uuid4())
                session.mkdir()
                (session / 'node.log').write_bytes(b'x' * logs.MIB)
                os.utime(session / 'node.log', (1, 1))
                os.utime(session, (index, index))
                sessions.append(session)
            (ros / 'latest').symlink_to(sessions[0])
            (sessions[1] / 'outside').symlink_to(outside)
            (output / 'old.tar.gz').write_bytes(b'old')
            (output / 'new.tar.gz').write_bytes(b'new')
            (ros / 'roslaunch-old.log').write_bytes(b'old root log')
            os.utime(ros / 'roslaunch-old.log', (1, 1))
            os.utime(output / 'old.tar.gz', (1, 1))
            with patch.object(logs, 'open_paths', return_value={(sessions[2] / 'node.log').resolve()}):
                logs.cleanup(ros, output, 1, 1, False)
                self.assertTrue(all(p.exists() for p in sessions))
                logs.cleanup(ros, output, 1, 1, True)
                self.assertTrue(sessions[0].exists())
                self.assertTrue(sessions[2].exists())
                self.assertFalse(sessions[1].exists())
            self.assertEqual(outside.read_text(), 'keep')
            self.assertFalse((output / 'old.tar.gz').exists())
            self.assertTrue((output / 'new.tar.gz').exists())
            self.assertFalse((ros / 'roslaunch-old.log').exists())

    def test_unreadable_proc_preserves_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            ros = Path(folder)
            session = ros / str(uuid.uuid4())
            session.mkdir()
            (session / 'node.log').write_bytes(b'x' * 2 * logs.MIB)
            os.utime(session / 'node.log', (1, 1))
            with patch.object(logs, 'open_paths', return_value=None):
                logs.cleanup(ros, ros / 'bundles', 1, 1, True)
            self.assertTrue(session.exists())

    def test_cron_preserves_unrelated_jobs_and_uses_absolute_paths(self):
        current = subprocess.CompletedProcess([], 0, '0 1 * * * unrelated\n', '')
        with patch.object(logs.subprocess, 'run', return_value=current) as run, \
                patch.object(logs.shutil, 'which', return_value='/usr/sbin/logrotate'):
            logs.automatic('on', Path('/tmp/runtime'), Path('/tmp/ros'), Path('/tmp/bundles'))
            content = run.call_args.kwargs['input']
            self.assertIn('0 1 * * * unrelated\n', content)
            self.assertIn('*/10 * * * *', content)
            self.assertIn('--ros-dir /tmp/ros', content)
            current.stdout = content
            logs.automatic('off', Path('/tmp/runtime'), Path('/tmp/ros'), Path('/tmp/bundles'))
            self.assertEqual(run.call_args.kwargs['input'], '0 1 * * * unrelated\n')


if __name__ == '__main__':
    unittest.main()
