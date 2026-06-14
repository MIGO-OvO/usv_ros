#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线地图瓦片包格式 (Map Pack Format)
=====================================
定义离线瓦片包的 manifest schema、确定性哈希、tar 打包/解包与路径安全校验。
包结构: 单个 tar 文件, 内含 manifest.json + tiles/{style}/{z}/{x}/{y}.png

依赖方向:
  map_network_fetch  ->  VALID_STYLES
  map_tile_store     ->  CACHE_DIR

Python: 3.8
"""

from __future__ import print_function

import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time

# 允许从脚本同目录导入兄弟模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from map_network_fetch import VALID_STYLES  # noqa: E402
from map_tile_store import CACHE_DIR  # noqa: E402


# 离线瓦片包 (导出/导入) 元数据
PACK_PROVIDER = "amap"        # 底图源标识, 导入时与本机源匹配, 防止导错底图
PACK_VERSION = 1              # 包格式版本
MANIFEST_NAME = "manifest.json"
TILE_PREFIX = "tiles"         # tar 内瓦片根目录


def iter_tiles_root(root):
    """遍历瓦片目录, 产出 (style, z, x, y, abs_path)。非法路径跳过。"""
    if not os.path.isdir(root):
        return
    for style in sorted(os.listdir(root)):
        sdir = os.path.join(root, style)
        if style not in VALID_STYLES or not os.path.isdir(sdir):
            continue
        for z in sorted(os.listdir(sdir)):
            zdir = os.path.join(sdir, z)
            if not os.path.isdir(zdir):
                continue
            for x in sorted(os.listdir(zdir)):
                xdir = os.path.join(zdir, x)
                if not os.path.isdir(xdir):
                    continue
                for name in sorted(os.listdir(xdir)):
                    if not name.endswith(".png"):
                        continue
                    y = name[:-4]
                    try:
                        yield style, int(z), int(x), int(y), os.path.join(xdir, name)
                    except (TypeError, ValueError):
                        continue


def _tile_relpath(style, z, x, y):
    return "%s/%s/%d/%d/%d.png" % (TILE_PREFIX, style, z, x, y)


def hash_tiles_root(root):
    """对瓦片目录内容做确定性 sha256 (按相对路径排序, 路径+字节)。"""
    h = hashlib.sha256()
    items = sorted(
        (_tile_relpath(s, z, x, y), p) for s, z, x, y, p in iter_tiles_root(root))
    count = 0
    for rel, path in items:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        h.update(rel.encode("utf-8"))
        h.update(data)
        count += 1
    return h.hexdigest(), count


def build_manifest(tiles_root, bbox, zoom_min, zoom_max, styles, provider=PACK_PROVIDER):
    """根据瓦片目录构造 manifest 字典 (含 sha256 与瓦片数)。"""
    sha, count = hash_tiles_root(tiles_root)
    return {
        "provider": provider,
        "version": PACK_VERSION,
        "styles": [s for s in (styles or []) if s in VALID_STYLES] or list(VALID_STYLES),
        "bbox": list(bbox) if bbox else None,
        "zoom_min": int(zoom_min),
        "zoom_max": int(zoom_max),
        "tile_count": count,
        "sha256": sha,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def create_pack(tiles_root, out_path, manifest):
    """把 tiles_root 与 manifest 打包成单个 tar 文件。"""
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    with tarfile.open(out_path, "w") as tar:
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size = len(manifest_bytes)
        info.mtime = int(time.time())
        from io import BytesIO
        tar.addfile(info, BytesIO(manifest_bytes))
        for style, z, x, y, path in iter_tiles_root(tiles_root):
            tar.add(path, arcname=_tile_relpath(style, z, x, y))
    return out_path


def read_pack_manifest(pack_path):
    """从包中读取 manifest, 失败返回 None。"""
    try:
        with tarfile.open(pack_path, "r") as tar:
            member = tar.getmember(MANIFEST_NAME)
            fobj = tar.extractfile(member)
            if fobj is None:
                return None
            return json.loads(fobj.read().decode("utf-8"))
    except (tarfile.TarError, KeyError, OSError, ValueError):
        return None


def _safe_member(name):
    """拒绝路径穿越/绝对路径; 仅允许 manifest 与 tiles/ 下的相对路径。"""
    if name == MANIFEST_NAME:
        return True
    if not name.startswith(TILE_PREFIX + "/"):
        return False
    norm = os.path.normpath(name)
    return not (norm.startswith("..") or os.path.isabs(norm) or ".." in norm.split("/"))


def import_pack(pack_path, cache_dir=CACHE_DIR, provider=PACK_PROVIDER, logger=None):
    """校验并合并瓦片包到缓存。返回 (ok, summary_dict)。

    校验: provider 匹配 / 解压后 sha256 与瓦片数一致 / 防损坏 / 防路径穿越。
    合并: 同路径已存在则跳过(增量累积), 失败不动现有缓存。
    """
    log = logger or (lambda *a, **k: None)
    manifest = read_pack_manifest(pack_path)
    if not isinstance(manifest, dict):
        return False, {"message": "无效包: 缺少或损坏的 manifest"}
    if manifest.get("provider") != provider:
        return False, {"message": "底图源不匹配: 包为 %s, 本机为 %s" % (
            manifest.get("provider"), provider)}
    tmp = tempfile.mkdtemp(prefix="usv_mappack_")
    try:
        try:
            with tarfile.open(pack_path, "r") as tar:
                members = [m for m in tar.getmembers()
                           if m.isfile() and _safe_member(m.name)]
                tar.extractall(tmp, members=members)
        except tarfile.TarError as exc:
            return False, {"message": "解包失败: %s" % str(exc)}
        tiles_root = os.path.join(tmp, TILE_PREFIX)
        sha, count = hash_tiles_root(tiles_root)
        if count != int(manifest.get("tile_count", -1)):
            return False, {"message": "瓦片数校验失败: 实际 %d, 期望 %s" % (
                count, manifest.get("tile_count"))}
        if sha != manifest.get("sha256"):
            return False, {"message": "校验和不匹配, 包可能损坏"}
        added = skipped = 0
        for style, z, x, y, src in iter_tiles_root(tiles_root):
            dst = os.path.join(cache_dir, style, str(z), str(x), "%d.png" % y)
            if os.path.isfile(dst):
                skipped += 1
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                added += 1
            except OSError as exc:
                log("合并瓦片失败 %s: %s", dst, str(exc))
        return True, {
            "message": "导入完成",
            "added": added,
            "skipped": skipped,
            "tile_count": count,
            "bbox": manifest.get("bbox"),
            "zoom_min": manifest.get("zoom_min"),
            "zoom_max": manifest.get("zoom_max"),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
