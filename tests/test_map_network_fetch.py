# -*- coding: utf-8 -*-
"""map_network_fetch 弹性回源契约测试 (T4)

锁定 map-download-redesign 计划 T4 的网络层韧性原语:
  - PNG_MAGIC + _is_valid_tile: 严格 PNG 头 + len>100 校验
  - FetchResult: 轻量结果对象, 字段 data/status/attempts/last_http/elapsed_ms
  - fetch_tile_resilient: 带指数退避+抖动+abort 的回源循环
  - 状态分类 (status taxonomy): ok / aborted / timeout / invalid / http

测试场景 (全部通过 _fetch 注入, 不触网):
  EF-01a 重试后成功: None x2 -> 合法 PNG -> status=ok, attempts=3
  EF-01b 网络全失败耗尽: 始终 None -> status=timeout, attempts=max
  EF-01c abort 立即返回: abort 已 set -> status=aborted, attempts=0
  EF-14   非 PNG 字节: HTML 错误页 -> status=invalid, attempts=max, data=None
  HP-04   合法 PNG 一次命中: PNG_MAGIC+padding -> status=ok
"""

import importlib
import os
import sys
import threading
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _ensure_scripts_on_path():
    p = str(SCRIPTS_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


def _fresh_import(name):
    _ensure_scripts_on_path()
    if name in sys.modules:
        del sys.modules[name]
    return importlib.import_module(name)


# 与 map_tile_store.PNG_MAGIC 保持一致, 测试不依赖具体模块导入顺序
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_VALID_PNG = _PNG_MAGIC + b"0" * 200


class IsValidTileTests(unittest.TestCase):
    """_is_valid_tile 必须严格按 PNG magic + 最小长度判定。"""

    def setUp(self):
        self.mnf = _fresh_import("map_network_fetch")

    def test_accepts_real_png(self):
        self.assertTrue(self.mnf._is_valid_tile(_VALID_PNG))

    def test_rejects_html_blob(self):
        html = b"<html><head><title>err</title></head>" + b"x" * 200
        self.assertGreater(len(html), 100)
        self.assertFalse(self.mnf._is_valid_tile(html))

    def test_rejects_short_png(self):
        short = _PNG_MAGIC + b"0" * 10  # magic 对但太短
        self.assertFalse(self.mnf._is_valid_tile(short))

    def test_rejects_none_and_empty(self):
        self.assertFalse(self.mnf._is_valid_tile(None))
        self.assertFalse(self.mnf._is_valid_tile(b""))


class FetchResultShapeTests(unittest.TestCase):
    """FetchResult 必须暴露契约字段。"""

    def setUp(self):
        self.mnf = _fresh_import("map_network_fetch")

    def test_default_construction(self):
        r = self.mnf.FetchResult()
        self.assertIsNone(r.data)
        self.assertEqual(r.status, "invalid")
        self.assertEqual(r.attempts, 0)
        self.assertIsNone(r.last_http)
        self.assertEqual(r.elapsed_ms, 0)

    def test_explicit_fields(self):
        r = self.mnf.FetchResult(
            data=b"abc", status="ok", attempts=2, last_http=200, elapsed_ms=42)
        self.assertEqual(r.data, b"abc")
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.attempts, 2)
        self.assertEqual(r.last_http, 200)
        self.assertEqual(r.elapsed_ms, 42)


class _Recorder(object):
    """记录每次 _fetch 调用 (sub 等参数), 按脚本依次返回字节或 None/异常。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def __call__(self, style, z, x, y, sub, timeout):
        self.calls.append((style, z, x, y, sub, timeout))
        if not self._script:
            return None
        head = self._script.pop(0)
        if isinstance(head, BaseException):
            raise head
        return head


class FetchTileResilientTests(unittest.TestCase):
    """fetch_tile_resilient 主循环契约。"""

    def setUp(self):
        self.mnf = _fresh_import("map_network_fetch")

    # ---- HP-04 ----
    def test_valid_png_first_try_returns_ok(self):
        rec = _Recorder([_VALID_PNG])
        r = self.mnf.fetch_tile_resilient(
            "satellite", 13, 100, 200,
            max_attempts=3, base_delay=0.001, _fetch=rec)
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.attempts, 1)
        self.assertEqual(r.data, _VALID_PNG)

    # ---- EF-01a ----
    def test_retry_then_success(self):
        rec = _Recorder([None, None, _VALID_PNG])
        r = self.mnf.fetch_tile_resilient(
            "satellite", 13, 100, 200,
            max_attempts=5, base_delay=0.001, _fetch=rec)
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.attempts, 3)
        self.assertEqual(r.data, _VALID_PNG)
        self.assertEqual(len(rec.calls), 3)

    # ---- EF-01b ----
    def test_exhaust_timeout_when_always_none(self):
        rec = _Recorder([None, None, None, None])
        r = self.mnf.fetch_tile_resilient(
            "satellite", 13, 100, 200,
            max_attempts=4, base_delay=0.001, _fetch=rec)
        self.assertEqual(r.status, "timeout",
                         "全 None 视为网络失败, 耗尽后 status 必须为 timeout")
        self.assertEqual(r.attempts, 4)
        self.assertIsNone(r.data)

    # ---- EF-01c ----
    def test_abort_before_call_returns_immediately(self):
        ev = threading.Event()
        ev.set()
        rec = _Recorder([_VALID_PNG])
        r = self.mnf.fetch_tile_resilient(
            "satellite", 13, 100, 200,
            max_attempts=5, base_delay=0.001, abort=ev, _fetch=rec)
        self.assertEqual(r.status, "aborted")
        self.assertEqual(r.attempts, 0)
        self.assertEqual(len(rec.calls), 0,
                         "abort 已 set 时不应触发任何底层 _fetch 调用")

    # ---- EF-14 ----
    def test_invalid_bytes_exhaust_attempts(self):
        html = b"<html>error</html>" + b"a" * 200
        rec = _Recorder([html, html, html])
        r = self.mnf.fetch_tile_resilient(
            "satellite", 13, 100, 200,
            max_attempts=3, base_delay=0.001, _fetch=rec)
        self.assertEqual(r.status, "invalid")
        self.assertEqual(r.attempts, 3)
        self.assertIsNone(r.data,
                          "拿到字节但非 PNG, 不应回填 data, 防止上层落盘错误页")

    def test_sub_picker_consulted_per_attempt(self):
        # 默认 sub_picker 应在 1..4 之间轮询; 每次 attempt 都会消费一次 picker。
        seen = []

        def picker():
            seen.append(1)
            return ((len(seen) - 1) % 4) + 1

        rec = _Recorder([None, None, _VALID_PNG])
        r = self.mnf.fetch_tile_resilient(
            "satellite", 13, 100, 200,
            sub_picker=picker, max_attempts=5, base_delay=0.001, _fetch=rec)
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.attempts, 3)
        self.assertEqual(len(seen), 3)
        # 注入的 picker 给出的 sub 应原样传入 _fetch
        subs = [c[4] for c in rec.calls]
        self.assertEqual(subs, [1, 2, 3])

    def test_url_error_treated_as_network_failure(self):
        # URLError/OSError 必须被吞掉并转换成 None 等价: 累计 attempts, 最终 status=timeout
        from map_network_fetch import URLError
        rec = _Recorder([URLError("boom"), OSError("dns"), URLError("again")])
        r = self.mnf.fetch_tile_resilient(
            "satellite", 13, 100, 200,
            max_attempts=3, base_delay=0.001, _fetch=rec)
        self.assertEqual(r.status, "timeout")
        self.assertEqual(r.attempts, 3)


class FetchTileBackwardCompatTests(unittest.TestCase):
    """fetch_tile 的旧契约不允许被破坏 (签名/返回行为)。"""

    def setUp(self):
        self.mnf = _fresh_import("map_network_fetch")

    def test_fetch_tile_signature_unchanged(self):
        import inspect
        sig = inspect.signature(self.mnf.fetch_tile)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["style", "z", "x", "y", "sub"])
        self.assertEqual(sig.parameters["sub"].default, 1)

    def test_fetch_tile_unknown_style_returns_none(self):
        self.assertIsNone(self.mnf.fetch_tile("unknown_style", 13, 0, 0, 1))


class CacheReExportTests(unittest.TestCase):
    """map_tile_cache 必须再导出 T4 新增的 fetch_tile_resilient / FetchResult。"""

    def test_reexports_resilient_symbols(self):
        mtc = _fresh_import("map_tile_cache")
        self.assertTrue(hasattr(mtc, "fetch_tile_resilient"))
        self.assertTrue(hasattr(mtc, "FetchResult"))
        # _is_valid_tile 是下划线开头, 不暴露
        self.assertFalse(hasattr(mtc, "_is_valid_tile"))


if __name__ == "__main__":
    unittest.main()
