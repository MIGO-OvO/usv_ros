# -*- coding: utf-8 -*-
"""map_pack_journal: 下载任务的崩溃安全日志 (T3).

为 prewarm / export 任务持久化进度, 使其能在进程退出、网络抖动、
设备重启之后继续断点续传。

设计目标:
  - 只依赖标准库, 与 map_tile_cache / map_pack_format / map_tile_store 解耦,
    保证可以早于 T2/T5/T6 单独验证;
  - tile key 一律使用普通 ``(style, z, x, y)`` 元组, 不依赖 TileKey 类;
  - append-only done.log + atomic state.json + 跨平台 lock 文件;
  - load_done 必须容忍空行 / 尾部 garbage / 字段不全, 模拟 kill 半截写。

落盘布局::

    {root}/.journal/{job_id}/
        spec.json       不可变 JobSpec, create() 时一次写入
        state.json      JobState, 每次 write_state 通过 tmp + os.replace 重写
        done.log        append-only, 每行 "style,z,x,y"
        failures.log    append-only, 每行 "style,z,x,y,attempts,error"
        lock            进程锁, POSIX 上额外加 fcntl.flock

跨平台锁语义 (固定):
  - lock 文件正文为 ``"{pid}\n{ts}\n"``;
  - POSIX 优先 fcntl.flock(LOCK_EX | LOCK_NB), 拿不到立即 JournalLocked;
  - Windows / 无 fcntl 环境下走 PID-file 逻辑:
      * 已存在的 lock 文件 -> JournalLocked (除非 ``steal_stale=True``);
      * release_lock 删除 lock 文件;
  - ``steal_stale=True`` 允许显式接管已存在的 lock, 用于崩溃后启动恢复。

写入安全:
  - append_done / append_failure 每次都 ``f.flush()`` + ``os.fsync(fd)``,
    被 kill 时已写入的整行不会丢失;
  - load_done 按行解析, 任何不可解析的行都被静默跳过, 不影响断点续传。
"""

import json
import os
import sys
import tempfile
import time

# fcntl 仅在 POSIX 存在; Windows 上以 None 兜底, 测试在 PID-file 路径上跑。
try:  # pragma: no cover - 平台相关
    import fcntl  # type: ignore
    _HAS_FCNTL = True
except Exception:  # pragma: no cover - Windows / 无 fcntl 环境
    fcntl = None  # type: ignore
    _HAS_FCNTL = False


JOURNAL_DIRNAME = ".journal"

# 任务状态枚举: T5/T6 会通过 write_state(status=...) 切换。
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_STOPPED = "stopped"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# list_resumable 视为可继续的状态集合
_RESUMABLE_STATUSES = (STATUS_RUNNING, STATUS_PAUSED)


class JournalLocked(Exception):
    """已有活跃持有者占用同一 job_id 的 journal 目录。"""


def _now():
    """统一时间源, 方便测试时 monkeypatch (当前未做注入, 留作扩展点)。"""
    return time.time()


def _atomic_write_json(path, payload):
    """通过临时文件 + os.replace 实现原子重写。

    在 ``state.json`` 上使用, 任何崩溃要么读到旧内容、要么读到新内容,
    不会出现写到一半的损坏文件。
    """
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # 某些文件系统 (比如部分网络盘) 不支持 fsync, 不应阻断写入。
                pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _safe_append_line(path, line):
    """append-only + flush + fsync, 保证已写整行在 kill 后存活。"""
    if not line.endswith("\n"):
        line = line + "\n"
    with open(path, "ab") as f:
        f.write(line.encode("utf-8"))
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            # 不支持 fsync 的文件系统不阻断追加。
            pass


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_done_line(line):
    """解析一行 ``style,z,x,y``, 失败返回 None.

    EF-12 要求: 空行、空白行、字段不足、坐标非整数全部静默跳过,
    不抛异常, 不污染 done 集合。
    """
    if line is None:
        return None
    s = line.strip()
    if not s:
        return None
    parts = s.split(",")
    if len(parts) < 4:
        return None
    style = parts[0].strip()
    if not style:
        return None
    try:
        z = int(parts[1])
        x = int(parts[2])
        y = int(parts[3])
    except (ValueError, TypeError):
        return None
    return (style, z, x, y)


class Journal(object):
    """单个下载任务的日志句柄.

    生命周期:
      - ``create(root, spec)``: 落 spec/state, 取得 lock, 返回新实例;
      - ``open_existing(root, job_id)``: 重启后只读复盘, 不取 lock;
      - ``finalize(status)``: 写终态 + 释放 lock, **保留目录** (审计需要,
        清理是 T6 的事)。
    """

    def __init__(self, root, job_id):
        self._root = os.fspath(root)
        self._job_id = str(job_id)
        self._jdir = os.path.join(self._root, JOURNAL_DIRNAME, self._job_id)
        self._spec_path = os.path.join(self._jdir, "spec.json")
        self._state_path = os.path.join(self._jdir, "state.json")
        self._done_path = os.path.join(self._jdir, "done.log")
        self._failures_path = os.path.join(self._jdir, "failures.log")
        self._lock_path = os.path.join(self._jdir, "lock")
        self._lock_fd = None  # POSIX flock 的 fd, 跨平台逻辑也持有它
        self._has_lock = False

    # --- 路径属性 ----------------------------------------------------------

    @property
    def journal_dir(self):
        return self._jdir

    @property
    def job_id(self):
        return self._job_id

    # --- 工厂方法 ----------------------------------------------------------

    @classmethod
    def create(cls, root, spec, steal_stale=False):
        """创建并锁定新 journal.

        参数:
          - ``root``: 缓存根目录 (即 cache_dir);
          - ``spec``: dict, 至少含 ``job_id``;
          - ``steal_stale``: True 时允许接管已存在的 lock 文件 (例如启动期
            发现历史进程留下的 stale lock)。

        失败:
          - 同 job_id 已有持有者 -> ``JournalLocked``。
        """
        if not isinstance(spec, dict):
            raise TypeError("spec 必须是 dict")
        job_id = spec.get("job_id")
        if not job_id:
            raise ValueError("spec.job_id 不可为空")
        j = cls(root, job_id)
        os.makedirs(j._jdir, exist_ok=True)
        # 1) 取锁 (阻塞重复 create)
        j._acquire_lock(steal_stale=steal_stale)
        try:
            # 2) spec.json 仅在不存在时写入, 同 job_id 复跑保持原 spec
            if not os.path.isfile(j._spec_path):
                _atomic_write_json(j._spec_path, dict(spec))
            # 3) 初始化 state.json (若已有则不覆盖, 由调用方决定是否 reset)
            if not os.path.isfile(j._state_path):
                init_state = {
                    "job_id": job_id,
                    "status": STATUS_RUNNING,
                    "started_at": _now(),
                    "updated_at": _now(),
                }
                _atomic_write_json(j._state_path, init_state)
        except Exception:
            # 创建过程出错时释放锁, 避免遗留 stale lock
            try:
                j.release_lock()
            except Exception:
                pass
            raise
        return j

    @classmethod
    def open_existing(cls, root, job_id):
        """只读打开已存在的 journal, 不取锁, 用于启动期复盘。"""
        j = cls(root, job_id)
        if not os.path.isdir(j._jdir):
            raise FileNotFoundError(
                "journal 不存在: %s" % j._jdir)
        if not os.path.isfile(j._spec_path):
            raise FileNotFoundError(
                "spec.json 缺失: %s" % j._spec_path)
        return j

    @classmethod
    def list_resumable(cls, root):
        """列出根目录下所有 status in (running, paused) 的任务.

        不取锁, 启动期可安全调用; 单个 job 的元数据缺失不会影响其它 job。
        """
        root = os.fspath(root)
        base = os.path.join(root, JOURNAL_DIRNAME)
        if not os.path.isdir(base):
            return []
        out = []
        for name in sorted(os.listdir(base)):
            jdir = os.path.join(base, name)
            if not os.path.isdir(jdir):
                continue
            spec_path = os.path.join(jdir, "spec.json")
            state_path = os.path.join(jdir, "state.json")
            if not (os.path.isfile(spec_path) and os.path.isfile(state_path)):
                continue
            try:
                spec = _read_json(spec_path)
                state = _read_json(state_path)
            except Exception:
                # 损坏的 journal 跳过, 不影响其他 job 的恢复
                continue
            status = state.get("status") if isinstance(state, dict) else None
            if status not in _RESUMABLE_STATUSES:
                continue
            out.append({
                "job_id": name,
                "spec": spec,
                "state": state,
            })
        return out

    # --- 锁管理 ------------------------------------------------------------

    def _acquire_lock(self, steal_stale=False):
        """跨平台取锁.

        语义:
          - 已存在 lock 文件 -> JournalLocked, 除非 ``steal_stale=True``;
          - POSIX 上额外用 fcntl.flock 防止 PID 复用造成的误判;
          - 取锁成功后写入 ``"{pid}\n{ts}\n"`` 用于审计。
        """
        if self._has_lock:
            return
        if os.path.exists(self._lock_path) and not steal_stale:
            raise JournalLocked(
                "journal 已被占用: %s" % self._lock_path)
        # 直接 O_CREAT | O_RDWR 打开 (O_TRUNC 会清掉 steal_stale 的旧内容)
        flags = os.O_RDWR | os.O_CREAT
        try:
            fd = os.open(self._lock_path, flags, 0o644)
        except OSError as exc:
            raise JournalLocked(
                "无法打开 lock 文件 %s: %s" % (self._lock_path, exc))
        try:
            if _HAS_FCNTL:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    os.close(fd)
                    raise JournalLocked(
                        "fcntl.flock 拿锁失败: %s" % exc)
            # 覆盖式写入 PID + 时间戳
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                os.ftruncate(fd, 0)
            except OSError:
                # 某些平台 ftruncate 不可用, 忽略即可 (最坏情况是旧内容拼接)
                pass
            payload = ("%d\n%f\n" % (os.getpid(), _now())).encode("utf-8")
            os.write(fd, payload)
            try:
                os.fsync(fd)
            except OSError:
                pass
        except JournalLocked:
            raise
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        self._lock_fd = fd
        self._has_lock = True

    def release_lock(self):
        """释放锁: flock 解锁, 关闭 fd, 删除 lock 文件 (best-effort)。"""
        if not self._has_lock:
            # 即使未持有锁也尝试清理 fd, 防止外部直接修改后状态错乱
            if self._lock_fd is not None:
                try:
                    os.close(self._lock_fd)
                except OSError:
                    pass
                self._lock_fd = None
            return
        if self._lock_fd is not None:
            if _HAS_FCNTL:
                try:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None
        self._has_lock = False
        # 删除 lock 文件让后续 create() 不必走 steal_stale 路径
        try:
            os.unlink(self._lock_path)
        except OSError:
            pass

    # --- 写入路径 ----------------------------------------------------------

    def append_done(self, style, z, x, y):
        """记录一个完成的 tile, 立即 flush + fsync.

        被 kill 后已落盘的整行不会丢失; 半截行由 load_done 容忍。
        """
        line = "%s,%d,%d,%d" % (str(style), int(z), int(x), int(y))
        _safe_append_line(self._done_path, line)

    def append_failure(self, style, z, x, y, attempts, error):
        """记录失败 tile + 重试次数 + 错误信息.

        error 中可能含逗号 / 换行, 这里做最小转义: 替换换行为空格,
        逗号保留 (审计用, 不参与解析)。失败日志暂不在 load_done 路径上,
        因此对解析没有强契约。
        """
        err = "" if error is None else str(error)
        err = err.replace("\r", " ").replace("\n", " ")
        line = "%s,%d,%d,%d,%d,%s" % (
            str(style), int(z), int(x), int(y), int(attempts), err)
        _safe_append_line(self._failures_path, line)

    def write_state(self, **fields):
        """合并 fields 到 state.json, 设置 updated_at 并原子重写。

        T5 PrewarmCoordinator 会通过该方法持续上报 total/done/failed/eta 等;
        这里不限制字段集, 谁写谁负责语义。
        """
        if os.path.isfile(self._state_path):
            try:
                state = _read_json(self._state_path)
            except Exception:
                state = {}
        else:
            state = {}
        if not isinstance(state, dict):
            state = {}
        state.update(fields)
        state["updated_at"] = _now()
        _atomic_write_json(self._state_path, state)
        return state

    def finalize(self, status):
        """落终态 + 释放锁, 不删除目录 (审计 / 历史查询)."""
        self.write_state(status=status, finished_at=_now())
        self.release_lock()

    # --- 读取路径 ----------------------------------------------------------

    def load_spec(self):
        return _read_json(self._spec_path)

    def load_state(self):
        return _read_json(self._state_path)

    def load_done(self):
        """解析 done.log -> set of (style,z,x,y).

        EF-12 / EF-03: 容忍空行、半截 garbage、字段不全的行,
        全部静默跳过, 仅返回可解析的 tile key。
        """
        out = set()
        if not os.path.isfile(self._done_path):
            return out
        # 用二进制读 + decode(errors='replace'), 防止 kill 中途写到一半导致
        # 出现非 UTF-8 字节序列时整体抛 UnicodeDecodeError。
        try:
            with open(self._done_path, "rb") as f:
                raw = f.read()
        except OSError:
            return out
        text = raw.decode("utf-8", errors="replace")
        # splitlines 不会把没有换行结尾的最后一行当作合法行丢弃,
        # 但 _parse_done_line 自身会检查格式, 半截行会被拒绝。
        for line in text.split("\n"):
            key = _parse_done_line(line)
            if key is not None:
                out.add(key)
        return out

    def remaining(self, full_tasks):
        """返回 ``full_tasks`` 中尚未在 done.log 出现的 tile 列表.

        参数:
          - ``full_tasks``: 任意可迭代, 元素必须是 ``(style, z, x, y)``;
            原始顺序保留, 调用方可借此控制下载顺序 (例如先低 zoom)。
        """
        done = self.load_done()
        remaining = []
        for t in full_tasks:
            # 容忍输入为列表/tuple 混用
            key = (t[0], int(t[1]), int(t[2]), int(t[3]))
            if key not in done:
                remaining.append(tuple(t))
        return remaining


__all__ = [
    "JOURNAL_DIRNAME",
    "STATUS_RUNNING",
    "STATUS_PAUSED",
    "STATUS_STOPPED",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "JournalLocked",
    "Journal",
]
