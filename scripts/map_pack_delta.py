#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线地图增量包构建器 (Map Pack Delta — Build Half)
==================================================
T9 落地 build 半边: 比对 ``new_root`` (新瓦片集) 与 ``base_root`` (基线瓦片集),
仅把 ``new_root`` 中存在、而 ``base_root`` 中不存在的瓦片打成一个增量
``.pack`` 文件。复用 ``map_pack_format`` 的 v2 manifest / tar 打包流程, 设置
``kind="delta"`` 与 ``base_sha256`` 字段, 供 T10 apply / diff 半边校验基线。

依赖方向 (单向):
  map_pack_delta  ->  map_pack_format

约束:
  - Python 3.8 兼容 (Jetson 现网); 仅依赖标准库 + map_pack_format。
  - 不修改 base_root / new_root 任何文件; 全程在临时目录 stage 后再打包。
  - 空 delta (new ⊆ base) 是合法输出: tile_count == 0 的有效 delta 包。

NOTE (设计修正): 计划文档原签名为 ``build_delta(new_root, base_manifest, ...)``,
但 v2 manifest 是基线的*指纹* (sha256 + tile_index_sha256), 并不内嵌 relpath
列表; 仅凭 manifest 无法枚举 base 已有瓦片集合, 也就无法计算
``new - base`` 差集。
所以这里把签名修正为 ``build_delta(new_root, base_root, out_path, ...)``,
``base_root`` 用于枚举 base 集; ``base_manifest`` 降级为可选参数, 仅用于覆盖
``base_sha256`` 字段 (例如调用方手上只有发布时的 manifest 但没有原始目录,
或者希望 delta 与某个历史 base 包对齐而非现场缓存)。这样 T10 apply 能用
``compute_tile_index_sha256(local_cache)`` 与 delta.base_sha256 比对,
确保增量被应用在正确的基线上。

NOTE (T10 占位): apply_delta / diff_pack 会在 T10 加入本文件,
届时需要校验 ``manifest_kind == "delta"`` 并比对 ``base_sha256`` 与目标
缓存的 ``compute_tile_index_sha256``。这里先不实现, 也不留空函数,
避免与 T10 的契约测试冲突。
"""

from __future__ import print_function

import os
import shutil
import sys
import tempfile

# 允许从脚本同目录导入兄弟模块 (与 map_pack_format / map_tile_cache 一致)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import map_pack_format as mpf  # noqa: E402


__all__ = ["build_delta"]


def _normalize_styles(styles):
    """规范 styles 参数: None -> None (代表不过滤); 否则去重并保留顺序。

    具体哪些 style 是合法的, 交给 ``map_pack_format.build_manifest`` 过滤
    (它会与 VALID_STYLES 取交集), 这里只负责去重。
    """
    if styles is None:
        return None
    seen = set()
    out = []
    for s in styles:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _collect_tile_set(tiles_root, styles):
    """枚举瓦片目录并返回 {(style, z, x, y): abs_path} 映射。

    ``styles`` 为 None 时不做风格过滤; 非 None 时仅保留指定风格瓦片。
    """
    out = {}
    style_filter = set(styles) if styles is not None else None
    for style, z, x, y, path in mpf.iter_tiles_root(tiles_root):
        if style_filter is not None and style not in style_filter:
            continue
        out[(style, z, x, y)] = path
    return out


def _stage_delta_tiles(new_tiles, base_keys, stage_root):
    """把 ``new - base`` 差集的瓦片复制到 stage_root, 维持
    ``{stage_root}/{style}/{z}/{x}/{y}.png`` 布局。

    返回 (delta_keys, zoom_min, zoom_max); 空 delta 时 zoom_min/max 都为 0。
    """
    delta_keys = []
    zooms = []
    for key, src in new_tiles.items():
        if key in base_keys:
            continue
        style, z, x, y = key
        dst_dir = os.path.join(stage_root, style, str(z), str(x))
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, "%d.png" % y)
        # copy2 保留时间戳, 让索引哈希与原 cache 在 size 维度上仍稳定。
        shutil.copy2(src, dst)
        delta_keys.append(key)
        zooms.append(z)
    if zooms:
        return delta_keys, min(zooms), max(zooms)
    return delta_keys, 0, 0


def _resolve_base_sha(base_manifest, base_root):
    """优先用调用方提供的 base_manifest.tile_index_sha256;
    否则现场计算 base_root 的 tile_index_sha256。
    """
    if isinstance(base_manifest, dict):
        sha = base_manifest.get("tile_index_sha256")
        if isinstance(sha, str) and sha:
            return sha
    return mpf.compute_tile_index_sha256(base_root)


def build_delta(new_root, base_root, out_path,
                styles=None, base_manifest=None):
    """构建增量瓦片包: 仅打包 ``new_root`` 中存在、而 ``base_root`` 中不存在
    的瓦片。

    参数:
      new_root: 新的完整瓦片目录 (内含 ``{style}/{z}/{x}/{y}.png``)。
      base_root: 基线瓦片目录, 用于枚举基线已有瓦片集合。
      out_path: 输出 ``.pack`` 文件路径 (tar)。
      styles: 可选, 限定参与比对与打包的 style 列表。None 表示不过滤,
              交由 ``build_manifest`` 与 ``VALID_STYLES`` 取交集。
      base_manifest: 可选 dict, 若含合法 ``tile_index_sha256`` 字段则
                     覆盖现算的 base 索引哈希, 用于与历史发布对齐。

    返回: 写入包内的 manifest dict (kind=="delta")。

    设计要点:
      - base_root 是必需的: 单凭 base_manifest 无法枚举瓦片集合
        (manifest 只是指纹)。
      - base_sha256 = base_manifest.tile_index_sha256 if provided
        else compute_tile_index_sha256(base_root)。这一语义被 T10
        apply 校验依赖, 不要修改。
      - 空 delta (new ⊆ base) 输出 tile_count==0 的合法 delta 包,
        kind 仍为 "delta", 便于上层做 "无需更新" 的语义判定。
    """
    styles_norm = _normalize_styles(styles)
    base_keys = set(_collect_tile_set(base_root, styles_norm).keys())
    new_tiles = _collect_tile_set(new_root, styles_norm)
    base_sha = _resolve_base_sha(base_manifest, base_root)

    stage_root = tempfile.mkdtemp(prefix="usv_packdelta_stage_")
    try:
        _delta_keys, zoom_min, zoom_max = _stage_delta_tiles(
            new_tiles, base_keys, stage_root)
        manifest = mpf.build_manifest(
            stage_root, None, zoom_min, zoom_max, styles_norm,
            kind=mpf.PACK_KIND_DELTA, base_sha256=base_sha)
        mpf.create_pack(stage_root, out_path, manifest)
        return manifest
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# T10 占位区: apply_delta / diff_pack 在 T10 任务中加入。届时:
#   - apply_delta(pack_path, cache_dir): 校验 kind=="delta",
#     compute_tile_index_sha256(cache_dir) == manifest["base_sha256"],
#     再走 import_pack 类似的合并流程。
#   - diff_pack(pack_path, cache_dir): 给出 added / missing / mismatched 集合。
# ---------------------------------------------------------------------------
