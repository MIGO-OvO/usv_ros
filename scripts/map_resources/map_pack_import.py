#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线地图瓦片包导入 CLI (Offline Map Tile Pack Importer)
================================================================
把 map_pack_export.py 产出的 tar 包校验后合并到本机缓存目录。

校验: provider 匹配 / 瓦片数与 sha256 一致 / 防损坏 / 防路径穿越。
合并: 增量累积, 同路径已存在则跳过; 校验失败不动现有缓存。

用法示例:
  # 先看包内容 (不写入)
  python3 map_pack_import.py wuhan_area.tar --inspect

  # 校验并合并到默认缓存目录 ~/usv_ws/map_cache
  python3 map_pack_import.py wuhan_area.tar

  # 指定缓存目录
  python3 map_pack_import.py wuhan_area.tar --cache-dir /data/map_cache

Python: 3.8
"""

from __future__ import print_function

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import map_tile_cache as mtc  # noqa: E402


def _print_manifest(manifest):
    print("包元数据:")
    print("  provider : %s" % manifest.get("provider"))
    print("  styles   : %s" % ", ".join(manifest.get("styles", [])))
    print("  bbox     : %s" % manifest.get("bbox"))
    print("  zoom     : %s - %s" % (manifest.get("zoom_min"), manifest.get("zoom_max")))
    print("  tiles    : %s" % manifest.get("tile_count"))
    print("  created  : %s" % manifest.get("created_at"))
    print("  sha256   : %s" % str(manifest.get("sha256"))[:16])


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线地图瓦片包导入")
    parser.add_argument("pack", help="待导入的 tar 包路径")
    parser.add_argument("--cache-dir", default=mtc.CACHE_DIR,
                        help="目标缓存目录 (默认 ~/usv_ws/map_cache)")
    parser.add_argument("--inspect", action="store_true",
                        help="仅查看包元数据, 不写入缓存")
    args = parser.parse_args(argv)

    pack = os.path.expanduser(args.pack)
    if not os.path.isfile(pack):
        print("包文件不存在: %s" % pack, file=sys.stderr)
        return 2

    manifest = mtc.read_pack_manifest(pack)
    if not isinstance(manifest, dict):
        print("无效包: 缺少或损坏的 manifest", file=sys.stderr)
        return 1
    _print_manifest(manifest)

    if args.inspect:
        return 0

    cache_dir = os.path.expanduser(args.cache_dir)
    ok, summary = mtc.import_pack(pack, cache_dir=cache_dir,
                                  logger=lambda *a: print(a[0] % a[1:], file=sys.stderr))
    if not ok:
        print("导入失败: %s" % summary.get("message"), file=sys.stderr)
        return 1
    print("导入完成: 新增 %d 张, 跳过 %d 张 (已存在)" % (
        summary.get("added", 0), summary.get("skipped", 0)))
    print("覆盖范围: bbox=%s zoom=%s-%s" % (
        summary.get("bbox"), summary.get("zoom_min"), summary.get("zoom_max")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
