import importlib
import os
import shutil
import sys
import tempfile
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
    _ensure_scripts_on_path()
    if name in sys.modules:
        del sys.modules[name]
    return importlib.import_module(name)


class _Sandbox(object):
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="usv_packexport_blank_")
        self.tiles = os.path.join(self.root, "tiles")
        os.makedirs(self.tiles, exist_ok=True)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class DownloadBlankTileFilterTests(unittest.TestCase):
    def setUp(self):
        self.mpe = _fresh_import("map_pack_export")
        self.sb = _Sandbox()

    def tearDown(self):
        self.sb.close()

    def test_download_rejects_blank_png_tile(self):
        blank_png = self.mpe.mtc.PLACEHOLDER_TILE
        task = ("satellite", 20, 1, 1)
        original_enumerate = self.mpe.mtc.enumerate_tiles
        original_fetch = self.mpe.mtc.fetch_tile_resilient
        self.mpe.mtc.enumerate_tiles = lambda _bbox, _zmin, _zmax, _styles: ([task], 1)
        self.mpe.mtc.fetch_tile_resilient = lambda *_args, **_kwargs: self.mpe.mtc.FetchResult(
            data=blank_png, status="ok", attempts=1)
        try:
            ok, fail = self.mpe._download_to_root(
                self.sb.tiles, (0, 0, 1, 1), 20, 20, ["satellite"], 1)
        finally:
            self.mpe.mtc.enumerate_tiles = original_enumerate
            self.mpe.mtc.fetch_tile_resilient = original_fetch

        self.assertEqual(ok, 0)
        self.assertEqual(fail, 1)
        self.assertFalse(os.path.exists(
            os.path.join(self.sb.tiles, "satellite", "20", "1", "1.png")))

    def test_existing_blank_tile_is_not_reused_as_cached(self):
        blank_png = self.mpe.mtc.PLACEHOLDER_TILE
        valid_png = b"\x89PNG\r\n\x1a\n" + b"1" * 200
        task = ("satellite", 18, 1, 1)
        target = os.path.join(self.sb.tiles, "satellite", "18", "1", "1.png")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(blank_png)
        original_enumerate = self.mpe.mtc.enumerate_tiles
        original_fetch = self.mpe.mtc.fetch_tile_resilient
        self.mpe.mtc.enumerate_tiles = lambda _bbox, _zmin, _zmax, _styles: ([task], 1)
        self.mpe.mtc.fetch_tile_resilient = lambda *_args, **_kwargs: self.mpe.mtc.FetchResult(
            data=valid_png, status="ok", attempts=1)
        try:
            ok, fail = self.mpe._download_to_root(
                self.sb.tiles, (0, 0, 1, 1), 18, 18, ["satellite"], 1)
        finally:
            self.mpe.mtc.enumerate_tiles = original_enumerate
            self.mpe.mtc.fetch_tile_resilient = original_fetch

        self.assertEqual(ok, 1)
        self.assertEqual(fail, 0)
        with open(target, "rb") as f:
            self.assertEqual(f.read(), valid_png)


if __name__ == "__main__":
    unittest.main()
