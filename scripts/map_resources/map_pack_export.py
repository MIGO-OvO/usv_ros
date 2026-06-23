#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线地图瓦片包导出 CLI (Offline Map Tile Pack Exporter)
================================================================
在任意有网络的设备上下载/打包地图瓦片, 产出单个 tar 包 (含 manifest.json),
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
import math
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

# 允许从脚本同目录导入 map_tile_cache
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import map_tile_cache as mtc  # noqa: E402

DEFAULT_INTERACTIVE_CENTER = (25.314167, 110.412778)
DEFAULT_INTERACTIVE_RANGE_KM = 2.0
DEFAULT_INTERACTIVE_OUT = "guilin_area_map.tar"


def _download_to_root(tiles_root, bbox, zoom_min, zoom_max, styles, workers):
    """按 bbox 下载瓦片到 tiles_root/{style}/{z}/{x}/{y}.png。返回 (ok, fail)。"""
    tasks, total = mtc.enumerate_tiles(bbox, zoom_min, zoom_max, styles)
    if total == 0:
        return 0, 0
    if total > mtc.MAX_PREWARM_TILES:
        print("范围过大(%d 张), 请缩小区域或降低层级" % total, file=sys.stderr)
        sys.exit(2)
    print("待下载瓦片: %d 张 (z%d-%d)" % (total, zoom_min, zoom_max))
    counter = {
        "cached": 0, "downloaded": 0, "blank": 0, "timeout": 0,
        "invalid": 0, "write_failed": 0, "n": 0, "retried": 0,
    }
    sub_state = {"idx": 0}
    sub_lock = threading.Lock()

    def _next_sub():
        with sub_lock:
            sub_state["idx"] = (sub_state["idx"] % 4) + 1
            return sub_state["idx"]

    def _one(task):
        style, z, x, y = task
        dst = os.path.join(tiles_root, style, str(z), str(x), "%d.png" % y)
        if os.path.isfile(dst):
            try:
                with open(dst, "rb") as f:
                    data = f.read()
                    if mtc.verify_tile_bytes(data) and not mtc.is_blank_tile(data):
                        return "cached", 0
                os.remove(dst)
            except OSError:
                return "write_failed", 0
        result = mtc.fetch_tile_resilient(
            style, z, x, y, sub_picker=_next_sub, max_attempts=5,
            base_delay=0.2, max_delay=2.0)
        if result.status != "ok" or not mtc.verify_tile_bytes(result.data):
            return result.status, max(0, int(result.attempts) - 1)
        if mtc.is_blank_tile(result.data):
            return "blank", max(0, int(result.attempts) - 1)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".tmp"
            with open(tmp, "wb") as f:
                f.write(result.data)
            os.replace(tmp, dst)
            return "downloaded", max(0, int(result.attempts) - 1)
        except OSError:
            return "write_failed", max(0, int(result.attempts) - 1)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for status, retried in pool.map(_one, tasks):
            counter["n"] += 1
            counter["retried"] += retried
            if status in counter:
                counter[status] += 1
            else:
                counter["timeout"] += 1
            if counter["n"] % 200 == 0 or counter["n"] == total:
                fail = (counter["timeout"] + counter["invalid"] +
                        counter["blank"] + counter["write_failed"])
                sys.stdout.write(
                    "\r进度: %d/%d (缓存 %d 下载 %d 失败 %d 重试 %d)" % (
                        counter["n"], total, counter["cached"],
                        counter["downloaded"], fail, counter["retried"]))
                sys.stdout.flush()
    print()
    fail = (counter["timeout"] + counter["invalid"] +
            counter["blank"] + counter["write_failed"])
    if fail:
        print("失败分类: timeout=%d invalid=%d blank=%d write_failed=%d" % (
            counter["timeout"], counter["invalid"], counter["blank"],
            counter["write_failed"]))
    return counter["cached"] + counter["downloaded"], fail


def _prompt_text(label, default):
    try:
        raw = input("%s [%s]: " % (label, default)).strip()
    except EOFError:
        raw = ""
    return raw or str(default)


def _prompt_int(label, default, lo=None, hi=None):
    while True:
        raw = _prompt_text(label, default)
        try:
            value = int(raw)
            if lo is not None:
                value = max(lo, value)
            if hi is not None:
                value = min(hi, value)
            return value
        except ValueError:
            print("请输入整数。")


def _prompt_float(label, default, lo=None, hi=None):
    while True:
        raw = _prompt_text(label, default)
        try:
            value = float(raw)
            if lo is not None:
                value = max(lo, value)
            if hi is not None:
                value = min(hi, value)
            return value
        except ValueError:
            print("请输入数字。")


def _bbox_from_center_range(lat, lng, range_km):
    lat = max(-85.0, min(85.0, float(lat)))
    lng = max(-180.0, min(180.0, float(lng)))
    distance_km = max(0.001, float(range_km))
    km_per_lat_deg = 111.32
    cos_lat = max(0.01, math.cos(math.radians(lat)))
    km_per_lng_deg = km_per_lat_deg * cos_lat
    south = max(-85.0, lat - distance_km / km_per_lat_deg)
    north = min(85.0, lat + distance_km / km_per_lat_deg)
    west = max(-180.0, lng - distance_km / km_per_lng_deg)
    east = min(180.0, lng + distance_km / km_per_lng_deg)
    return (west, south, east, north)


def _prompt_center_range_bbox(default_center, default_range_km):
    lat = _prompt_float("中心纬度(北纬为正)", default_center[0], -85.0, 85.0)
    lng = _prompt_float("中心经度(东经为正)", default_center[1], -180.0, 180.0)
    range_km = _prompt_float("东南西北边界到中心距离 km", default_range_km, 0.001, None)
    bbox = _bbox_from_center_range(lat, lng, range_km)
    print("下载范围 bbox(W,S,E,N): %.6f %.6f %.6f %.6f" % bbox)
    return bbox


def _prompt_styles(default):
    default_text = ",".join(default)
    valid = set(mtc.VALID_STYLES)
    while True:
        raw = _prompt_text("瓦片类型(默认谷歌 gsatellite/gannotation), 逗号分隔", default_text)
        styles = [s.strip() for s in raw.replace(";", ",").split(",") if s.strip()]
        if styles and all(style in valid for style in styles):
            return styles
        print("可选瓦片类型: %s" % ", ".join(mtc.VALID_STYLES))


def _fill_interactive_args(args):
    print("USV 地图离线包导出向导 (默认谷歌国际版瓦片, WGS-84)")
    mode = _prompt_text("来源 download/from-cache", "download").lower()
    if mode in ("cache", "from-cache", "from_cache"):
        args.from_cache = args.from_cache or _prompt_text("已有缓存目录", mtc.CACHE_DIR)
    args.out = args.out or _prompt_text("输出 tar 包路径", DEFAULT_INTERACTIVE_OUT)
    args.styles = _prompt_styles(args.styles)
    args.zoom_min = _prompt_int("最小缩放级别", args.zoom_min, mtc.ZOOM_HARD_MIN, mtc.ZOOM_HARD_MAX)
    args.zoom_max = _prompt_int("最大缩放级别", args.zoom_max, mtc.ZOOM_HARD_MIN, mtc.ZOOM_HARD_MAX)
    if args.zoom_min > args.zoom_max:
        args.zoom_min, args.zoom_max = args.zoom_max, args.zoom_min
    args.workers = _prompt_int("并发下载线程", args.workers, 1, 32)
    if not args.from_cache:
        args.bbox = args.bbox or _prompt_center_range_bbox(
            DEFAULT_INTERACTIVE_CENTER, DEFAULT_INTERACTIVE_RANGE_KM)
        bbox = (args.bbox[0], args.bbox[1], args.bbox[2], args.bbox[3])
        _, total = mtc.enumerate_tiles(bbox, args.zoom_min, args.zoom_max, args.styles)
        print("预计下载瓦片: %d 张 (z%d-z%d)" % (total, args.zoom_min, args.zoom_max))
        confirm = _prompt_text("开始下载并打包? y/n", "y").lower()
        if confirm not in ("y", "yes"):
            args.cancelled = True


def main(argv=None):
    raw_argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="离线地图瓦片包导出")
    parser.add_argument("--out", help="输出 tar 包路径")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"),
                        help="经纬度范围: 西 南 东 北 (下载模式必填)")
    parser.add_argument("--zoom-min", type=int, default=mtc.DEFAULT_ZOOM_MIN)
    parser.add_argument("--zoom-max", type=int, default=mtc.DEFAULT_ZOOM_MAX)
    parser.add_argument("--styles", nargs="+", default=list(mtc.DEFAULT_PREWARM_STYLES),
                        choices=list(mtc.VALID_STYLES), help="瓦片类型")
    parser.add_argument("--workers", type=int, default=mtc.PREWARM_WORKERS)
    parser.add_argument("--from-cache", metavar="DIR",
                        help="跳过下载, 直接打包该缓存目录")
    parser.add_argument("--work-dir", metavar="DIR",
                        help="下载模式断点缓存目录; 默认 <out去后缀>.work")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="交互式问询输出路径、经纬度范围和缩放级别")
    args = parser.parse_args(argv)
    args.cancelled = False

    interactive = args.interactive or len(raw_argv) == 0
    if interactive:
        _fill_interactive_args(args)
        if args.cancelled:
            print("已取消。")
            return 1
    if not args.out:
        parser.error("需要 --out, 或直接无参数运行进入交互式向导")

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
        out_abs = os.path.abspath(os.path.expanduser(args.out))
        work_root = os.path.abspath(os.path.expanduser(
            args.work_dir or (os.path.splitext(out_abs)[0] + ".work")))
        tiles_root = os.path.join(work_root, mtc.TILE_PREFIX)
        os.makedirs(tiles_root, exist_ok=True)
        print("断点缓存目录: %s" % work_root)
        ok, fail = _download_to_root(
            tiles_root, bbox, zoom_min, zoom_max, args.styles, args.workers)
        if ok == 0:
            print("未下载到任何瓦片, 中止打包", file=sys.stderr)
            return 1
        manifest = mtc.build_manifest(tiles_root, bbox, zoom_min, zoom_max, args.styles)
        os.makedirs(os.path.dirname(out_abs) or ".", exist_ok=True)
        mtc.create_pack(tiles_root, out_abs, manifest)
        args.out = out_abs
        print("可用瓦片 %d 张, 本轮失败 %d 张" % (ok, fail))

    size_mb = os.path.getsize(args.out) / 1048576.0
    print("已生成包: %s (%d 张, %.1f MB)" % (
        args.out, manifest["tile_count"], size_mb))
    print("校验和: %s" % manifest["sha256"][:16])
    return 0


if __name__ == "__main__":
    sys.exit(main())
