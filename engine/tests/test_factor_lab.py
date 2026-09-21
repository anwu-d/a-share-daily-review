# -*- coding: utf-8 -*-
"""
因子流水线测试（pytest 兼容；无 pytest 时可直接 python 运行）
    python engine/tests/test_factor_lab.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

from factor_lab import (  # noqa: E402
    build_label,
    ic_report,
    layer_returns,
    newey_west_t,
    report_table,
    tradable_mask,
    xs_norm,
)

from factor_lab import _synth  # noqa: E402


def test_xs_norm_range_and_center():
    close, mom20, good, noise, _ = _synth(n_days=40, n_stocks=50)
    z = xs_norm(mom20, "rank")
    assert np.nanmin(z.to_numpy()) >= -0.5 - 1e-9
    assert np.nanmax(z.to_numpy()) <= 0.5 + 1e-9
    zs = xs_norm(mom20, "zscore")
    assert abs(float(np.nanmean(zs.to_numpy()[-1]))) < 1e-6


def test_label_tail_is_nan_and_no_lookahead_shift():
    close, *_ = _synth(n_days=60, n_stocks=30)
    h = 5
    label = build_label(close, horizon=h)
    assert label.iloc[-h:].isna().all().all(), "尾端不足 horizon 应为 NaN"
    # label[T] 必须等于 close[T+h]/close[T]-1（不允许用到 T 之后以外的数据）
    t = 10
    manual = close.iloc[t + h] / close.iloc[t] - 1
    assert np.allclose(label.iloc[t].to_numpy(float), manual.to_numpy(float), equal_nan=True)


def test_shuffled_label_kills_ic():
    """把 label 在横截面内打乱 → IC 应回到 0 附近（未来函数/错位检测）"""
    close, mom20, good, noise, fwd5 = _synth(seed=11, n_days=200, n_stocks=120)
    label = build_label(close, horizon=5)
    base = ic_report(good, label, horizon=5)["ic_mean"]
    rng = np.random.default_rng(0)
    shuf = label.apply(lambda r: pd.Series(rng.permutation(r.to_numpy()), index=r.index), axis=1)
    after = ic_report(good, shuf, horizon=5)["ic_mean"]
    assert abs(base) > 0.2, f"原因子 IC 应显著: {base}"
    assert abs(after) < 0.05, f"打乱后 IC 应接近 0: {after}"


def test_noise_factor_has_near_zero_ic():
    close, mom20, good, noise, _ = _synth(seed=3, n_days=200, n_stocks=120)
    label = build_label(close, horizon=5)
    r = ic_report(noise, label, horizon=5)
    assert abs(r["ic_mean"]) < 0.05, r["ic_mean"]


def test_layer_monotonic_for_informative_factor():
    close, mom20, good, noise, _ = _synth(seed=5, n_days=200, n_stocks=120)
    lay = layer_returns(good, close, n=5)
    means = [lay["layer_mean"][f"Q{i}"] for i in range(1, 6)]
    assert means == sorted(means), f"分层应单调: {means}"
    assert lay["monotonic"] > 0.9


def test_newey_west_t_on_known_series():
    # 常数正值序列 → t 很大；零均值噪声 → |t| 小
    pos = np.full(200, 0.01)
    assert newey_west_t(pos) > 10
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.01, 500)
    assert abs(newey_west_t(noise)) < 3


def test_tradable_mask_excludes_suspended_and_limit_up():
    close, *_ = _synth(n_days=30, n_stocks=20)
    vol = pd.DataFrame(1.0, index=close.index, columns=close.columns)
    vol.iloc[0, 0] = np.nan
    close.iloc[1, 1] = np.nan
    limit_up = pd.DataFrame(False, index=close.index, columns=close.columns)
    limit_up.iloc[2, 2] = True
    m = tradable_mask(close, vol, limit_up=limit_up)
    assert not m.iloc[0, 0]      # 停牌（无成交）
    assert not m.iloc[1, 1]      # 无价格
    assert not m.iloc[2, 2]      # 涨停不可买
    assert m.iloc[3, 3]


def test_report_table_shape():
    close, mom20, good, noise, _ = _synth(n_days=150, n_stocks=100)
    label = build_label(close, horizon=5)
    tbl = report_table({"good": good, "noise": noise}, label, close, horizon=5)
    assert set(["factor", "ic", "icir", "t_nw", "mono", "ls_ann"]).issubset(tbl.columns)
    assert list(tbl["factor"])[0] == "good", "有效因子应排在首位"


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
