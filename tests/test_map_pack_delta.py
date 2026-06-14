# -*- coding: utf-8 -*-
"""map_pack_delta build 半边契约测试 (T9)

锁定增量包构建器 ``build_delta`` 的核心契约 (HP-04 build 半边):

  - HP-04a: 增量包只包含 new_root 中存在但 base_root 中不存在的瓦片;
            base 已有的瓦片必须从 delta 中剔除。
  - HP-04b: 增量包 manifest.kind == "delta", manifest.base_sha256 等于
            base_root 当前的 tile_index_sha256 (或显式 base_manifest
            提供的字段, 若非空)。
  - HP-04c: base_root 的瓦片集合 ∪ delta 包内瓦片集合 == new_root 的
            瓦片集合 (在 (style, z, x, y) 维度上重建完整新集合)。
  - HP-04d: 当 new_root ⊆ base_root 时, build_delta 仍产出有效 delta 包,
            tile_count == 0, kind == "delta"。

注意: 这里只测试 build 半边; apply / diff 是 T10 的任务, 本文件不涉及。
"""

import importlib
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

# 与 test_map_pack_format_v2.py 保持同样的最小 PNG 头, 便于 hash_tiles_root
# 等内容哈希函数与索引哈希函数都能识别为合法 tile。
_PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def _ensure_scripts_on_path():
    p = str(SCRIPTS_DIR)
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


if __name__ == "__main__":
    unittest.main()
