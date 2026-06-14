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
from concurrent.futures import ThreadPoolExecutor

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
    fetch_tile,
)
from map_tile_store import (  # noqa: E402,F401
    CACHE_DIR,
    DEFAULT_ZOOM_MAX,
    DEFAULT_ZOOM_MIN,
    MAX_PREWARM_TILES,
    PLACEHOLDER_TILE,
    ZOOM_HARD_MAX,
    ZOOM_HARD_MIN,
    clamp_zoom,
    deg2tile,
    enumerate_tiles,
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
    import_pack,
    iter_tiles_root,
    manifest_kind,
    read_pack_manifest,
)


class MapTileCache(object):
    """瓦片缓存代理 + 预热管理 (线程安全, 单实例)。"""

    def __init__(self, cache_dir=CACHE_DIR, logger=None, offline_mode=False):
        self.cache_dir = cache_dir
        self._log = logger or (lambda *a, **k: None)
        self._sub_idx = 0
        self._sub_lock = threading.Lock()
        self._state_path = os.path.join(cache_dir, ".offline_mode")
        # 旧版本的离线模式会持久化并阻止回源; 现在统一使用缓存优先,
        # 联网可用时自动落盘, 无网时自然回退到已缓存瓦片/占位瓦片。
        self.offline_mode = False
        # 预热任务状态
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self.prewarm = {
            "running": False,
            "total": 0,
            "done": 0,
            "failed": 0,
            "zoom": 0,
            "started_at": 0.0,
            "finished_at": 0.0,
            "stopped": False,
        }
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
        with self._lock:
            return dict(self.prewarm)

    def stop_prewarm(self):
        self._stop_event.set()
        return self.prewarm_status()

    def start_prewarm(self, bbox, zoom_min, zoom_max, styles, progress_cb=None):
        """启动后台预热。已有任务运行时拒绝。返回 (ok, info)。"""
        with self._lock:
            if self.prewarm["running"]:
                return False, {"message": "预热任务进行中"}
        zoom_min = clamp_zoom(zoom_min, DEFAULT_ZOOM_MIN)
        zoom_max = clamp_zoom(zoom_max, DEFAULT_ZOOM_MAX)
        if zoom_min > zoom_max:
            zoom_min, zoom_max = zoom_max, zoom_min
        tasks, total = enumerate_tiles(bbox, zoom_min, zoom_max, styles)
        if total == 0:
            return False, {"message": "范围内无瓦片"}
        if total > MAX_PREWARM_TILES:
            return False, {"message": "范围过大(%d 张), 请缩小区域或降低层级" % total}
        self._stop_event.clear()
        with self._lock:
            self.prewarm.update({
                "running": True, "total": total, "done": 0, "failed": 0,
                "zoom": zoom_min, "started_at": time.time(),
                "finished_at": 0.0, "stopped": False,
            })
        worker = threading.Thread(
            target=self._run_prewarm, args=(tasks, progress_cb), daemon=True)
        worker.start()
        return True, {"message": "预热已开始", "total": total,
                      "zoom_min": zoom_min, "zoom_max": zoom_max}

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
