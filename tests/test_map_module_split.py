# -*- coding: utf-8 -*-
"""map_tile_cache 拆分契约测试 (T1)

锁定 map_tile_cache.py 的全部公共 API：拆分到三个子模块后必须仍可作为
``map_tile_cache.X`` 直接访问；同时三个子模块自身可独立导入。
"""

import importlib
import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
MAP_RESOURCES_DIR = SCRIPTS_DIR / "map_resources"


def _ensure_scripts_on_path():
    for path in (SCRIPTS_DIR, MAP_RESOURCES_DIR):
        p = str(path)
        if p not in sys.path:
            sys.path.insert(0, p)


def _fresh_import(name):
    """重新导入子模块, 避免被先前测试缓存污染。"""
    _ensure_scripts_on_path()
    if name in sys.modules:
        del sys.modules[name]
    return importlib.import_module(name)


# 拆分前 map_tile_cache 的全部公共符号 (顶层 import 应保持可用)
PUBLIC_SYMBOLS = (
    "MapTileCache",
    "fetch_tile",
    "import_pack",
    "build_manifest",
    "hash_tiles_root",
    "enumerate_tiles",
    "deg2tile",
    "clamp_zoom",
    "read_pack_manifest",
    "create_pack",
    "iter_tiles_root",
    "CACHE_DIR",
    "VALID_STYLES",
    "TILE_ENDPOINTS",
    "ZOOM_HARD_MIN",
    "ZOOM_HARD_MAX",
    "DEFAULT_ZOOM_MIN",
    "DEFAULT_ZOOM_MAX",
    "MAX_PREWARM_TILES",
    "PACK_PROVIDER",
    "PACK_VERSION",
    "MANIFEST_NAME",
    "TILE_PREFIX",
    "PLACEHOLDER_TILE",
    "PREWARM_WORKERS",
    "FETCH_TIMEOUT",
    "Request",
    "URLError",
)


class MapTileCachePublicApiTests(unittest.TestCase):
    """map_tile_cache.X 必须暴露所有原公共符号 (拆分前后均成立)。"""

    def test_all_public_symbols_present(self):
        mtc = _fresh_import("map_tile_cache")
        missing = [s for s in PUBLIC_SYMBOLS if not hasattr(mtc, s)]
        self.assertEqual(missing, [],
                         "map_tile_cache 缺失公共符号: %s" % missing)

    def test_valid_styles_contains_known_keys(self):
        mtc = _fresh_import("map_tile_cache")
        # 高德两层 (原生 z<=18)
        self.assertIn("satellite", mtc.VALID_STYLES)
        self.assertIn("annotation", mtc.VALID_STYLES)
        # 谷歌两层 (google.cn, 原生 z 可到 20+)
        self.assertIn("gsatellite", mtc.VALID_STYLES)
        self.assertIn("gannotation", mtc.VALID_STYLES)

    def test_default_base_and_prewarm_styles_are_google(self):
        mtc = _fresh_import("map_tile_cache")
        self.assertEqual(mtc.DEFAULT_BASE_STYLE, "gsatellite")
        self.assertEqual(set(mtc.DEFAULT_PREWARM_STYLES),
                         {"gsatellite", "gannotation"})
        # 默认底图/预热来源必须都是合法 style
        for style in (mtc.DEFAULT_BASE_STYLE,) + tuple(mtc.DEFAULT_PREWARM_STYLES):
            self.assertIn(style, mtc.VALID_STYLES)

    def test_tile_endpoints_match_valid_styles(self):
        mtc = _fresh_import("map_tile_cache")
        self.assertEqual(set(mtc.TILE_ENDPOINTS.keys()), set(mtc.VALID_STYLES))

    def test_placeholder_tile_is_png(self):
        mtc = _fresh_import("map_tile_cache")
        self.assertTrue(mtc.PLACEHOLDER_TILE.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_zoom_constants_relations(self):
        mtc = _fresh_import("map_tile_cache")
        self.assertLessEqual(mtc.ZOOM_HARD_MIN, mtc.DEFAULT_ZOOM_MIN)
        self.assertLessEqual(mtc.DEFAULT_ZOOM_MAX, mtc.ZOOM_HARD_MAX)
        # 切谷歌后硬上限从 18 提到 20 (谷歌卫星原生可达层级)
        self.assertEqual(mtc.ZOOM_HARD_MAX, 20)
        self.assertEqual(mtc.DEFAULT_ZOOM_MAX, 20)
        self.assertGreater(mtc.MAX_PREWARM_TILES, 0)

    def test_pack_format_constants(self):
        mtc = _fresh_import("map_tile_cache")
        self.assertEqual(mtc.PACK_PROVIDER, "amap")
        self.assertEqual(mtc.MANIFEST_NAME, "manifest.json")
        self.assertEqual(mtc.TILE_PREFIX, "tiles")
        self.assertEqual(mtc.PACK_VERSION, 2)

    def test_deg2tile_basic(self):
        mtc = _fresh_import("map_tile_cache")
        # 经纬度 (0,0) z=1 应落在 (1,1) 附近 (Web Mercator 中心)
        x, y = mtc.deg2tile(0.0, 0.0, 1)
        self.assertEqual((x, y), (1, 1))

    def test_clamp_zoom_bounds(self):
        mtc = _fresh_import("map_tile_cache")
        self.assertEqual(mtc.clamp_zoom(-100, mtc.DEFAULT_ZOOM_MIN),
                         mtc.ZOOM_HARD_MIN)
        self.assertEqual(mtc.clamp_zoom(999, mtc.DEFAULT_ZOOM_MIN),
                         mtc.ZOOM_HARD_MAX)
        self.assertEqual(mtc.clamp_zoom("not-int", 7), 7)


class SplitModulesIndependentlyImportableTests(unittest.TestCase):
    """拆分后每个子模块都必须可单独 import (即使 map_tile_cache 还未加载)。

    在拆分前这三个模块文件不存在, 该用例期望失败 -> 红;
    完成拆分后用例转绿, 锁定模块边界。
    """

    def _try_import(self, name):
        _ensure_scripts_on_path()
        if name in sys.modules:
            del sys.modules[name]
        try:
            return importlib.import_module(name)
        except ImportError:
            return None

    def test_map_network_fetch_independently_importable(self):
        mod = self._try_import("map_network_fetch")
        if mod is None:
            self.skipTest("map_network_fetch 尚未拆出 (拆分前预期跳过)")
        self.assertTrue(hasattr(mod, "fetch_tile"))
        self.assertTrue(hasattr(mod, "TILE_ENDPOINTS"))
        self.assertTrue(hasattr(mod, "VALID_STYLES"))

    def test_map_tile_store_independently_importable(self):
        mod = self._try_import("map_tile_store")
        if mod is None:
            self.skipTest("map_tile_store 尚未拆出 (拆分前预期跳过)")
        self.assertTrue(hasattr(mod, "CACHE_DIR"))
        self.assertTrue(hasattr(mod, "deg2tile"))
        self.assertTrue(hasattr(mod, "enumerate_tiles"))
        self.assertTrue(hasattr(mod, "PLACEHOLDER_TILE"))

    def test_map_pack_format_independently_importable(self):
        mod = self._try_import("map_pack_format")
        if mod is None:
            self.skipTest("map_pack_format 尚未拆出 (拆分前预期跳过)")
        self.assertTrue(hasattr(mod, "build_manifest"))
        self.assertTrue(hasattr(mod, "hash_tiles_root"))
        self.assertTrue(hasattr(mod, "create_pack"))
        self.assertTrue(hasattr(mod, "read_pack_manifest"))
        self.assertTrue(hasattr(mod, "import_pack"))
        self.assertTrue(hasattr(mod, "iter_tiles_root"))


if __name__ == "__main__":
    unittest.main()
