# -*- coding: utf-8 -*-
"""
CandleCombo 2026 · 全市场非ST（含 OOS 约束）
  股票池：主板（600/601/603/605）+ 深主板（000/001/002/003）+ 创业板（300/301）
  剔除：科创 688 / 北交 / ST / 退
  组合筛选：仅在 --fit-end 之前的事件研究里挑 mean_5d>0 的组合
  报告：仅在 --oos-start 之后计算净值，日志标明窗口
  输出：data/experiments/<ts>_candle_maingem/
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

PROVIDER = str(ROOT / "data" / "qlib_cn")
OUT_DIR = ROOT / "data" / "strategies"
FEAT = ROOT / "data" / "qlib_cn" / "features"


def init_qlib():
    import os

    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    import qlib
    from qlib.constant import REG_CN

    qlib.init(provider_uri=PROVIDER, region=REG_CN, joblib_backend="threading")


def list_main_gem(include_gem: bool = True) -> list[str]:
    """沪主板 60x + 深主板 00x + 创业板 30x；不含科创 688 / 北交"""
    names = []
    if not FEAT.exists():
        return names
    for p in sorted(FEAT.iterdir()):
        n = p.name.upper()
        num = n[2:] if len(n) >= 8 else n
        if n.startswith("SH") and num.startswith(("600", "601", "603", "605")):
            names.append(n)
        elif n.startswith("SZ") and num.startswith(("000", "001", "002", "003")):
            names.append(n)
        elif include_gem and n.startswith("SZ") and num.startswith(("300", "301")):
            names.append(n)
    return names


def drop_st(names: list[str]) -> list[str]:
    nm = {}
    cache = ROOT / "data" / "cache" / "stock_names.json"
    if cache.exists():
        try:
            nm = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            nm = {}
    out = []
    dropped = []
    for c in names:
        c6 = c[2:]
        name = nm.get(c6) or nm.get(c) or ""
        up = name.upper()
        if name.startswith("*") or "ST" in up or "退" in name or "PT" in up:
            dropped.append((c, name))
            continue
        out.append(c)
    if dropped:
        print(f"  drop ST/退 {len(dropped)}  e.g. {dropped[:3]}", flush=True)
    return out


def load_ohlc(codes, start, end):
    from qlib.data import D

    fields = ["$open", "$high", "$low", "$close", "$volume"]
    batches = [codes[i : i + 100] for i in range(0, len(codes), 100)]
    frames = []
    for i, b in enumerate(batches):
        print(f"  data {i+1}/{len(batches)} n={len(b)}", flush=True)
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


def detect_all(o, h, l, c):
    ret1 = c.pct_change()
    po, ph, pl, pc = o.shift(1), h.shift(1), l.shift(1), c.shift(1)
    p2o, p2h, p2l, p2c = o.shift(2), h.shift(2), l.shift(2), c.shift(2)
    p3o, p3c = o.shift(3), c.shift(3)
    body = c - o
    up_shadow = h - np.maximum(o, c)
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
    yun_xian_bear, yun_xian_bull = harami_bear, harami_bull
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
        "qingpen": qingpen, "morning_star": morning_star, "evening_star": evening_star,
        "yun_xian_bear": yun_xian_bear, "yun_xian_bull": yun_xian_bull,
        "red3": red3, "three_yin": three_yin, "one_yang": one_yang,
        "flat_top": flat_top, "south3": south3, "duo_fang_pao": duo_fang_pao, "da_di": da_di,
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


def portfolio_backtest(pat, masks, keep_names, start_i, end_i, topk, rebalance=3, cost_bps=15.0,
                       use_gate=True):
    c = pat["close"]
    ret1 = pat["ret1"]
    dates = c.index
    sig = None
    score = None
    for name in keep_names:
        sig = masks[name] if sig is None else (sig | masks[name])
        add = masks[name].astype(float)
        score = add if score is None else score + add
    if sig is None:
        sig = c * False
        score = c * 0.0
    score = score.where(sig)

    # 广度门控：近 5 日平均红盘率
    red = (ret1 > 0).sum(axis=1) / ret1.notna().sum(axis=1).clip(lower=1)
    red_ma = red.rolling(5, min_periods=1).mean()

    def gate(i, has_signal):
        """仅在有信号的交易日按广度打折；无信号日不动仓位（持仓延续）。"""
        if not use_gate or not has_signal:
            return 1.0
        r = red_ma.iloc[i]
        if r != r:
            return 1.0
        if r < 0.35:
            return 0.30
        if r < 0.45:
            return 0.60
        return 1.0

    port, nh, gh = [], [], []
    hold = []
    cur_g = 1.0
    for i in range(start_i, end_i):
        row = score.iloc[i].dropna()
        has_sig = len(row) > 0
        if (i - start_i) % rebalance == 0 or not hold:
            hold = list(row.sort_values(ascending=False).head(topk).index) if has_sig else []
            nh.append(len(hold))
            # 调仓/开仓日：用当日信号+广度定仓位权重，持有至下次调仓
            cur_g = gate(i, has_sig)
        gh.append(cur_g)
        if i + 1 >= len(c):
            port.append(0.0)
            continue
        if not hold:
            port.append(0.0)
            continue
        r = (c.iloc[i + 1][hold] / c.iloc[i][hold] - 1).mean()
        if (i - start_i) % rebalance == 0:
            r -= cost_bps / 10000.0
        port.append(float(r * cur_g) if r == r else 0.0)

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
        "avg_gate": float(np.mean(gh)) if gh else 0,
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
    ap.add_argument("--warm-start", default="2025-11-01")
    ap.add_argument("--include-gem", type=int, default=1, help="1=含创业板 0=仅主板")
    ap.add_argument("--topk", type=int, default=30)
    ap.add_argument("--rebalance", type=int, default=3)
    ap.add_argument("--min-hits", type=int, default=80)
    ap.add_argument("--split", default="2026-06-01")
    ap.add_argument("--gate", type=int, default=1, help="1=广度门控 0=满仓")
    ap.add_argument("--fit-end", default="2026-05-31", help="组合筛选期最后一天（只能用在此之前的数据挑组合）")
    ap.add_argument("--oos-start", default="", help="样本外起点，默认取 --split")
    ap.add_argument("--strict", type=int, default=1, help="1=越界即报错")
    args = ap.parse_args()

    from constraints import TimeLock, assert_oos, experiment_dir, record_experiment

    oos_start = args.oos_start or args.split
    assert_oos(args.fit_end, oos_start, strict=bool(args.strict))

    print("init ...", flush=True)
    init_qlib()

    codes = list_main_gem(include_gem=bool(args.include_gem))
    codes = drop_st(codes)
    print(f"universe main+gem nonST: {len(codes)}", flush=True)

    print("load ohlcv ...", flush=True)
    data = load_ohlc(codes, args.warm_start, args.end)
    n_stocks = data["$close"].shape[1]
    print(f"loaded stocks: {n_stocks}", flush=True)

    print("patterns ...", flush=True)
    pat = detect_all(data["$open"], data["$high"], data["$low"], data["$close"])
    masks = build_masks(pat)

    dates = [str(x)[:10] for x in pat["close"].index]
    data_end = dates[-1]
    lock = TimeLock(base=args.fit_end, data_end=data_end, strict=bool(args.strict))
    print(f"[constraint] TimeLock base={lock.base} data_end={data_end} strict={lock.strict}", flush=True)

    start_i = next((i for i, d in enumerate(dates) if d >= args.start), 30)
    fit_end_i = next((i for i, d in enumerate(dates) if d >= args.fit_end), len(dates) - 1)
    oos_i = next((i for i, d in enumerate(dates) if d >= oos_start), None)
    if oos_i is None:
        print(f"[error] 样本外起点 {oos_start} 晚于数据末尾 {dates[-1]}，无法报告 OOS", flush=True)
        return
    end_i = len(dates) - 2
    # 事件研究的标签是 T→T+5 收益：最后可用行 = fit_end 前 6 个交易日，
    # 保证标签的**出场价**（第 5 个交易日之后）仍落在拟合期内，不触及样本外。
    ev_end_i = max(start_i + 1, fit_end_i - 6)
    lock.check(pat["close"].iloc[: ev_end_i + 1])
    print(
        f"[constraint] fit窗口 {dates[start_i]}..{dates[ev_end_i]}（标签不越界）"
        f" | OOS {dates[oos_i]}..{dates[end_i]}",
        flush=True,
    )

    print("\n== 事件研究（仅拟合期，禁止看样本外）==", flush=True)
    ev = event_study_range(pat, masks, start_i, ev_end_i)
    print(ev.to_string(index=False), flush=True)

    keep = list(ev[(ev["mean_5d"] > 0) & (ev["hits"] >= args.min_hits)]["combo"])
    print(f"\n拟合期保留 {len(keep)} 个组合 (hits>={args.min_hits} & mean_5d>0):", flush=True)
    print("  " + "、".join(keep) if keep else "  （无）", flush=True)
    if not keep:
        print("无合格组合，退出", flush=True)
        return

    print(f"\n== 组合 TopK={args.topk}（样本外报告）==", flush=True)
    res = portfolio_backtest(
        pat, masks, keep, oos_i, end_i, topk=args.topk, rebalance=args.rebalance,
        use_gate=bool(args.gate),
    )
    print(
        f"OOS gate={args.gate}: avg_hold={res['avg_hold']:.1f} avg_gate={res['avg_gate']:.2f} "
        f"nav={res['nav'][-1]:.3f} ann={res['ann']:.2%} dd={res['dd']:.2%} "
        f"sharpe={res['sharpe']:.2f} win={res['win']:.1%}",
        flush=True,
    )
    print(f"BENCH(OOS): nav={res['bnav'][-1]:.3f} ann={res['bann']:.2%}", flush=True)

    out_dir = experiment_dir("candle_maingem")
    record_experiment(
        "candle_maingem",
        {
            "fit_end": args.fit_end,
            "oos_start": oos_start,
            "topk": args.topk,
            "rebalance": args.rebalance,
            "min_hits": args.min_hits,
            "gate": bool(args.gate),
            "keep_combos": keep,
            "universe_n": int(n_stocks),
            "data_end": data_end,
        },
        out_dir=out_dir,
    )
    pd.DataFrame({
        "date": res["dates"], "ret": res["ret"], "nav": res["nav"],
        "bench_ret": res["bench_ret"][: len(res["dates"])],
        "bench_nav": res["bnav"][: len(res["dates"])],
    }).to_csv(out_dir / "nav_oos.csv", index=False)
    ev.to_csv(out_dir / "event_study_fit.csv", index=False)
    print(f"saved {out_dir}", flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
