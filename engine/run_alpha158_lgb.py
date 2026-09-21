# -*- coding: utf-8 -*-
"""
Alpha158 + LightGBM 选股（科技/证券/有色）
qlib 官方 DataHandler + LGB + TopkDropout 回测

用法:
  python run_alpha158_lgb.py
  python run_alpha158_lgb.py --train-end 2025-06-30 --valid-end 2025-12-31 --end 2026-09-11
"""
from __future__ import annotations

import argparse
import multiprocessing
import sys
import time
from pathlib import Path

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

    exp_uri = f"sqlite:///{(ROOT / 'data' / 'mlflow.db').as_posix()}"
    qlib.init(
        provider_uri=PROVIDER,
        region=REG_CN,
        joblib_backend="threading",
        exp_uri=exp_uri,
    )


def build_market_instruments(codes: list[str]) -> dict:
    """qlib instruments 市场过滤：用自定义 list 作为 market 字段会失败；
    Alpha158 需要 instruments 参数，支持 list / dict。
    这里直接返回股票代码 list。
    """
    return codes


def run(args):
    init_qlib()
    from qlib.contrib.data.handler import Alpha158
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.data.dataset import DatasetH
    from qlib.contrib.strategy import TopkDropoutStrategy
    from qlib.backtest import backtest as qlib_backtest
    from qlib.backtest.executor import SimulatorExecutor
    from qlib.workflow.record_temp import SignalRecord, SigAnaRecord, PortAnaRecord
    import qlib.contrib.strategy as qlib_strategy

    from sector_universe import combined_pool, tech_pool, securities_pool, nonferrous_pool

    pools = {
        "all3": combined_pool(),
        "tech": tech_pool(),
        "securities": securities_pool(),
        "nonferrous": nonferrous_pool(),
    }
    if args.pool not in pools:
        raise SystemExit(f"unknown pool {args.pool}, choose from {list(pools)}")
    instruments = pools[args.pool]
    print(f"pool={args.pool} n={len(instruments)}", flush=True)
    if len(instruments) < 10:
        raise SystemExit("池子太小，请检查 sector_universe 与 qlib features 是否匹配")

    # ── DataHandler Alpha158 ──
    print("Alpha158 handler ...", flush=True)
    t0 = time.time()
    handler = Alpha158(
        instruments=instruments,
        start_time=args.start,
        end_time=args.end,
        fit_start_time=args.start,
        fit_end_time=args.train_end,
        freq="day",
    )
    dataset = DatasetH(handler=handler, segments={
        "train": (args.start, args.train_end),
        "valid": (args.train_end, args.valid_end),
        "test": (args.valid_end, args.end),
    })
    print(f"  handler {time.time()-t0:.1f}s", flush=True)

    # ── LGB ──
    print("train LGB ...", flush=True)
    t1 = time.time()
    model = LGBModel(
        loss="mse",
        colsample_bytree=0.85,
        learning_rate=0.03,
        subsample=0.85,
        lambda_l1=1.0,
        lambda_l2=1.0,
        max_depth=7,
        num_leaves=127,
        n_estimators=400,
        early_stopping_rounds=50,
        num_threads=4,
        min_child_samples=50,
    )
    model.fit(dataset)
    print(f"  train {time.time()-t1:.1f}s", flush=True)

    pred = model.predict(dataset)
    print(f"  pred last\n{pred.tail(8)}", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        p = pred.copy()
        if isinstance(p, pd.Series):
            p = p.to_frame("score")
        p.to_csv(OUT_DIR / f"pred_{args.pool}_{args.end}.csv")
        print(f"saved pred {OUT_DIR}/pred_{args.pool}_{args.end}.csv", flush=True)
    except Exception as e:
        print("save pred fail", e, flush=True)

    # 轻量 TopK 回测（T 日分数 → T+1 持有）
    print("local TopK backtest ...", flush=True)
    try:
        local_nav(pred, instruments, args, OUT_DIR)
    except Exception as e:
        print("local nav fail", e, flush=True)
        import traceback

        traceback.print_exc()


def benchmark_code(pool: str, instruments: list[str]) -> str:
    # 用沪深300作基准
    return "SH000300"


def local_nav(pred, instruments, args, out_dir: Path):
    """T 日分数 → T+1 持有 TopK；对比池内等权"""
    from qlib.data import D
    import numpy as np

    close_s = D.features(instruments, ["$close"], start_time=args.valid_end, end_time=args.end)["$close"]
    if isinstance(close_s.index, pd.MultiIndex):
        close = close_s.unstack(level="instrument" if "instrument" in close_s.index.names else 0)
        if not str(close.columns[0]).startswith(("SH", "SZ")):
            close = close.T
    else:
        close = close_s
    close.columns = close.columns.astype(str)

    s = pred
    if isinstance(s, pd.Series):
        # typical: MultiIndex (datetime, instrument)
        if isinstance(s.index, pd.MultiIndex):
            names = list(s.index.names)
            # unstack instrument to columns
            lvl = "instrument" if "instrument" in names else -1
            score = s.unstack(level=lvl)
        else:
            score = s.to_frame().T
    else:
        score = s
    if not isinstance(score.columns, pd.Index):
        score.columns = score.columns.astype(str)
    score.columns = [str(c) for c in score.columns]
    # 若 columns 是日期则转置
    if not any(str(c).startswith(("SH", "SZ")) for c in score.columns[:5]):
        score = score.T
        score.columns = [str(c) for c in score.columns]

    common = [c for c in score.columns if c in close.columns]
    print(f"  nav align stocks={len(common)} score={score.shape} close={close.shape}", flush=True)
    score, close = score[common], close[common]
    idx = score.index.intersection(close.index)
    score, close = score.loc[idx], close.loc[idx]
    topk = args.topk
    rets, bench_rets = [], []
    for i in range(len(score) - 1):
        row = score.iloc[i].dropna()
        c0, c1 = close.iloc[i], close.iloc[i + 1]
        br = (c1 / c0 - 1).mean()
        bench_rets.append(float(br) if br == br else 0.0)
        if row.empty:
            rets.append(0.0)
            continue
        picks = list(row.sort_values(ascending=False).head(topk).index)
        r = (c1.reindex(picks) / c0.reindex(picks) - 1).mean()
        rets.append(float(r) if r == r else 0.0)
    nav = np.cumprod(1 + np.array(rets)) if rets else np.array([1.0])
    bnav = np.cumprod(1 + np.array(bench_rets)) if bench_rets else np.array([1.0])
    n = max(len(nav), 1)
    ann = nav[-1] ** (252 / n) - 1
    bann = bnav[-1] ** (252 / n) - 1
    peak = np.maximum.accumulate(nav)
    dd = (nav / peak - 1).min()
    sharpe = float(np.mean(rets) / (np.std(rets) + 1e-12) * np.sqrt(252))
    print(
        f"  TOPK: nav={nav[-1]:.3f} ann={ann:.2%} dd={dd:.2%} sharpe={sharpe:.2f} days={len(nav)}",
        flush=True,
    )
    print(f"  BENCH: nav={bnav[-1]:.3f} ann={bann:.2%}", flush=True)
    pd.DataFrame(
        {
            "date": [str(x)[:10] for x in idx[: len(rets)]],
            "ret": rets,
            "nav": nav,
            "bench_ret": bench_rets,
            "bench_nav": bnav,
        }
    ).to_csv(out_dir / f"nav_alpha158_{args.pool}.csv", index=False)

    # 最近一日持仓（带股票名称）
    if len(score):
        from stock_names import get_name_map, pretty

        last = score.iloc[-1].dropna().sort_values(ascending=False).head(topk)
        nmap = get_name_map(list(last.index))
        hold = pd.DataFrame(
            {
                "code": last.index,
                "name": [pretty(c, nmap) for c in last.index],
                "score": last.values,
            }
        )
        hold.to_csv(out_dir / f"holdings_{args.pool}_{args.end}.csv", index=False)
        print(f"  latest holdings → holdings_{args.pool}_{args.end}.csv", flush=True)
        print(hold.to_string(index=False), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="all3", choices=["all3", "tech", "securities", "nonferrous"])
    ap.add_argument("--start", default="2021-01-01", help="因子/训练起点")
    ap.add_argument("--train-end", default="2025-05-31", help="训练段结束")
    ap.add_argument("--valid-end", default="2026-05-31", help="验证段结束=样本外起点前一日")
    ap.add_argument("--end", default="2026-09-11", help="样本外终点（最新）")
    ap.add_argument("--topk", type=int, default=15)
    ap.add_argument("--ndrop", type=int, default=2)
    args = ap.parse_args()
    print(
        f"样本外回测: {args.valid_end} 之后 → {args.end}（训练 {args.start}~{args.train_end}）",
        flush=True,
    )
    run(args)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
