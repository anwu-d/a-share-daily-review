# -*- coding: utf-8 -*-
"""
CandleCombo 2026
  - 仅回测 2026
  - 保留事件研究中「后5日均涨幅 > 0」的组合
  - 更大股票池 + 更大 TopK
  - 结果拆成：2026 上半年 / 下半年
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
sys.path.insert(0, str(ROOT / "engine"))

PROVIDER = str(ROOT / "data" / "qlib_cn")
OUT_DIR = ROOT / "data" / "strategies"


def init_qlib():
    import os

    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    import qlib
    from qlib.constant import REG_CN

    qlib.init(provider_uri=PROVIDER, region=REG_CN, joblib_backend="threading")


def load_universe(start, end, mode="all_pool"):
    from sector_universe import tech_pool, securities_pool, nonferrous_pool
    from qlib.data import D

    t, s, n = tech_pool(), securities_pool(), nonferrous_pool()
    all_codes = sorted(set(t + s + n))
    print(f"pool sizes tech={len(t)} sec={len(s)} nonfer={len(n)} all={len(all_codes)}", flush=True)

    fields = ["$open", "$high", "$low", "$close", "$volume"]
    batches = [all_codes[i : i + 80] for i in range(0, len(all_codes), 80)]
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

    if mode == "core":
        vol = out["$volume"]
        core = []
        for pool in (t, s, n):
            members = [c for c in pool if c in vol.columns]
            avg = vol[members].tail(60).mean().dropna().sort_values(ascending=False)
            core.extend(list(avg.head(60).index))
        core = sorted(set(core))
        for k in out:
            out[k] = out[k][[c for c in core if c in out[k].columns]]
        print(f"core-60 selected: {len(core)}", flush=True)
    else:
        # 全池（已滤 ST）
        print(f"using full sector pool: {out['$close'].shape[1]} stocks", flush=True)
    return out


def detect_all(o, h, l, c):
    ret1 = c.pct_change()
    po, ph, pl, pc = o.shift(1), h.shift(1), l.shift(1), c.shift(1)
    p2o, p2h, p2l, p2c = o.shift(2), h.shift(2), l.shift(2), c.shift(2)
    p3o, p3c = o.shift(3), c.shift(3)
    body = c - o
    up_shadow = h - np.maximum(o, c)
    dn_shadow = np.minimum(o, c) - l
    rng = (h - l).replace(0, np.nan)

    gap_up = (o > pc * 1.002).fillna(False)
    gap_dn = (o < pc * 0.998).fillna(False)
    top_frac = ((h > ph) & (h > p2h)).fillna(False)
    bot_frac = ((l < pl) & (l < p2l)).fillna(False)
    shooting = ((up_shadow > 2 * body.abs()) & (body.abs() < 0.35 * rng)).fillna(False)
    top_frac_star = top_frac | shooting

    bull_engulf = ((c > o) & (pc < po) & (c >= po) & (o <= pc)).fillna(False)
    bear_engulf = ((c < o) & (pc > po) & (c <= po) & (o >= pc)).fillna(False)
    yang_bao_yin = bull_engulf
    piercing = ((pc < po) & (o < pc) & (c > (po + pc) / 2) & (c < po)).fillna(False)
    qingpen = ((pc > po) & (o > pc) & (c < po)).fillna(False)
    dark_cloud = ((pc > po) & (o > pc) & (c < (po + pc) / 2) & (c > po)).fillna(False)
    morning_star = (
        (p2c < p2o) & (pl < np.minimum(p2c, p2o)) & (ph < np.maximum(p2c, p2o) * 1.02)
        & (c > o) & (c > (p2o + p2c) / 2)
    ).fillna(False)
    evening_star = (
        (p2c > p2o) & (ph > np.maximum(p2c, p2o)) & (pl > np.minimum(p2c, p2o) * 0.98)
        & (c < o) & (c < (p2o + p2c) / 2)
    ).fillna(False)
    harami_bull = ((pc < po) & (c > o) & (c < po) & (o > pc)).fillna(False)
    harami_bear = ((pc > po) & (c < o) & (c > po) & (o < pc)).fillna(False)
    yun_xian_bear = harami_bear
    yun_xian_bull = harami_bull
    red3 = (
        (c > o) & (pc > po) & (p2c > p2o) & (c > pc) & (pc > p2c) & (o > po) & (o < pc)
    ).fillna(False)
    three_yin = (
        (p2c > p2o) & (pc < po) & (c < o) & (pl >= p2o * 0.995) & (l >= p2o * 0.995)
    ).fillna(False)
    one_yang = (
        (c > o) & (pc < po) & (p2c < p2o)
        & (c > np.maximum.reduce([po, p2o, p3o.fillna(po)]))
        & (o < np.minimum.reduce([pc, p2c, p3c.fillna(pc)]))
    ).fillna(False)
    flat_top = ((np.abs(h - ph) / ph.clip(lower=0.01) < 0.004) & (h >= ph * 0.998)).fillna(False)
    south3 = (
        (p2c < p2o) & ((pc - o.shift(1)).abs() < 0.4 * (p2c - p2o).abs())
        & (c > o) & (c > (p2o + p2c) / 2)
    ).fillna(False)
    duo_fang_pao = (
        (c > o) & (pc < po) & (p2c > p2o) & (pl >= p2l * 0.99) & (c > p2h)
    ).fillna(False)
    ma20 = c.rolling(20).mean()
    high20 = h.rolling(20).max()
    da_di = ((c > o) & (up_shadow > 1.5 * body.abs()) & (h >= high20 * 0.97)).fillna(False)

    return {
        "ret1": ret1, "close": c, "ma20": ma20,
        "gap_up": gap_up, "gap_dn": gap_dn,
        "top_frac": top_frac, "bot_frac": bot_frac,
        "top_frac_star": top_frac_star, "shooting": shooting,
        "bull_engulf": bull_engulf, "bear_engulf": bear_engulf,
        "yang_bao_yin": yang_bao_yin, "piercing": piercing,
        "qingpen": qingpen, "dark_cloud": dark_cloud,
        "morning_star": morning_star, "evening_star": evening_star,
        "yun_xian_bear": yun_xian_bear, "yun_xian_bull": yun_xian_bull,
        "red3": red3, "three_yin": three_yin, "one_yang": one_yang,
        "flat_top": flat_top, "south3": south3,
        "duo_fang_pao": duo_fang_pao, "da_di": da_di,
    }


GOOD_2 = [
    ("南三→阳包阴", "south3", "yang_bao_yin", 1),
    ("倾盆大雨→孕线", "qingpen", "yun_xian_bear", 1),
    ("刺透→空头吞噬", "piercing", "bear_engulf", 1),
    ("孕线多头→倾盆大雨", "yun_xian_bull", "qingpen", 1),
    ("底分型→南三", "bot_frac", "south3", 1),
    ("三阴不破阳→一阳吞三阴", "three_yin", "one_yang", 1),
    ("三阴不破阳→南三", "three_yin", "south3", 1),
    ("黄昏星→一阳吞三阴", "evening_star", "one_yang", 1),
    ("南三→倾盆大雨", "south3", "qingpen", 1),
    ("流星→多方炮", "top_frac_star", "duo_fang_pao", 1),
    ("大敌当前→跳空高开", "da_di", "gap_up", 1),
    ("大敌当前→倾盆大雨", "da_di", "qingpen", 1),
    ("阳包阴→跳空高开", "yang_bao_yin", "gap_up", 1),
    ("红三兵→流星", "red3", "top_frac_star", 1),
    ("刺透→黄昏星", "piercing", "evening_star", 1),
    ("启明星→刺透", "morning_star", "piercing", 1),
]

GOOD_3 = [
    ("倾盆大雨→孕线→跳空低开", "qingpen", "yun_xian_bear", "gap_dn"),
    ("阳包阴→跳空高开→流星", "yang_bao_yin", "gap_up", "shooting"),
    ("底分型→倾盆大雨→孕线", "bot_frac", "qingpen", "yun_xian_bear"),
    ("孕线多头→跳空高开→顶分型", "yun_xian_bull", "gap_up", "top_frac"),
    ("跳空低开→顶分型→孕线", "gap_dn", "top_frac", "yun_xian_bear"),
    ("倾盆大雨→跳空低开→孕线", "qingpen", "gap_dn", "yun_xian_bear"),
    ("跳空低开→多方炮→平顶", "gap_dn", "duo_fang_pao", "flat_top"),
    ("底分型→红三兵→平顶", "bot_frac", "red3", "flat_top"),
    ("跳空低开→刺透→孕线", "gap_dn", "piercing", "yun_xian_bear"),
    ("跳空高开→孕线多头→启明星", "gap_up", "yun_xian_bull", "morning_star"),
    ("刺透→黄昏星→底分型", "piercing", "evening_star", "bot_frac"),
    ("启明星→刺透→黄昏星", "morning_star", "piercing", "evening_star"),
]

BAD_2 = [
    ("跳空高→跳空低", "gap_up", "gap_dn", 1),
    ("空头吞噬→平顶", "bear_engulf", "flat_top", 1),
    ("底分型→跳空低开", "bot_frac", "gap_dn", 1),
    ("跳空低开→底分型", "gap_dn", "bot_frac", 1),
    ("双高开→跳空低开", "gap_up", "gap_dn", 2),
]


def combo2(pat, a, b, lag=1):
    return pat[a].shift(lag).fillna(False) & pat[b].fillna(False)


def combo3(pat, a, b, c):
    return pat[a].shift(2).fillna(False) & pat[b].shift(1).fillna(False) & pat[c].fillna(False)


def build_masks(pat):
    masks = {}
    for name, a, b, lag in GOOD_2:
        masks[name] = combo2(pat, a, b, lag)
    for name, a, b, c in GOOD_3:
        masks[name] = combo3(pat, a, b, c)
    for name, a, b, lag in BAD_2:
        masks["BAD_" + name] = combo2(pat, a, b, lag)
    return masks


def event_study_range(pat, masks, i0, i1):
    c = pat["close"]
    f5 = c.pct_change(5).shift(-5)
    rows = []
    for name, m in masks.items():
        if name.startswith("BAD_"):
            continue
        hits = int(m.iloc[i0:i1].sum().sum())
        r5 = f5.where(m).iloc[i0:i1]
        mean5 = float(r5.stack().mean()) if r5.stack().shape[0] else float("nan")
        rows.append({"combo": name, "hits": hits, "mean_5d": mean5})
    return pd.DataFrame(rows).sort_values("mean_5d", ascending=False)


def filter_positive_combos(ev: pd.DataFrame, min_hits: int = 30) -> list[str]:
    ev2 = ev[(ev["mean_5d"] > 0) & (ev["hits"] >= min_hits)]
    return list(ev2["combo"])


def portfolio_backtest(pat, masks, keep_names, start_i, end_i, topk, rebalance=3, cost_bps=15.0):
    c = pat["close"]
    dates = c.index
    # 并集信号 + 命中数作简单打分
    sig = None
    for name in keep_names:
        sig = masks[name] if sig is None else (sig | masks[name])
    if sig is None:
        sig = c * False
    # 打分：组合命中个数
    score = None
    for name in keep_names:
        add = masks[name].astype(float)
        score = add if score is None else score + add
    score = score.where(sig)

    port, nh = [], []
    hold = []
    for i in range(start_i, end_i):
        if (i - start_i) % rebalance == 0 or not hold:
            row = score.iloc[i].dropna()
            hold = list(row.sort_values(ascending=False).head(topk).index) if not row.empty else []
            nh.append(len(hold))
        if i + 1 >= len(c):
            port.append(0.0)
            continue
        if not hold:
            port.append(0.0)
            continue
        r = (c.iloc[i + 1][hold] / c.iloc[i][hold] - 1).mean()
        if (i - start_i) % rebalance == 0:
            r -= cost_bps / 10000.0
        port.append(float(r) if r == r else 0.0)

    nav = np.cumprod(1 + np.array(port)) if port else np.array([1.0])
    bench = pat["ret1"].iloc[start_i + 1 : start_i + 1 + len(port)].mean(axis=1).fillna(0).values
    bnav = np.cumprod(1 + bench) if len(bench) else np.array([1.0])
    days = max(len(nav), 1)
    return {
        "nav": nav, "bnav": bnav, "ret": port, "bench_ret": list(bench),
        "ann": float(nav[-1] ** (252 / days) - 1) if days else 0,
        "bann": float(bnav[-1] ** (252 / days) - 1) if days else 0,
        "dd": float((nav / np.maximum.accumulate(nav) - 1).min()) if len(nav) else 0,
        "sharpe": float(np.mean(port) / (np.std(port) + 1e-12) * np.sqrt(252)) if port else 0,
        "win": float(np.mean([1 if x > 0 else 0 for x in port])) if port else 0,
        "avg_hold": float(np.mean(nh)) if nh else 0,
        "n_hold_days": len(nh),
        "dates": [str(dates[j])[:10] for j in range(start_i + 1, start_i + 1 + len(port))],
    }


def slice_stats(res, d0, d1):
    dates = res["dates"]
    idx = [i for i, d in enumerate(dates) if d0 <= d < d1]
    if not idx:
        return None
    r = np.array([res["ret"][i] for i in idx])
    b = np.array([res["bench_ret"][i] for i in idx]) if res["bench_ret"] else np.zeros_like(r)
    nav = np.cumprod(1 + r)
    bnav = np.cumprod(1 + b)
    days = max(len(nav), 1)
    return {
        "days": len(nav),
        "nav": float(nav[-1]),
        "ann": float(nav[-1] ** (252 / days) - 1),
        "dd": float((nav / np.maximum.accumulate(nav) - 1).min()),
        "bnav": float(bnav[-1]),
        "bann": float(bnav[-1] ** (252 / days) - 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-09-11")
    ap.add_argument("--warm", type=int, default=25, help="均线预热（用 2025 尾部更佳，见 --warm-start）")
    ap.add_argument("--warm-start", default="2025-11-01", help="为算 MA/分型多载一点前置数据")
    ap.add_argument("--pool", default="all", choices=["all", "core"], help="all=三行业全池 core=每板块60流动性")
    ap.add_argument("--topk", type=int, default=30)
    ap.add_argument("--rebalance", type=int, default=3)
    ap.add_argument("--min-hits", type=int, default=30)
    ap.add_argument("--split", default="2026-06-01")
    args = ap.parse_args()

    print("init ...", flush=True)
    init_qlib()
    data = load_universe(args.warm_start, args.end, mode=args.pool)
    print("patterns ...", flush=True)
    pat = detect_all(data["$open"], data["$high"], data["$low"], data["$close"])
    masks = build_masks(pat)

    # 回测区间：正式从 2026-01 起
    dates = [str(x)[:10] for x in pat["close"].index]
    # warm 到 start
    warm_i = next((i for i, d in enumerate(dates) if d >= args.warm_start), 0)
    start_i = next((i for i, d in enumerate(dates) if d >= args.start), args.warm)
    end_i = len(dates) - 2
    split_i = next((i for i, d in enumerate(dates) if d >= args.split), None)
    print(f"window {dates[start_i]} .. {dates[end_i]}  split={args.split} idx={split_i}", flush=True)

    print("\n== 事件研究 2026 全年（含 warm 段尾部）==", flush=True)
    ev_all = event_study_range(pat, masks, start_i, end_i)
    print(ev_all.to_string(index=False), flush=True)
    keep = filter_positive_combos(ev_all, min_hits=args.min_hits)
    print(f"\n保留正收益且命中>={args.min_hits} 的组合 {len(keep)} 个:", flush=True)
    print("  " + "、".join(keep), flush=True)

    print(f"\n== 组合回测 TopK={args.topk} 池={args.pool} ==", flush=True)
    res = portfolio_backtest(pat, masks, keep, start_i, end_i, topk=args.topk, rebalance=args.rebalance)
    print(
        f"FULL 2026: avg_hold={res['avg_hold']:.1f} nav={res['nav'][-1]:.3f} "
        f"ann={res['ann']:.2%} dd={res['dd']:.2%} sharpe={res['sharpe']:.2f} win={res['win']:.1%}",
        flush=True,
    )
    print(f"BENCH EW:  nav={res['bnav'][-1]:.3f} ann={res['bann']:.2%}", flush=True)

    h1 = slice_stats(res, args.start, args.split)
    h2 = slice_stats(res, args.split, args.end)
    for label, s in [("H1", h1), ("H2", h2)]:
        if s:
            print(
                f"{label} {s['days']}d: combo nav={s['nav']:.3f} ann={s['ann']:.2%} dd={s['dd']:.2%} | "
                f"bench nav={s['bnav']:.3f} ann={s['bann']:.2%}",
                flush=True,
            )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ev_all.to_csv(OUT_DIR / "candle_combo_2026_event.csv", index=False)
    pd.DataFrame({
        "date": res["dates"], "ret": res["ret"], "nav": res["nav"],
        "bench_ret": res["bench_ret"][: len(res["dates"])],
        "bench_nav": res["bnav"][: len(res["dates"])],
    }).to_csv(OUT_DIR / "nav_candle_2026.csv", index=False)
    Path(OUT_DIR / "candle_combo_2026_keep.txt").write_text("\n".join(keep), encoding="utf-8")
    print(f"saved {OUT_DIR}/candle_combo_2026_*", flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
