# -*- coding: utf-8 -*-
"""
仓位锚融合测试（pytest 兼容；无 pytest 时可直接运行：
    python engine/tests/test_position_blend.py

覆盖 S2.3：宏观 position（0–0.9 比例）→ 系数 [0.6, 1.0] → 盘面锚 × 系数，
以及「宏观缺失 / 滞后超限不调整」的边界。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

from metrics import (  # noqa: E402
    MACRO_COEF_MIN,
    MACRO_LAG_MAX_DAYS,
    blend_position_anchor,
    macro_coefficient,
)

EPS = 1e-9


def test_coefficient_range_and_anchors():
    """值域与两个锚点：宏观满配不削减，宏观为 0 保留六成。"""
    assert abs(macro_coefficient(0.9) - 1.0) < EPS
    assert abs(macro_coefficient(0.0) - MACRO_COEF_MIN) < EPS
    # 越界一律钳制
    assert abs(macro_coefficient(1.2) - 1.0) < EPS
    assert abs(macro_coefficient(-0.5) - MACRO_COEF_MIN) < EPS


def test_coefficient_known_values():
    """线性归一化：0.6 + 0.4 × (pos / 0.9)。"""
    assert abs(macro_coefficient(0.27) - 0.72) < 1e-3
    assert abs(macro_coefficient(0.4) - 0.7778) < 1e-4
    assert abs(macro_coefficient(0.7) - 0.9111) < 1e-4


def test_coefficient_unavailable_returns_identity():
    """None / NaN / 非数值 → 1.0，等价于宏观未参与。"""
    for bad in (None, float("nan"), "x", [], {}):
        assert macro_coefficient(bad) == 1.0, bad


def test_coefficient_is_monotonic():
    xs = [0.0, 0.1, 0.27, 0.4, 0.55, 0.7, 0.85, 0.9, 1.0]
    cs = [macro_coefficient(x) for x in xs]
    assert all(b >= a - EPS for a, b in zip(cs, cs[1:])), cs
    assert all(MACRO_COEF_MIN <= c <= 1.0 for c in cs), cs


def test_blend_matches_spec_example():
    """S2.3 的算例：盘面锚 5.0 × 宏观 0.4 → 3.9 成。"""
    r = blend_position_anchor(5.0, 0.4, "2026-09-16", "2026-09-16")
    assert r["final"] == 3.9, r
    assert r["board"] == 5.0 and r["adjusted"] is True, r
    assert abs(r["coeff"] - 0.7778) < 1e-4, r


def test_blend_only_cuts_never_raises():
    """系数 ≤ 1，最终值不可能高于盘面锚。"""
    for board in (1.0, 2.0, 3.0, 4.0, 5.0, 7.0):
        for pos in (None, 0.0, 0.27, 0.4, 0.7, 0.9):
            r = blend_position_anchor(board, pos, "2026-09-16", "2026-09-16")
            assert r["final"] <= board + EPS, (board, pos, r)
            assert r["final"] >= 1.0 - EPS, (board, pos, r)
            assert r["final"] <= 7.0 + EPS, (board, pos, r)


def test_blend_floor_is_one_cheng():
    """低盘面锚 × 最悲观宏观也不会被压到 1 成以下。"""
    r = blend_position_anchor(1.0, 0.0, "2026-09-16", "2026-09-16")
    assert r["final"] == 1.0, r


def test_blend_without_macro_is_identity():
    r = blend_position_anchor(5.0, None, None, "2026-09-16")
    assert r["final"] == 5.0 and r["adjusted"] is False and r["coeff"] == 1.0, r


def test_blend_lag_threshold_both_sides():
    """间隔 ≤ MACRO_LAG_MAX_DAYS 天仍生效；超过则不调整。"""
    inside = blend_position_anchor(5.0, 0.4, "2026-09-11", "2026-09-16")
    assert MACRO_LAG_MAX_DAYS == 5 and inside["final"] == 3.9, inside

    outside = blend_position_anchor(5.0, 0.4, "2026-09-10", "2026-09-16")
    assert outside["adjusted"] is False and outside["final"] == 5.0, outside
    assert "滞后" in outside["reason"], outside


def test_blend_rejects_macro_newer_than_review_day():
    """复盘旧日期时，晚于复盘日的宏观时点属于未来信息，必须拒绝。"""
    r = blend_position_anchor(5.0, 0.4, "2026-09-18", "2026-09-16")
    assert r["adjusted"] is False and r["final"] == 5.0, r
    assert "晚于复盘日" in r["reason"], r


def test_blend_caps_at_seven():
    """上限 7 成；盘面锚本身不会超过 7，这里直接验证函数边界。"""
    r = blend_position_anchor(9.0, 0.9, "2026-09-16", "2026-09-16")
    assert r["final"] == 7.0, r


def test_blend_lag_unparsable_date_does_not_block():
    """日期解析失败时不阻断调整（宁可调整也不静默失效）。"""
    r = blend_position_anchor(5.0, 0.4, "not-a-date", "2026-09-16")
    assert r["final"] == 3.9 and r["adjusted"] is True, r


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
