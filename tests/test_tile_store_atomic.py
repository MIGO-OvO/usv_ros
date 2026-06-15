# -*- coding: utf-8 -*-
"""map_tile_store 原子写 / PNG 校验 / 孤儿 tmp 清扫 契约测试 (T2)

锁定 map-download-redesign 计划 T2 的下沉原语:
  - TileKey: 瓦片标识 + 磁盘路径解析 + pack 内部 relpath
  - tile_disk_path / write_tile_atomic / read_tile: 写入端 (tmp -> fsync -> replace)
  - verify_tile_bytes: PNG magic + 最小长度校验, 拒 HTML/截断
  - sweep_orphan_tmp: 进程崩溃后清理超龄 *.png.tmp

测试场景:
  HP-05 写盘读盘往返一致, disk_path 与现有目录布局一致
  EF-02 os.replace 抛 OSError -> 返回 False, 不留 .png 与 .png.tmp
  EF-03 sweep_orphan_tmp: 老 tmp 删, 新 tmp 留, 正常 .png 永不动
  EF-04 verify_tile_bytes 拒绝截断 PNG / HTML 错误页
  EF-14 verify_tile_bytes 接受真实 PNG; 拒绝 >100B 非 PNG blob
"""

import importlib
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
MAP_RESOURCES_DIR = SCRIPTS_DIR / "map_resources"


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


class _Sandbox(object):
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="usv_tilestore_atomic_")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TileKeyAndAtomicWriteTests(unittest.TestCase):
    """HP-05: 写盘读盘往返一致, disk_path 与现有 {root}/{style}/{z}/{x}/{y}.png 布局一致。"""

    def setUp(self):
        self.mts = _fresh_import("map_tile_store")
        self.sb = _Sandbox()

    def tearDown(self):
        self.sb.close()

    def test_disk_path_matches_existing_layout(self):
        key = self.mts.TileKey("satellite", 13, 100, 200)
        expected = os.path.join(
            self.sb.root, "satellite", "13", "100", "200.png")
        self.assertEqual(key.disk_path(self.sb.root), expected)
        self.assertEqual(self.mts.tile_disk_path(self.sb.root, key), expected)

    def test_relpath_for_pack_layout(self):
        # pack 内部 relpath 形式: tiles/{style}/{z}/{x}/{y}.png
        key = self.mts.TileKey("satellite", 13, 100, 200)
        self.assertEqual(key.relpath(), "tiles/satellite/13/100/200.png")

    def test_write_then_read_roundtrip(self):
        key = self.mts.TileKey("satellite", 13, 100, 200)
        payload = self.mts.PLACEHOLDER_TILE
        ok = self.mts.write_tile_atomic(self.sb.root, key, payload)
        self.assertTrue(ok)
        path = key.disk_path(self.sb.root)
        self.assertTrue(os.path.isfile(path))
        # tmp 中间文件不能残留
        self.assertFalse(os.path.exists(path + ".tmp"))
        # read_tile 返回完全一致字节
        got = self.mts.read_tile(self.sb.root, key)
        self.assertEqual(got, payload)

    def test_read_tile_missing_returns_none(self):
        key = self.mts.TileKey("satellite", 13, 100, 999)
        self.assertIsNone(self.mts.read_tile(self.sb.root, key))


class WriteAtomicFailureTests(unittest.TestCase):
    """EF-02: os.replace 失败时返回 False, 不留 .png 与 .png.tmp。"""

    def setUp(self):
        self.mts = _fresh_import("map_tile_store")
        self.sb = _Sandbox()

    def tearDown(self):
        self.sb.close()

    def test_replace_failure_returns_false_and_cleans_tmp(self):
        key = self.mts.TileKey("satellite", 13, 100, 200)
        path = key.disk_path(self.sb.root)
        # 模拟 os.replace 抛 OSError (磁盘满 / 跨卷 / 权限) 触发清理路径
        with mock.patch.object(self.mts.os, "replace",
                               side_effect=OSError("simulated")):
            ok = self.mts.write_tile_atomic(
                self.sb.root, key, self.mts.PLACEHOLDER_TILE)
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(path),
                         "失败时不应留下最终 .png")
        self.assertFalse(os.path.exists(path + ".tmp"),
                         "失败时必须清掉 .png.tmp 半截文件")


class SweepOrphanTmpTests(unittest.TestCase):
    """EF-03: 老 *.png.tmp 删, 新 tmp 留, 正常 .png 永不动。"""

    def setUp(self):
        self.mts = _fresh_import("map_tile_store")
        self.sb = _Sandbox()
        self.style_dir = os.path.join(
            self.sb.root, "satellite", "13", "100")
        os.makedirs(self.style_dir, exist_ok=True)

    def tearDown(self):
        self.sb.close()

    def _touch(self, path, age_sec=0, payload=b"x"):
        with open(path, "wb") as f:
            f.write(payload)
        if age_sec > 0:
            old = time.time() - age_sec
            os.utime(path, (old, old))

    def test_sweep_removes_only_stale_tmp(self):
        stale_tmp = os.path.join(self.style_dir, "200.png.tmp")
        fresh_tmp = os.path.join(self.style_dir, "201.png.tmp")
        real_png = os.path.join(self.style_dir, "202.png")
        self._touch(stale_tmp, age_sec=120)
        self._touch(fresh_tmp, age_sec=0)
        self._touch(real_png, age_sec=120)  # 哪怕老的 .png 也不能动

        removed = self.mts.sweep_orphan_tmp(self.sb.root, max_age_sec=60)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(stale_tmp))
        self.assertTrue(os.path.exists(fresh_tmp))
        self.assertTrue(os.path.exists(real_png))

    def test_sweep_empty_root_returns_zero(self):
        # 不存在的目录或空目录: 返回 0 不抛
        empty = os.path.join(self.sb.root, "empty")
        os.makedirs(empty, exist_ok=True)
        self.assertEqual(self.mts.sweep_orphan_tmp(empty, max_age_sec=60), 0)


class VerifyTileBytesTests(unittest.TestCase):
    """EF-04 + EF-14: verify_tile_bytes 必须拒绝非 PNG / 截断, 接受真实 PNG。"""

    def setUp(self):
        self.mts = _fresh_import("map_tile_store")

    def test_rejects_truncated_png(self):
        # 仅有不完整的 magic 前 4 字节, 后面随便接一字节
        truncated = b"\x89PNG" + b"x"
        self.assertFalse(self.mts.verify_tile_bytes(truncated))

    def test_rejects_html_error_page(self):
        # 高德返回 HTML 错误页: 长度可能 >100, 但 magic 不对, 必须拒
        html = b"<html><head><title>error</title></head><body>" + b"y" * 200
        self.assertGreater(len(html), 100)
        self.assertFalse(self.mts.verify_tile_bytes(html))

    def test_rejects_empty_and_none(self):
        self.assertFalse(self.mts.verify_tile_bytes(b""))
        self.assertFalse(self.mts.verify_tile_bytes(None))

    def test_accepts_real_png(self):
        # PLACEHOLDER_TILE 是模块自生成的合法 PNG, 应被接受
        png = self.mts.PLACEHOLDER_TILE
        self.assertTrue(self.mts.verify_tile_bytes(png))
        self.assertGreater(len(png), 100)

    def test_rejects_long_non_png_blob(self):
        # 长度 >100 但 magic 错: 仍必须拒
        blob = b"NOTPNG__" + b"z" * 200
        self.assertGreater(len(blob), 100)
        self.assertFalse(self.mts.verify_tile_bytes(blob))


class CacheReExportTests(unittest.TestCase):
    """map_tile_cache 必须再导出 T2 新增的写入/校验/清扫符号。"""

    def test_reexports_t2_helpers(self):
        mtc = _fresh_import("map_tile_cache")
        for name in ("TileKey", "tile_disk_path", "write_tile_atomic",
                     "read_tile", "verify_tile_bytes", "sweep_orphan_tmp"):
            self.assertTrue(hasattr(mtc, name),
                            "map_tile_cache 缺少再导出: %s" % name)


if __name__ == "__main__":
    unittest.main()
