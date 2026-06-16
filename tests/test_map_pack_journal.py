# -*- coding: utf-8 -*-
"""map_pack_journal 崩溃安全断点续传日志契约测试 (T3)

锁定 Journal 模块的核心契约:
  - HP-06 / EF-03: 崩溃后通过 open_existing 复盘 done.log,
    remaining(full_tasks) 返回未完成切片.
  - EF-12: done.log 中混入空行 / 非法行不抛异常, 仅被跳过.
  - EF-13: 同一 job_id 的并发 create() 必须冲突 (JournalLocked);
    显式 release_lock 后允许新的 create().
  - list_resumable: 仅列出 status in (running, paused) 的任务.

设计独立性:
  Journal 不依赖 map_tile_store / map_tile_cache 等本仓其他模块,
  tile key 一律使用普通 (style, z, x, y) 元组. 这样 T3 可以早于
  T2/T5/T6 单独验证.
"""

import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
MAP_RESOURCES_DIR = SCRIPTS_DIR / "map_resources"


def _ensure_scripts_on_path():
    for path in (SCRIPTS_DIR, MAP_RESOURCES_DIR):
        p = str(path)
        if p not in sys.path:
            sys.path.insert(0, p)


def _fresh_import(name):
    _ensure_scripts_on_path()
    if name in sys.modules:
        del sys.modules[name]
    return importlib.import_module(name)


class _Sandbox(object):
    """临时目录上下文, 自动清理。"""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="usv_journal_")

    def close(self):
        # Windows 上偶发只读残留, 容错删除
        shutil.rmtree(self.root, ignore_errors=True)


def _make_spec(job_id="job-001", kind="prewarm",
               styles=("satellite",), zoom_min=13, zoom_max=14,
               bbox=(0.0, 0.0, 1.0, 1.0), workers=2,
               created_at=1700000000.0):
    return {
        "job_id": job_id,
        "kind": kind,
        "bbox": list(bbox),
        "zoom_min": zoom_min,
        "zoom_max": zoom_max,
        "styles": list(styles),
        "workers": workers,
        "created_at": created_at,
    }


def _enumerate_tasks(n, style="satellite", z=13, x_base=100):
    """生成 n 个稳定的 tile key, 用于 remaining() 测试。"""
    return [(style, z, x_base + i, 200 + i) for i in range(n)]


class JournalConstantsTests(unittest.TestCase):
    """模块常量与状态枚举的最小契约。"""

    def setUp(self):
        self.mpj = _fresh_import("map_pack_journal")

    def test_journal_dirname_constant(self):
        self.assertEqual(self.mpj.JOURNAL_DIRNAME, ".journal")

    def test_status_constants(self):
        self.assertEqual(self.mpj.STATUS_RUNNING, "running")
        self.assertEqual(self.mpj.STATUS_PAUSED, "paused")
        self.assertEqual(self.mpj.STATUS_STOPPED, "stopped")
        self.assertEqual(self.mpj.STATUS_COMPLETED, "completed")
        self.assertEqual(self.mpj.STATUS_FAILED, "failed")

    def test_journal_locked_is_exception(self):
        self.assertTrue(issubclass(self.mpj.JournalLocked, Exception))


class JournalCreateAndLayoutTests(unittest.TestCase):
    """create() 必须落盘 spec.json + state.json + lock, 并返回可用 Journal."""

    def setUp(self):
        self.mpj = _fresh_import("map_pack_journal")
        self.sb = _Sandbox()

    def tearDown(self):
        self.sb.close()

    def test_create_writes_spec_state_and_lock(self):
        spec = _make_spec(job_id="layout-1")
        j = self.mpj.Journal.create(self.sb.root, spec)
        try:
            jdir = j.journal_dir
            self.assertTrue(os.path.isdir(jdir))
            self.assertTrue(os.path.isfile(os.path.join(jdir, "spec.json")))
            self.assertTrue(os.path.isfile(os.path.join(jdir, "state.json")))
            self.assertTrue(os.path.isfile(os.path.join(jdir, "lock")))
            # spec 落盘内容与传入一致
            loaded_spec = j.load_spec()
            self.assertEqual(loaded_spec["job_id"], "layout-1")
            self.assertEqual(loaded_spec["kind"], "prewarm")
            # 初始 state.status == running
            st = j.load_state()
            self.assertEqual(st["status"], self.mpj.STATUS_RUNNING)
        finally:
            j.release_lock()

    def test_journal_dir_under_root_journal_dirname(self):
        spec = _make_spec(job_id="layout-2")
        j = self.mpj.Journal.create(self.sb.root, spec)
        try:
            expected = os.path.join(
                self.sb.root, self.mpj.JOURNAL_DIRNAME, "layout-2")
            self.assertEqual(os.path.normpath(j.journal_dir),
                             os.path.normpath(expected))
        finally:
            j.release_lock()


class JournalReplayAfterCrashTests(unittest.TestCase):
    """HP-06 / EF-03: 崩溃后 open_existing 必须恢复 done 集合, remaining 正确。"""

    def setUp(self):
        self.mpj = _fresh_import("map_pack_journal")
        self.sb = _Sandbox()

    def tearDown(self):
        self.sb.close()

    def test_resume_after_crash_replays_done_and_remaining(self):
        spec = _make_spec(job_id="crash-1")
        full = _enumerate_tasks(100)

        j = self.mpj.Journal.create(self.sb.root, spec)
        for (style, z, x, y) in full[:60]:
            j.append_done(style, z, x, y)
        # 模拟崩溃: 不调用 finalize, 仅释放锁让 open_existing 可读
        j.release_lock()
        del j

        j2 = self.mpj.Journal.open_existing(self.sb.root, "crash-1")
        try:
            done = j2.load_done()
            self.assertEqual(len(done), 60)
            for t in full[:60]:
                self.assertIn(t, done)
            remaining = j2.remaining(full)
            self.assertEqual(len(remaining), 40)
            self.assertEqual(set(remaining), set(full[60:]))
        finally:
            try:
                j2.release_lock()
            except Exception:
                pass

    def test_partial_last_line_after_kill_is_skipped(self):
        """模拟写入 59 条干净行 + 1 条半截 garbage 行, load_done 仅返回 59 条。"""
        spec = _make_spec(job_id="crash-2")
        full = _enumerate_tasks(60)

        j = self.mpj.Journal.create(self.sb.root, spec)
        for (style, z, x, y) in full[:59]:
            j.append_done(style, z, x, y)
        # 直接追加一个未结束的 garbage 行 (无逗号 + 无换行)
        done_log = os.path.join(j.journal_dir, "done.log")
        with open(done_log, "ab") as f:
            f.write(b"satellite,13,9999,trun")  # 注意: 缺少 y, 缺少换行
        j.release_lock()

        j2 = self.mpj.Journal.open_existing(self.sb.root, "crash-2")
        try:
            done = j2.load_done()
            self.assertEqual(len(done), 59)
            for t in full[:59]:
                self.assertIn(t, done)
        finally:
            j2.release_lock()


class JournalGarbageToleranceTests(unittest.TestCase):
    """EF-12: load_done 容忍空行 / 字段不全 / 数字不可解析的行, 不抛异常。"""

    def setUp(self):
        self.mpj = _fresh_import("map_pack_journal")
        self.sb = _Sandbox()

    def tearDown(self):
        self.sb.close()

    def test_blank_and_malformed_lines_are_skipped(self):
        spec = _make_spec(job_id="garbage-1")
        j = self.mpj.Journal.create(self.sb.root, spec)
        j.append_done("satellite", 13, 100, 200)
        j.append_done("satellite", 13, 100, 201)

        done_log = os.path.join(j.journal_dir, "done.log")
        with open(done_log, "ab") as f:
            f.write(b"\n")                       # 空行
            f.write(b"   \n")                    # 全空白
            f.write(b"satellite,13,foo,bar\n")   # 数字不可解析
            f.write(b"too,few,fields\n")         # 字段不足
            f.write(b"satellite,13,100,202\n")   # 合法尾巴, 必须被采纳

        j.release_lock()

        j2 = self.mpj.Journal.open_existing(self.sb.root, "garbage-1")
        try:
            done = j2.load_done()
            self.assertIn(("satellite", 13, 100, 200), done)
            self.assertIn(("satellite", 13, 100, 201), done)
            self.assertIn(("satellite", 13, 100, 202), done)
            self.assertEqual(len(done), 3)
        finally:
            j2.release_lock()


class JournalLockTests(unittest.TestCase):
    """EF-13: 同 job_id 重复 create() 必须 JournalLocked; release_lock 后允许新 create。

    Windows 跨平台契约: 通过 lock 文件 (含 PID + ts) 识别活锁;
    flock 仅作 POSIX 加强, 不影响测试可达。
    """

    def setUp(self):
        self.mpj = _fresh_import("map_pack_journal")
        self.sb = _Sandbox()

    def tearDown(self):
        self.sb.close()

    def test_duplicate_create_raises_journal_locked(self):
        spec = _make_spec(job_id="lock-1")
        j = self.mpj.Journal.create(self.sb.root, spec)
        try:
            with self.assertRaises(self.mpj.JournalLocked):
                self.mpj.Journal.create(self.sb.root, spec)
        finally:
            j.release_lock()

    def test_release_then_create_again_succeeds(self):
        spec = _make_spec(job_id="lock-2")
        j = self.mpj.Journal.create(self.sb.root, spec)
        j.release_lock()
        # 释放后再创建, 应成功
        j2 = self.mpj.Journal.create(self.sb.root, spec)
        try:
            self.assertEqual(j2.load_spec()["job_id"], "lock-2")
        finally:
            j2.release_lock()

    def test_steal_stale_flag_allows_takeover(self):
        """stale lock 场景: 显式 steal_stale=True 必须接管已存在的 lock 文件。"""
        spec = _make_spec(job_id="lock-3")
        j = self.mpj.Journal.create(self.sb.root, spec)
        # 不释放, 模拟进程崩溃后留下的 lock 文件
        # (留 reference 防止 j 被 gc 自动释放)
        try:
            j2 = self.mpj.Journal.create(self.sb.root, spec, steal_stale=True)
            try:
                self.assertEqual(j2.load_spec()["job_id"], "lock-3")
            finally:
                j2.release_lock()
        finally:
            try:
                j.release_lock()
            except Exception:
                pass


class JournalStateAndFinalizeTests(unittest.TestCase):
    """write_state 合并 + finalize 落状态 + 保留目录用于审计。"""

    def setUp(self):
        self.mpj = _fresh_import("map_pack_journal")
        self.sb = _Sandbox()

    def tearDown(self):
        self.sb.close()

    def test_write_state_merges_fields_and_sets_updated_at(self):
        spec = _make_spec(job_id="state-1")
        j = self.mpj.Journal.create(self.sb.root, spec)
        try:
            new_state = j.write_state(total=100, done=37, zoom=14)
            self.assertEqual(new_state["total"], 100)
            self.assertEqual(new_state["done"], 37)
            self.assertEqual(new_state["zoom"], 14)
            self.assertIn("updated_at", new_state)
            # 重新加载验证落盘
            reloaded = j.load_state()
            self.assertEqual(reloaded["total"], 100)
            self.assertEqual(reloaded["done"], 37)
            # 后续 write_state 不应清空已有字段
            j.write_state(done=42)
            again = j.load_state()
            self.assertEqual(again["total"], 100)
            self.assertEqual(again["done"], 42)
        finally:
            j.release_lock()

    def test_finalize_sets_status_and_keeps_directory(self):
        spec = _make_spec(job_id="state-2")
        j = self.mpj.Journal.create(self.sb.root, spec)
        j.finalize(self.mpj.STATUS_COMPLETED)
        # 不应删除目录, 用于审计
        self.assertTrue(os.path.isdir(j.journal_dir))
        # 状态应当更新
        st = j.load_state()
        self.assertEqual(st["status"], self.mpj.STATUS_COMPLETED)
        self.assertIn("finished_at", st)


class JournalAppendFailureTests(unittest.TestCase):
    """failures.log 追加 + 容忍非法行。"""

    def setUp(self):
        self.mpj = _fresh_import("map_pack_journal")
        self.sb = _Sandbox()

    def tearDown(self):
        self.sb.close()

    def test_append_failure_writes_line(self):
        spec = _make_spec(job_id="fail-1")
        j = self.mpj.Journal.create(self.sb.root, spec)
        try:
            j.append_failure("satellite", 13, 100, 200, 3, "HTTP 500")
            fp = os.path.join(j.journal_dir, "failures.log")
            self.assertTrue(os.path.isfile(fp))
            with open(fp, "rb") as f:
                content = f.read()
            self.assertIn(b"satellite", content)
            self.assertIn(b"HTTP 500", content)
        finally:
            j.release_lock()


class JournalListResumableTests(unittest.TestCase):
    """list_resumable: 仅返回 status in (running, paused) 的任务。"""

    def setUp(self):
        self.mpj = _fresh_import("map_pack_journal")
        self.sb = _Sandbox()

    def tearDown(self):
        self.sb.close()

    def test_list_resumable_filters_completed(self):
        # 任务 A: 保持 running 状态, finalize 不调用
        spec_a = _make_spec(job_id="resume-A")
        ja = self.mpj.Journal.create(self.sb.root, spec_a)
        ja.release_lock()

        # 任务 B: 完成后 finalize -> completed, 不应被 list_resumable 列出
        spec_b = _make_spec(job_id="resume-B")
        jb = self.mpj.Journal.create(self.sb.root, spec_b)
        jb.finalize(self.mpj.STATUS_COMPLETED)

        items = self.mpj.Journal.list_resumable(self.sb.root)
        ids = [it["job_id"] for it in items]
        self.assertIn("resume-A", ids)
        self.assertNotIn("resume-B", ids)
        for it in items:
            self.assertIn("spec", it)
            self.assertIn("state", it)

    def test_list_resumable_includes_paused(self):
        spec = _make_spec(job_id="resume-P")
        j = self.mpj.Journal.create(self.sb.root, spec)
        j.write_state(status=self.mpj.STATUS_PAUSED)
        j.release_lock()

        items = self.mpj.Journal.list_resumable(self.sb.root)
        ids = [it["job_id"] for it in items]
        self.assertIn("resume-P", ids)


if __name__ == "__main__":
    unittest.main()
