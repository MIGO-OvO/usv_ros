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
