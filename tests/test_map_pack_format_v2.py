# -*- coding: utf-8 -*-
"""map_pack_format v2 schema 契约测试 (T8)

锁定 manifest v2 的新增字段与 v1 向下兼容:
  - PACK_VERSION == 2
  - build_manifest 输出 kind / base_sha256 / tile_index_sha256
  - 新增 compute_tile_index_sha256 (relpath+size 的轻量索引哈希)
  - 新增 manifest_kind: v1 (无 kind) 视为 full
  - delta 与 full kind 经 create_pack/read_pack_manifest 往返保持
  - import_pack 仍接受 v1 包 (新字段缺失不致校验失败)
"""

import importlib
import importlib.util
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

# 一字节 PNG 头 + 占位字节, 便于哈希时区分内容
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


class _Sandbox(object):
    """临时目录上下文, 自动清理。"""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="usv_packfmt_v2_")
        self.tiles = os.path.join(self.root, "tiles")
        os.makedirs(self.tiles, exist_ok=True)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class PackFormatV2SchemaTests(unittest.TestCase):
    """v2 manifest 必含的新字段和版本号。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.sb = _Sandbox()
        _make_tile(self.sb.tiles, "satellite", 13, 100, 200)
        _make_tile(self.sb.tiles, "satellite", 13, 100, 201)

    def tearDown(self):
        self.sb.close()

    def test_pack_version_is_two(self):
        self.assertEqual(self.mpf.PACK_VERSION, 2)

    def test_build_manifest_full_default(self):
        m = self.mpf.build_manifest(
            self.sb.tiles, [0, 0, 1, 1], 13, 13, ["satellite"])
        self.assertEqual(m["version"], 2)
        self.assertIn("kind", m)
        self.assertEqual(m["kind"], "full")
        self.assertIn("base_sha256", m)
        self.assertIsNone(m["base_sha256"])
        self.assertIn("tile_index_sha256", m)
        self.assertIsInstance(m["tile_index_sha256"], str)
        self.assertEqual(len(m["tile_index_sha256"]), 64)
        # 旧字段必须仍存在
        for k in ("provider", "version", "styles", "bbox",
                  "zoom_min", "zoom_max", "tile_count", "sha256",
                  "created_at"):
            self.assertIn(k, m)

    def test_build_manifest_delta_kind(self):
        base = "a" * 64
        m = self.mpf.build_manifest(
            self.sb.tiles, None, 13, 13, ["satellite"],
            kind="delta", base_sha256=base)
        self.assertEqual(m["kind"], "delta")
        self.assertEqual(m["base_sha256"], base)


class TileIndexShaTests(unittest.TestCase):
    """compute_tile_index_sha256 必须确定且对集合敏感。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.sb = _Sandbox()

    def tearDown(self):
        self.sb.close()

    def test_index_hash_deterministic(self):
        _make_tile(self.sb.tiles, "satellite", 13, 100, 200)
        _make_tile(self.sb.tiles, "satellite", 13, 100, 201)
        h1 = self.mpf.compute_tile_index_sha256(self.sb.tiles)
        h2 = self.mpf.compute_tile_index_sha256(self.sb.tiles)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_index_hash_changes_when_tile_added(self):
        _make_tile(self.sb.tiles, "satellite", 13, 100, 200)
        before = self.mpf.compute_tile_index_sha256(self.sb.tiles)
        _make_tile(self.sb.tiles, "satellite", 13, 100, 201)
        after = self.mpf.compute_tile_index_sha256(self.sb.tiles)
        self.assertNotEqual(before, after)

    def test_index_hash_does_not_read_bytes(self):
        """同 relpath 同 size 不同字节内容 -> 索引哈希不变 (设计契约)。"""
        p = _make_tile(self.sb.tiles, "satellite", 13, 100, 200)
        h1 = self.mpf.compute_tile_index_sha256(self.sb.tiles)
        # 仅修改字节内容 (替换最后一个字符), 大小保持不变
        with open(p, "rb") as f:
            data = bytearray(f.read())
        data[-1] = data[-1] ^ 0xFF
        with open(p, "wb") as f:
            f.write(bytes(data))
        self.assertEqual(os.path.getsize(p), len(data))
        h2 = self.mpf.compute_tile_index_sha256(self.sb.tiles)
        self.assertEqual(h1, h2)


class ManifestKindHelperTests(unittest.TestCase):
    """manifest_kind: v1 缺失 kind -> "full"; 非法值兜底 "full"。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")

    def test_v1_missing_kind_treated_as_full(self):
        v1 = {"provider": "amap", "version": 1, "tile_count": 0,
              "sha256": "x" * 64}
        self.assertEqual(self.mpf.manifest_kind(v1), "full")

    def test_v2_full_kind(self):
        m = {"kind": "full"}
        self.assertEqual(self.mpf.manifest_kind(m), "full")

    def test_v2_delta_kind(self):
        m = {"kind": "delta"}
        self.assertEqual(self.mpf.manifest_kind(m), "delta")

    def test_invalid_kind_falls_back_to_full(self):
        self.assertEqual(self.mpf.manifest_kind({"kind": "weird"}), "full")
        self.assertEqual(self.mpf.manifest_kind(None), None)


class V2RoundTripTests(unittest.TestCase):
    """create_pack/read_pack_manifest 必须保留 kind/base_sha256/tile_index_sha256。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.sb = _Sandbox()
        _make_tile(self.sb.tiles, "satellite", 13, 100, 200)

    def tearDown(self):
        self.sb.close()

    def test_full_pack_round_trip(self):
        m = self.mpf.build_manifest(
            self.sb.tiles, None, 13, 13, ["satellite"])
        out = os.path.join(self.sb.root, "full.pack")
        self.mpf.create_pack(self.sb.tiles, out, m)
        loaded = self.mpf.read_pack_manifest(out)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["version"], 2)
        self.assertEqual(loaded["kind"], "full")
        self.assertIsNone(loaded["base_sha256"])
        self.assertEqual(loaded["tile_index_sha256"], m["tile_index_sha256"])
        self.assertEqual(self.mpf.manifest_kind(loaded), "full")

    def test_delta_pack_round_trip(self):
        base = "b" * 64
        m = self.mpf.build_manifest(
            self.sb.tiles, None, 13, 13, ["satellite"],
            kind="delta", base_sha256=base)
        out = os.path.join(self.sb.root, "delta.pack")
        self.mpf.create_pack(self.sb.tiles, out, m)
        loaded = self.mpf.read_pack_manifest(out)
        self.assertEqual(loaded["kind"], "delta")
        self.assertEqual(loaded["base_sha256"], base)
        self.assertEqual(self.mpf.manifest_kind(loaded), "delta")


class V1BackwardCompatTests(unittest.TestCase):
    """手工构造 v1 包: import_pack 必须仍能接受 (不强求新字段)。"""

    def setUp(self):
        self.mpf = _fresh_import("map_pack_format")
        self.sb = _Sandbox()
        _make_tile(self.sb.tiles, "satellite", 13, 100, 200)
        _make_tile(self.sb.tiles, "satellite", 13, 100, 201)

    def tearDown(self):
        self.sb.close()

    def _build_v1_manifest(self):
        sha, count = self.mpf.hash_tiles_root(self.sb.tiles)
        return {
            "provider": self.mpf.PACK_PROVIDER,
            "version": 1,
            "styles": ["satellite"],
            "bbox": None,
            "zoom_min": 13,
            "zoom_max": 13,
            "tile_count": count,
            "sha256": sha,
            "created_at": "2024-01-01T00:00:00",
        }

    def _create_v1_pack(self, out_path):
        from io import BytesIO
        manifest = self._build_v1_manifest()
        manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        with tarfile.open(out_path, "w") as tar:
            info = tarfile.TarInfo(self.mpf.MANIFEST_NAME)
            info.size = len(manifest_bytes)
            tar.addfile(info, BytesIO(manifest_bytes))
            for style, z, x, y, path in self.mpf.iter_tiles_root(self.sb.tiles):
                rel = "%s/%s/%d/%d/%d.png" % (
                    self.mpf.TILE_PREFIX, style, z, x, y)
                tar.add(path, arcname=rel)

    def test_v1_pack_manifest_kind_is_full(self):
        out = os.path.join(self.sb.root, "v1.pack")
        self._create_v1_pack(out)
        loaded = self.mpf.read_pack_manifest(out)
        self.assertEqual(loaded["version"], 1)
        self.assertNotIn("tile_index_sha256", loaded)
        self.assertEqual(self.mpf.manifest_kind(loaded), "full")

    def test_v1_pack_still_imports(self):
        out = os.path.join(self.sb.root, "v1.pack")
        self._create_v1_pack(out)
        cache = os.path.join(self.sb.root, "cache")
        ok, summary = self.mpf.import_pack(out, cache_dir=cache)
        self.assertTrue(ok, "v1 包应仍可导入: %s" % summary.get("message"))
        self.assertEqual(summary["tile_count"], 2)


class CacheReExportTests(unittest.TestCase):
    """map_tile_cache 必须再导出 v2 新增符号。"""

    def test_reexports_v2_helpers(self):
        mtc = _fresh_import("map_tile_cache")
        self.assertTrue(hasattr(mtc, "compute_tile_index_sha256"))
        self.assertTrue(hasattr(mtc, "manifest_kind"))
        self.assertEqual(mtc.PACK_VERSION, 2)


if __name__ == "__main__":
    unittest.main()
