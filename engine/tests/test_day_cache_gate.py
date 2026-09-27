# -*- coding: utf-8 -*-
"""
日缓存版本/完整度门控测试（pytest 兼容；无 pytest 时可直接运行：
    python engine/tests/test_day_cache_gate.py

背景（复核发现）：
- 旧结构缓存没有 fullDay 字段，若只判 fullDay，--offline 的降级逃逸就永远走不到；
- 反过来也不能把只存涨停池的「昨日」缓存当成完整日缓存（会 KeyError）。
这里在临时目录上验证四种组合，不触碰真实 data/。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

import run_daily as RD  # noqa: E402

FULL_MIN = {"zt": [], "dt": [], "zb": [], "sh": {}, "breadth": {"amount_yi": 1000.0, "complete": True}}
ZT_ONLY = {"zt": []}


def _with_cache(payload: dict):
    """把 cache_path 指到临时文件，写入 payload，返回 (load 结果工厂, 文件路径)。"""
    tmpdir = tempfile.mkdtemp(prefix="daycache_")
    path = Path(tmpdir) / "raw.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    orig = RD.cache_path
    RD.cache_path = lambda date: path
    return path, orig


def _load(payload, **kw):
    path, orig = _with_cache(payload)
    try:
        return RD.load_cache("2026-09-16", **kw)
    finally:
        RD.cache_path = orig


def _payload_legacy_full():
    return dict(FULL_MIN)  # 无 cacheVersion / 无 fullDay


def _payload_fresh_full():
    return {**FULL_MIN, "cacheVersion": RD.DAY_CACHE_VERSION, "fullDay": True}


def _payload_fresh_zt_only():
    return {**ZT_ONLY, "cacheVersion": RD.DAY_CACHE_VERSION, "fullDay": False}


def _payload_legacy_zt_only():
    return dict(ZT_ONLY)  # 无 cacheVersion / 无 fullDay


def test_fresh_full_day_is_accepted():
    d = _load(_payload_fresh_full(), require_full=True)
    assert d is not None and d["fullDay"] is True, d


def test_fresh_zt_only_is_rejected_even_with_allow_stale():
    """C2：zt-only 缓存不能冒充完整日，即使 --offline 放开旧版本。"""
    assert _load(_payload_fresh_zt_only(), require_full=True) is None
    assert _load(_payload_fresh_zt_only(), require_full=True, allow_stale=True) is None


def test_legacy_full_day_is_rejected_online_and_accepted_offline():
    """C3：旧结构完整日缓存 —— 联网时重取，离线时沿用并降级标注。"""
    assert _load(_payload_legacy_full(), require_full=True) is None

    d = _load(_payload_legacy_full(), require_full=True, allow_stale=True)
    assert d is not None, "离线模式必须能沿用旧缓存，否则会偷偷联网"
    assert d["breadth"]["complete"] is False, d["breadth"]


def test_legacy_zt_only_is_rejected_even_offline():
    """离线沿用只对「结构完整」的旧缓存开放。"""
    assert _load(_payload_legacy_zt_only(), require_full=True, allow_stale=True) is None


def test_stale_load_does_not_rewrite_cache_file():
    """降级标注只作用于返回值，不得把 complete=False 写回磁盘（避免污染缓存）。"""
    path, orig = _with_cache(_payload_legacy_full())
    try:
        before = path.read_bytes()
        d = RD.load_cache("2026-09-16", require_full=True, allow_stale=True)
        assert d["breadth"]["complete"] is False, d["breadth"]
        assert path.read_bytes() == before, "load_cache 不应写盘"
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["breadth"]["complete"] is True, on_disk["breadth"]
    finally:
        RD.cache_path = orig


def test_stale_v2_cache_rescales_fund_to_yi():
    """缓存版本 2 的 zt[].fund 是 /1e4 旧口径（误标为亿）；离线沿用必须折算。

    复核发现的残留漏洞：DAY_CACHE_VERSION 只挡在线缓存，--offline 放行旧缓存时
    若不折算，会把 21491.05 万当作 21491.05 亿发到页面上。
    """
    payload = {"cacheVersion": 2,
               "zt": [{"code": "000001", "fund": 21491.0502, "amount": 39.14},
                       {"code": "000002", "fund": "x", "amount": 1.0}],
               "dt": [], "zb": [], "sh": {"close": 3891.6},
               "breadth": {"up": 1, "down": 0, "flat": 0, "total": 1, "red_pct": 100.0,
                           "amount_yi": 1000.0, "complete": True}}
    d = _load(payload, require_full=True, allow_stale=True)
    assert d is not None
    # 21491.0502 万 → 2.15 亿
    assert abs(d["zt"][0]["fund"] - 2.15) < 0.01, d["zt"][0]
    assert d["zt"][1]["fund"] is None, "无法解析的旧值必须置 None，不得猜单位"


def test_stale_unknown_cache_blanks_fund():
    """版本不是 2（单位口径未知）时置 None —— 宁可显示 — 也不猜。"""
    payload = {"zt": [{"code": "000001", "fund": 21491.05}], "dt": [], "zb": [],
               "sh": {}, "breadth": {"up": 1}}
    d = _load(payload, require_full=True, allow_stale=True)
    assert d is not None and d["zt"][0]["fund"] is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
