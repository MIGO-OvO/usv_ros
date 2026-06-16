#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
地图瓦片本地存储 (Map Tile Store)
==================================
管理瓦片的本地缓存目录、瓦片编号换算、缩放级别约束以及离线占位瓦片。
依赖方向: 仅依赖 map_network_fetch 提供 VALID_STYLES (单一事实来源)。

Python: 3.8
"""

from __future__ import print_function

import math
import os
import struct
import sys
import time
import zlib

# 允许从脚本同目录导入兄弟模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from map_network_fetch import VALID_STYLES  # noqa: E402

# 缓存根目录, 与 ~/usv_ws/config 平级, 不随代码更新被覆盖
CACHE_DIR = os.path.expanduser("~/usv_ws/map_cache")

# 预热缩放级别默认上下限 (可被请求参数覆盖)
DEFAULT_ZOOM_MIN = 13
DEFAULT_ZOOM_MAX = 20
ZOOM_HARD_MIN = 3
ZOOM_HARD_MAX = 20

# 单次预热瓦片总数安全阀, 防止误选超大范围炸盘
MAX_PREWARM_TILES = 2000000


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


# ---- T2: 原子写 / PNG 校验 / 孤儿 tmp 清扫 ----
# 设计要点:
#   - TileKey 仅承担瓦片标识 + 路径解析, 不读不写, 便于在 PrewarmCoordinator/
#     journal 等上层结构中安全传递。
#   - PACK_TILE_PREFIX 自包含 (不引 map_pack_format), 避免循环导入:
#     map_tile_store <- map_pack_format, 反向引用会成环。
#   - write_tile_atomic 严格走 tmp -> fsync -> os.replace 路径, 任何 OSError
#     都清掉半截 *.png.tmp, 不在磁盘留下不可校验的脏数据。
#   - verify_tile_bytes 仅做 magic + 最小长度判定, 不解析 chunk; 高德错误页
#     (HTML/JSON) 与传输截断都会被一并拒掉。
#   - sweep_orphan_tmp 只清 *.png.tmp, 不会误删正常 *.png; max_age_sec 默认
#     60s, 给当前正在写入的进程留出足够富余。

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MIN_BYTES = 100  # 任何小于该值的瓦片图片都视作不完整

# 包内 relpath 前缀; 与 map_pack_format.TILE_PREFIX 形式保持一致, 但物理
# 上不引该模块, 避免反向依赖。
PACK_TILE_PREFIX = "tiles"


class TileKey(object):
    """瓦片标识: style/z/x/y。

    - disk_path(root): 解析为本地缓存绝对路径, 与现有
      ``{root}/{style}/{z}/{x}/{y}.png`` 布局完全一致。
    - relpath(): 包内相对路径, 形如 ``tiles/{style}/{z}/{x}/{y}.png``,
      供 T3/T5 写 manifest/journal 时使用。
    """

    __slots__ = ("style", "z", "x", "y")

    def __init__(self, style, z, x, y):
        self.style = style
        self.z = int(z)
        self.x = int(x)
        self.y = int(y)

    def disk_path(self, root):
        return os.path.join(
            root, self.style, str(self.z), str(self.x),
            "%d.png" % self.y)

    def relpath(self):
        return "%s/%s/%d/%d/%d.png" % (
            PACK_TILE_PREFIX, self.style, self.z, self.x, self.y)

    def __repr__(self):
        return "TileKey(%s, %d, %d, %d)" % (
            self.style, self.z, self.x, self.y)

    def __eq__(self, other):
        if not isinstance(other, TileKey):
            return NotImplemented
        return (self.style == other.style and self.z == other.z
                and self.x == other.x and self.y == other.y)

    def __hash__(self):
        return hash((self.style, self.z, self.x, self.y))


def tile_disk_path(root, key):
    """与 ``key.disk_path(root)`` 等价的函数式接口, 便于按需 import。"""
    return key.disk_path(root)


def verify_tile_bytes(data):
    """瓦片图片字节合法性快速校验。

    通过判据: 非空 + 以 PNG/JPEG magic 开头 + 长度 > PNG_MIN_BYTES。
    用于过滤 HTML 错误页、JSON 错误体、传输截断等非图片响应,
    避免污染瓦片缓存。
    """
    if not data:
        return False
    if not (data.startswith(PNG_MAGIC) or data.startswith(JPEG_MAGIC)):
        return False
    return len(data) > PNG_MIN_BYTES


def write_tile_atomic(root, key, data):
    """原子写瓦片字节: 写 ``*.png.tmp`` -> fsync -> ``os.replace``。

    - 不校验 ``data`` 是否为 PNG (调用方决定); 但写入失败必须清理半截
      tmp, 不能在缓存里留下既不是合法 PNG 也无法 mtime 判定的孤儿。
    - 任何 OSError 都返回 False, 不抛; 由调用方负责重试或上报。
    """
    path = key.disk_path(root)
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def read_tile(root, key):
    """读取瓦片字节; 文件不存在或 IO 失败返回 None。"""
    path = key.disk_path(root)
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def sweep_orphan_tmp(root, max_age_sec=60):
    """清理 ``root`` 下超过 ``max_age_sec`` 秒的 ``*.png.tmp`` 孤儿文件。

    用于进程崩溃后回收半截写入。仅删除以 ``.png.tmp`` 结尾的文件,
    永远不动正常的 ``*.png`` 瓦片。返回删除的文件数量。
    不存在的目录视为 0, 不抛。
    """
    if not os.path.isdir(root):
        return 0
    threshold = time.time() - float(max_age_sec)
    removed = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".png.tmp"):
                continue
            full = os.path.join(dirpath, name)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            if mtime <= threshold:
                try:
                    os.remove(full)
                    removed += 1
                except OSError:
                    # 并发写入或权限拒绝, 跳过即可, 下次再扫
                    continue
    return removed
