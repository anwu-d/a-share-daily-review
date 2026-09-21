# -*- coding: utf-8 -*-
"""
【自研策略】PatternDip · 形态反直觉（参考「K线形态」分享）

论文要点（本地样本统计，2026 上半年）：
  - 趋势里接「大阴棒」后续几天可能不差
  - 次日去追「趋势大阳加速」容易亏
  - 经典看跌形态（顶分型/黄昏星/跳空低开）多数仍差
  - 底分型+启明星+跳空低开 的看涨作用在减弱

本策略（无未来函数）：
  信号 T 日收盘可算，T+1 持有
  1) TREND_DIP：站上 MA20 且 MA20 上行 + 当日实体大阴（跌幅>阈值）+ 收盘仍在 MA20 上
  2) 不追加速：近 10 日涨幅已高 + 当日跳空高开大阳 → 禁止买入
  3) 组合：核心池内按 TREND_DIP 打分取 TopK，周频调仓
"""
from __future__ import annotations

import argparse
import multiprocessing
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

PROVIDER = str(ROOT / "data" / "qlib_cn")
OUT_DIR = ROOT / "data" / "strategies"


def init_qlib():
    import os

    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    import qlib
    from qlib.constant import REG_CN

    qlib.init(provider_uri=PROVIDER, region=REG_CN, joblib_backend="threading")


def load_pool_and_data(start, end, core_n=40):
    from sector_universe import tech_pool, securities_pool, nonferrous_pool
    from qlib.data import D

    pools = {
        "tech": tech_pool(),
        "securities": securities_pool(),
        "nonferrous": nonferrous_pool(),
    }
    all_codes = sorted(set(sum(pools.values(), [])))
    print(f"load {len(all_codes)} ...", flush=True)
    fields = ["$open", "$high", "$low", "$close", "$volume"]
    batches = [all_codes[i : i + 80] for i in range(0, len(all_codes), 80)]
    frames = []
    for i, b in enumerate(batches):
        print(f"  {i+1}/{len(batches)}", flush=True)
        frames.append(D.features(b, fields, start_time=start, end_time=end))
    panel = pd.concat(frames, axis=0)
    out = {}
    for col in panel.columns:
        s = panel[col]
        if isinstance(s.index, pd.MultiIndex):
            names = list(s.index.names)
            w = s.unstack(level="instrument" if "instrument" in names else -1)
        else:
            w = s
        if not any(str(c).startswith(("SH", "SZ")) for c in w.columns[:3]):
            w = w.T
        w.columns = [str(c) for c in w.columns]
        out[col] = w

    # 核心流动性池
    vol = out["$volume"]
    core = []
    for pool in pools.values():
        members = [c for c in pool if c in vol.columns]
        avg = vol[members].tail(120).mean().dropna().sort_values(ascending=False)
        core.extend(list(avg.head(core_n).index))
    core = sorted(set(core))
    for k in out:
        out[k] = out[k][[c for c in core if c in out[k].columns]]
    print(f"core {len(core)}", flush=True)
    return out


def build_patterns(ohlc):
    o, h, l, c = ohlc["$open"], ohlc["$high"], ohlc["$low"], ohlc["$close"]
    ret1 = c.pct_change()
    body = (c - o) / c.shift(1)
    body_up = (o - c) / c.shift(1)  # 阴线实体占比（正=收跌）
    ma20 = c.rolling(20).mean()
    ma20_up = ma20 > ma20.shift(3)
    above_ma20 = c >= ma20
    up10 = c.pct_change(10)
    gap_up = o / c.shift(1) - 1
    gap_dn = c.shift(1) / o - 1  # 正数=低开幅度

    # 实体大阴：收跌且实体 > 3%
    big_red = (c < o) & (body_up > 0.03)
    # 实体大阳：收涨且实体 > 3%
    big_green = (c > o) & ((c - o) / c.shift(1) > 0.03)
    # 跳空高开 >1%
    gap_up_big = gap_up > 0.01
    # 跳空低开 >1%
    gap_dn_big = (o / c.shift(1) - 1) < -0.01

    # 信号：趋势中的大阴低吸（T 日收盘）
    trend_dip = ma20_up & above_ma20 & big_red & (ret1 > -0.095)  # 排除接近跌停崩盘

    # 禁止：加速追高（近10日已强 + 跳空大阳）
    chase_bad = (up10 > 0.15) & big_green & gap_up_big

    # 打分：阴线越长、趋势越稳，分越高（负收益越大越好接）
    score = (-ret1).where(trend_dip & ~chase_bad)

    return {
        "open": o, "high": h, "low": l, "close": c,
        "ret1": ret1, "ma20": ma20, "ma20_up": ma20_up,
        "big_red": big_red, "big_green": big_green,
        "gap_up_big": gap_up_big, "gap_dn_big": gap_dn_big,
        "trend_dip": trend_dip, "chase_bad": chase_bad,
        "score": score, "up10": up10,
    }


def backtest(pat, start_i, end_i, topk=15, rebalance=5, cost_bps=15.0, mode="dip"):
    c, score = pat["close"], pat["score"]
    dates = c.index
    n = len(dates)
    port, hold_n = [], []
    hold = []
    for i in range(start_i, end_i):
        if (i - start_i) % rebalance == 0 or not hold:
            row = score.iloc[i].dropna()
            if mode == "dip":
                hold = list(row.sort_values(ascending=False).head(topk).index) if not row.empty else []
            else:
                hold = []
            hold_n.append(len(hold))
        if i + 1 >= n:
            port.append(0.0)
            continue
        if not hold:
            # 无信号则空仓（或可退回等权，这里空仓以测信号纯度）
            port.append(0.0)
            continue
        r = (c.iloc[i + 1][hold] / c.iloc[i][hold] - 1).mean()
        if (i - start_i) % rebalance == 0:
            r -= cost_bps / 10000.0
        port.append(float(r) if r == r else 0.0)

    nav = np.cumprod(1 + np.array(port)) if port else np.array([1.0])
    # 基准：池内等权
    bench = pat["ret1"].iloc[start_i + 1 : start_i + 1 + len(port)].mean(axis=1).fillna(0).values
    bnav = np.cumprod(1 + bench) if len(bench) else np.array([1.0])
    days = max(len(nav), 1)
    return {
        "nav": nav,
        "bnav": bnav,
        "ann": float(nav[-1] ** (252 / days) - 1),
        "bann": float(bnav[-1] ** (252 / days) - 1),
        "dd": float((nav / np.maximum.accumulate(nav) - 1).min()),
        "sharpe": float(np.mean(port) / (np.std(port) + 1e-12) * np.sqrt(252)),
        "win": float(np.mean([1 if x > 0 else 0 for x in port])) if port else 0,
        "days": len(nav),
        "avg_hold": float(np.mean(hold_n)) if hold_n else 0,
        "dates": [str(dates[j])[:10] for j in range(start_i + 1, start_i + 1 + len(port))],
        "ret": port,
        "bench_ret": list(bench) if len(bench) else [],
    }


def oos(res, oos_start):
    idx = next((i for i, d in enumerate(res["dates"]) if d >= oos_start), None)
    if idx is None:
        return None
    r = np.array(res["ret"][idx:])
    b = np.array(res["bench_ret"][idx:])
    nav = np.cumprod(1 + r)
    bnav = np.cumprod(1 + b)
    days = max(len(nav), 1)
    return {
        "nav": float(nav[-1]),
        "ann": float(nav[-1] ** (252 / days) - 1),
        "dd": float((nav / np.maximum.accumulate(nav) - 1).min()),
        "bnav": float(bnav[-1]),
        "bann": float(bnav[-1] ** (252 / days) - 1),
        "days": len(nav),
    }


def event_study(pat, start_i, end_i, horizons=(1, 3, 5)):
    """大阴 vs 大阳加速 的事件研究（T 日信号 → 后 N 日收益）"""
    c = pat["close"]
    ret_fwd = {k: c.pct_change(k).shift(-k) for k in horizons}
    rows = []
    for name, mask in [
        ("trend_dip 大阴接飞刀", pat["trend_dip"]),
        ("chase_bad 追加速", pat["chase_bad"]),
        ("big_red 任意大阴", pat["big_red"]),
        ("big_green 任意大阳", pat["big_green"]),
    ]:
        rec = {"pattern": name, "n_days": int(mask.iloc[start_i:end_i].sum().sum())}
        for k in horizons:
            r = ret_fwd[k].where(mask)
            slice_r = r.iloc[start_i:end_i]
            rec[f"mean_{k}d"] = float(slice_r.stack().mean()) if not slice_r.stack().empty else float("nan")
        rows.append(rec)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-09-11")
    ap.add_argument("--warm", type=int, default=40)
    ap.add_argument("--core-n", type=int, default=40)
    ap.add_argument("--topk", type=int, default=15)
    ap.add_argument("--rebalance", type=int, default=5)
    ap.add_argument("--oos-start", default="2026-06-01")
    args = ap.parse_args()

    print("init ...", flush=True)
    init_qlib()
    data = load_pool_and_data(args.start, args.end, core_n=args.core_n)
    print("patterns ...", flush=True)
    pat = build_patterns(data)

    start_i = args.warm
    end_i = len(pat["close"]) - 2
    print(f"backtest {pat['close'].index[start_i]} .. {pat['close'].index[end_i]}", flush=True)

    print("\n== 事件研究（T 日信号 → 后 N 日均收益）==", flush=True)
    ev = event_study(pat, start_i, end_i)
    print(ev.to_string(index=False), flush=True)

    print("\n== 组合回测 TREND_DIP TopK ==", flush=True)
    res = backtest(pat, start_i, end_i, topk=args.topk, rebalance=args.rebalance, mode="dip")
    print(
        f"DIP top{args.topk}: nav={res['nav'][-1]:.3f} ann={res['ann']:.2%} dd={res['dd']:.2%} "
        f"sharpe={res['sharpe']:.2f} win={res['win']:.1%} avg_hold={res['avg_hold']:.1f}",
        flush=True,
    )
    print(f"BENCH EW:       nav={res['bnav'][-1]:.3f} ann={res['bann']:.2%}", flush=True)
    o = oos(res, args.oos_start)
    if o:
        print(
            f"OOS({args.oos_start}→): dip nav={o['nav']:.3f} ann={o['ann']:.2%} dd={o['dd']:.2%} | "
            f"bench nav={o['bnav']:.3f} ann={o['bann']:.2%}",
            flush=True,
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "date": res["dates"], "ret": res["ret"], "nav": res["nav"],
        "bench_ret": res["bench_ret"][: len(res["dates"])],
        "bench_nav": res["bnav"][: len(res["dates"])],
    }).to_csv(OUT_DIR / "nav_pattern_dip.csv", index=False)
    ev.to_csv(OUT_DIR / "pattern_event_study.csv", index=False)
    print(f"saved {OUT_DIR}/nav_pattern_dip.csv & pattern_event_study.csv", flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
