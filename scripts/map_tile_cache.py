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

不依赖 ROS / Flask, 便于独立测试与复用。
Python: 3.8
"""

from __future__ import print_function

import math
import os
import shutil
import struct
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

try:
    from urllib.request import Request, urlopen
    from urllib.error import URLError
except ImportError:  # pragma: no cover - py2 兜底, 实际运行为 py3
    from urllib2 import Request, urlopen, URLError

# 缓存根目录, 与 ~/usv_ws/config 平级, 不随代码更新被覆盖
CACHE_DIR = os.path.expanduser("~/usv_ws/map_cache")

# 高德规则栅格瓦片端点 (z/x/y, 子域 1..4 负载均衡)
TILE_ENDPOINTS = {
    "satellite": "https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}",
    "annotation": "https://webrd0{s}.is.autonavi.com/appmaptile"
                  "?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
}
VALID_STYLES = tuple(TILE_ENDPOINTS.keys())

# 预热缩放级别默认上下限 (可被请求参数覆盖)
DEFAULT_ZOOM_MIN = 13
DEFAULT_ZOOM_MAX = 18
ZOOM_HARD_MIN = 3
ZOOM_HARD_MAX = 20

# 单次预热瓦片总数安全阀, 防止误选超大范围炸盘
MAX_PREWARM_TILES = 200000

# 回源网络参数
FETCH_TIMEOUT = 8.0
PREWARM_WORKERS = 6
_USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) USV-OfflineMap/1.0"
_REFERER = "https://www.amap.com/"


def _make_solid_png(rgb, size=256):
    """生成纯色 PNG 字节, 作为离线未命中占位瓦片 (避免破图)。"""
    r, g, b = rgb

    def _chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = bytearray()
    row = bytes([r, g, b]) * size
    for _ in range(size):
        raw.append(0)          # 每行过滤器类型 0
        raw.extend(row)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8bit RGB
    idat = zlib.compress(bytes(raw), 9)
    return (b"\x89PNG\r\n\x1a\n" +
            _chunk(b"IHDR", ihdr) +
            _chunk(b"IDAT", idat) +
            _chunk(b"IEND", b""))


# 深灰占位瓦片, 与暗色地图风格协调; 模块加载时生成一次
PLACEHOLDER_TILE = _make_solid_png((42, 42, 42))


def deg2tile(lat, lng, zoom):
    """经纬度 (度) -> 瓦片 x/y 编号 (与 OSM/高德一致的 Web Mercator 方案)。"""
    n = 2 ** zoom
    x = int((lng + 180.0) / 360.0 * n)
    lat = max(-85.05112878, min(85.05112878, lat))
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    x = max(0, min(n - 1, x))
    y = max(0, min(n - 1, y))
    return x, y


def clamp_zoom(value, default):
    try:
        z = int(value)
    except (TypeError, ValueError):
        return default
    return max(ZOOM_HARD_MIN, min(ZOOM_HARD_MAX, z))


def enumerate_tiles(bbox, zoom_min, zoom_max, styles):
    """按 bbox(min_lng,min_lat,max_lng,max_lat) 与缩放范围枚举瓦片任务。

    返回 (tasks, total)，tasks 为 (style, z, x, y) 列表。
    """
    min_lng, min_lat, max_lng, max_lat = bbox
    if min_lng > max_lng:
        min_lng, max_lng = max_lng, min_lng
    if min_lat > max_lat:
        min_lat, max_lat = max_lat, min_lat
    styles = [s for s in styles if s in VALID_STYLES] or list(VALID_STYLES)

    tasks = []
    for z in range(int(zoom_min), int(zoom_max) + 1):
        # 西北角 -> (较小 x, 较小 y), 东南角 -> (较大 x, 较大 y)
        x0, y0 = deg2tile(max_lat, min_lng, z)
        x1, y1 = deg2tile(min_lat, max_lng, z)
        x_lo, x_hi = min(x0, x1), max(x0, x1)
        y_lo, y_hi = min(y0, y1), max(y0, y1)
        for x in range(x_lo, x_hi + 1):
            for y in range(y_lo, y_hi + 1):
                for style in styles:
                    tasks.append((style, z, x, y))
    return tasks, len(tasks)


class MapTileCache(object):
    """瓦片缓存代理 + 预热管理 (线程安全, 单实例)。"""

    def __init__(self, cache_dir=CACHE_DIR, logger=None):
        self.cache_dir = cache_dir
        self._log = logger or (lambda *a, **k: None)
        self._sub_idx = 0
        self._sub_lock = threading.Lock()
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
        url = TILE_ENDPOINTS[style].format(s=self._next_sub(), x=x, y=y, z=z)
        req = Request(url, headers={"User-Agent": _USER_AGENT, "Referer": _REFERER})
        try:
            resp = urlopen(req, timeout=FETCH_TIMEOUT)
            data = resp.read()
            if data and len(data) > 100:
                return data
        except (URLError, OSError, ValueError):
            return None
        return None

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
