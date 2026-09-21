# -*- coding: utf-8 -*-
"""
【自研策略】CandleComboAll
  复现《K线形态》PDF 中「后五日较好」的全部组合（含邪道），做事件研究 + 组合回测。

组合来源（PDF 总榜/邪道，出现次数与均值见文件头注释）
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


def _shift(df, n):
    return df.shift(n)


def detect_all(o, h, l, c):
    """完整形态库（近似经典定义，无未来函数）"""
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

    # 分型（左确认）
    top_frac = (h > ph) & (h > p2h)
    bot_frac = (l < pl) & (l < p2l)
    # 流星/射击之星：长上影小实体
    shooting = (up_shadow > 2 * body.abs()) & (body.abs() < 0.35 * rng) & (c <= o + body.abs() * 0.2)
    top_frac_star = top_frac | shooting  # 文中 顶分型(流星)

    bull_engulf = (c > o) & (pc < po) & (c >= po) & (o <= pc)
    bear_engulf = (c < o) & (pc > po) & (c <= po) & (o >= pc)
    yang_bao_yin = bull_engulf

    piercing = (pc < po) & (o < pc) & (c > (po + pc) / 2) & (c < po)
    dark_cloud = (pc > po) & (o > pc) & (c < (po + pc) / 2) & (c > po)
    qingpen = (pc > po) & (o > pc) & (c < po)

    morning_star = (
        (p2c < p2o) & (pl < np.minimum(p2c, p2o)) & (ph < np.maximum(p2c, p2o) * 1.02)
        & (c > o) & (c > (p2o + p2c) / 2)
    )
    evening_star = (
        (p2c > p2o) & (ph > np.maximum(p2c, p2o)) & (pl > np.minimum(p2c, p2o) * 0.98)
        & (c < o) & (c < (p2o + p2c) / 2)
    )

    harami_bull = (pc < po) & (c > o) & (c < po) & (o > pc)
    harami_bear = (pc > po) & (c < o) & (c > po) & (o < pc)
    # 文中「孕线/空头母子」「孕线/多头母子」按方向合并
    yun_xian_bear = harami_bear | ((pc > po) & (body.abs() < 0.3 * (po - pc).abs()) & (c < o))
    yun_xian_bull = harami_bull | ((pc < po) & (body.abs() < 0.3 * (pc - po).abs()) & (c > o))

    red3 = (c > o) & (pc > po) & (p2c > p2o) & (c > pc) & (pc > p2c) & (o > po) & (o < pc)
    three_yin = (p2c > p2o) & (pc < po) & (c < o) & (pl >= p2o * 0.995) & (l >= p2o * 0.995)
    one_yang = (
        (c > o) & (pc < po) & (p2c < p2o)
        & (c > np.maximum.reduce([po, p2o, p3o.fillna(po)]))
        & (o < np.minimum.reduce([pc, p2c, p3c.fillna(pc)]))
    )
    flat_top = (np.abs(h - ph) / ph.clip(lower=0.01) < 0.004) & (h >= ph * 0.998)
    south3 = (
        (p2c < p2o) & ((pc - o.shift(1)).abs() < 0.4 * (p2c - p2o).abs())
        & (c > o) & (c > (p2o + p2c) / 2)
    )
    # 多方炮：阳-阴-阳，中间阴不破首阳低点，末阳收过首阳高点（近似）
    duo_fang_pao = (
        (c > o) & (pc < po) & (p2c > p2o)
        & (pl >= p2l * 0.99) & (c > p2h)
    )
    # 空方炮
    kong_fang_pao = (
        (c < o) & (pc > po) & (p2c < p2o)
        & (ph <= p2h * 1.01) & (c < p2l)
    )
    # 双响炮：两阳夹一阴且实体接近
    shuang_xiang = (
        (c > o) & (pc < po) & (p2c > p2o)
        & (np.abs(p2c - p2o) > 0.5 * np.abs(c - o))
        & (c > pc)
    )
    # 大敌当前：长上影阳线在相对高位（近 20 日分位）
    ma20 = c.rolling(20).mean()
    high20 = h.rolling(20).max()
    da_di = (c > o) & (up_shadow > 1.5 * body.abs()) & (h >= high20 * 0.97)
    hammer = (dn_shadow > 2 * body.abs()) & (body.abs() < 0.35 * rng)

    return {
        "ret1": ret1, "close": c, "ma20": ma20,
        "gap_up": gap_up, "gap_dn": gap_dn,
        "top_frac": top_frac.fillna(False),
        "bot_frac": bot_frac.fillna(False),
        "top_frac_star": top_frac_star.fillna(False),
        "shooting": shooting.fillna(False),
        "bull_engulf": bull_engulf.fillna(False),
        "bear_engulf": bear_engulf.fillna(False),
        "yang_bao_yin": yang_bao_yin.fillna(False),
        "piercing": piercing.fillna(False),
        "dark_cloud": dark_cloud.fillna(False),
        "qingpen": qingpen.fillna(False),
        "morning_star": morning_star.fillna(False),
        "evening_star": evening_star.fillna(False),
        "yun_xian_bear": yun_xian_bear.fillna(False),
        "yun_xian_bull": yun_xian_bull.fillna(False),
        "red3": red3.fillna(False),
        "three_yin": three_yin.fillna(False),
        "one_yang": one_yang.fillna(False),
        "flat_top": flat_top.fillna(False),
        "south3": south3.fillna(False),
        "duo_fang_pao": duo_fang_pao.fillna(False),
        "kong_fang_pao": kong_fang_pao.fillna(False),
        "shuang_xiang": shuang_xiang.fillna(False),
        "da_di": da_di.fillna(False),
        "hammer": hammer.fillna(False),
    }


def combo_mask(pat, a, b, lag=1):
    """A 在 lag 日前，B 在今日"""
    return pat[a].shift(lag).fillna(False) & pat[b].fillna(False)


def combo_mask3(pat, a, b, c):
    return pat[a].shift(2).fillna(False) & pat[b].shift(1).fillna(False) & pat[c].fillna(False)


# PDF「后五日较好」组合（2 形态，lag=1 表示前一日→今日）
GOOD_2 = [
    ("南三→阳包阴", "south3", "yang_bao_yin", 1, 122, 3.89),
    ("倾盆大雨→孕线", "qingpen", "yun_xian_bear", 1, 172, 3.79),
    ("刺透→空头吞噬", "piercing", "bear_engulf", 1, 155, 3.73),
    ("孕线多头→倾盆大雨", "yun_xian_bull", "qingpen", 1, 127, 3.68),
    ("底分型→南三", "bot_frac", "south3", 1, 258, 3.56),
    ("三阴不破阳→一阳吞三阴", "three_yin", "one_yang", 1, 100, 3.54),
    ("三阴不破阳→南三", "three_yin", "south3", 1, 108, 3.49),
    ("黄昏星→一阳吞三阴", "evening_star", "one_yang", 1, 106, 3.35),
    # 邪道高均值（次数少）
    ("南三→倾盆大雨", "south3", "qingpen", 1, 26, 22.56),
    ("流星→多方炮", "top_frac_star", "duo_fang_pao", 1, 12, 16.16),
    ("大敌当前→跳空高开", "da_di", "gap_up", 1, 16, 9.36),
    ("大敌当前→倾盆大雨", "da_di", "qingpen", 1, 21, 12.83),
    ("阳包阴→跳空高开", "yang_bao_yin", "gap_up", 1, 11, 9.39),
    ("红三兵→流星", "red3", "top_frac_star", 1, 15, 12.21),
]

# 3 形态组合
GOOD_3 = [
    ("倾盆大雨→孕线→跳空低开", "qingpen", "yun_xian_bear", "gap_dn", 16, 9.59),
    ("阳包阴→跳空高开→流星", "yang_bao_yin", "gap_up", "shooting", 11, 9.39),
    ("底分型→倾盆大雨→孕线", "bot_frac", "qingpen", "yun_xian_bear", 14, 8.55),
    ("孕线多头→跳空高开→顶分型", "yun_xian_bull", "gap_up", "top_frac", 27, 7.35),
    ("跳空低开→顶分型→孕线", "gap_dn", "top_frac", "yun_xian_bear", 15, 7.19),
    ("倾盆大雨→跳空低开→孕线", "qingpen", "gap_dn", "yun_xian_bear", 14, 7.18),
    ("跳空低开→多方炮→平顶", "gap_dn", "duo_fang_pao", "flat_top", 16, 7.14),
    ("底分型→红三兵→平顶", "bot_frac", "red3", "flat_top", 14, 6.77),
    ("跳空低开→刺透→孕线", "gap_dn", "piercing", "yun_xian_bear", 17, 6.71),
    ("跳空高开→孕线多头→启明星", "gap_up", "yun_xian_bull", "morning_star", 13, 6.32),
]

BAD_2 = [
    ("跳空高→跳空低→顶分型", "gap_up", "gap_dn", 1, -1.40),
    ("空头吞噬→平顶→黄昏星", "bear_engulf", "flat_top", 1, -1.36),
    ("双高开→跳空低开", "gap_up", "gap_dn", 2, -1.15),  # 近似：两日前高开今日低开
    ("底分型→跳空低开", "bot_frac", "gap_dn", 1, -1.13),
    ("跳空低开→底分型", "gap_dn", "bot_frac", 1, -0.93),
    ("双高开→顶分型", "gap_up", "top_frac", 2, -0.87),
]


def build_combo_signals(pat):
    """返回 {name: mask} 与并集信号"""
    masks = {}
    for name, a, b, lag, n_pdf, mean_pdf in GOOD_2:
        masks[name] = combo_mask(pat, a, b, lag)
    for name, a, b, c3, n_pdf, mean_pdf in GOOD_3:
        masks[name] = combo_mask3(pat, a, b, c3)
    for name, a, b, lag, mean_pdf in BAD_2:
        if lag == 2:
            masks["BAD_" + name] = pat[a].shift(2).fillna(False) & pat[b].fillna(False)
        else:
            masks["BAD_" + name] = combo_mask(pat, a, b, lag)

    bad_any = None
    for k, v in masks.items():
        if k.startswith("BAD_"):
            bad_any = v if bad_any is None else (bad_any | v)
    good_any = None
    for k, v in masks.items():
        if not k.startswith("BAD_"):
            good_any = v if good_any is None else (good_any | v)
    if bad_any is None:
        bad_any = good_any & False
    if good_any is None:
        good_any = bad_any & False
    signal = good_any & ~bad_any
    # 优先用 PDF 均值加权打分
    score = None
    for name, a, b, lag, n_pdf, mean_pdf in GOOD_2:
        w = mean_pdf / 10.0
        add = masks[name].astype(float) * w
        score = add if score is None else score + add
    for name, a, b, c3, n_pdf, mean_pdf in GOOD_3:
        w = mean_pdf / 10.0
        score = score + masks[name].astype(float) * w
    score = score.where(signal)
    return masks, signal, score, bad_any


def event_study(pat, masks, start_i, end_i, horizons=(1, 3, 5)):
    fwd = {k: pat["close"].pct_change(k).shift(-k) for k in horizons}
    rows = []
    for name, m in masks.items():
        if name.startswith("BAD_"):
            continue
        sl = m.iloc[start_i:end_i]
        n = int(sl.sum().sum())
        rec = {"combo": name, "hits": n}
        for k in horizons:
            r = fwd[k].where(m).iloc[start_i:end_i]
            rec[f"mean_{k}d"] = float(r.stack().mean()) if r.stack().shape[0] else float("nan")
        rows.append(rec)
    df = pd.DataFrame(rows).sort_values("mean_5d", ascending=False)
    return df


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
        "n_sig_days": int(signal.iloc[start_i:end_i].any(axis=1).sum()),
        "dates": [str(dates[j])[:10] for j in range(start_i + 1, start_i + 1 + len(port))],
        "ret": port, "bench_ret": list(bench),
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
    pat = detect_all(data["$open"], data["$high"], data["$low"], data["$close"])
    masks, signal, score, bad_any = build_combo_signals(pat)

    start_i = args.warm
    end_i = len(pat["close"]) - 2

    print("\n== 事件研究：全部「较好」组合（核心池 2021-2026）==", flush=True)
    ev = event_study(pat, masks, start_i, end_i)
    pd.set_option("display.width", 120)
    pd.set_option("display.max_rows", 40)
    print(ev.to_string(index=False), flush=True)

    print("\n== 组合并集 TopK 回测 ==", flush=True)
    res = backtest(pat, signal, score, start_i, end_i, topk=args.topk, rebalance=args.rebalance)
    print(
        f"COMBO-ALL: signal_days={res['n_sig_days']} avg_hold={res['avg_hold']:.1f} "
        f"nav={res['nav'][-1]:.3f} ann={res['ann']:.2%} dd={res['dd']:.2%} sharpe={res['sharpe']:.2f} win={res['win']:.1%}",
        flush=True,
    )
    print(f"BENCH EW:  nav={res['bnav'][-1]:.3f} ann={res['bann']:.2%}", flush=True)
    o = oos(res, args.oos_start)
    if o:
        print(
            f"OOS({args.oos_start}→): combo nav={o['nav']:.3f} ann={o['ann']:.2%} dd={o['dd']:.2%} | "
            f"bench nav={o['bnav'][-1] if False else o['bnav']:.3f} ann={o['bann']:.2%}",
            flush=True,
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ev.to_csv(OUT_DIR / "candle_combo_event_study.csv", index=False)
    pd.DataFrame({
        "date": res["dates"], "ret": res["ret"], "nav": res["nav"],
        "bench_ret": res["bench_ret"][: len(res["dates"])],
        "bench_nav": res["bnav"][: len(res["dates"])],
    }).to_csv(OUT_DIR / "nav_candle_combo_all.csv", index=False)
    print(f"saved {OUT_DIR}/candle_combo_event_study.csv & nav_candle_combo_all.csv", flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
