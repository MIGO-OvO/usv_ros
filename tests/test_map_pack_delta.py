# -*- coding: utf-8 -*-
"""map_pack_delta build + apply 半边契约测试 (T9 + T10)

T9 已锁定 ``build_delta`` 的契约 (HP-04 build 半边)。
T10 在本文件追加 ``diff_pack`` / ``apply_delta`` 的契约 (HP-04 apply 半边):

  - HP-04 apply: 把 delta 应用到匹配基线的缓存, 缓存被原子合并为
                  base ∪ delta (新瓦片 added, 已有瓦片 skipped)。
  - EF-06 corrupt: 包损坏 (sha / tile_count 不一致) -> 拒绝,
                   缓存不动, staging 清理。
  - EF-07 traversal: tar 含路径穿越成员 (例如 tiles/../../evil.png) ->
                     拒绝/过滤, 缓存与缓存外路径都不出现 evil.png。
  - EF-08 base mismatch: delta.base_sha256 与现场缓存
                          tile_index_sha256 不等 -> 拒绝, 给出
                          expected/actual, 缓存不动。
  - diff dry-run: diff_pack 不写入任何文件, 只返回 would_add /
                   would_skip / conflicts / base_match 等只读分析。
"""

import importlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
MAP_RESOURCES_DIR = SCRIPTS_DIR / "map_resources"

# 与 test_map_pack_format_v2.py 保持同样的最小 PNG 头, 便于 hash_tiles_root
# 等内容哈希函数与索引哈希函数都能识别为合法 tile。
_PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def _ensure_scripts_on_path():
    for path in (SCRIPTS_DIR, MAP_RESOURCES_DIR):
        p = str(path)
        if p not in sys.path:
            sys.path.insert(0, p)


def _fresh_import(name):
    _ensure_scripts_on_path()
    if name in sys.modules:
        del sys.modules[name]
    return importlib.import_module(name)


def _make_tile(tiles_root, style, z, x, y, payload=b""):
    """在 tiles_root/{style}/{z}/{x}/{y}.png 写入一个最小 PNG。"""
    d = os.path.join(tiles_root, style, str(z), str(x))
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "%d.png" % y)
    with open(p, "wb") as f:
        f.write(_PNG_HEAD + b"0" * 200 + payload)
    return p


def _tile_set_from_root(mpf, root, styles=None):
    s = set()
    for style, z, x, y, _path in mpf.iter_tiles_root(root):
        if styles is not None and style not in styles:
            continue
        s.add((style, z, x, y))
    return s


def _tile_set_from_pack(mpf, pack_path):
    """从 tar 包中枚举所有 tiles/ 下文件, 解析回 (style,z,x,y)。"""
    s = set()
    with tarfile.open(pack_path, "r") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            name = m.name
            prefix = mpf.TILE_PREFIX + "/"
            if not name.startswith(prefix):
                continue
            rest = name[len(prefix):]
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
            s.add((style, z, x, y))
    return s


class _Sandbox(object):
    """临时目录上下文, base/new/out 三个子目录, 自动清理。"""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="usv_packdelta_")
        self.base = os.path.join(self.root, "base")
        self.new = os.path.join(self.root, "new")
        os.makedirs(self.base, exist_ok=True)
        os.makedirs(self.new, exist_ok=True)
        self.out = os.path.join(self.root, "delta.pack")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class BuildDeltaExclusionTests(unittest.TestCase):
    """HP-04a: delta 必须排除 base_root 已有瓦片。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.mpd = _fresh_import("map_pack_delta")
        self.sb = _Sandbox()
        # base: A=(13,100,200), B=(13,100,201)
        _make_tile(self.sb.base, "satellite", 13, 100, 200, payload=b"A")
        _make_tile(self.sb.base, "satellite", 13, 100, 201, payload=b"B")
        # new: A,B + C=(13,100,202), D=(14,200,400)
        _make_tile(self.sb.new, "satellite", 13, 100, 200, payload=b"A")
        _make_tile(self.sb.new, "satellite", 13, 100, 201, payload=b"B")
        _make_tile(self.sb.new, "satellite", 13, 100, 202, payload=b"C")
        _make_tile(self.sb.new, "satellite", 14, 200, 400, payload=b"D")

    def tearDown(self):
        self.sb.close()

    def test_delta_only_contains_new_tiles(self):
        manifest = self.mpd.build_delta(self.sb.new, self.sb.base, self.sb.out)
        self.assertEqual(manifest["tile_count"], 2)
        self.assertEqual(manifest["kind"], "delta")
        delta_set = _tile_set_from_pack(self.mpf, self.sb.out)
        self.assertEqual(
            delta_set,
            {("satellite", 13, 100, 202), ("satellite", 14, 200, 400)},
        )

    def test_delta_pack_round_trip_via_read(self):
        manifest = self.mpd.build_delta(self.sb.new, self.sb.base, self.sb.out)
        loaded = self.mpf.read_pack_manifest(self.sb.out)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["kind"], "delta")
        self.assertEqual(loaded["tile_count"], manifest["tile_count"])
        self.assertEqual(loaded["base_sha256"], manifest["base_sha256"])


class BuildDeltaBaseShaTests(unittest.TestCase):
    """HP-04b: kind=delta, base_sha256 = base_root 的 tile_index_sha256。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.mpd = _fresh_import("map_pack_delta")
        self.sb = _Sandbox()
        _make_tile(self.sb.base, "satellite", 13, 100, 200)
        _make_tile(self.sb.new, "satellite", 13, 100, 200)
        _make_tile(self.sb.new, "satellite", 13, 100, 201)

    def tearDown(self):
        self.sb.close()

    def test_base_sha_matches_base_root_index(self):
        expected = self.mpf.compute_tile_index_sha256(self.sb.base)
        manifest = self.mpd.build_delta(self.sb.new, self.sb.base, self.sb.out)
        self.assertEqual(manifest["kind"], "delta")
        self.assertEqual(manifest["base_sha256"], expected)
        self.assertEqual(len(manifest["base_sha256"]), 64)

    def test_base_manifest_override_takes_precedence(self):
        # 显式提供 base_manifest 时, 用其 tile_index_sha256, 而非现算 base_root。
        injected = "f" * 64
        manifest = self.mpd.build_delta(
            self.sb.new, self.sb.base, self.sb.out,
            base_manifest={"tile_index_sha256": injected, "kind": "full"},
        )
        self.assertEqual(manifest["base_sha256"], injected)


class BuildDeltaReconstructTests(unittest.TestCase):
    """HP-04c: base ∪ delta 必须能重建 new 的完整瓦片集合。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.mpd = _fresh_import("map_pack_delta")
        self.sb = _Sandbox()
        # base 含若干瓦片
        for y in (200, 201, 202):
            _make_tile(self.sb.base, "satellite", 13, 100, y)
        _make_tile(self.sb.base, "annotation", 13, 100, 200)
        # new 是 base 的真超集 (跨 style 和 zoom)
        for y in (200, 201, 202, 203, 204):
            _make_tile(self.sb.new, "satellite", 13, 100, y)
        _make_tile(self.sb.new, "satellite", 14, 200, 400)
        _make_tile(self.sb.new, "annotation", 13, 100, 200)
        _make_tile(self.sb.new, "annotation", 13, 100, 201)

    def tearDown(self):
        self.sb.close()

    def test_base_plus_delta_equals_new(self):
        self.mpd.build_delta(self.sb.new, self.sb.base, self.sb.out)
        base_set = _tile_set_from_root(self.mpf, self.sb.base)
        delta_set = _tile_set_from_pack(self.mpf, self.sb.out)
        new_set = _tile_set_from_root(self.mpf, self.sb.new)
        # base 与 delta 不重叠
        self.assertFalse(base_set & delta_set)
        # 并集复原 new 集
        self.assertEqual(base_set | delta_set, new_set)


class BuildDeltaEmptyTests(unittest.TestCase):
    """HP-04d: new ⊆ base 时, delta 仍是合法包, tile_count==0。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.mpd = _fresh_import("map_pack_delta")
        self.sb = _Sandbox()
        _make_tile(self.sb.base, "satellite", 13, 100, 200)
        _make_tile(self.sb.base, "satellite", 13, 100, 201)
        # new 只有 base 已有的子集
        _make_tile(self.sb.new, "satellite", 13, 100, 200)

    def tearDown(self):
        self.sb.close()

    def test_empty_delta_is_valid_pack(self):
        manifest = self.mpd.build_delta(self.sb.new, self.sb.base, self.sb.out)
        self.assertEqual(manifest["kind"], "delta")
        self.assertEqual(manifest["tile_count"], 0)
        loaded = self.mpf.read_pack_manifest(self.sb.out)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["kind"], "delta")
        self.assertEqual(loaded["tile_count"], 0)
        # tar 中不应含任何 tiles/* 文件
        self.assertEqual(_tile_set_from_pack(self.mpf, self.sb.out), set())


class BuildDeltaStylesFilterTests(unittest.TestCase):
    """styles 过滤: 仅指定 style 参与 base/new 集合比对与打包。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.mpd = _fresh_import("map_pack_delta")
        self.sb = _Sandbox()
        _make_tile(self.sb.base, "satellite", 13, 100, 200)
        _make_tile(self.sb.new, "satellite", 13, 100, 200)
        _make_tile(self.sb.new, "satellite", 13, 100, 201)
        # annotation 只在 new 出现, 但 styles=["satellite"] 时应被忽略
        _make_tile(self.sb.new, "annotation", 13, 100, 999)

    def tearDown(self):
        self.sb.close()

    def test_styles_filter_excludes_other_styles(self):
        self.mpd.build_delta(
            self.sb.new, self.sb.base, self.sb.out, styles=["satellite"])
        delta_set = _tile_set_from_pack(self.mpf, self.sb.out)
        self.assertEqual(delta_set, {("satellite", 13, 100, 201)})


# ---------------------------------------------------------------------------
# T10 apply / diff_pack 测试 (HP-04 apply, EF-06, EF-07, EF-08, dry-run)
# ---------------------------------------------------------------------------


class _ApplySandbox(object):
    """T10 沙箱: cache (合并目标) + new (用于 build_delta 的源) + out 路径。

    cache_dir 直接作为 ``mpf.iter_tiles_root`` / ``compute_tile_index_sha256``
    的 root, 与 import_pack 一致 (cache 根直接放 {style}/{z}/{x}/{y}.png)。
    """

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="usv_packdelta_apply_")
        self.cache = os.path.join(self.root, "cache")
        self.new = os.path.join(self.root, "new")
        os.makedirs(self.cache, exist_ok=True)
        os.makedirs(self.new, exist_ok=True)
        self.out = os.path.join(self.root, "delta.pack")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def _count_cache_tiles(mpf, root):
    """统计缓存内瓦片数 (跟 hash_tiles_root 同口径)。"""
    n = 0
    for _ in mpf.iter_tiles_root(root):
        n += 1
    return n


def _walk_files(root):
    """枚举 root 下所有普通文件的相对路径 (用于断言无路径穿越残留)。"""
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            out.append(os.path.relpath(full, root))
    return out


class ApplyDeltaHappyPathTests(unittest.TestCase):
    """HP-04 apply: 把 delta 应用到匹配基线的 cache, 缓存被合并为 base ∪ delta。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.mpd = _fresh_import("map_pack_delta")
        self.sb = _ApplySandbox()
        # base 缓存: A,B 已在 cache 里
        _make_tile(self.sb.cache, "satellite", 13, 100, 200, payload=b"A")
        _make_tile(self.sb.cache, "satellite", 13, 100, 201, payload=b"B")
        # new: A,B + C,D
        _make_tile(self.sb.new, "satellite", 13, 100, 200, payload=b"A")
        _make_tile(self.sb.new, "satellite", 13, 100, 201, payload=b"B")
        _make_tile(self.sb.new, "satellite", 13, 100, 202, payload=b"C")
        _make_tile(self.sb.new, "satellite", 14, 200, 400, payload=b"D")

    def tearDown(self):
        self.sb.close()

    def test_apply_delta_merges_new_tiles(self):
        # 用 cache 当 base_root, 这样 base_sha256 一定与 cache 当前指纹匹配
        manifest = self.mpd.build_delta(self.sb.new, self.sb.cache, self.sb.out)
        self.assertEqual(manifest["tile_count"], 2)
        ok, summary = self.mpd.apply_delta(self.sb.out, self.sb.cache)
        self.assertTrue(ok, msg=summary)
        self.assertEqual(summary["added"], 2)
        # cache 现在应有 A,B,C,D 四块瓦片
        self.assertEqual(_count_cache_tiles(self.mpf, self.sb.cache), 4)
        # 验证 C,D 已落盘
        self.assertTrue(os.path.isfile(
            os.path.join(self.sb.cache, "satellite", "13", "100", "202.png")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.sb.cache, "satellite", "14", "200", "400.png")))

    def test_apply_full_pack_also_works(self):
        # apply_delta 必须同时支持 full 包 (kind=='full' 不做 base 校验)
        # 先把 new_root 作为 source 打成 full 包
        full_pack = os.path.join(self.sb.root, "full.pack")
        manifest = self.mpf.build_manifest(self.sb.new, None, 13, 14, ["satellite"])
        self.mpf.create_pack(self.sb.new, full_pack, manifest)
        ok, summary = self.mpd.apply_delta(full_pack, self.sb.cache)
        self.assertTrue(ok, msg=summary)
        # cache 已有 A,B; full 包里 A,B,C,D 全有 -> 新增 C,D, 跳过 A,B
        self.assertEqual(summary["added"], 2)
        self.assertEqual(summary["skipped"], 2)


class ApplyDeltaBaseMismatchTests(unittest.TestCase):
    """EF-08: delta.base_sha256 与现场 cache 的 tile_index_sha256 不一致 -> 拒绝。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.mpd = _fresh_import("map_pack_delta")
        self.sb = _ApplySandbox()
        _make_tile(self.sb.cache, "satellite", 13, 100, 200, payload=b"A")
        _make_tile(self.sb.cache, "satellite", 13, 100, 201, payload=b"B")
        _make_tile(self.sb.new, "satellite", 13, 100, 200, payload=b"A")
        _make_tile(self.sb.new, "satellite", 13, 100, 201, payload=b"B")
        _make_tile(self.sb.new, "satellite", 13, 100, 202, payload=b"C")

    def tearDown(self):
        self.sb.close()

    def test_base_mismatch_rejected_and_cache_unchanged(self):
        # 1) 基于当前 cache 状态构建 delta (base_sha256 = 当前 index)
        self.mpd.build_delta(self.sb.new, self.sb.cache, self.sb.out)
        # 2) 然后改变 cache 状态 -> tile_index_sha256 变化
        _make_tile(self.sb.cache, "satellite", 13, 100, 250, payload=b"E")
        before_count = _count_cache_tiles(self.mpf, self.sb.cache)
        before_index = self.mpf.compute_tile_index_sha256(self.sb.cache)
        # 3) 现在 apply 应该失败 (base mismatch)
        ok, summary = self.mpd.apply_delta(self.sb.out, self.sb.cache)
        self.assertFalse(ok)
        self.assertIn("expected_base", summary)
        self.assertIn("actual_base", summary)
        self.assertNotEqual(summary["expected_base"], summary["actual_base"])
        self.assertEqual(summary["actual_base"], before_index)
        self.assertIn("基线", summary.get("message", ""))
        # 4) cache 状态没变
        self.assertEqual(_count_cache_tiles(self.mpf, self.sb.cache), before_count)
        self.assertEqual(self.mpf.compute_tile_index_sha256(self.sb.cache),
                         before_index)


class ApplyDeltaCorruptTests(unittest.TestCase):
    """EF-06: 包损坏 (sha256 / tile_count 不一致) -> 拒绝, 缓存不动。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.mpd = _fresh_import("map_pack_delta")
        self.sb = _ApplySandbox()
        _make_tile(self.sb.cache, "satellite", 13, 100, 200, payload=b"A")
        _make_tile(self.sb.new, "satellite", 13, 100, 200, payload=b"A")
        _make_tile(self.sb.new, "satellite", 13, 100, 201, payload=b"B")

    def tearDown(self):
        self.sb.close()

    def _build_then_tamper_tile_count(self):
        """构建有效 delta 后改写 tar 内 manifest 的 tile_count。"""
        self.mpd.build_delta(self.sb.new, self.sb.cache, self.sb.out)
        # 读出 manifest, 篡改后重建 tar (只改 manifest, 不动 tiles)
        manifest = self.mpf.read_pack_manifest(self.sb.out)
        manifest["tile_count"] = 999  # 与实际不符
        # 把 manifest 与原 tar 内 tiles/* 重新打包
        bad_path = os.path.join(self.sb.root, "bad.pack")
        with tarfile.open(self.sb.out, "r") as src, \
                tarfile.open(bad_path, "w") as dst:
            mb = json.dumps(manifest).encode("utf-8")
            info = tarfile.TarInfo(self.mpf.MANIFEST_NAME)
            info.size = len(mb)
            dst.addfile(info, io.BytesIO(mb))
            for m in src.getmembers():
                if m.name == self.mpf.MANIFEST_NAME:
                    continue
                f = src.extractfile(m)
                if f is None:
                    continue
                data = f.read()
                ni = tarfile.TarInfo(m.name)
                ni.size = len(data)
                dst.addfile(ni, io.BytesIO(data))
        return bad_path

    def test_apply_rejects_bad_tile_count_and_cache_unchanged(self):
        bad = self._build_then_tamper_tile_count()
        before_count = _count_cache_tiles(self.mpf, self.sb.cache)
        before_index = self.mpf.compute_tile_index_sha256(self.sb.cache)
        ok, summary = self.mpd.apply_delta(bad, self.sb.cache)
        self.assertFalse(ok)
        self.assertIn("message", summary)
        self.assertEqual(_count_cache_tiles(self.mpf, self.sb.cache),
                         before_count)
        self.assertEqual(self.mpf.compute_tile_index_sha256(self.sb.cache),
                         before_index)


class ApplyDeltaTraversalTests(unittest.TestCase):
    """EF-07: tar 含路径穿越成员 (tiles/../../evil.png) -> 必须拒绝该成员,
    且缓存与缓存外路径都不会出现 evil.png。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.mpd = _fresh_import("map_pack_delta")
        self.sb = _ApplySandbox()
        _make_tile(self.sb.cache, "satellite", 13, 100, 200, payload=b"A")

    def tearDown(self):
        self.sb.close()

    def _craft_traversal_pack(self):
        """手工构造一个 tar:
        - 合法 manifest (kind=delta, 但 sha256/tile_count 故意失配)
        - 包内含一个穿越成员 tiles/../../evil.png
        即使后续校验通过, 穁越成员也必须在解包阶段被 _safe_member 过滤。
        """
        path = os.path.join(self.sb.root, "evil.pack")
        evil_payload = _PNG_HEAD + b"X" * 200
        manifest = {
            "provider": self.mpf.PACK_PROVIDER,
            "version": self.mpf.PACK_VERSION,
            "kind": self.mpf.PACK_KIND_DELTA,
            "base_sha256": self.mpf.compute_tile_index_sha256(self.sb.cache),
            "styles": ["satellite"],
            "bbox": None,
            "zoom_min": 0,
            "zoom_max": 0,
            "tile_count": 0,
            "sha256": "0" * 64,
            "tile_index_sha256": "0" * 64,
            "created_at": "2026-06-14T00:00:00",
        }
        with tarfile.open(path, "w") as tar:
            mb = json.dumps(manifest).encode("utf-8")
            mi = tarfile.TarInfo(self.mpf.MANIFEST_NAME)
            mi.size = len(mb)
            tar.addfile(mi, io.BytesIO(mb))
            evil = tarfile.TarInfo("tiles/../../evil.png")
            evil.size = len(evil_payload)
            tar.addfile(evil, io.BytesIO(evil_payload))
        return path

    def test_traversal_member_is_rejected_and_does_not_escape(self):
        evil_pack = self._craft_traversal_pack()
        before_count = _count_cache_tiles(self.mpf, self.sb.cache)
        ok, _summary = self.mpd.apply_delta(evil_pack, self.sb.cache)
        # 不论返回 True / False, 都不允许 evil.png 出现在沙箱根、cache、
        # 或 cache 上一层目录。
        for probe_root in (self.sb.root, self.sb.cache,
                           os.path.dirname(self.sb.cache)):
            for rel in _walk_files(probe_root):
                self.assertNotIn("evil.png", rel,
                                 msg="穁越成员落盘了: %s" % rel)
        # cache 瓦片数不应被穁越成员扩张
        self.assertEqual(_count_cache_tiles(self.mpf, self.sb.cache),
                         before_count)
        # 校验失败本身也是合法结果 (manifest 故意 sha 失配)
        self.assertFalse(ok)


class DiffPackDryRunTests(unittest.TestCase):
    """diff_pack: 只读分析, 不写任何文件。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.mpd = _fresh_import("map_pack_delta")
        self.sb = _ApplySandbox()
        _make_tile(self.sb.cache, "satellite", 13, 100, 200, payload=b"A")
        _make_tile(self.sb.cache, "satellite", 13, 100, 201, payload=b"B")
        _make_tile(self.sb.new, "satellite", 13, 100, 200, payload=b"A")
        _make_tile(self.sb.new, "satellite", 13, 100, 201, payload=b"B")
        _make_tile(self.sb.new, "satellite", 13, 100, 202, payload=b"C")
        _make_tile(self.sb.new, "satellite", 14, 200, 400, payload=b"D")

    def tearDown(self):
        self.sb.close()

    def test_diff_pack_against_matching_base(self):
        manifest = self.mpd.build_delta(self.sb.new, self.sb.cache, self.sb.out)
        # 计算 dry-run 前后的快照, 任何一项变化都说明 diff_pack 写盘了
        before_index = self.mpf.compute_tile_index_sha256(self.sb.cache)
        before_count = _count_cache_tiles(self.mpf, self.sb.cache)
        before_files = sorted(_walk_files(self.sb.cache))
        info = self.mpd.diff_pack(self.sb.out, self.sb.cache)
        self.assertEqual(info["kind"], "delta")
        self.assertTrue(info["base_match"])
        self.assertEqual(info["would_add"], manifest["tile_count"])
        self.assertEqual(info["tile_count"], manifest["tile_count"])
        # diff_pack 不写任何东西
        self.assertEqual(self.mpf.compute_tile_index_sha256(self.sb.cache),
                         before_index)
        self.assertEqual(_count_cache_tiles(self.mpf, self.sb.cache),
                         before_count)
        self.assertEqual(sorted(_walk_files(self.sb.cache)), before_files)

    def test_diff_pack_against_mismatched_base_flags_it(self):
        self.mpd.build_delta(self.sb.new, self.sb.cache, self.sb.out)
        # 改变 cache, 让 base 不匹配
        _make_tile(self.sb.cache, "satellite", 13, 100, 250, payload=b"E")
        info = self.mpd.diff_pack(self.sb.out, self.sb.cache)
        self.assertEqual(info["kind"], "delta")
        self.assertFalse(info["base_match"])


if __name__ == "__main__":
    unittest.main()
