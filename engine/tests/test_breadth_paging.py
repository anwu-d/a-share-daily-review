# -*- coding: utf-8 -*-
"""
market_breadth 分页回归测试（pytest 兼容；无 pytest 时可直接运行：
    python engine/tests/test_breadth_paging.py

背景：东财 clist 接口每页实际上限 100，传 pz=500 会被静默截断。
旧实现按 pz=500 估算页数，只取到 1200/5559 只，且因按涨跌幅降序取数
拿到的全是当日红盘样本 → 红盘率被算成 100%。

这里用伪 _get 复现「服务端截断」与「中途空页」两种情形，不联网。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

import eastmoney as em  # noqa: E402
from metrics import pick_market_amount  # noqa: E402

SERVER_PAGE_CAP = 100  # 服务端真实上限


def make_fake_get(total: int, server_cap: int = SERVER_PAGE_CAP, stop_after_page: int | None = None):
    """构造伪 _get：按 f3 降序返回全样本，每页最多 server_cap 条。

    前 60% 标的为正涨幅、接下来 25% 为负、余下为平盘，成交额固定 1 亿元/只，
    这样期望值可以手算。
    """
    n_up = int(total * 0.6)
    n_down = int(total * 0.25)

    rows = []
    for i in range(total):
        if i < n_up:
            pct = 3.0
        elif i < n_up + n_down:
            pct = -2.0
        else:
            pct = 0.0
        rows.append({"f3": pct, "f6": 1e8})
    calls = {"pages": 0}

    def fake_get(url, params, timeout=None):
        pn = int(params.get("pn", 1))
        pz = int(params.get("pz", pn))
        if pz == 1:  # probe
            return {"data": {"total": total}}
        calls["pages"] += 1
        if stop_after_page is not None and pn > stop_after_page:
            return {"data": {"diff": []}}
        size = min(pz, server_cap)
        start = (pn - 1) * size
        chunk = rows[start:start + size]
        return {"data": {"diff": chunk}}

    fake_get.calls = calls
    return fake_get, n_up, n_down, total - n_up - n_down


def _run(total, server_cap=SERVER_PAGE_CAP, stop_after_page=None):
    fake, n_up, n_down, n_flat = make_fake_get(total, server_cap, stop_after_page)
    orig = em._get
    em._get = fake
    try:
        return em.market_breadth(), n_up, n_down, n_flat, fake.calls
    finally:
        em._get = orig


def test_full_coverage_when_server_caps_page_size():
    """核心回归：服务端把 pz 截断到 100 时，仍必须取满全部样本。"""
    total = 5559
    b, n_up, n_down, n_flat, calls = _run(total)
    assert b["fetched"] == total, f"fetched={b['fetched']} != total={total}"
    assert b["complete"] is True, b
    assert b["up"] == n_up and b["down"] == n_down and b["flat"] == n_flat, b
    assert b["total"] == total, b
    assert calls["pages"] >= (total + SERVER_PAGE_CAP - 1) // SERVER_PAGE_CAP, calls


def test_partial_pages_do_not_report_full_sample():
    """服务端每页只回 30 条时也要取满，而不是按名义 pz 提前停止。"""
    total = 5559
    b, n_up, n_down, n_flat, _ = _run(total, server_cap=30)
    assert b["fetched"] == total, b
    assert b["complete"] is True, b
    assert b["up"] == n_up and b["down"] == n_down, b


def test_amount_is_summed_over_all_rows():
    total = 5559
    b, *_ = _run(total)
    assert abs(b["amount_yi"] - total) < 0.5, b  # 每只 1 亿元


def test_truncated_run_is_flagged_incomplete():
    """中途空页 → complete=False，便于调用方拒绝把截断样本当全量用。"""
    total = 5559
    b, *_ = _run(total, stop_after_page=3)
    assert b["complete"] is False, b
    assert b["fetched"] < total, b
    assert b["fetched"] == 3 * SERVER_PAGE_CAP, b


def test_red_pct_matches_sample():
    """红盘率必须由实际统计到的样本算出，而不是隐含的全红盘。"""
    total = 1000
    b, n_up, n_down, n_flat, _ = _run(total)
    expect = round(n_up * 100.0 / total, 1)
    assert b["red_pct"] == expect, (b["red_pct"], expect)
    assert 0 < b["red_pct"] < 100, b


def test_amount_caliber_rejects_incomplete_sample():
    """只有 complete=True 的全 A 值可用；截断样本必须回退沪市并如实标注。"""
    full = {"amount_yi": 18390.09, "complete": True}
    assert pick_market_amount(full, 8711.41) == (18390.09, "全A")

    truncated = {"amount_yi": 1000.0, "complete": False}
    assert pick_market_amount(truncated, 8711.41) == (8711.41, "沪市（回退）")

    # 旧结构缓存没有 complete 字段 → 不可采信
    legacy = {"amount_yi": 1000.0}
    assert pick_market_amount(legacy, 8711.41) == (8711.41, "沪市（回退）")

    # 两个来源都缺
    assert pick_market_amount({}, None) == (None, "不可用")
    assert pick_market_amount(None, 8711.41) == (8711.41, "沪市（回退）")


def test_truncated_sample_would_mislabel_caliber_without_guard():
    """回归护栏：截断样本经 pick_market_amount 后不会再被标成全 A。"""
    total = 5559
    b, *_ = _run(total, stop_after_page=3)
    assert b["complete"] is False and b["amount_yi"] is not None
    amount, scope = pick_market_amount(b, 8711.41)
    assert scope != "全A", (amount, scope)


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
