# -*- coding: utf-8 -*-
"""
【自研策略】SectorPulse · 板块脉冲

一句话：
  在「科技 / 证券 / 有色」内，选「近期涨停/大涨多的板块」里
  「动量强、波动相对低、换手不过热」的票，周频调仓；
  大盘退潮（红盘率低、涨停少）时降仓。

为什么这样设计：
  1. A股短线有板块聚集效应——单票动量不如「板块热 + 个票强」
  2. 高换手/超高波动容易追高，用低波+换手过滤
  3. 牛市/退潮差异大，用广度与涨停家数做仓位门控
  4. 周频降低摩擦（日频 TopK 换手成本吃掉超额）

信号（T 日收盘算，T+1 开盘附近按收盘近似成交）：
  score = 0.40 * rank(板块热度)
        + 0.35 * rank(个股20日动量)
        + 0.15 * rank(-20日波动)
        + 0.10 * rank(-换手相对历史)

  板块热度 = 过去 10 日该股所属板块的「涨停次数 + 涨幅>5% 次数」横截面
  仓位：市场状态 gate → 全仓 / 半仓 / 空仓
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
sys.path.insert(0, str(ROOT / "vendor" / "MyTT"))

PROVIDER = str(ROOT / "data" / "qlib_cn")
OUT_DIR = ROOT / "data" / "strategies"


def init_qlib():
    import os

    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    import qlib
    from qlib.constant import REG_CN

    qlib.init(provider_uri=PROVIDER, region=REG_CN, joblib_backend="threading")


def sector_of(code: str, tech, sec, nonferrous) -> str:
    if code in tech:
        return "tech"
    if code in sec:
        return "securities"
    if code in nonferrous:
        return "nonferrous"
    return "other"


def load_pool():
    from sector_universe import combined_pool, tech_pool, securities_pool, nonferrous_pool

    t, s, n = set(tech_pool()), set(securities_pool()), set(nonferrous_pool())
    codes = combined_pool()
    sec_map = {c: sector_of(c, t, s, n) for c in codes}
    return codes, sec_map


def load_ohlcv(codes, start, end):
    from qlib.data import D

    fields = ["$open", "$high", "$low", "$close", "$volume"]
    batches = [codes[i : i + 80] for i in range(0, len(codes), 80)]
    frames = []
    for i, b in enumerate(batches):
        print(f"  ohlcv {i+1}/{len(batches)}", flush=True)
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


def compute_scores(ohlc, sec_map, lookback_heat=10, lookback_mom=20):
    close, high, low, vol = ohlc["$close"], ohlc["$high"], ohlc["$low"], ohlc["$volume"]
    # 涨跌停近似
    cols = list(close.columns)
    lim = np.array([0.198 if c[2:4] in ("30", "68") or c.startswith("BJ") else 0.098 for c in cols])
    lim_w = pd.DataFrame(np.tile(lim, (len(close), 1)), index=close.index, columns=cols)
    ret1 = close.pct_change()
    is_zt = ret1 >= (lim_w - 0.002)
    is_big = ret1 >= 0.05

    # 板块热度：按 sector 聚合（过去 lookback_heat 日涨停+大涨次数）
    sectors = pd.Series({c: sec_map.get(c, "other") for c in cols})
    heat = {}
    for sec_name in sectors.unique():
        mask = sectors[sectors == sec_name].index
        if len(mask) == 0:
            continue
        h = (is_zt[mask].rolling(lookback_heat).sum() + is_big[mask].rolling(lookback_heat).sum()).mean(axis=1)
        heat[sec_name] = h
    heat_df = pd.DataFrame(heat)
    # 每只票映射其板块热度
    stock_heat = pd.DataFrame(
        np.column_stack([heat_df[sectors[c]].values for c in cols]),
        index=close.index,
        columns=cols,
    )

    mom20 = close.pct_change(lookback_mom)
    vol20 = ret1.rolling(20).std()
    turn = (vol.rolling(5).mean() / vol.rolling(20).mean()).replace([np.inf, -np.inf], np.nan)

    # 价量情绪代理：近 10 日（涨停+大涨）密度
    big = (ret1 >= 0.05).astype(float)
    emotion = (is_zt.astype(float).rolling(10).sum() + big.rolling(10).sum()) / 10.0
    emotion_d = emotion.diff(3)  # 3 日变化：上行/下行
    emo_rank = emotion.rank(axis=1, pct=True)  # 当日横截面分位

    # 情绪门控 v2（放宽）：只禁「极高」或「明显下行」
    #   允许买：分位 < 0.95 且 3 日情绪变化 >= -0.15
    #   不买：  分位 >= 0.95（过热） 或 emotion_d < -0.15（快速退潮）
    emo_ok = (emo_rank < 0.95) & (emotion_d >= -0.15)

    def rank_xs(df):
        return df.rank(axis=1, pct=True)

    # 打分只用结构因子；情绪只做可买/不可买
    score = (
        0.40 * rank_xs(stock_heat)
        + 0.40 * rank_xs(mom20)
        + 0.10 * rank_xs(-vol20)
        + 0.10 * rank_xs(-turn)
    )
    # 不可买 → 分数置 NaN，回测里 dropna 自动跳过
    score = score.where(emo_ok)

    meta = {
        "stock_heat": stock_heat,
        "emotion": emotion,
        "emotion_d": emotion_d,
        "emo_rank": emo_rank,
        "emo_ok": emo_ok,
        "mom20": mom20,
        "vol20": vol20,
        "turn": turn,
        "is_zt": is_zt,
        "ret1": ret1,
        "close": close,
        "score": score,
    }
    return meta


def market_gate(ret1, is_zt, i, warm=60):
    """
    宽松门控：极端退潮才减仓，避免牛市长期空仓。
    1.0 满仓 / 0.6 偏多 / 0.25 轻仓
    """
    if i < warm:
        return 1.0
    win = slice(i - 19, i + 1)
    red = (ret1.iloc[win] > 0).sum(axis=1).mean() / max(ret1.shape[1], 1)
    zt_n = is_zt.iloc[win].sum(axis=1).mean()
    if red < 0.28 and zt_n < 1.5:
        return 0.25
    if red < 0.38 and zt_n < 2.5:
        return 0.6
    return 1.0


def backtest(meta, start_i, end_i, topk=15, rebalance_every=5, cost_bps=18.0):
    close, score = meta["close"], meta["score"]
    ret1, is_zt = meta["ret1"], meta["is_zt"]
    dates = close.index
    n = len(dates)
    port = []
    gate_hist = []
    hold = []
    for i in range(start_i, end_i):
        g = market_gate(ret1, is_zt, i)
        gate_hist.append(g)
        # 调仓日
        if (i - start_i) % rebalance_every == 0 or not hold:
            row = score.iloc[i].dropna()
            if g <= 0.01 or row.empty:
                hold = []
            else:
                hold = list(row.sort_values(ascending=False).head(topk).index)
        if i + 1 >= n or not hold:
            port.append(0.0)
            continue
        r = (close.iloc[i + 1][hold] / close.iloc[i][hold] - 1).mean() * g
        # 换手成本只在调仓日近似
        if (i - start_i) % rebalance_every == 0:
            r -= cost_bps / 10000.0 * g
        port.append(float(r) if r == r else 0.0)

    nav = np.cumprod(1 + np.array(port)) if port else np.array([1.0])
    bench_r = ret1.iloc[start_i + 1 : start_i + 1 + len(port)].mean(axis=1).fillna(0).values
    bnav = np.cumprod(1 + bench_r) if len(bench_r) else np.array([1.0])
    days = max(len(nav), 1)
    ann = nav[-1] ** (252 / days) - 1
    bann = bnav[-1] ** (252 / days) - 1 if len(bnav) else 0
    dd = (nav / np.maximum.accumulate(nav) - 1).min()
    sharpe = float(np.mean(port) / (np.std(port) + 1e-12) * np.sqrt(252))
    win = float(np.mean([1 if x > 0 else 0 for x in port])) if port else 0
    return {
        "nav": nav,
        "bench_nav": bnav,
        "ann": float(ann),
        "bann": float(bann),
        "dd": float(dd),
        "sharpe": sharpe,
        "win": win,
        "days": len(nav),
        "avg_gate": float(np.mean(gate_hist)) if gate_hist else 0,
        "dates": [str(dates[j])[:10] for j in range(start_i + 1, start_i + 1 + len(port))],
        "ret": port,
        "bench_ret": list(bench_r) if len(bench_r) else [],
    }


def latest_holdings(meta, topk, day=None):
    from stock_names import get_name_map, pretty

    score = meta["score"]
    i = -1 if day is None else score.index.get_loc(day)
    row = score.iloc[i].dropna().sort_values(ascending=False).head(topk)
    nmap = get_name_map(list(row.index))
    heat = meta["stock_heat"].iloc[i]
    mom = meta["mom20"].iloc[i]
    return pd.DataFrame(
        {
            "code": row.index,
            "name": [pretty(c, nmap) for c in row.index],
            "score": row.values,
            "heat": [heat.get(c, np.nan) for c in row.index],
            "mom20": [mom.get(c, np.nan) for c in row.index],
        }
    )


def main():
    ap = argparse.ArgumentParser(description="SectorPulse 板块脉冲策略")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-09-11")
    ap.add_argument("--warm", type=int, default=80, help="预热交易日")
    ap.add_argument("--topk", type=int, default=15)
    ap.add_argument("--rebalance", type=int, default=5, help="每 N 交易日调仓")
    ap.add_argument("--oos-start", default="2026-06-01", help="样本外起始（只打印对比）")
    args = ap.parse_args()

    print("init qlib / pool ...", flush=True)
    init_qlib()
    codes, sec_map = load_pool()
    print(f"  pool {len(codes)}", flush=True)

    print("load ohlcv ...", flush=True)
    ohlc = load_ohlcv(codes, args.start, args.end)

    print("compute scores ...", flush=True)
    meta = compute_scores(ohlc, sec_map)

    start_i = args.warm
    end_i = len(meta["close"]) - 2
    print(f"backtest full {meta['close'].index[start_i]} .. {meta['close'].index[end_i]}", flush=True)
    full = backtest(meta, start_i, end_i, topk=args.topk, rebalance_every=args.rebalance)
    print(
        f"  FULL: nav={full['nav'][-1]:.3f} ann={full['ann']:.2%} dd={full['dd']:.2%} "
        f"sharpe={full['sharpe']:.2f} win={full['win']:.1%} gate={full['avg_gate']:.2f} "
        f"| bench nav={full['bench_nav'][-1]:.3f} ann={full['bann']:.2%}",
        flush=True,
    )

    # 样本外切片
    oos_mask = [d >= args.oos_start for d in full["dates"]]
    if any(oos_mask):
        # 用日收益从 oos 起点重算 nav
        idx0 = next(i for i, f in enumerate(oos_mask) if f)
        oos_ret = full["ret"][idx0:]
        oos_b = full["bench_ret"][idx0:] if full["bench_ret"] else []
        onav = np.cumprod(1 + np.array(oos_ret))
        obnav = np.cumprod(1 + np.array(oos_b)) if oos_b else np.array([1.0])
        days = max(len(onav), 1)
        print(
            f"  OOS({args.oos_start}→): nav={onav[-1]:.3f} ann={onav[-1]**(252/days)-1:.2%} "
            f"dd={(onav/np.maximum.accumulate(onav)-1).min():.2%} | bench nav={obnav[-1]:.3f}",
            flush=True,
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": full["dates"],
            "ret": full["ret"],
            "nav": full["nav"],
            "bench_ret": full["bench_ret"][: len(full["dates"])],
            "bench_nav": full["bench_nav"][: len(full["dates"])],
        }
    ).to_csv(OUT_DIR / "nav_sectorpulse.csv", index=False)

    hold = latest_holdings(meta, args.topk)
    hold.to_csv(OUT_DIR / "holdings_sectorpulse.csv", index=False)
    print("latest holdings:", flush=True)
    print(hold.to_string(index=False), flush=True)
    print(f"saved {OUT_DIR}/nav_sectorpulse.csv & holdings_sectorpulse.csv", flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
