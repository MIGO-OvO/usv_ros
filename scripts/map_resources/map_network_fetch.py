#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
地图瓦片回源 (Map Tile Network Fetch)
======================================
集中管理栅格瓦片的远端端点与回源逻辑, 不依赖文件系统/打包格式。
此模块为依赖底层: 不允许反向 import map_tile_store / map_pack_format。

底图来源 (缓存路径按 style 天然隔离):
  - 高德 (amap):   satellite=卫星影像(style=6) / annotation=注记叠加(style=8)
                   GCJ-02 坐标系; 原生层级仅到 z=18, 超过返回空白页 -> 判空丢弃。
  - 谷歌 (google): gsatellite=卫星影像(lyrs=s) / gannotation=注记路网(lyrs=h)
                   走国际版 mt{s}.google.com, 原生层级可到 z=20+。

坐标系警示 (重要):
  - 高德为 GCJ-02; 谷歌国际版 (.com) 为 WGS-84 真实坐标 (与船 GPS 一致)。
  - 因此使用谷歌国际版底图时, Web 端叠加层 (船位/航点/采样点) 必须用 WGS-84
    原始坐标, 不能再做 WGS84->GCJ-02 偏移, 否则会偏移约 500m。坐标系对齐由
    web_config_server 的 map_overlay_datum 逻辑处理, 见该文件相关说明。
端点公开, 无需 Key/签名; 仅供比赛/演示用途, 长期商用需评估正规授权。
Python: 3.8
"""

from __future__ import print_function

import random
import threading
import time

try:
    from urllib.request import Request, urlopen
    from urllib.error import URLError
except ImportError:  # pragma: no cover - py2 兜底, 实际运行为 py3
    from urllib2 import Request, urlopen, URLError


# 栅格瓦片端点 (z/x/y, {s} 为子域占位)。
# 高德子域 1..4 (webst01..04); 谷歌子域 0..3 (mt0..3)。子域范围差异由
# TILE_SUBDOMAINS 描述, 调用方传入的 sub 序号经 _resolve_sub 映射到合法值。
TILE_ENDPOINTS = {
    # ---- 高德 (GCJ-02, 原生 z<=18) ----
    "satellite": "https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}",
    "annotation": "https://webrd0{s}.is.autonavi.com/appmaptile"
                  "?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
    # ---- 谷歌国际版 (WGS-84, 原生 z 可到 20+) ----
    "gsatellite": "https://mt{s}.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}",
    "gannotation": "https://mt{s}.google.com/vt/lyrs=h&hl=en&x={x}&y={y}&z={z}",
}
VALID_STYLES = tuple(TILE_ENDPOINTS.keys())

# 每个来源的合法子域序号集合 (负载均衡)。调用方仍按 1..4 轮询传入 sub,
# _resolve_sub 把它映射进对应来源的合法范围, 保持 fetch_tile 签名不变。
TILE_SUBDOMAINS = {
    "satellite": (1, 2, 3, 4),
    "annotation": (1, 2, 3, 4),
    "gsatellite": (0, 1, 2, 3),
    "gannotation": (0, 1, 2, 3),
}

# 部分来源需要匹配的 Referer, 否则可能被拒。默认空 Referer。
_STYLE_REFERER = {
    "satellite": "https://www.amap.com/",
    "annotation": "https://www.amap.com/",
    "gsatellite": "https://www.google.com/maps",
    "gannotation": "https://www.google.com/maps",
}

# 回源网络参数
FETCH_TIMEOUT = 8.0
PREWARM_WORKERS = 6
_USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) USV-OfflineMap/1.0"
_REFERER = "https://www.amap.com/"


def _resolve_sub(style, sub):
    """把调用方传入的子域序号映射到该来源的合法子域。

    调用方 (轮询器) 统一按 1..4 传 sub; 高德直接用, 谷歌需落到 0..3。
    映射规则: 按来源子域列表取模索引, 保证负载均衡且永不越界。
    未知 style 回退到原 sub (由 _raw_fetch 的 style 校验拒绝)。
    """
    domains = TILE_SUBDOMAINS.get(style)
    if not domains:
        return sub
    try:
        idx = (int(sub) - 1) % len(domains)
    except (TypeError, ValueError):
        idx = 0
    return domains[idx]

# 瓦片字节合法性: 接受 PNG/JPEG magic 且长度 >100, 拒 HTML 错误页/截断包。
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
_MIN_TILE_BYTES = 100


def _is_valid_tile(data):
    """严格图片校验: PNG/JPEG magic + len>100。HTML 错误页虽可能 >100 但 magic 不对必拒。"""
    if not data:
        return False
    return (data.startswith(PNG_MAGIC) or data.startswith(JPEG_MAGIC)) and len(data) > _MIN_TILE_BYTES


def _raw_fetch(style, z, x, y, sub, timeout):
    """底层 urlopen: 失败抛 URLError/OSError; 成功返回 bytes (可能不是 PNG)。
    style 不合法时返回 None (不抛, 与 fetch_tile 旧契约一致)。

    sub 为调用方轮询序号 (统一 1..4); 这里经 _resolve_sub 映射到该来源的
    合法子域 (高德 1..4 / 谷歌 0..3), 保持上层轮询逻辑零改动。
    """
    if style not in TILE_ENDPOINTS:
        return None
    resolved_sub = _resolve_sub(style, sub)
    url = TILE_ENDPOINTS[style].format(s=resolved_sub, x=x, y=y, z=z)
    referer = _STYLE_REFERER.get(style, _REFERER)
    req = Request(url, headers={"User-Agent": _USER_AGENT, "Referer": referer})
    resp = urlopen(req, timeout=timeout)
    return resp.read()


def fetch_tile(style, z, x, y, sub=1):
    """从高德回源单张瓦片字节, 失败返回 None。供缓存代理与导出 CLI 复用。

    注意: 旧契约 — 仅做 ``len(data) > 100`` 的弱校验, 不强制 PNG magic。
    新调用方请使用 ``fetch_tile_resilient`` 获得带退避/abort 的严格 PNG 校验。
    """
    try:
        data = _raw_fetch(style, z, x, y, sub, FETCH_TIMEOUT)
    except (URLError, OSError, ValueError):
        return None
    if data and len(data) > _MIN_TILE_BYTES:
        return data
    return None


# ---- T4: 弹性回源 ----

# 默认子域轮询计数 (1..4); 弱多线程语义即可, 无需严格锁。
_DEFAULT_SUB_COUNTER = [0]


def _default_sub_picker():
    """模块级默认子域轮询: 返回 1..4。无需线程安全 — 子域用于负载均衡, 偶发同号无害。"""
    _DEFAULT_SUB_COUNTER[0] = (_DEFAULT_SUB_COUNTER[0] + 1) % 4
    return _DEFAULT_SUB_COUNTER[0] + 1


class FetchResult(object):
    """弹性回源结果。

    status 取值:
      ok       — 拿到合法 PNG; data 为字节
      aborted  — abort.is_set 触发, 调用方需要立即停手
      timeout  — 网络层全部失败 (URLError/OSError 或 _fetch 返回 None) 耗尽重试
      invalid  — 始终拿到字节但都不是合法 PNG (高德错误页等), 不落盘
      http     — 预留: HTTP 状态码错误细分 (T4 暂未细化, 框架字段保留)
    """

    __slots__ = ("data", "status", "attempts", "last_http", "elapsed_ms")

    def __init__(self, data=None, status="invalid", attempts=0,
                 last_http=None, elapsed_ms=0):
        self.data = data
        self.status = status
        self.attempts = attempts
        self.last_http = last_http
        self.elapsed_ms = elapsed_ms


def _backoff_delay(attempt_idx, base_delay, max_delay):
    """指数退避 + [0,base) 抖动。
    attempt_idx 从 0 起记 (第一次失败后 sleep 用 attempt_idx=0 的延时)。"""
    exp = base_delay * (2 ** attempt_idx)
    capped = min(exp, max_delay)
    jitter = random.uniform(0.0, base_delay)
    return capped + jitter


def fetch_tile_resilient(style, z, x, y, sub_picker=None, max_attempts=5,
                         base_delay=0.5, max_delay=8.0, abort=None,
                         timeout=FETCH_TIMEOUT, _fetch=None):
    """带指数退避+抖动的回源。

    - sub_picker: 可调用, 返回 1..4 的子域序号; 默认轮询。
    - abort: threading.Event; set 后尽快返回 status='aborted'。
    - _fetch: 可注入的底层抓取函数 (style,z,x,y,sub,timeout)->bytes|None,
      仅供测试; 默认用真实 _raw_fetch 逻辑。
    返回 FetchResult。status in {ok, http, timeout, invalid, aborted}.

    成功条件: 拿到字节且 _is_valid_tile 通过 -> status='ok'。
    重试: 网络类失败 (URLError/OSError/timeout/None) 指数退避
      base*2^n, 上限 max_delay, 加 [0,base) 抖动; 最多 max_attempts 次。
    非 PNG (拿到字节但 _is_valid_tile False): 视为 invalid 重试,
      attempts 用尽 -> status='invalid'。
    每次 sleep 前检查 abort; abort.set -> 立即返回 status='aborted'。
    """
    if sub_picker is None:
        sub_picker = _default_sub_picker
    if _fetch is None:
        _fetch = _raw_fetch

    # 进入循环前先看一次 abort, 满足 "abort 已 set 时 attempts==0" 契约。
    if abort is not None and abort.is_set():
        return FetchResult(status="aborted", attempts=0)

    started = time.time()
    attempts = 0
    last_failure = "timeout"  # 网络全失败默认归入 timeout
    saw_bytes = False         # 是否曾经拿到过字节 (即便非 PNG)

    for i in range(max_attempts):
        if abort is not None and abort.is_set():
            return FetchResult(
                status="aborted", attempts=attempts,
                elapsed_ms=int((time.time() - started) * 1000))

        sub = sub_picker()
        attempts += 1
        try:
            data = _fetch(style, z, x, y, sub, timeout)
        except (URLError, OSError, ValueError):
            data = None
            last_failure = "timeout"

        if data is None:
            last_failure = "timeout"
        else:
            saw_bytes = True
            if _is_valid_tile(data):
                return FetchResult(
                    data=data, status="ok", attempts=attempts,
                    last_http=200,
                    elapsed_ms=int((time.time() - started) * 1000))
            # 拿到字节但不是 PNG: 视为 invalid, 继续重试 (可能下次切到健康子域)
            last_failure = "invalid"

        # 还有重试机会, sleep 前再看 abort, 避免空等。
        if i < max_attempts - 1:
            if abort is not None and abort.is_set():
                return FetchResult(
                    status="aborted", attempts=attempts,
                    elapsed_ms=int((time.time() - started) * 1000))
            delay = _backoff_delay(i, base_delay, max_delay)
            if abort is not None:
                # 用 abort.wait 替代 sleep, 让 abort 能尽快打断。
                if abort.wait(delay):
                    return FetchResult(
                        status="aborted", attempts=attempts,
                        elapsed_ms=int((time.time() - started) * 1000))
            else:
                time.sleep(delay)

    # 耗尽: 至少拿到过字节但都非 PNG -> invalid; 否则全网络失败 -> timeout。
    final_status = "invalid" if (saw_bytes and last_failure == "invalid") else "timeout"
    return FetchResult(
        status=final_status, attempts=attempts,
        elapsed_ms=int((time.time() - started) * 1000))
