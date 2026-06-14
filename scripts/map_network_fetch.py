#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
地图瓦片回源 (Map Tile Network Fetch)
======================================
集中管理高德栅格瓦片的远端端点与回源逻辑, 不依赖文件系统/打包格式。
此模块为依赖底层: 不允许反向 import map_tile_store / map_pack_format。

底图: 高德卫星影像 (style=6) + 注记叠加层 (style=8), GCJ-02。
端点公开, 无需 Key/签名; 仅供比赛/演示用途, 长期商用需评估正规授权。
Python: 3.8
"""

from __future__ import print_function

try:
    from urllib.request import Request, urlopen
    from urllib.error import URLError
except ImportError:  # pragma: no cover - py2 兜底, 实际运行为 py3
    from urllib2 import Request, urlopen, URLError


# 高德规则栅格瓦片端点 (z/x/y, 子域 1..4 负载均衡)
TILE_ENDPOINTS = {
    "satellite": "https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}",
    "annotation": "https://webrd0{s}.is.autonavi.com/appmaptile"
                  "?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
}
VALID_STYLES = tuple(TILE_ENDPOINTS.keys())

# 回源网络参数
FETCH_TIMEOUT = 8.0
PREWARM_WORKERS = 6
_USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) USV-OfflineMap/1.0"
_REFERER = "https://www.amap.com/"


def fetch_tile(style, z, x, y, sub=1):
    """从高德回源单张瓦片字节, 失败返回 None。供缓存代理与导出 CLI 复用。"""
    if style not in TILE_ENDPOINTS:
        return None
    url = TILE_ENDPOINTS[style].format(s=sub, x=x, y=y, z=z)
    req = Request(url, headers={"User-Agent": _USER_AGENT, "Referer": _REFERER})
    try:
        resp = urlopen(req, timeout=FETCH_TIMEOUT)
        data = resp.read()
        if data and len(data) > 100:
            return data
    except (URLError, OSError, ValueError):
        return None
    return None
