#!/usr/bin/env python3
"""USV log bundles, usage and retention (Python 3.8+)."""
import argparse
import fcntl
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import uuid

import rotate_runtime_logs

MIB = 1024 * 1024
MARKER = '# usv-log-retention'


def files(root):
    if not root.exists():
        return []
    result = []
    for directory, dirs, names in os.walk(str(root), followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
        result.extend(Path(directory) / n for n in names
                      if not (Path(directory) / n).is_symlink()
                      and (Path(directory) / n).is_file())
    return result


def size(root):
    return sum(p.stat().st_size for p in files(root))


def sessions(root):
    result = []
    if root.exists():
        for path in root.iterdir():
            try:
                uuid.UUID(path.name)
            except ValueError:
                continue
            if path.is_dir() and not path.is_symlink():
                result.append(path)
    return sorted(result, key=lambda p: p.stat().st_mtime, reverse=True)


def bundle(runtime, ros, output, name, count):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', name):
        raise ValueError('包名只允许字母、数字、下划线、点和短横线，最多 80 字符')
    output.mkdir(parents=True, exist_ok=True)
    target = output / (name + '.tar.gz')
    if shutil.disk_usage(str(output)).free < 128 * MIB:
        raise ValueError('剩余空间不足 128 MiB，请先清理日志')
    runtime_names = rotate_runtime_logs.LOG_NAMES + tuple(n + '.1' for n in rotate_runtime_logs.LOG_NAMES)
    candidates = [(p, 'runtime/' + p.name) for p in files(runtime)
                  if p.parent == runtime and p.name in runtime_names]
    for session in sessions(ros)[:count]:
        candidates.extend((p, 'ros/' + str(p.relative_to(ros))) for p in files(session)
                          if p.name.endswith(('.log', '.log.1')))
    # ROS also writes roslaunch logs directly in ROS_LOG_DIR.
    candidates.extend((p, 'ros/' + p.name) for p in sorted(
        [p for p in files(ros) if p.parent == ros and p.name.endswith(('.log', '.log.1'))],
        key=lambda p: p.stat().st_mtime, reverse=True)[:count])
    if not candidates:
        raise ValueError('没有找到日志')
    remaining = 64 * MIB
    manifest = []
    # Exclusive creation keeps a repeated bundle name from overwriting evidence.
    with target.open('xb') as destination:
        try:
            os.chmod(str(target), 0o600)
            with tarfile.open(fileobj=destination, mode='w:gz') as archive:
                for path, arcname in candidates:
                    with path.open('rb') as source:
                        length = os.fstat(source.fileno()).st_size
                        take = min(length, 2 * MIB, remaining)
                        source.seek(max(0, length - take))
                        data = source.read(take)
                    manifest.append({'path': arcname, 'source_bytes': length,
                                     'packed_bytes': len(data), 'tail_only': take < length})
                    if take:
                        info = tarfile.TarInfo(arcname)
                        info.size = len(data)
                        archive.addfile(info, io.BytesIO(data))
                        remaining -= len(data)
                data = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
                info = tarfile.TarInfo('manifest.json')
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        except BaseException:
            target.unlink()
            raise
    print(target)
    return target


def open_paths():
    """Return None when an own process cannot be inspected: retain ROS sessions."""
    result = set()
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            for fd in (process / 'fd').iterdir():
                try:
                    result.add(fd.resolve(strict=True))
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
        except PermissionError:
            print('WARNING: 无法检查进程占用，本次跳过过期日志删除', file=sys.stderr)
            return None
    return result


def cleanup(ros, output, keep, budget, apply):
    print('保留规则: 每类最近 %d 份、使用中及最近一小时日志优先；容量预算仅告警' % keep)
    opened = open_paths()
    latest = (ros / 'latest').resolve()
    root_logs = sorted([p for p in files(ros) if p.parent == ros
                        and re.search(r'\.log(?:\.[1-5](?:\.gz)?)?$', p.name)],
                       key=lambda p: p.stat().st_mtime, reverse=True)
    groups = [(sessions(ros), ros), (root_logs, ros)]
    archives = sorted([p for p in files(output) if p.parent == output
                       and p.name.endswith('.tar.gz')],
                      key=lambda p: p.stat().st_mtime, reverse=True)
    groups.append((archives, output))
    projected = {ros: size(ros), output: size(output)}
    for entries, parent in groups:
        total = projected[parent]
        for index in range(len(entries) - 1, -1, -1):
            path = entries[index]
            members = files(path) if path.is_dir() else [path]
            amount = sum(p.stat().st_size for p in members)
            if index < keep:
                continue
            if (path == latest or opened is None
                    or any(p.resolve() in (opened or set()) for p in members)
                    or any(p.stat().st_mtime > time.time() - 3600 for p in members)):
                print('保留使用中/最近一小时日志:', path)
                continue
            print(('删除: ' if apply else '将删除: ') + str(path) +
                  ' (超出最近 %d 份，%.2f MiB)' % (keep, amount / MIB))
            if apply:
                # Only direct UUID sessions, log files or bundles under the selected root.
                if path.is_symlink() or path.resolve().parent != parent:
                    raise ValueError('日志路径越界: ' + str(path))
                if path.is_dir():
                    shutil.rmtree(str(path))
                else:
                    path.unlink()
            total -= amount
        projected[parent] = total
    for parent, limit in ((ros, budget * MIB), (output, 256 * MIB)):
        total = projected[parent]
        if total > limit:
            print('WARNING: %s 保留日志 %.1f MiB 超过预算 %.1f MiB；保留最近 %d 份及活动/一小时保护' %
                  (parent, total / MIB, limit / MIB, keep), file=sys.stderr)


def automatic(action, runtime, ros, output):
    env = dict(os.environ, LC_ALL='C')
    current = subprocess.run(['crontab', '-l'], capture_output=True, text=True, env=env)
    if current.returncode and 'no crontab for' not in current.stderr:
        raise RuntimeError(current.stderr.strip() or '无法读取 crontab')
    lines = current.stdout.splitlines() if current.returncode == 0 else []
    if action == 'status':
        print('\n'.join(line for line in lines if line.endswith(MARKER)) or '自动清理未启用')
        return
    lines = [line for line in lines if not line.endswith(MARKER)]
    if action == 'on':
        if not shutil.which('logrotate'):
            raise ValueError('请先安装 logrotate: sudo apt install logrotate')
        command = shlex.join([sys.executable, str(Path(__file__).resolve()),
                              '--runtime-dir', str(runtime), '--ros-dir', str(ros),
                              '--output-dir', str(output), 'maintain'])
        if any(c in command for c in ('\n', '\r', '%')):
            raise ValueError('定时任务路径不能包含换行或百分号')
        lines.append('*/10 * * * * PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin '
                     + command + ' 2>&1 | /usr/bin/logger -t usv-logs ' + MARKER)
    subprocess.run(['crontab', '-'], input='\n'.join(lines) + '\n', text=True, check=True)
    print('自动清理: ' + action + '（需要 cron 服务运行；每 10 分钟检查）')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    workspace = Path(__file__).resolve().parents[3]
    parser.add_argument('--runtime-dir', type=Path, default=workspace / '.usv_run/logs')
    parser.add_argument('--ros-dir', type=Path, default=Path(os.environ.get(
        'ROS_LOG_DIR', str(Path(os.environ.get('ROS_HOME', str(Path.home() / '.ros'))) / 'log'))))
    parser.add_argument('--output-dir', type=Path, default=workspace / '.usv_run/bundles')
    sub = parser.add_subparsers(dest='command', required=True)
    pack = sub.add_parser('pack', help='最近 3 个会话；每文件末尾 2 MiB，总计最多 64 MiB')
    pack.add_argument('name', nargs='?', default=time.strftime('usv-logs-%Y%m%d-%H%M%S'))
    pack.add_argument('--count', type=int, default=3)
    sub.add_parser('usage')
    clean = sub.add_parser('clean', help='预览过期日志；--apply 才删除')
    clean.add_argument('--apply', action='store_true')
    clean.add_argument('--keep', type=int, default=3, help='每类至少保留最近 N 份，优先于容量预算')
    clean.add_argument('--max-mib', type=int, default=512, help='ROS 容量告警预算；不突破保留数量或活动保护')
    auto = sub.add_parser('auto')
    auto.add_argument('action', choices=['on', 'off', 'status'])
    sub.add_parser('maintain', help='定时任务入口：轮转并清理')
    args = parser.parse_args(argv)
    if any(getattr(args, key, 1) < 1 for key in ('count', 'keep', 'max_mib')):
        parser.error('数量与预算必须大于零')
    runtime, ros, output = (p.absolute() for p in (args.runtime_dir, args.ros_dir, args.output_dir))
    for path in (runtime, ros, output):
        if path.resolve() != path or path == Path(path.anchor) or path == Path.home():
            parser.error('日志根目录不能是符号链接、磁盘根目录或用户主目录')
    if any(a == b or a in b.parents or b in a.parents
           for a, b in ((runtime, ros), (runtime, output), (ros, output))):
        parser.error('日志与归档目录不能重叠')
    if args.command == 'usage':
        for label, path in [('启动日志', runtime), ('ROS 日志', ros), ('日志包', output)]:
            print('%s: %.1f MiB (%s)' % (label, size(path) / MIB, path))
        print('工作区剩余: %.1f MiB' % (shutil.disk_usage(str(workspace)).free / MIB))
        return 0
    if args.command == 'auto':
        automatic(args.action, runtime, ros, output)
        return 0
    output.mkdir(parents=True, exist_ok=True)
    lock = output / '.usv-logs.lock'
    if lock.is_symlink():
        raise ValueError('锁文件不能是符号链接')
    with lock.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == 'pack':
            bundle(runtime, ros, output, args.name, args.count)
        else:
            runtime.mkdir(parents=True, exist_ok=True)
            apply = args.command == 'maintain' or args.apply
            options = ['--log-dir', str(runtime), '--ros-log-dir', str(ros)]
            result = rotate_runtime_logs.main(options + (['--rotate'] if apply else []))
            if result:
                return result
            cleanup(ros, output, getattr(args, 'keep', 3), getattr(args, 'max_mib', 512), apply)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print('usvlogs: ' + str(error), file=sys.stderr)
        sys.exit(1)
