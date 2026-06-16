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
import tarfile
import tempfile
import uuid

# 允许从脚本同目录导入兄弟模块 (与 map_pack_format / map_tile_cache 一致)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import map_pack_format as mpf  # noqa: E402


__all__ = ["build_delta", "diff_pack", "apply_delta"]


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
# T10: apply / diff 半边
# ---------------------------------------------------------------------------
#
# 设计要点 (与 import_pack 对齐):
#   - 单一原子合并入口 apply_delta(pack, cache_dir): 同时支持 full / delta 包,
#     T11 会把 import_pack 的合并部分替换为 apply_delta, 让 full 与 delta 走
#     同一条 staging + 校验 + 合并路径。
#   - 校验顺序: provider -> safe members 解包 -> sha256 + tile_count ->
#     (delta 才有) base_sha256 == compute_tile_index_sha256(cache_dir)。
#     任何一步失败都不动 cache, staging 立即清理。
#   - staging 目录: cache_dir/.staging/{uuid4hex}/ , 与 cache_dir 同盘以便
#     最终走 os.replace (跨盘会退化为 shutil.copy)。
#   - 合并采用 import_pack 同款 "存在则跳过" 累积语义, 不覆盖现有瓦片。
#   - diff_pack 只读, 走完整解包 + 校验 (provider/safe/sha/count) 后再做集合
#     比对; staging 同样在 finally 里清理, 全程不动 cache_dir。


_STAGING_DIRNAME = ".staging"


def _safe_extract(pack_path, dest_dir):
    """按 mpf._safe_member 过滤后解包到 dest_dir; 返回 (ok, message)。

    member 列表过滤掉路径穿越/绝对路径成员, 与 import_pack 同款保护
    (EF-07)。tar 损坏返回 (False, message)。
    """
    try:
        with tarfile.open(pack_path, "r") as tar:
            members = [m for m in tar.getmembers()
                       if m.isfile() and mpf._safe_member(m.name)]
            tar.extractall(dest_dir, members=members)
    except tarfile.TarError as exc:
        return False, "解包失败: %s" % str(exc)
    return True, ""


def _validate_pack_payload(staged_root, manifest):
    """校验 staged 目录与 manifest 的 sha256 / tile_count 一致 (EF-06)。

    staged_root 指向 staging 内的 ``tiles/`` 目录 (与 hash_tiles_root 同口径)。
    返回 (ok, message)。
    """
    sha, count = mpf.hash_tiles_root(staged_root)
    expected_count = manifest.get("tile_count")
    try:
        expected_count_i = int(expected_count)
    except (TypeError, ValueError):
        return False, "瓦片数字段非法: %r" % (expected_count,)
    if count != expected_count_i:
        return False, "瓦片数校验失败: 实际 %d, 期望 %d" % (count, expected_count_i)
    if sha != manifest.get("sha256"):
        return False, "校验和不匹配, 包可能损坏"
    return True, ""


def _merge_staged_tiles(staged_tiles_root, cache_dir):
    """把 staged_tiles_root 下的瓦片按 import_pack 风格合并到 cache_dir。

    存在即跳过, 不覆盖 (累积语义)。返回 (added, skipped, errors[list])。
    """
    added = skipped = 0
    errors = []
    for style, z, x, y, src in mpf.iter_tiles_root(staged_tiles_root):
        dst = os.path.join(cache_dir, style, str(z), str(x), "%d.png" % y)
        if os.path.isfile(dst):
            skipped += 1
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            added += 1
        except OSError as exc:
            errors.append("合并瓦片失败 %s: %s" % (dst, str(exc)))
    return added, skipped, errors


def _make_staging_dir(cache_dir):
    """在 cache_dir/.staging/{uuid4hex}/ 建临时 staging 目录。

    放在 cache_dir 内是为了与 cache 同盘, 后续合并阶段瓦片复制不跨盘;
    .staging 前缀不在 VALID_STYLES 中, ``iter_tiles_root`` / hash 会自动忽略,
    不会被当成瓦片目录扫描。
    """
    base = os.path.join(cache_dir, _STAGING_DIRNAME)
    os.makedirs(base, exist_ok=True)
    stage = os.path.join(base, uuid.uuid4().hex)
    os.makedirs(stage, exist_ok=False)
    return stage


def _enumerate_pack_tiles(pack_path):
    """只读扫描 tar 内 tiles/ 成员, 解析回 (style,z,x,y) 集合 + 元数据。

    用于 diff_pack 不解包就能给出 would_add/would_skip 估算。返回:
      keys: set of (style,z,x,y)
      sizes: dict (style,z,x,y) -> tarfile member size
    损坏 tar 返回 (None, message)。
    """
    keys = set()
    sizes = {}
    prefix = mpf.TILE_PREFIX + "/"
    try:
        with tarfile.open(pack_path, "r") as tar:
            for m in tar.getmembers():
                if not m.isfile():
                    continue
                if not mpf._safe_member(m.name):
                    continue
                if not m.name.startswith(prefix):
                    continue
                rest = m.name[len(prefix):]
                parts = rest.split("/")
                if len(parts) != 4 or not parts[3].endswith(".png"):
                    continue
                style = parts[0]
                try:
                    z = int(parts[1])
                    x = int(parts[2])
                    y = int(parts[3][:-4])
                except (TypeError, ValueError):
                    continue
                key = (style, z, x, y)
                keys.add(key)
                sizes[key] = m.size
    except tarfile.TarError as exc:
        return None, "解包失败: %s" % str(exc)
    return keys, sizes


def _enumerate_cache_tiles(cache_dir):
    """枚举 cache_dir 下瓦片, 返回 {(style,z,x,y): size}。

    cache 不存在视为空集合。
    """
    out = {}
    if not os.path.isdir(cache_dir):
        return out
    for style, z, x, y, path in mpf.iter_tiles_root(cache_dir):
        try:
            out[(style, z, x, y)] = os.path.getsize(path)
        except OSError:
            continue
    return out


def diff_pack(pack_path, cache_dir):
    """只读分析: 不写任何文件, 评估把 pack_path 应用到 cache_dir 的影响。

    返回 dict 字段:
      ok (bool): 整体是否可解析 (manifest 合法 / tar 可读)。
      kind (str|None): "full" / "delta" / None (无效 manifest)。
      base_sha256 (str|None): 仅 delta 包有意义。
      base_match (bool|None): 仅 delta 包有意义, 包内 base_sha256 是否等于
        当前 cache 的 tile_index_sha256; full 包恒为 None。
      would_add (int): 包内有、cache 内没有的瓦片数。
      would_skip (int): 包内有、cache 内已有的瓦片数 (apply 时会被跳过)。
      conflicts (int): 包内有、cache 内已有但字节大小不同的瓦片数 (信息性,
        当前合并语义会跳过, 不覆盖)。
      tile_count (int|None): 包内 manifest 声明的瓦片数 (信息性)。
      message (str): 简要说明。
    """
    manifest = mpf.read_pack_manifest(pack_path)
    if not isinstance(manifest, dict):
        return {
            "ok": False,
            "kind": None,
            "base_sha256": None,
            "base_match": None,
            "would_add": 0,
            "would_skip": 0,
            "conflicts": 0,
            "tile_count": None,
            "message": "无效包: 缺少或损坏的 manifest",
        }
    kind = mpf.manifest_kind(manifest)
    if manifest.get("provider") != mpf.PACK_PROVIDER:
        return {
            "ok": False,
            "kind": kind,
            "base_sha256": manifest.get("base_sha256"),
            "base_match": None,
            "would_add": 0,
            "would_skip": 0,
            "conflicts": 0,
            "tile_count": manifest.get("tile_count"),
            "message": "底图源不匹配: 包为 %s, 本机为 %s" % (
                manifest.get("provider"), mpf.PACK_PROVIDER),
        }
    verify_tmp = tempfile.mkdtemp(prefix="usv_mappack_diff_")
    try:
        ok, msg = _safe_extract(pack_path, verify_tmp)
        if not ok:
            return {
                "ok": False,
                "kind": kind,
                "base_sha256": manifest.get("base_sha256"),
                "base_match": None,
                "would_add": 0,
                "would_skip": 0,
                "conflicts": 0,
                "tile_count": manifest.get("tile_count"),
                "message": msg,
            }
        ok, msg = _validate_pack_payload(
            os.path.join(verify_tmp, mpf.TILE_PREFIX), manifest)
        if not ok:
            return {
                "ok": False,
                "kind": kind,
                "base_sha256": manifest.get("base_sha256"),
                "base_match": None,
                "would_add": 0,
                "would_skip": 0,
                "conflicts": 0,
                "tile_count": manifest.get("tile_count"),
                "message": msg,
            }
    finally:
        shutil.rmtree(verify_tmp, ignore_errors=True)
    pack_keys, pack_sizes = _enumerate_pack_tiles(pack_path)
    if pack_keys is None:
        return {
            "ok": False,
            "kind": kind,
            "base_sha256": manifest.get("base_sha256"),
            "base_match": None,
            "would_add": 0,
            "would_skip": 0,
            "conflicts": 0,
            "tile_count": manifest.get("tile_count"),
            "message": pack_sizes,  # message string from _enumerate_pack_tiles
        }
    cache_sizes = _enumerate_cache_tiles(cache_dir)
    would_add = would_skip = conflicts = 0
    for key in pack_keys:
        if key in cache_sizes:
            would_skip += 1
            if cache_sizes[key] != pack_sizes.get(key):
                conflicts += 1
        else:
            would_add += 1
    base_sha = manifest.get("base_sha256")
    base_match = None
    if kind == mpf.PACK_KIND_DELTA:
        actual_base = mpf.compute_tile_index_sha256(cache_dir)
        base_match = bool(base_sha) and (actual_base == base_sha)
    return {
        "ok": True,
        "kind": kind,
        "base_sha256": base_sha,
        "base_match": base_match,
        "would_add": would_add,
        "would_skip": would_skip,
        "conflicts": conflicts,
        "tile_count": manifest.get("tile_count"),
        "message": "干跑分析完成",
    }


def apply_delta(pack_path, cache_dir, provider=mpf.PACK_PROVIDER, logger=None):
    """校验并原子合并 (full 或 delta) 瓦片包到 cache_dir。

    返回 (ok: bool, summary: dict)。失败时 cache_dir 不被修改, staging 清理。

    校验顺序:
      1. provider 必须等于 mpf.PACK_PROVIDER。
      2. tar 解包到 staging 时仅允许 mpf._safe_member (EF-07 防穿越)。
      3. staged tiles 的 sha256 与 tile_count 必须等于 manifest 声明 (EF-06)。
      4. 仅 delta: manifest.base_sha256 必须等于
         compute_tile_index_sha256(cache_dir) (EF-08)。
      5. 校验全部通过后, 逐瓦片 copy2 到 cache_dir (存在则跳过, 累积语义)。

    staging 目录: cache_dir/.staging/{uuid4hex}/ , finally 清理。
    """
    manifest = mpf.read_pack_manifest(pack_path)
    if not isinstance(manifest, dict):
        return False, {"message": "无效包: 缺少或损坏的 manifest"}
    if manifest.get("provider") != provider:
        return False, {"message": "底图源不匹配: 包为 %s, 本机为 %s" % (
            manifest.get("provider"), provider)}
    kind = mpf.manifest_kind(manifest)

    # cache_dir 必须存在 (即使 base 为空也应是已存在的目录)
    os.makedirs(cache_dir, exist_ok=True)
    stage = _make_staging_dir(cache_dir)
    try:
        ok, msg = _safe_extract(pack_path, stage)
        if not ok:
            return False, {"message": msg}
        staged_tiles_root = os.path.join(stage, mpf.TILE_PREFIX)
        ok, msg = _validate_pack_payload(staged_tiles_root, manifest)
        if not ok:
            return False, {"message": msg}
        if kind == mpf.PACK_KIND_DELTA:
            expected_base = manifest.get("base_sha256")
            actual_base = mpf.compute_tile_index_sha256(cache_dir)
            if not expected_base or expected_base != actual_base:
                return False, {
                    "message": ("增量包基线不匹配, 拒绝合并: 包期望 base "
                                "tile_index_sha256=%s, 本地缓存为 %s" % (
                                    expected_base, actual_base)),
                    "expected_base": expected_base,
                    "actual_base": actual_base,
                }
        added, skipped, errors = _merge_staged_tiles(staged_tiles_root, cache_dir)
        return True, {
            "message": "应用完成",
            "kind": kind,
            "added": added,
            "skipped": skipped,
            "errors": errors,
            "tile_count": manifest.get("tile_count"),
            "bbox": manifest.get("bbox"),
            "zoom_min": manifest.get("zoom_min"),
            "zoom_max": manifest.get("zoom_max"),
        }
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        # 清空 .staging 父目录中遗留的空目录 (best-effort, 失败忽略)
        staging_root = os.path.join(cache_dir, _STAGING_DIRNAME)
        try:
            if os.path.isdir(staging_root) and not os.listdir(staging_root):
                os.rmdir(staging_root)
        except OSError:
            pass
