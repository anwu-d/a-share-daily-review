# -*- coding: utf-8 -*-
"""
【自研策略】HybridCore
  70% 三板块核心池等权（底仓）
  30% 三行业轮动 50/30/20（卫星）
  周频再平衡，扣双边成本

股票池：科技/证券/有色，滤 ST；每板块取成交额前 40 只核心。
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
SEC_NAMES = ["tech", "securities", "nonferrous"]


def init_qlib():
    import os

    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    import qlib
    from qlib.constant import REG_CN

    qlib.init(provider_uri=PROVIDER, region=REG_CN, joblib_backend="threading")


def load_pools():
    from sector_universe import tech_pool, securities_pool, nonferrous_pool

    return {
        "tech": tech_pool(),
        "securities": securities_pool(),
        "nonferrous": nonferrous_pool(),
    }


def load_close_vol(codes, start, end):
    from qlib.data import D

    batches = [codes[i : i + 80] for i in range(0, len(codes), 80)]
    frames = []
    for i, b in enumerate(batches):
        print(f"  data {i+1}/{len(batches)}", flush=True)
        frames.append(D.features(b, ["$close", "$volume"], start_time=start, end_time=end))
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
    return out


def core_pools(pools, vol, n_each=40, lookback=120):
    tail = vol.tail(lookback)
    core = {}
    for sec, codes in pools.items():
        members = [c for c in codes if c in vol.columns]
        avg_v = tail[members].mean().dropna().sort_values(ascending=False)
        core[sec] = list(avg_v.head(n_each).index)
        print(f"  core {sec}: {len(core[sec])}", flush=True)
    return core


def sector_signals(close, core, mom_n=20, heat_n=10):
    ret1 = close.pct_change()
    cols = list(close.columns)
    lim = np.array([0.198 if c[2:4] in ("30", "68") else 0.098 for c in cols])
    lim_w = pd.DataFrame(np.tile(lim, (len(close), 1)), index=close.index, columns=cols)
    is_zt = ret1 >= (lim_w - 0.002)
    is_big = ret1 >= 0.05

    stats = {}
    for sec, members in core.items():
        members = [c for c in members if c in close.columns]
        if not members:
            continue
        eq = ret1[members].mean(axis=1)
        mom = (1 + eq).rolling(mom_n).apply(lambda x: x.prod() - 1, raw=True)
        heat = (is_zt[members].sum(axis=1) + is_big[members].sum(axis=1)).rolling(heat_n).mean()
        stats[sec] = {"members": members, "eq": eq, "mom": mom, "heat": heat}
    return stats, ret1, is_zt


def rotation_weights(stats, i, scheme=(0.5, 0.3, 0.2)):
    moms = {s: d["mom"].iloc[i] for s, d in stats.items()}
    heats = {s: d["heat"].iloc[i] for s, d in stats.items()}
    mom_r = pd.Series(moms).rank(ascending=False)
    heat_r = pd.Series(heats).rank(ascending=False)
    score = mom_r * 0.6 + heat_r * 0.4
    order = list(score.sort_values().index)
    w = {s: 0.0 for s in SEC_NAMES}
    for k, s in enumerate(order):
        if k < len(scheme):
            w[s] = scheme[k]
    return w, order, moms


def hybrid_backtest(close, core, start_i, end_i, base_w=0.70, sat_w=0.30,
                    rebalance=5, cost_bps=12.0):
    """组合日收益 = 70%核心等权 + 30%轮动板块等权"""
    stats, ret1, is_zt = sector_signals(close, core)
    all_core = []
    for m in core.values():
        all_core.extend(m)
    all_core = [c for c in all_core if c in close.columns]
    # 底仓：核心全池等权
    base_ret = ret1[all_core].mean(axis=1)

    dates = close.index
    port, rot_only, base_only = [], [], []
    alloc_hist = []
    cur_w = None
    for i in range(start_i, end_i):
        if (i - start_i) % rebalance == 0 or cur_w is None:
            cur_w, order, moms = rotation_weights(stats, i)
            alloc_hist.append(
                (str(dates[i])[:10], order, dict(cur_w), {k: round(v, 4) for k, v in moms.items()})
            )

        if i + 1 >= len(close):
            port.append(0.0)
            rot_only.append(0.0)
            base_only.append(0.0)
            continue

        # 卫星：按权重配三板块等权日收益
        rot = 0.0
        for sec, w in cur_w.items():
            if w > 0 and sec in stats:
                members = stats[sec]["members"]
                rot += w * ret1.iloc[i + 1][members].mean()
        b = float(base_ret.iloc[i + 1]) if ret1.iloc[i + 1][all_core].notna().any() else 0.0
        if rot != rot:
            rot = 0.0
        r = base_w * b + sat_w * rot
        if (i - start_i) % rebalance == 0:
            r -= cost_bps / 10000.0
        port.append(float(r))
        rot_only.append(float(rot))
        base_only.append(float(b))

    nav = np.cumprod(1 + np.array(port))
    bnav = np.cumprod(1 + np.array(base_only))
    days = max(len(nav), 1)
    return {
        "nav": nav,
        "base_nav": bnav,
        "ann": float(nav[-1] ** (252 / days) - 1),
        "bann": float(bnav[-1] ** (252 / days) - 1),
        "dd": float((nav / np.maximum.accumulate(nav) - 1).min()),
        "sharpe": float(np.mean(port) / (np.std(port) + 1e-12) * np.sqrt(252)),
        "win": float(np.mean([1 if x > 0 else 0 for x in port])),
        "days": len(nav),
        "dates": [str(dates[j])[:10] for j in range(start_i + 1, start_i + 1 + len(port))],
        "ret": port,
        "base_ret": base_only,
        "last_alloc": alloc_hist[-1] if alloc_hist else None,
        "alloc_hist": alloc_hist[-8:],
    }


def oos(res, oos_start):
    dates = res["dates"]
    idx = next((i for i, d in enumerate(dates) if d >= oos_start), None)
    if idx is None:
        return None
    r = np.array(res["ret"][idx:])
    b = np.array(res["base_ret"][idx:])
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
        "excess": float(nav[-1] - bnav[-1]),
    }


def main():
    ap = argparse.ArgumentParser(description="HybridCore 70/30")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-09-11")
    ap.add_argument("--warm", type=int, default=60)
    ap.add_argument("--core-n", type=int, default=40)
    ap.add_argument("--base-w", type=float, default=0.70)
    ap.add_argument("--sat-w", type=float, default=0.30)
    ap.add_argument("--rebalance", type=int, default=5)
    ap.add_argument("--oos-start", default="2026-06-01")
    args = ap.parse_args()

    print("init / pools ...", flush=True)
    init_qlib()
    pools = load_pools()
    all_codes = sorted(set(sum(pools.values(), [])))
    print(f"load {len(all_codes)} ...", flush=True)
    data = load_close_vol(all_codes, args.start, args.end)
    close = data["$close"]

    print(f"core {args.core_n}/sector ...", flush=True)
    core = core_pools(pools, data["$volume"], n_each=args.core_n)
    keep = sorted(set(sum(core.values(), [])))
    close = close[[c for c in keep if c in close.columns]]

    start_i = args.warm
    end_i = len(close) - 2
    print(f"hybrid backtest {close.index[start_i]} .. {close.index[end_i]}", flush=True)
    res = hybrid_backtest(
        close, core, start_i, end_i,
        base_w=args.base_w, sat_w=args.sat_w, rebalance=args.rebalance,
    )
    print(
        f"HYBRID {args.base_w:.0%}/{args.sat_w:.0%}: nav={res['nav'][-1]:.3f} "
        f"ann={res['ann']:.2%} dd={res['dd']:.2%} sharpe={res['sharpe']:.2f} win={res['win']:.1%}",
        flush=True,
    )
    print(
        f"BASE   core EW:    nav={res['base_nav'][-1]:.3f} ann={res['bann']:.2%}",
        flush=True,
    )
    o = oos(res, args.oos_start)
    if o:
        print(
            f"OOS({args.oos_start}→): hybrid nav={o['nav']:.3f} ann={o['ann']:.2%} dd={o['dd']:.2%} | "
            f"base nav={o['bnav']:.3f} ann={o['bann']:.2%} | excess nav={o['excess']:+.3f}",
            flush=True,
        )
    print("last alloc:", res["last_alloc"], flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "date": res["dates"],
        "hybrid_ret": res["ret"],
        "hybrid_nav": res["nav"],
        "base_ret": res["base_ret"][: len(res["dates"])],
        "base_nav": res["base_nav"][: len(res["dates"])],
    }).to_csv(OUT_DIR / "nav_hybridcore.csv", index=False)
    print(f"saved {OUT_DIR}/nav_hybridcore.csv", flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
