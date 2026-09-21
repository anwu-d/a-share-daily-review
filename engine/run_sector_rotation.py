# -*- coding: utf-8 -*-
"""
【自研策略】三行业轮动 SectorRotation
科技 / 证券 / 有色

逻辑：
  1. 每板块算「等权指数」
  2. 周频比较三板块：20日动量 + 10日涨停/大涨密度
  3. 仓位分配：
     - 最强板块 50%
     - 次强 30%
     - 最弱 20%   （或极端分化时只配前两个）
  4. 板块内默认等权（可选 topk 动量选股）

对比基准：三板块合并池等权
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
SECTORS = ["tech", "securities", "nonferrous"]


def init_qlib():
    import os

    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    import qlib
    from qlib.constant import REG_CN

    qlib.init(provider_uri=PROVIDER, region=REG_CN, joblib_backend="threading")


def load_pools():
    from sector_universe import tech_pool, securities_pool, nonferrous_pool

    t, s, n = tech_pool(), securities_pool(), nonferrous_pool()
    return {"tech": t, "securities": s, "nonferrous": n}


def load_features(codes: list[str], start: str, end: str, fields=None) -> dict:
    """返回 {field: wide df}"""
    from qlib.data import D

    fields = fields or ["$close", "$volume"]
    batches = [codes[i : i + 80] for i in range(0, len(codes), 80)]
    frames = []
    for i, b in enumerate(batches):
        print(f"  data {i+1}/{len(batches)}", flush=True)
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
    return out


def select_core_pool(pools: dict, vol: pd.DataFrame, n_each: int = 40, lookback: int = 120) -> dict:
    """
    每板块取「近 lookback 日均成交量」最大的 n_each 只（流动性代理，近似核心票）。
    证券/有色不足 n_each 则全保留。
    """
    core = {}
    tail = vol.tail(lookback)
    for sec, codes in pools.items():
        members = [c for c in codes if c in vol.columns]
        if not members:
            core[sec] = []
            continue
        avg_v = tail[members].mean().dropna().sort_values(ascending=False)
        picked = list(avg_v.head(n_each).index)
        core[sec] = picked
        print(f"  core {sec}: {len(picked)}/{len(members)}  例 {picked[:5]}", flush=True)
    return core


def sector_stats(close: pd.DataFrame, pools: dict, mom_n=20, heat_n=10):
    """板块等权收益、动量、涨停密度"""
    ret1 = close.pct_change()
    cols = list(close.columns)
    lim = np.array([0.198 if c[2:4] in ("30", "68") else 0.098 for c in cols])
    lim_w = pd.DataFrame(np.tile(lim, (len(close), 1)), index=close.index, columns=cols)
    is_zt = ret1 >= (lim_w - 0.002)
    is_big = ret1 >= 0.05

    stats = {}
    for sec, codes in pools.items():
        members = [c for c in codes if c in close.columns]
        if not members:
            continue
        r = ret1[members]
        eq = r.mean(axis=1)  # 板块等权日收益
        mom = (1 + eq).rolling(mom_n).apply(lambda x: x.prod() - 1, raw=True)
        zt_n = is_zt[members].sum(axis=1).rolling(heat_n).mean()
        big_n = is_big[members].sum(axis=1).rolling(heat_n).mean()
        heat = zt_n + big_n
        stats[sec] = {
            "members": members,
            "eq_ret": eq,
            "mom": mom,
            "heat": heat,
            "n": len(members),
        }
    return stats, ret1, is_zt


def rank_sectors(stats, i):
    """T 日对三板块打分：mom rank + heat rank"""
    moms, heats = {}, {}
    for sec, d in stats.items():
        moms[sec] = d["mom"].iloc[i]
        heats[sec] = d["heat"].iloc[i]
    # rank 1=最强
    mom_s = pd.Series(moms).rank(ascending=False)
    heat_s = pd.Series(heats).rank(ascending=False)
    # 综合：动量更重要
    score = mom_s * 0.6 + heat_s * 0.4
    order = list(score.sort_values().index)  # 分数低=排名靠前=更强
    return order, score.to_dict(), moms, heats


def backtest(stats, ret1, close, start_i, end_i, topk_inside=0, mode="weights",
             weights=(0.5, 0.3, 0.2), cost_bps=12.0, rebalance=5):
    """
    mode:
      weights — 三板块按强弱分 50/30/20，板块内等权
      top2    — 只配前两板块各 50%
      topk    — 最强板块内选 topk 动量股
    """
    dates = close.index
    port, alloc_hist = [], []
    prev_alloc = None
    for i in range(start_i, end_i):
        if (i - start_i) % rebalance == 0 or prev_alloc is None:
            order, score_d, moms, heats = rank_sectors(stats, i)
            if mode == "top2":
                alloc = {order[0]: 0.5, order[1]: 0.5, order[2]: 0.0}
            elif mode == "topk":
                alloc = {order[0]: 1.0, order[1]: 0.0, order[2]: 0.0}
            else:
                alloc = {order[0]: weights[0], order[1]: weights[1], order[2]: weights[2]}
            alloc_hist.append((str(dates[i])[:10], order, dict(alloc), {k: round(v, 3) for k, v in moms.items()}))
            prev_alloc = alloc

        if i + 1 >= len(dates):
            port.append(0.0)
            continue

        r = 0.0
        for sec, w in prev_alloc.items():
            if w <= 0 or sec not in stats:
                continue
            members = stats[sec]["members"]
            if mode == "topk" and topk_inside > 0 and sec == max(prev_alloc, key=prev_alloc.get):
                # 最强板块内按 20 日动量选 topk
                mom20 = close[members].pct_change(20).iloc[i].dropna()
                picks = list(mom20.sort_values(ascending=False).head(topk_inside).index)
                if not picks:
                    picks = members[:5]
                r += w * (close.iloc[i + 1][picks] / close.iloc[i][picks] - 1).mean()
            else:
                r += w * ret1.iloc[i + 1][members].mean()

        if (i - start_i) % rebalance == 0:
            r -= cost_bps / 10000.0
        port.append(float(r) if r == r else 0.0)

    nav = np.cumprod(1 + np.array(port)) if port else np.array([1.0])
    all_members = []
    for d in stats.values():
        all_members.extend(d["members"])
    bench_r = ret1.iloc[start_i + 1 : start_i + 1 + len(port)][list(set(all_members))].mean(axis=1).fillna(0).values
    bnav = np.cumprod(1 + bench_r) if len(bench_r) else np.array([1.0])
    days = max(len(nav), 1)
    return {
        "nav": nav,
        "bench_nav": bnav,
        "ann": float(nav[-1] ** (252 / days) - 1),
        "bann": float(bnav[-1] ** (252 / days) - 1) if len(bnav) else 0.0,
        "dd": float((nav / np.maximum.accumulate(nav) - 1).min()),
        "sharpe": float(np.mean(port) / (np.std(port) + 1e-12) * np.sqrt(252)),
        "win": float(np.mean([1 if x > 0 else 0 for x in port])) if port else 0.0,
        "days": len(nav),
        "dates": [str(dates[j])[:10] for j in range(start_i + 1, start_i + 1 + len(port))],
        "ret": port,
        "bench_ret": list(bench_r) if len(bench_r) else [],
        "alloc_hist": alloc_hist[-20:],
        "last_alloc": alloc_hist[-1] if alloc_hist else None,
    }


def oos_slice(res, oos_start: str):
    dates = res["dates"]
    idx = next((i for i, d in enumerate(dates) if d >= oos_start), None)
    if idx is None:
        return None
    r = res["ret"][idx:]
    b = res["bench_ret"][idx:] if res["bench_ret"] else []
    nav = np.cumprod(1 + np.array(r))
    bnav = np.cumprod(1 + np.array(b)) if b else np.array([1.0])
    days = max(len(nav), 1)
    return {
        "nav": float(nav[-1]),
        "ann": float(nav[-1] ** (252 / days) - 1),
        "dd": float((nav / np.maximum.accumulate(nav) - 1).min()),
        "bnav": float(bnav[-1]),
        "days": len(nav),
    }


def main():
    ap = argparse.ArgumentParser(description="三行业轮动")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-09-11")
    ap.add_argument("--warm", type=int, default=60)
    ap.add_argument("--rebalance", type=int, default=5)
    ap.add_argument("--oos-start", default="2026-06-01")
    ap.add_argument("--mode", default="weights", choices=["weights", "top2", "topk"])
    ap.add_argument("--topk-inside", type=int, default=10)
    ap.add_argument("--core-n", type=int, default=40, help="每板块流动性核心股数量；0=不限")
    args = ap.parse_args()

    print("init / pools ...", flush=True)
    init_qlib()
    pools = load_pools()
    for k, v in pools.items():
        print(f"  {k}: {len(v)}", flush=True)

    all_codes = sorted(set(sum(pools.values(), [])))
    print(f"load data {len(all_codes)} ...", flush=True)
    data = load_features(all_codes, args.start, args.end, ["$close", "$volume"])
    close = data["$close"]

    if args.core_n and args.core_n > 0:
        print(f"select core {args.core_n}/sector by liquidity ...", flush=True)
        pools = select_core_pool(pools, data["$volume"], n_each=args.core_n)
        # 只保留 core 内的列
        keep = sorted(set(sum(pools.values(), [])))
        close = close[[c for c in keep if c in close.columns]]

    print("sector stats ...", flush=True)
    stats, ret1, is_zt = sector_stats(close, pools)

    start_i = args.warm
    end_i = len(close) - 2
    print(f"backtest {close.index[start_i]} .. {close.index[end_i]} mode={args.mode}", flush=True)

    # 三种配置一起跑便于对比
    configs = [
        ("weights_50_30_20", "weights", 0),
        ("top2_50_50", "top2", 0),
        ("top1_topk10", "topk", args.topk_inside),
    ]
    for name, mode, tk in configs:
        res = backtest(
            stats, ret1, close, start_i, end_i,
            topk_inside=tk, mode=mode, rebalance=args.rebalance,
        )
        oos = oos_slice(res, args.oos_start)
        print(
            f"  {name}: FULL nav={res['nav'][-1]:.3f} ann={res['ann']:.2%} dd={res['dd']:.2%} "
            f"sharpe={res['sharpe']:.2f} | bench nav={res['bench_nav'][-1]:.3f} ann={res['bann']:.2%}",
            flush=True,
        )
        if oos:
            print(
                f"    OOS({args.oos_start}→): nav={oos['nav']:.3f} ann={oos['ann']:.2%} dd={oos['dd']:.2%} "
                f"| bench nav={oos['bnav']:.3f} days={oos['days']}",
                flush=True,
            )
        if name.startswith("weights"):
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"date": res["dates"], "ret": res["ret"], "nav": res["nav"],
                          "bench_ret": res["bench_ret"][:len(res["dates"])],
                          "bench_nav": res["bench_nav"][:len(res["dates"])]}
                         ).to_csv(OUT_DIR / "nav_sector_rotation.csv", index=False)
            print("  last alloc:", res["last_alloc"], flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
