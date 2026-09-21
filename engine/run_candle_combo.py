# -*- coding: utf-8 -*-
"""
【自研策略】CandleCombo
  参考《分享一些关于k线形态有趣的东西》中的高频组合

  买入（出现次数较多、后五日均涨幅靠前的 2 形态组合，T 日收盘确认）：
    刺透线 → 空头吞噬
    南方三星 → 阳包阴/多头吞噬
    底分型 → 南方三星
    三阴不破阳 → 一阳吞三阴
    倾盆大雨 → 孕线/空头母子

  规避（后五日最差表）：
    跳空低开 → 底分型 → 启明星
    黄昏星 / 乌云盖顶 / 高位平顶 且近端跳空低开

  组合：核心池内命中买入组合取 Top（按近 20 日趋势过滤），周频
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


def load_ohlc(codes, start, end):
    from qlib.data import D

    fields = ["$open", "$high", "$low", "$close", "$volume"]
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


def detect_patterns(o, h, l, c):
    """逐日形态布尔表（index=date, columns=code）"""
    ret1 = c.pct_change()
    prev_o, prev_h, prev_l, prev_c = o.shift(1), h.shift(1), l.shift(1), c.shift(1)
    p2_o, p2_h, p2_l, p2_c = o.shift(2), h.shift(2), l.shift(2), c.shift(2)

    body = c - o
    rng = (h - l).replace(0, np.nan)
    mid = (h + l) / 2

    # 跳空
    gap_up = o > prev_c * 1.002
    gap_dn = o < prev_c * 0.998

    # 顶/底分型（T 为顶/底，需看 T-1 与 T+1 → 这里用 T,T-1,T-2 的「左确认」近似：T 高于前后）
    # 无未来：用 T 高于 T-1 与 T-2 的高点作为「局部高点」
    top_frac = (h > prev_h) & (h > p2_h)
    bot_frac = (l < prev_l) & (l < p2_l)

    # 吞噬
    bull_engulf = (c > o) & (prev_c < prev_o) & (c >= prev_o) & (o <= prev_c)
    bear_engulf = (c < o) & (prev_c > prev_o) & (c <= prev_o) & (o >= prev_c)

    # 阳包阴 = 多头吞噬（同 bull_engulf）
    yang_bao_yin = bull_engulf

    # 刺透线：前阴，今低开后收过前日实体一半以上且收在昨开下方未完全吞没
    piercing = (
        (prev_c < prev_o)
        & (o < prev_c)
        & (c > (prev_o + prev_c) / 2)
        & (c < prev_o)
    )

    # 乌云盖顶 / 倾盆大雨（近似：前阳，今高开收破前日实体一半以下）
    dark_cloud = (
        (prev_c > prev_o)
        & (o > prev_c)
        & (c < (prev_o + prev_c) / 2)
        & (c > prev_o)
    )
    qingpen = (
        (prev_c > prev_o)
        & (o > prev_c)
        & (c < prev_o)  # 收破昨开更强
    )

    # 启明星 / 黄昏星（三日）
    morning_star = (
        (p2_c < p2_o)
        & (prev_l < np.minimum(p2_c, p2_o))
        & (prev_h < np.maximum(p2_c, p2_o) * 1.02)  # 中间实体小
        & (c > o)
        & (c > (p2_o + p2_c) / 2)
    )
    evening_star = (
        (p2_c > p2_o)
        & (prev_h > np.maximum(p2_c, p2_o))
        & (prev_l > np.minimum(p2_c, p2_o) * 0.98)
        & (c < o)
        & (c < (p2_o + p2_c) / 2)
    )

    # 孕线 / 母子
    harami_bull = (prev_c < prev_o) & (c > o) & (c < prev_o) & (o > prev_c)
    harami_bear = (prev_c > prev_o) & (c < o) & (c > prev_o) & (o < prev_c)

    # 流星 / 射击之星：上影长、实体小、收近低端
    shooting = ((h - np.maximum(o, c)) > 2 * np.abs(body)) & (np.abs(body) < 0.3 * rng)
    # 上吊线：下影长、实体小、在高位（用 20 日分位近似可选，先用当日结构）
    hammer = ((np.minimum(o, c) - l) > 2 * np.abs(body)) & (np.abs(body) < 0.3 * rng)

    # 红三兵：三日连阳且每日收在前日实体上部
    red3 = (
        (c > o) & (prev_c > prev_o) & (p2_c > p2_o)
        & (c > prev_c) & (prev_c > p2_c)
        & (o > prev_o) & (o < prev_c)
    )

    # 三阴不破阳：前阳后三阴均未破前阳开盘
    three_yin = (
        (p2_c > p2_o)
        & (prev_c < prev_o) & (c.shift(2) > c.shift(3))  # placeholder simplified below
    )
    # 简化：T-2 大阳，T-1 与 T 为阴且不破 T-2 开盘
    three_yin = (
        (p2_c > p2_o * 1.0)
        & (prev_c < prev_o)
        & (c < o)
        & (prev_l >= p2_o * 0.995)
        & (l >= p2_o * 0.995)
    )

    # 一阳吞三阴：今大阳吞没前三日
    one_yang = (
        (c > o)
        & (prev_c < prev_o) & (p2_c < p2_o)
        & (c > np.maximum.reduce([prev_o, p2_o, o.shift(3)]))
        & (o < np.minimum.reduce([prev_c, p2_c, c.shift(3)]))
    )

    # 高位平顶：两日高点接近
    flat_top = (np.abs(h - prev_h) / prev_h < 0.003) & (h >= prev_h * 0.999)

    # 南方三星（经典三日看涨）：大阴 + 缩量小星 + 阳收回
    south3 = (
        (p2_c < p2_o)
        & (np.abs(prev_c - prev_o) < 0.35 * np.abs(p2_c - p2_o))
        & (c > o)
        & (c > (p2_o + p2_c) / 2)
    )

    # 锤线/十字等略
    return {
        "ret1": ret1,
        "gap_up": gap_up.fillna(False),
        "gap_dn": gap_dn.fillna(False),
        "top_frac": top_frac.fillna(False),
        "bot_frac": bot_frac.fillna(False),
        "bull_engulf": bull_engulf.fillna(False),
        "bear_engulf": bear_engulf.fillna(False),
        "yang_bao_yin": yang_bao_yin.fillna(False),
        "piercing": piercing.fillna(False),
        "dark_cloud": dark_cloud.fillna(False),
        "qingpen": qingpen.fillna(False),
        "morning_star": morning_star.fillna(False),
        "evening_star": evening_star.fillna(False),
        "harami_bull": harami_bull.fillna(False),
        "harami_bear": harami_bear.fillna(False),
        "shooting": shooting.fillna(False),
        "hammer": hammer.fillna(False),
        "red3": red3.fillna(False),
        "three_yin": three_yin.fillna(False),
        "one_yang": one_yang.fillna(False),
        "flat_top": flat_top.fillna(False),
        "south3": south3.fillna(False),
        "close": c,
        "ma20": c.rolling(20).mean(),
    }


def combo_signals(pat):
    """两日组合：T-1 形态 A，T 形态 B → T 收盘信号"""
    # 好组合
    good = (
        (pat["piercing"].shift(1) & pat["bear_engulf"])  # 刺透→空头吞噬
        | (pat["south3"].shift(1) & pat["yang_bao_yin"])  # 南方三星→阳包阴
        | (pat["bot_frac"].shift(1) & pat["south3"])  # 底分型→南方三星
        | (pat["three_yin"].shift(1) & pat["one_yang"])  # 三阴不破阳→一阳吞三阴
        | (pat["qingpen"].shift(1) & pat["harami_bear"])  # 倾盆大雨→孕线
        | (pat["three_yin"].shift(1) & pat["bull_engulf"])  # 三阴不破阳→多头吞噬
    )
    # 坏组合（出现多且差）
    bad = (
        (pat["gap_dn"] & pat["bot_frac"].shift(1) & pat["morning_star"].shift(2))
        | (pat["evening_star"] & pat["gap_dn"].shift(1))
        | (pat["dark_cloud"] & pat["gap_dn"].shift(1))
        | (pat["gap_up"].shift(1) & pat["gap_up"].shift(2) & pat["gap_dn"])  # 双高开后低开
        | (pat["top_frac"] & pat["gap_up"].shift(1) & pat["gap_up"].shift(2))
    )
    # 趋势过滤：不在 MA20 下方太远
    above = pat["close"] >= pat["ma20"] * 0.97
    score = pat["ret1"].mul(-1)  # 阴线越深分越高（接组合后的低吸）
    signal = good & ~bad & above
    return signal.fillna(False), score.where(signal)


def backtest(pat, signal, score, start_i, end_i, topk=12, rebalance=5, cost_bps=15.0):
    c = pat["close"]
    dates = c.index
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
        "nav": nav, "bnav": bnav,
        "ann": float(nav[-1] ** (252 / days) - 1),
        "bann": float(bnav[-1] ** (252 / days) - 1),
        "dd": float((nav / np.maximum.accumulate(nav) - 1).min()),
        "sharpe": float(np.mean(port) / (np.std(port) + 1e-12) * np.sqrt(252)),
        "win": float(np.mean([1 if x > 0 else 0 for x in port])) if port else 0,
        "avg_hold": float(np.mean(nh)) if nh else 0,
        "days": len(nav),
        "dates": [str(dates[j])[:10] for j in range(start_i + 1, start_i + 1 + len(port))],
        "ret": port, "bench_ret": list(bench),
        "n_signals": int(signal.iloc[start_i:end_i].sum().sum()),
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
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-09-11")
    ap.add_argument("--warm", type=int, default=40)
    ap.add_argument("--topk", type=int, default=12)
    ap.add_argument("--rebalance", type=int, default=5)
    ap.add_argument("--oos-start", default="2026-06-01")
    args = ap.parse_args()

    print("init / pool ...", flush=True)
    init_qlib()
    from sector_universe import tech_pool, securities_pool, nonferrous_pool

    codes = sorted(set(tech_pool() + securities_pool() + nonferrous_pool()))
    print(f"load {len(codes)} ...", flush=True)
    data = load_ohlc(codes, args.start, args.end)
    # 取核心流动性
    vol = data["$volume"]
    core = []
    for pool in (tech_pool(), securities_pool(), nonferrous_pool()):
        members = [c for c in pool if c in vol.columns]
        avg = vol[members].tail(120).mean().dropna().sort_values(ascending=False)
        core.extend(list(avg.head(40).index))
    core = sorted(set(core))
    for k in data:
        data[k] = data[k][[c for c in core if c in data[k].columns]]
    print(f"core {len(core)}", flush=True)

    print("patterns ...", flush=True)
    pat = detect_patterns(data["$open"], data["$high"], data["$low"], data["$close"])
    signal, score = combo_signals(pat)

    start_i = args.warm
    end_i = len(pat["close"]) - 2
    print(f"backtest {pat['close'].index[start_i]} .. {pat['close'].index[end_i]}", flush=True)
    res = backtest(pat, signal, score, start_i, end_i, topk=args.topk, rebalance=args.rebalance)
    print(
        f"COMBO top{args.topk}: signals={res['n_signals']} avg_hold={res['avg_hold']:.1f} "
        f"nav={res['nav'][-1]:.3f} ann={res['ann']:.2%} dd={res['dd']:.2%} sharpe={res['sharpe']:.2f} win={res['win']:.1%}",
        flush=True,
    )
    print(f"BENCH EW: nav={res['bnav'][-1]:.3f} ann={res['bann']:.2%}", flush=True)
    o = oos(res, args.oos_start)
    if o:
        print(
            f"OOS({args.oos_start}→): combo nav={o['nav']:.3f} ann={o['ann']:.2%} dd={o['dd']:.2%} | "
            f"bench nav={o['bnav']:.3f} ann={o['bann']:.2%}",
            flush=True,
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "date": res["dates"], "ret": res["ret"], "nav": res["nav"],
        "bench_ret": res["bench_ret"][: len(res["dates"])],
        "bench_nav": res["bnav"][: len(res["dates"])],
    }).to_csv(OUT_DIR / "nav_candle_combo.csv", index=False)
    print(f"saved {OUT_DIR}/nav_candle_combo.csv", flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
