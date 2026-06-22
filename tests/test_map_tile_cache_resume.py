import importlib
import os
import shutil
import sys
import tempfile
import threading
import time
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


def _fresh_import(name="map_tile_cache"):
    _ensure_scripts_on_path()
    if name in sys.modules:
        del sys.modules[name]
    return importlib.import_module(name)


class _Sandbox(object):
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="usv_cache_resume_")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class _FetchRecorder(object):
    def __init__(self, data, fail=False, gate=None):
        self.data = data
        self.fail = fail
        self.gate = gate
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, style, z, x, y, sub, timeout):
        if self.gate is not None:
            self.gate.wait(2.0)
        key = (style, int(z), int(x), int(y))
        with self.lock:
            self.calls.append(key)
        if self.fail:
            raise OSError("offline")
        return self.data


class MapTileCacheResumeTests(unittest.TestCase):
    def setUp(self):
        self.mtc = _fresh_import()
        self.sb = _Sandbox()
        self.png = b"\x89PNG\r\n\x1a\n" + b"0" * 200
        self.bbox = (113.0, 22.0, 113.002, 22.002)

    def tearDown(self):
        self.sb.close()

    def _cache(self, fetch=None):
        return self.mtc.MapTileCache(cache_dir=self.sb.root, _fetch=fetch)

    def _tasks(self, zoom=13, styles=("satellite",)):
        tasks, _total = self.mtc.enumerate_tiles(self.bbox, zoom, zoom, styles)
        return tasks

    def _wait_idle(self, cache, timeout=5.0):
        deadline = time.time() + timeout
        last = cache.prewarm_status()
        while time.time() < deadline:
            last = cache.prewarm_status()
            if not last.get("running"):
                return last
            time.sleep(0.01)
        self.fail("prewarm did not stop: %r" % last)

    def _tile_exists(self, task):
        style, z, x, y = task
        return os.path.isfile(os.path.join(
            self.sb.root, style, str(z), str(x), "%d.png" % y))

    def test_hp01_cold_prewarm_completes_and_reports_v2_status(self):
        fetch = _FetchRecorder(self.png)
        cache = self._cache(fetch)

        ok, info = cache.start_prewarm(self.bbox, 13, 13, ["satellite"])
        self.assertTrue(ok, info)
        self.assertIn("job_id", info)
        self.assertEqual(info["message"], "预热已开始")
        self.assertIn("total", info)
        self.assertIn("zoom_min", info)
        self.assertIn("zoom_max", info)

        st = self._wait_idle(cache)
        self.assertEqual(st["done"], st["total"])
        self.assertEqual(st["failed"], 0)
        self.assertEqual(st["status"], "completed")
        for key in ("job_id", "reason", "retried", "updated_at",
                    "rate_tiles_per_sec", "eta_seconds"):
            self.assertIn(key, st)
        for task in self._tasks():
            self.assertTrue(self._tile_exists(task), task)

    def test_hp05_existing_valid_tiles_are_skipped_without_network(self):
        tasks = self._tasks()
        self.assertGreater(len(tasks), 0)
        existing = tasks[0]
        key = self.mtc.TileKey(*existing)
        self.assertTrue(self.mtc.write_tile_atomic(self.sb.root, key, self.png))
        fetch = _FetchRecorder(self.png)
        cache = self._cache(fetch)

        ok, info = cache.start_prewarm(self.bbox, 13, 13, ["satellite"])
        self.assertTrue(ok, info)
        st = self._wait_idle(cache)

        self.assertEqual(st["status"], "completed")
        self.assertNotIn(existing, fetch.calls)
        self.assertEqual(st["done"], st["total"])

    def test_get_tile_drops_existing_blank_cache_before_remote_fetch(self):
        target = os.path.join(self.sb.root, "satellite", "13", "1", "1.png")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(self.mtc.PLACEHOLDER_TILE)
        cache = self._cache()
        cache._fetch_remote = lambda style, z, x, y: self.png

        data, hit = cache.get_tile("satellite", 13, 1, 1)

        self.assertEqual(data, self.png)
        self.assertEqual(hit, "remote")
        with open(target, "rb") as f:
            self.assertEqual(f.read(), self.png)

    def test_get_tile_rejects_remote_blank_tile_without_writing_cache(self):
        target = os.path.join(self.sb.root, "satellite", "13", "1", "1.png")
        cache = self._cache()
        cache._fetch_remote = lambda style, z, x, y: self.mtc.PLACEHOLDER_TILE

        data, hit = cache.get_tile("satellite", 13, 1, 1)

        self.assertEqual(data, self.mtc.PLACEHOLDER_TILE)
        self.assertEqual(hit, "placeholder")
        self.assertFalse(os.path.exists(target))

    def test_hp06_resume_uses_journal_done_and_does_not_refetch(self):
        old_workers = self.mtc.PREWARM_WORKERS
        self.mtc.PREWARM_WORKERS = 1
        try:
            first_fetch = _FetchRecorder(self.png)
            first = self._cache(first_fetch)
            stopped_once = {"done": False}

            def stop_after_first(status):
                if status.get("done", 0) >= 1 and not stopped_once["done"]:
                    stopped_once["done"] = True
                    first.stop_prewarm()

            ok, info = first.start_prewarm(
                self.bbox, 13, 13, ["satellite"], progress_cb=stop_after_first)
            self.assertTrue(ok, info)
            job_id = info["job_id"]
            st1 = self._wait_idle(first)
            self.assertTrue(st1["done"] >= 1, st1)
            already_done = set(first_fetch.calls)

            second_fetch = _FetchRecorder(self.png)
            second = self._cache(second_fetch)
            ok2, info2 = second.resume_job(job_id)
            self.assertTrue(ok2, info2)
            st2 = self._wait_idle(second)

            self.assertEqual(st2["status"], "completed")
            self.assertFalse(already_done.intersection(second_fetch.calls))
            journal = self.mtc.Journal.open_existing(self.sb.root, job_id)
            self.assertEqual(len(journal.load_done()), st2["total"])
        finally:
            self.mtc.PREWARM_WORKERS = old_workers

    def test_ef01_mass_failure_pauses_before_burning_entire_job(self):
        old_min = self.mtc.MASS_FAILURE_MIN_ATTEMPTS
        old_ratio = self.mtc.MASS_FAILURE_RATIO
        self.mtc.MASS_FAILURE_MIN_ATTEMPTS = 3
        self.mtc.MASS_FAILURE_RATIO = 0.30
        try:
            fetch = _FetchRecorder(self.png, fail=True)
            cache = self._cache(fetch)
            bbox = (110.0, 20.0, 120.0, 30.0)

            ok, info = cache.start_prewarm(bbox, 7, 7, ["satellite"])
            self.assertTrue(ok, info)
            st = self._wait_idle(cache)

            self.assertEqual(st["status"], "paused")
            self.assertEqual(st["reason"], "mass_failure")
            self.assertGreater(st["failed"], 0)
            self.assertLess(st["failed"], st["total"])
        finally:
            self.mtc.MASS_FAILURE_MIN_ATTEMPTS = old_min
            self.mtc.MASS_FAILURE_RATIO = old_ratio

    def test_ef02_write_failure_counts_failed_without_crash(self):
        original_write = self.mtc.write_tile_atomic
        self.mtc.write_tile_atomic = lambda root, key, data: False
        try:
            fetch = _FetchRecorder(self.png)
            cache = self._cache(fetch)
            ok, info = cache.start_prewarm(self.bbox, 13, 13, ["satellite"])
            self.assertTrue(ok, info)
            st = self._wait_idle(cache)
            self.assertEqual(st["done"], 0)
            self.assertEqual(st["failed"], st["total"])
            self.assertEqual(st["status"], "failed")
        finally:
            self.mtc.write_tile_atomic = original_write

    def test_ef09_concurrent_start_returns_running_job_id(self):
        gate = threading.Event()
        fetch = _FetchRecorder(self.png, gate=gate)
        cache = self._cache(fetch)
        ok, info = cache.start_prewarm(self.bbox, 13, 13, ["satellite"])
        self.assertTrue(ok, info)

        try:
            deadline = time.time() + 2.0
            while time.time() < deadline and not cache.prewarm_status()["running"]:
                time.sleep(0.01)
            ok2, info2 = cache.start_prewarm(self.bbox, 13, 13, ["satellite"])
            self.assertFalse(ok2)
            self.assertIn("进行中", info2["message"])
            self.assertEqual(info2["job_id"], info["job_id"])
        finally:
            gate.set()
            self._wait_idle(cache)

    def test_ef10_too_large_range_returns_old_message_without_job(self):
        old_max = self.mtc.MAX_PREWARM_TILES
        self.mtc.MAX_PREWARM_TILES = 0
        try:
            cache = self._cache(_FetchRecorder(self.png))
            ok, info = cache.start_prewarm(self.bbox, 13, 13, ["satellite"])
            self.assertFalse(ok)
            self.assertIn("范围过大", info["message"])
            self.assertNotIn("job_id", info)
        finally:
            self.mtc.MAX_PREWARM_TILES = old_max


if __name__ == "__main__":
    unittest.main()
