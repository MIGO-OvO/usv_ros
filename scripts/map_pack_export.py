#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线地图瓦片包导出 CLI (Offline Map Tile Pack Exporter)
================================================================
在任意有网络的设备上下载/打包高德瓦片, 产出单个 tar 包 (含 manifest.json),
随后通过任意通道 (Git LFS / U盘 / scp) 传到 ROS 设备, 用 map_pack_import.py 导入。

仅依赖 Python 标准库, 复用 map_tile_cache 的端点/枚举/下载逻辑, 保证瓦片
路径与 ROS 端缓存完全一致。

两种来源:
  1. 默认: 按 bbox + 缩放范围直接联网下载并打包。
  2. --from-cache DIR: 不下载, 直接把已有缓存目录打包。

用法示例:
  # 按范围下载打包 (经纬度: 西 南 东 北)
  python3 map_pack_export.py --bbox 120.10 30.20 120.20 30.30 \
      --zoom-min 13 --zoom-max 18 --out wuhan_area.tar

  # 直接导出现有缓存目录
  python3 map_pack_export.py --from-cache ~/usv_ws/map_cache --out cache.tar

Python: 3.8
"""

from __future__ import print_function

import argparse
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

# 允许从脚本同目录导入 map_tile_cache
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import map_tile_cache as mtc  # noqa: E402


def _download_to_root(tiles_root, bbox, zoom_min, zoom_max, styles, workers):
    """按 bbox 下载瓦片到 tiles_root/{style}/{z}/{x}/{y}.png。返回 (ok, fail)。"""
    tasks, total = mtc.enumerate_tiles(bbox, zoom_min, zoom_max, styles)
    if total == 0:
        return 0, 0
    if total > mtc.MAX_PREWARM_TILES:
        print("范围过大(%d 张), 请缩小区域或降低层级" % total, file=sys.stderr)
        sys.exit(2)
    print("待下载瓦片: %d 张 (z%d-%d)" % (total, zoom_min, zoom_max))
    counter = {"ok": 0, "fail": 0, "n": 0}

    def _one(task):
        style, z, x, y = task
        dst = os.path.join(tiles_root, style, str(z), str(x), "%d.png" % y)
        if os.path.isfile(dst):
            return True
        sub = (counter["n"] % 4) + 1
        data = mtc.fetch_tile(style, z, x, y, sub)
        if not data:
            return False
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)
            return True
        except OSError:
            return False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for ok in pool.map(_one, tasks):
            counter["n"] += 1
            counter["ok" if ok else "fail"] += 1
            if counter["n"] % 200 == 0 or counter["n"] == total:
                sys.stdout.write("\r进度: %d/%d (失败 %d)" % (
                    counter["n"], total, counter["fail"]))
                sys.stdout.flush()
    print()
    return counter["ok"], counter["fail"]


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线地图瓦片包导出")
    parser.add_argument("--out", required=True, help="输出 tar 包路径")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"),
                        help="经纬度范围: 西 南 东 北 (下载模式必填)")
    parser.add_argument("--zoom-min", type=int, default=mtc.DEFAULT_ZOOM_MIN)
    parser.add_argument("--zoom-max", type=int, default=mtc.DEFAULT_ZOOM_MAX)
    parser.add_argument("--styles", nargs="+", default=list(mtc.VALID_STYLES),
                        choices=list(mtc.VALID_STYLES), help="瓦片类型")
    parser.add_argument("--workers", type=int, default=mtc.PREWARM_WORKERS)
    parser.add_argument("--from-cache", metavar="DIR",
                        help="跳过下载, 直接打包该缓存目录")
    args = parser.parse_args(argv)

    zoom_min = mtc.clamp_zoom(args.zoom_min, mtc.DEFAULT_ZOOM_MIN)
    zoom_max = mtc.clamp_zoom(args.zoom_max, mtc.DEFAULT_ZOOM_MAX)
    if zoom_min > zoom_max:
        zoom_min, zoom_max = zoom_max, zoom_min

    if args.from_cache:
        tiles_root = os.path.expanduser(args.from_cache)
        if not os.path.isdir(tiles_root):
            print("缓存目录不存在: %s" % tiles_root, file=sys.stderr)
            return 2
        bbox = None
        manifest = mtc.build_manifest(tiles_root, bbox, zoom_min, zoom_max, args.styles)
        mtc.create_pack(tiles_root, args.out, manifest)
    else:
        if not args.bbox:
            print("下载模式需提供 --bbox W S E N", file=sys.stderr)
            return 2
        bbox = (args.bbox[0], args.bbox[1], args.bbox[2], args.bbox[3])
        tmp = tempfile.mkdtemp(prefix="usv_mapexport_")
        try:
            tiles_root = os.path.join(tmp, mtc.TILE_PREFIX)
            os.makedirs(tiles_root, exist_ok=True)
            ok, fail = _download_to_root(
                tiles_root, bbox, zoom_min, zoom_max, args.styles, args.workers)
            if ok == 0:
                print("未下载到任何瓦片, 中止打包", file=sys.stderr)
                return 1
            manifest = mtc.build_manifest(tiles_root, bbox, zoom_min, zoom_max, args.styles)
            mtc.create_pack(tiles_root, args.out, manifest)
            print("下载成功 %d 张, 失败 %d 张" % (ok, fail))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    size_mb = os.path.getsize(args.out) / 1048576.0
    print("已生成包: %s (%d 张, %.1f MB)" % (
        args.out, manifest["tile_count"], size_mb))
    print("校验和: %s" % manifest["sha256"][:16])
    return 0


if __name__ == "__main__":
    sys.exit(main())
