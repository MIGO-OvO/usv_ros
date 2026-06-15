#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线地图瓦片缓存 (Offline Map Tile Cache)
==========================================
为 Web 配置页地图提供离线能力:
  - 高德栅格瓦片本地反向代理 (缓存优先, 永不主动过期)
  - 按地理范围批量预热下载 (有限并发)
  - 缓存统计 / 清空
  - 在线探活

底图: 高德卫星影像 (style=6) + 注记叠加层 (style=8), GCJ-02, 国内水域。
瓦片端点公开, 无需 Key/签名。仅供比赛/演示用途, 长期商用需评估正规授权。

不依赖 ROS / Flask, 便于独立测试与复用。本文件仅承载 ``MapTileCache`` 运行时
对象; 纯函数/常量已迁出到三个兄弟模块, 这里通过再导出保持原有 ``import
map_tile_cache as mtc`` 形式调用方零成本兼容:

  - ``map_network_fetch``  端点、UA/Referer、``fetch_tile``
  - ``map_tile_store``     缓存目录、瓦片编号、缩放约束、占位 PNG
  - ``map_pack_format``    manifest schema、哈希、tar 打包/导入

Python: 3.8
"""

from __future__ import print_function

import os
import shutil
import sys
import threading
import time
import uuid
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

# 允许从脚本同目录导入兄弟模块 (脚本/测试两种入口都能解析)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- 再导出: 保持 map_tile_cache.X 完整公共表面 ----
from map_network_fetch import (  # noqa: E402,F401
    Request,
    URLError,
    FETCH_TIMEOUT,
    PREWARM_WORKERS,
    TILE_ENDPOINTS,
    VALID_STYLES,
    FetchResult,
    fetch_tile,
    fetch_tile_resilient,
)
from map_pack_journal import (  # noqa: E402,F401
    Journal,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_RUNNING,
    STATUS_STOPPED,
)
from map_tile_store import (  # noqa: E402,F401
    CACHE_DIR,
    DEFAULT_ZOOM_MAX,
    DEFAULT_ZOOM_MIN,
    MAX_PREWARM_TILES,
    PACK_TILE_PREFIX,
    PLACEHOLDER_TILE,
    PNG_MAGIC,
    PNG_MIN_BYTES,
    TileKey,
    ZOOM_HARD_MAX,
    ZOOM_HARD_MIN,
    clamp_zoom,
    deg2tile,
    enumerate_tiles,
    read_tile,
    sweep_orphan_tmp,
    tile_disk_path,
    verify_tile_bytes,
    write_tile_atomic,
)
from map_pack_format import (  # noqa: E402,F401
    MANIFEST_NAME,
    PACK_PROVIDER,
    PACK_VERSION,
    TILE_PREFIX,
    build_manifest,
    compute_tile_index_sha256,
    create_pack,
    hash_tiles_root,
    iter_tiles_root,
    manifest_kind,
    read_pack_manifest,
)
from map_pack_delta import apply_delta as import_pack  # noqa: E402,F401
from map_pack_delta import diff_pack  # noqa: E402,F401


MASS_FAILURE_MIN_ATTEMPTS = 20
MASS_FAILURE_RATIO = 0.30
PROGRESS_EMIT_INTERVAL_SEC = 0.5
_EPSILON = 0.001


def _empty_prewarm_state():
    return {
        "running": False,
        "total": 0,
        "done": 0,
        "failed": 0,
        "zoom": 0,
        "started_at": 0.0,
        "finished_at": 0.0,
        "stopped": False,
        "job_id": None,
        "status": "idle",
        "reason": "",
        "retried": 0,
        "updated_at": 0.0,
        "rate_tiles_per_sec": 0.0,
        "eta_seconds": 0.0,
    }


class PrewarmCoordinator(object):
    def __init__(self, cache_dir, logger=None, sub_picker=None,
                 fetch_impl=None, workers=PREWARM_WORKERS):
        self.cache_dir = cache_dir
        self._log = logger or (lambda *a, **k: None)
        self._sub_picker = sub_picker
        self._fetch_impl = fetch_impl
        self.workers = int(workers or PREWARM_WORKERS)
        self._lock = threading.RLock()
        self._active = {}

    def start(self, bbox, zoom_min, zoom_max, styles, progress_cb=None):
        with self._lock:
            running = self._running_job_locked()
            if running:
                return False, {"message": "预热任务进行中", "job_id": running["job_id"]}
        tasks, total = enumerate_tiles(bbox, zoom_min, zoom_max, styles)
        if total == 0:
            return False, {"message": "范围内无瓦片"}
        if total > MAX_PREWARM_TILES:
            return False, {"message": "范围过大(%d 张), 请缩小区域或降低层级" % total}
        job_id = uuid.uuid4().hex
        spec = {
            "job_id": job_id,
            "kind": "prewarm",
            "bbox": list(bbox),
            "zoom_min": int(zoom_min),
            "zoom_max": int(zoom_max),
            "styles": list(styles),
            "workers": self.workers,
            "created_at": time.time(),
        }
        journal = Journal.create(self.cache_dir, spec)
        journal.write_state(
            total=total, done=0, failed=0, retried=0, zoom=int(zoom_min),
            status=STATUS_RUNNING, reason="", started_at=spec["created_at"],
            finished_at=0.0, rate_tiles_per_sec=0.0, eta_seconds=0.0)
        return self._launch(job_id, spec, journal, tasks, progress_cb)

    def resume(self, job_id, progress_cb=None):
        with self._lock:
            running = self._running_job_locked()
            if running:
                return False, {"message": "预热任务进行中", "job_id": running["job_id"]}
        existing = Journal.open_existing(self.cache_dir, job_id)
        spec = existing.load_spec()
        existing.release_lock()
        tasks, total = enumerate_tiles(
            spec.get("bbox"), spec.get("zoom_min"), spec.get("zoom_max"),
            spec.get("styles") or VALID_STYLES)
        journal = Journal.create(self.cache_dir, spec, steal_stale=True)
        done_count = len(journal.load_done())
        state = journal.load_state()
        journal.write_state(
            total=total, done=done_count, failed=int(state.get("failed", 0)),
            retried=int(state.get("retried", 0)), status=STATUS_RUNNING,
            reason="", finished_at=0.0)
        return self._launch(job_id, spec, journal, tasks, progress_cb,
                            message="预热已恢复")

    def stop(self, job_id):
        with self._lock:
            job = self._active.get(job_id)
            if job:
                job["stop"].set()
        return self.status(job_id)

    def pause(self, job_id):
        with self._lock:
            job = self._active.get(job_id)
            if job:
                job["reason"] = "paused"
                job["stop"].set()
        return self.status(job_id)

    def list_jobs(self):
        items = []
        seen = set()
        for it in Journal.list_resumable(self.cache_dir):
            seen.add(it["job_id"])
            payload = dict(it.get("state") or {})
            payload.update(it.get("spec") or {})
            payload["job_id"] = it["job_id"]
            items.append(payload)
        base = os.path.join(self.cache_dir, ".journal")
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                if name in seen:
                    continue
                try:
                    j = Journal.open_existing(self.cache_dir, name)
                    payload = j.load_state()
                    spec = j.load_spec()
                    payload.update(spec)
                    payload["job_id"] = name
                    items.append(payload)
                except Exception:
                    continue
        return items

    def status(self, job_id):
        with self._lock:
            job = self._active.get(job_id)
            if job:
                return dict(job["status"])
        try:
            j = Journal.open_existing(self.cache_dir, job_id)
            spec = j.load_spec()
            state = j.load_state()
            return self._status_from_state(spec, state)
        except Exception:
            st = _empty_prewarm_state()
            st.update({"job_id": job_id, "status": "missing", "reason": "not_found"})
            return st

    def active_status(self):
        with self._lock:
            running = self._running_job_locked()
            if running:
                return dict(running["status"])
        resumable = Journal.list_resumable(self.cache_dir)
        if resumable:
            it = resumable[-1]
            return self._status_from_state(it["spec"], it["state"])
        jobs = self.list_jobs()
        if jobs:
            latest = sorted(jobs, key=lambda x: x.get("updated_at", 0))[-1]
            return self._status_from_state(latest, latest)
        return _empty_prewarm_state()

    def _launch(self, job_id, spec, journal, tasks, progress_cb, message="预热已开始"):
        stop_event = threading.Event()
        status = self._status_from_state(spec, journal.load_state())
        status.update({"running": True, "stopped": False, "job_id": job_id,
                       "status": STATUS_RUNNING, "finished_at": 0.0})
        job = {"job_id": job_id, "spec": spec, "journal": journal,
               "tasks": list(tasks), "stop": stop_event, "status": status,
               "reason": ""}
        with self._lock:
            self._active[job_id] = job
        worker = threading.Thread(
            target=self._run, args=(job, progress_cb), daemon=True)
        job["thread"] = worker
        worker.start()
        return True, {"message": message, "total": len(tasks),
                      "zoom_min": spec.get("zoom_min"),
                      "zoom_max": spec.get("zoom_max"), "job_id": job_id}

    def _run(self, job, progress_cb):
        journal = job["journal"]
        stop_event = job["stop"]
        spec = job["spec"]
        started_at = job["status"].get("started_at") or time.time()
        done = len(journal.load_done())
        failed = int(job["status"].get("failed", 0) or 0)
        retried = int(job["status"].get("retried", 0) or 0)
        total = len(job["tasks"])
        window = deque(maxlen=max(1, int(MASS_FAILURE_MIN_ATTEMPTS)))
        final_status = STATUS_COMPLETED
        reason = ""
        last_emit = 0.0
        pending = set()
        remaining_tasks = list(self._remaining_without_valid_disk(journal, job["tasks"]))
        done = len(journal.load_done())
        iterator = iter(remaining_tasks)
        sweep_orphan_tmp(self.cache_dir)
        try:
            with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
                while not stop_event.is_set() and len(pending) < max(1, self.workers):
                    task = next(iterator, None)
                    if task is None:
                        break
                    pending.add(pool.submit(self._download_one, task, stop_event))
                while pending:
                    done_futures, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for fut in done_futures:
                        task, ok, attempts, err, aborted = fut.result()
                        if aborted:
                            stop_event.set()
                            continue
                        style, z, x, y = task
                        retried += max(0, int(attempts) - 1)
                        if ok:
                            journal.append_done(style, z, x, y)
                            done += 1
                            window.append(True)
                        else:
                            failed += 1
                            journal.append_failure(style, z, x, y, attempts, err)
                            window.append(False)
                        status = self._write_status(
                            job, journal, total, done, failed, retried, z,
                            started_at, STATUS_RUNNING, reason)
                        now = time.time()
                        if progress_cb and now - last_emit >= PROGRESS_EMIT_INTERVAL_SEC:
                            last_emit = now
                            progress_cb(status)
                        if self._mass_failure(window):
                            final_status = STATUS_PAUSED
                            reason = "mass_failure"
                            stop_event.set()
                            break
                    while not stop_event.is_set() and len(pending) < max(1, self.workers):
                        task = next(iterator, None)
                        if task is None:
                            break
                        pending.add(pool.submit(self._download_one, task, stop_event))
                    if stop_event.is_set():
                        for fut in pending:
                            fut.cancel()
                        break
            if final_status == STATUS_COMPLETED:
                if stop_event.is_set():
                    final_status = STATUS_STOPPED
                    reason = job.get("reason") or reason
                elif failed > 0 and done < total:
                    final_status = STATUS_FAILED
        finally:
            status = self._write_status(
                job, journal, total, done, failed, retried,
                job["status"].get("zoom", spec.get("zoom_min", 0)),
                started_at, final_status, reason, finished=True)
            journal.release_lock()
            with self._lock:
                self._active.pop(job["job_id"], None)
            if progress_cb:
                progress_cb(status)

    def _remaining_without_valid_disk(self, journal, tasks):
        done_on_disk = set(journal.load_done())
        for task in tasks:
            style, z, x, y = task
            key = (style, int(z), int(x), int(y))
            tile_key = TileKey(style, z, x, y)
            if key in done_on_disk:
                continue
            data = read_tile(self.cache_dir, tile_key)
            if verify_tile_bytes(data):
                journal.append_done(style, z, x, y)
                continue
            yield task

    def _download_one(self, task, stop_event):
        style, z, x, y = task
        result = fetch_tile_resilient(
            style, z, x, y, sub_picker=self._sub_picker, abort=stop_event,
            base_delay=0.001, max_delay=0.001, _fetch=self._fetch_impl)
        if result.status == "aborted":
            return task, False, result.attempts, "aborted", True
        if result.status == "ok" and verify_tile_bytes(result.data):
            if write_tile_atomic(self.cache_dir, TileKey(style, z, x, y), result.data):
                return task, True, result.attempts, "", False
            return task, False, result.attempts, "write_failed", False
        return task, False, result.attempts, result.status, False

    def _write_status(self, job, journal, total, done, failed, retried, zoom,
                      started_at, status, reason, finished=False):
        now = time.time()
        elapsed = max(now - float(started_at or now), _EPSILON)
        rate = float(done) / elapsed
        remaining = max(0, int(total) - int(done) - int(failed))
        eta = float(remaining) / max(rate, _EPSILON)
        payload = {
            "running": status == STATUS_RUNNING,
            "total": int(total),
            "done": int(done),
            "failed": int(failed),
            "zoom": int(zoom or 0),
            "started_at": float(started_at or 0.0),
            "finished_at": now if finished else 0.0,
            "stopped": status == STATUS_STOPPED,
            "job_id": job["job_id"],
            "status": status,
            "reason": reason or "",
            "retried": int(retried),
            "updated_at": now,
            "rate_tiles_per_sec": rate,
            "eta_seconds": eta,
        }
        journal.write_state(**payload)
        with self._lock:
            job["status"] = dict(payload)
        return dict(payload)

    def _status_from_state(self, spec, state):
        st = _empty_prewarm_state()
        if isinstance(state, dict):
            st.update(state)
        st["job_id"] = st.get("job_id") or (spec or {}).get("job_id")
        st["running"] = st.get("status") == STATUS_RUNNING
        st["stopped"] = st.get("status") == STATUS_STOPPED
        return st

    def _running_job_locked(self):
        for job in self._active.values():
            if job["status"].get("running"):
                return job
        return None

    def _mass_failure(self, window):
        if len(window) < int(MASS_FAILURE_MIN_ATTEMPTS):
            return False
        failures = len([ok for ok in window if not ok])
        return (float(failures) / float(len(window))) > float(MASS_FAILURE_RATIO)


class MapTileCache(object):
    """瓦片缓存代理 + 预热管理 (线程安全, 单实例)。"""

    def __init__(self, cache_dir=CACHE_DIR, logger=None, offline_mode=False, _fetch=None):
        self.cache_dir = cache_dir
        self._log = logger or (lambda *a, **k: None)
        self._resilient_fetch = _fetch
        self._sub_idx = 0
        self._sub_lock = threading.Lock()
        self._state_path = os.path.join(cache_dir, ".offline_mode")
        # 旧版本的离线模式会持久化并阻止回源; 现在统一使用缓存优先,
        # 联网可用时自动落盘, 无网时自然回退到已缓存瓦片/占位瓦片。
        self.offline_mode = False
        # 预热任务状态
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self.prewarm = _empty_prewarm_state()
        self._prewarm = PrewarmCoordinator(
            self.cache_dir, logger=self._log, sub_picker=self._next_sub,
            fetch_impl=self._resilient_fetch, workers=PREWARM_WORKERS)
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except OSError as exc:
            self._log("创建地图缓存目录失败: %s", str(exc))

    # ---- 路径与回源 ----
    def _tile_path(self, style, z, x, y):
        return os.path.join(self.cache_dir, style, str(z), str(x), "%d.png" % y)

    def _next_sub(self):
        with self._sub_lock:
            self._sub_idx = (self._sub_idx % 4) + 1
            return self._sub_idx

    def _fetch_remote(self, style, z, x, y):
        """从高德回源单张瓦片字节, 失败返回 None。"""
        return fetch_tile(style, z, x, y, self._next_sub())

    def _write_tile(self, style, z, x, y, data):
        path = self._tile_path(style, z, x, y)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            return True
        except OSError as exc:
            self._log("写入瓦片失败 %s: %s", path, str(exc))
            return False

    def _load_offline_state(self, default):
        try:
            with open(self._state_path, "r") as f:
                return f.read().strip() == "1"
        except (OSError, AttributeError):
            return bool(default)

    def set_offline_mode(self, enabled):
        """兼容旧接口; 新策略始终为缓存优先并允许联网回源。"""
        self.offline_mode = False
        try:
            if os.path.exists(self._state_path):
                os.remove(self._state_path)
        except OSError as exc:
            self._log("清理旧离线模式状态失败: %s", str(exc))
        return self.offline_mode

    def get_tile(self, style, z, x, y, allow_remote=True):
        """读取瓦片: 缓存优先 -> (在线)回源落盘 -> 占位瓦片。

        返回 (data_bytes, hit_str)。hit_str: cache/remote/placeholder。
        """
        if style not in VALID_STYLES:
            return PLACEHOLDER_TILE, "placeholder"
        path = self._tile_path(style, z, x, y)
        if os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    return f.read(), "cache"
            except OSError:
                pass
        if allow_remote:
            data = self._fetch_remote(style, z, x, y)
            if data:
                self._write_tile(style, z, x, y, data)
                return data, "remote"
        return PLACEHOLDER_TILE, "placeholder"

    # ---- 预热 ----
    def prewarm_status(self):
        status = self._prewarm.active_status()
        with self._lock:
            self.prewarm.update(status)
            return dict(self.prewarm)

    def stop_prewarm(self):
        job_id = self.prewarm_status().get("job_id")
        if job_id:
            status = self._prewarm.stop(job_id)
            with self._lock:
                self.prewarm.update(status)
        return self.prewarm_status()

    def start_prewarm(self, bbox, zoom_min, zoom_max, styles, progress_cb=None):
        """启动后台预热。已有任务运行时拒绝。返回 (ok, info)。"""
        with self._lock:
            if self.prewarm["running"]:
                return False, {"message": "预热任务进行中",
                               "job_id": self.prewarm.get("job_id")}
        zoom_min = clamp_zoom(zoom_min, DEFAULT_ZOOM_MIN)
        zoom_max = clamp_zoom(zoom_max, DEFAULT_ZOOM_MAX)
        if zoom_min > zoom_max:
            zoom_min, zoom_max = zoom_max, zoom_min
        ok, info = self._prewarm.start(bbox, zoom_min, zoom_max, styles, progress_cb)
        if ok:
            status = self._prewarm.status(info.get("job_id"))
            with self._lock:
                self.prewarm.update(status)
        return ok, info

    def list_jobs(self):
        return self._prewarm.list_jobs()

    def job_status(self, job_id):
        return self._prewarm.status(job_id)

    def pause_job(self, job_id):
        return self._prewarm.pause(job_id)

    def resume_job(self, job_id, progress_cb=None):
        ok, info = self._prewarm.resume(job_id, progress_cb=progress_cb)
        if ok:
            status = self._prewarm.status(info.get("job_id"))
            with self._lock:
                self.prewarm.update(status)
        return ok, info

    def stop_job(self, job_id):
        return self._prewarm.stop(job_id)

    def _run_prewarm(self, tasks, progress_cb):
        def _one(task):
            if self._stop_event.is_set():
                return None
            style, z, x, y = task
            if os.path.isfile(self._tile_path(style, z, x, y)):
                return (z, True)
            data = self._fetch_remote(style, z, x, y)
            if data and self._write_tile(style, z, x, y, data):
                return (z, True)
            return (z, False)

        last_emit = 0.0
        try:
            with ThreadPoolExecutor(max_workers=PREWARM_WORKERS) as pool:
                for result in pool.map(_one, tasks):
                    if result is None:
                        continue
                    z, ok = result
                    with self._lock:
                        self.prewarm["done"] += 1
                        self.prewarm["zoom"] = z
                        if not ok:
                            self.prewarm["failed"] += 1
                    now = time.time()
                    if progress_cb and (now - last_emit) >= 0.5:
                        last_emit = now
                        progress_cb(self.prewarm_status())
        finally:
            with self._lock:
                self.prewarm["running"] = False
                self.prewarm["finished_at"] = time.time()
                self.prewarm["stopped"] = self._stop_event.is_set()
            if progress_cb:
                progress_cb(self.prewarm_status())

    # ---- 统计与清理 ----
    def stats(self):
        count = 0
        size = 0
        for root, _dirs, files in os.walk(self.cache_dir):
            for name in files:
                if name.endswith(".png"):
                    count += 1
                    try:
                        size += os.path.getsize(os.path.join(root, name))
                    except OSError:
                        pass
        return {"tiles": count, "bytes": size, "cache_dir": self.cache_dir}

    def clear(self):
        if self.prewarm_status()["running"]:
            return False, "预热进行中, 请先停止"
        try:
            if os.path.isdir(self.cache_dir):
                shutil.rmtree(self.cache_dir)
            os.makedirs(self.cache_dir, exist_ok=True)
            return True, "缓存已清空"
        except OSError as exc:
            return False, "清空失败: %s" % str(exc)
