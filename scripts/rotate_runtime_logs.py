#!/usr/bin/env python3
"""Opt-in rotation of the three launcher logs; dry-run unless --rotate."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

LOG_NAMES = ('roscore.log', 'usv_system.log', 'mavlink_router.log')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rotate', action='store_true', help='apply size-based rotation (may expire old rotations)')
    parser.add_argument('--log-dir', type=Path,
                        default=Path(__file__).resolve().parents[3] / '.usv_run' / 'logs')
    args = parser.parse_args(argv)
    directory = args.log_dir.resolve()
    if not directory.is_dir():
        parser.error('log directory does not exist: ' + str(directory))
    executable = shutil.which('logrotate')
    if not executable:
        parser.error('logrotate is required; no logs have been changed')
    targets = [directory / name for name in LOG_NAMES]
    state = directory / '.logrotate.state'
    if any(path.is_symlink() for path in targets + [state]):
        parser.error('refusing symlinked log or state file')
    if any(c in str(directory) for c in ('"', '\n', '\r', '\\')):
        parser.error('unsupported log directory characters')
    config = ' '.join('"' + str(path) + '"' for path in targets) + ''' {
    size 10M
    rotate 5
    missingok
    notifempty
    copytruncate
    compress
    delaycompress
}
'''
    # copytruncate preserves the launcher's open append descriptor. Its small
    # copy/truncate race can lose log lines; it is not an evidence archive.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.conf', delete=False) as file:
        file.write(config)
        config_path = file.name
    try:
        options = ['--verbose'] if args.rotate else ['--debug']
        return subprocess.run([executable, *options, '--state', str(state), config_path], check=False).returncode
    finally:
        os.unlink(config_path)


if __name__ == '__main__':
    raise SystemExit(main())
