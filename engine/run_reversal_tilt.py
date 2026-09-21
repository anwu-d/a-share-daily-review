# -*- coding: utf-8 -*-
"""
【自研策略】ReversalTilt
  80% 三板块核心池等权
  20% 核心池内「10日超跌反转」倾斜
    - 取 10 日跌幅最大的一批
    - 过滤仍在加速下跌的（近 1 日跌幅不过大 / 未再创新低附近）
  周频再平衡

说明：主力净流入无免费历史，回测用价量反转；实盘可在卫星仓再加东财资金流过滤。
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


def load_pools():
    from sector_universe import tech_pool, securities_pool, nonferrous_pool

    return {
        "tech": tech_pool(),
        "securities": securities_pool(),
        "nonferrous": nonferrous_pool(),
    }


def load_ohlcv(codes, start, end):
    from qlib.data import D

    batches = [codes[i : i + 80] for i in range(0, len(codes), 80)]
    frames = []
    for i, b in enumerate(batches):
        print(f"  data {i+1}/{len(batches)}", flush=True)
        frames.append(D.features(b, ["$close", "$volume", "$low"], start_time=start, end_time=end))
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


def pick_reversal(close, members, i, topk=15, lookback=10, max_drop1=-0.095, min_mom=-0.45):
    """
    T 日选超跌票：
      - 10 日动量最低（跌最多）
      - 近 1 日不在加速崩（跌幅 > max_drop1 剔除，如 -9.5%）
      - 10 日跌幅不过分极端（< min_mom 如 -45% 剔除，防退市边缘）
    """
    if i < lookback + 1:
        return []
    c = close[members]
    mom10 = c.iloc[i] / c.iloc[i - lookback] - 1
    ret1 = c.iloc[i] / c.iloc[i - 1] - 1
    mom10 = mom10.dropna()
    if mom10.empty:
        return []
    # 过滤
    ok = []
    for code, m in mom10.items():
        r1 = ret1.get(code, np.nan)
        if r1 != r1:
            continue
        if r1 < max_drop1:  # 当日仍在大跌
            continue
        if m < min_mom:
            continue
        ok.append((code, m, r1))
    if not ok:
        return []
    ok.sort(key=lambda x: x[1])  # 跌最多在前
    return [x[0] for x in ok[:topk]]


def backtest(close, core, start_i, end_i, base_w=0.80, sat_w=0.20,
             topk=15, rebalance=5, cost_bps=14.0):
    all_core = []
    for m in core.values():
        all_core.extend([c for c in m if c in close.columns])
    all_core = sorted(set(all_core))
    ret1 = close.pct_change()

    dates = close.index
    port, base_only, sat_only = [], [], []
    hold_sat = []
    for i in range(start_i, end_i):
        if (i - start_i) % rebalance == 0 or not hold_sat:
            hold_sat = pick_reversal(close, all_core, i, topk=topk)

        if i + 1 >= len(close):
            port.append(0.0); base_only.append(0.0); sat_only.append(0.0)
            continue

        b = float(ret1.iloc[i + 1][all_core].mean())
        if hold_sat:
            s = float(ret1.iloc[i + 1][hold_sat].mean())
        else:
            s = b  # 无卫星则退回基准
        r = base_w * b + sat_w * s
        if (i - start_i) % rebalance == 0:
            r -= cost_bps / 10000.0
        port.append(float(r) if r == r else 0.0)
        base_only.append(float(b) if b == b else 0.0)
        sat_only.append(float(s) if s == s else 0.0)

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
        "sat_ret": sat_only,
        "last_hold": hold_sat,
    }


def oos_slice(res, oos_start):
    dates = res["dates"]
    idx = next((i for i, d in enumerate(dates) if d >= oos_start), None)
    if idx is None:
        return None
    r = np.array(res["ret"][idx:])
    b = np.array(res["base_ret"][idx:])
    s = np.array(res["sat_ret"][idx:])
    nav = np.cumprod(1 + r)
    bnav = np.cumprod(1 + b)
    snav = np.cumprod(1 + s)
    days = max(len(nav), 1)
    return {
        "nav": float(nav[-1]),
        "ann": float(nav[-1] ** (252 / days) - 1),
        "dd": float((nav / np.maximum.accumulate(nav) - 1).min()),
        "bnav": float(bnav[-1]),
        "bann": float(bnav[-1] ** (252 / days) - 1),
        "snv": float(snav[-1]),
        "days": len(nav),
    }


def main():
    ap = argparse.ArgumentParser(description="ReversalTilt 80/20")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-09-11")
    ap.add_argument("--warm", type=int, default=60)
    ap.add_argument("--core-n", type=int, default=40)
    ap.add_argument("--base-w", type=float, default=0.80)
    ap.add_argument("--sat-w", type=float, default=0.20)
    ap.add_argument("--topk", type=int, default=15)
    ap.add_argument("--rebalance", type=int, default=5)
    ap.add_argument("--oos-start", default="2026-06-01")
    args = ap.parse_args()

    print("init / pools ...", flush=True)
    init_qlib()
    pools = load_pools()
    all_codes = sorted(set(sum(pools.values(), [])))
    print(f"load {len(all_codes)} ...", flush=True)
    data = load_ohlcv(all_codes, args.start, args.end)
    close = data["$close"]

    print(f"core {args.core_n} ...", flush=True)
    core = core_pools(pools, data["$volume"], n_each=args.core_n)
    keep = sorted(set(sum(core.values(), [])))
    close = close[[c for c in keep if c in close.columns]]

    start_i = args.warm
    end_i = len(close) - 2
    print(f"backtest {close.index[start_i]} .. {close.index[end_i]}", flush=True)
    res = backtest(
        close, core, start_i, end_i,
        base_w=args.base_w, sat_w=args.sat_w,
        topk=args.topk, rebalance=args.rebalance,
    )
    print(
        f"REVTILT {args.base_w:.0%}/{args.sat_w:.0%} top{args.topk}: "
        f"nav={res['nav'][-1]:.3f} ann={res['ann']:.2%} dd={res['dd']:.2%} sharpe={res['sharpe']:.2f}",
        flush=True,
    )
    print(f"BASE    core EW: nav={res['base_nav'][-1]:.3f} ann={res['bann']:.2%}", flush=True)
    o = oos_slice(res, args.oos_start)
    if o:
        print(
            f"OOS: hybrid nav={o['nav']:.3f} ann={o['ann']:.2%} | base nav={o['bnav']:.3f} "
            f"ann={o['bann']:.2%} | sat-only nav={o['snv']:.3f}",
            flush=True,
        )

    from stock_names import get_name_map, pretty

    if res["last_hold"]:
        nmap = get_name_map(res["last_hold"])
        print("latest satellite:", flush=True)
        for c in res["last_hold"]:
            print(f"  {pretty(c, nmap)}", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "date": res["dates"],
        "ret": res["ret"], "nav": res["nav"],
        "base_ret": res["base_ret"][: len(res["dates"])],
        "base_nav": res["base_nav"][: len(res["dates"])],
        "sat_ret": res["sat_ret"][: len(res["dates"])],
    }).to_csv(OUT_DIR / "nav_reversal_tilt.csv", index=False)
    print(f"saved {OUT_DIR}/nav_reversal_tilt.csv", flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
