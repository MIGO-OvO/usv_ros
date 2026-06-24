#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""地图瓦片最高层级探测 (Map Tile Max-Zoom Probe)
====================================================
对指定中心点 (默认桂林作业区), 从某个起始缩放级别 (默认 z=18) 起逐级向上
直接回源单张瓦片, 报告每一级能否拿到"真实可用"瓦片 (PNG/JPEG 合法且非纯色
空白页), 从而探测当前来源在该区域实际支持到多少缩放等级。

用途: 切换谷歌来源后, 验证 gsatellite/gannotation 在你的作业区到底能放大到
z=20 还是更高 (z=21/22 随区域而异)。高德 satellite/annotation 超过 z=18 会
返回空白页, 本脚本会把空白页判为 blank 而非 ok, 直观呈现"18 以上失效"。

判定口径 (与缓存层一致):
  - ok      : 拿到合法 PNG/JPEG 且非纯色空白 -> 该级可用
  - blank   : 拿到合法图片但整张纯色 (高德越界空白页) -> 该级不可用
  - invalid : 拿到字节但非图片 (HTML/JSON 错误页) -> 该级不可用
  - timeout : 网络层全部失败 -> 无法判定 (可能是网络问题, 非层级上限)

用法示例:
  # 默认: gsatellite, 桂林中心, z=18..23 逐级探测
  python3 map_tile_probe.py

  # 指定来源/中心/范围
  python3 map_tile_probe.py --style gsatellite --lat 25.314167 --lng 110.412778 \
      --zoom-start 18 --zoom-end 23

  # 对比高德 (会看到 18 以上变 blank)
  python3 map_tile_probe.py --style satellite

Python: 3.8
"""

from __future__ import print_function

import argparse
import os
import sys

# 允许从脚本同目录导入 map_tile_cache 及兄弟模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import map_tile_cache as mtc  # noqa: E402

# 默认作业区中心 (与 web_config_server 地图默认中心一致: 桂林)
DEFAULT_LAT = 25.314167
DEFAULT_LNG = 110.412778
DEFAULT_ZOOM_START = 18
DEFAULT_ZOOM_END = 23


def probe_level(style, lat, lng, zoom, max_attempts=4):
    """探测单个缩放级别中心点瓦片。返回 (status, detail_dict)。

    status in {ok, blank, invalid, timeout, aborted}。
    """
    x, y = mtc.deg2tile(lat, lng, zoom)
    result = mtc.fetch_tile_resilient(
        style, zoom, x, y, max_attempts=max_attempts,
        base_delay=0.2, max_delay=2.0)
    detail = {
        "x": x, "y": y, "attempts": result.attempts,
        "bytes": len(result.data) if result.data else 0,
        "elapsed_ms": result.elapsed_ms,
    }
    if result.status != "ok" or not result.data:
        return result.status, detail
    if not mtc.verify_tile_bytes(result.data):
        return "invalid", detail
    if mtc.is_blank_tile(result.data):
        return "blank", detail
    return "ok", detail


def run_probe(style, lat, lng, zoom_start, zoom_end, max_attempts=4):
    """从 zoom_start 逐级探测到 zoom_end, 打印逐级结果并返回最高可用层级。

    返回 (max_usable_zoom 或 None, rows)。rows 为每级 (zoom, status, detail)。
    """
    if style not in mtc.VALID_STYLES:
        print("未知 style: %s; 可选: %s" % (style, ", ".join(mtc.VALID_STYLES)),
              file=sys.stderr)
        return None, []

    print("探测来源 style=%s 中心=(%.6f, %.6f) z=%d..%d" % (
        style, lat, lng, zoom_start, zoom_end))
    print("级别  结果      瓦片(x,y)        字节   尝试  耗时ms")
    print("-" * 56)

    rows = []
    max_usable = None
    for z in range(int(zoom_start), int(zoom_end) + 1):
        status, detail = probe_level(style, lat, lng, z, max_attempts)
        rows.append((z, status, detail))
        print("z=%-3d %-9s (%d,%d)%s%7d %4d %7d" % (
            z, status, detail["x"], detail["y"],
            " " * max(1, 12 - len("%d,%d" % (detail["x"], detail["y"]))),
            detail["bytes"], detail["attempts"], detail["elapsed_ms"]))
        if status == "ok":
            max_usable = z
        elif status in ("blank", "invalid"):
            # 该级已明确不可用; 更高级别通常也不可用, 但继续探测便于看全貌。
            pass
    print("-" * 56)
    if max_usable is not None:
        print("==> 该区域 %s 最高可用缩放等级: z=%d" % (style, max_usable))
    else:
        print("==> 未能在 z=%d..%d 区间获得任何可用瓦片 (可能网络不可达)" % (
            zoom_start, zoom_end))
    return max_usable, rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="地图瓦片最高缩放层级探测")
    parser.add_argument("--style", default="gsatellite",
                        choices=list(mtc.VALID_STYLES),
                        help="瓦片来源 (默认 gsatellite=谷歌卫星)")
    parser.add_argument("--lat", type=float, default=DEFAULT_LAT,
                        help="中心点纬度 (WGS84/GCJ-02 近似即可)")
    parser.add_argument("--lng", type=float, default=DEFAULT_LNG,
                        help="中心点经度")
    parser.add_argument("--zoom-start", type=int, default=DEFAULT_ZOOM_START,
                        help="起始缩放级别 (默认 18)")
    parser.add_argument("--zoom-end", type=int, default=DEFAULT_ZOOM_END,
                        help="结束缩放级别 (默认 23)")
    parser.add_argument("--attempts", type=int, default=4,
                        help="单级最大重试次数")
    args = parser.parse_args(argv)

    zoom_start = max(0, args.zoom_start)
    zoom_end = max(zoom_start, args.zoom_end)
    max_usable, _ = run_probe(
        args.style, args.lat, args.lng, zoom_start, zoom_end, args.attempts)
    return 0 if max_usable is not None else 1


if __name__ == "__main__":
    sys.exit(main())
