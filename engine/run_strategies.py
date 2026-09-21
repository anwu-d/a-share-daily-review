# -*- coding: utf-8 -*-
"""
qlib 策略研究入口（Windows 安全）
四类策略 + 完整回测（手续费/滑点）

用法:
  python run_strategies.py
  python run_strategies.py --start 2023-01-01 --end 2025-12-31 --topk 30
"""
from __future__ import annotations

import argparse
import multiprocessing
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "vendor" / "MyTT"))

PROVIDER = str(ROOT / "data" / "qlib_cn")
OUT_DIR = ROOT / "data" / "strategies"


def init_qlib():
    import qlib
    from qlib.constant import REG_CN

    qlib.init(provider_uri=PROVIDER, region=REG_CN, joblib_backend="threading")
    return qlib


def load_universe(max_n: int = 400) -> list[str]:
    """优先主板（SH6 / SZ000），再创业板/科创，去掉明显指数代码"""
    feat = ROOT / "data" / "qlib_cn" / "features"
    codes = []
    for p in sorted(feat.iterdir()):
        name = p.name.upper()
        if not name.startswith(("SH", "SZ")):
            continue
        num = name[2:]
        # 跳过沪指/深指等
        if name.startswith("SH0") or name.startswith("SZ399"):
            continue
        codes.append(name)
    main = [c for c in codes if c.startswith("SH6") or c.startswith("SZ000")]
    gem = [c for c in codes if c.startswith("SZ30")]
    star = [c for c in codes if c.startswith("SH68")]
    ordered = main + gem + star
    return ordered[:max_n] if max_n else ordered


def load_panel(codes: list[str], start: str, end: str):
    """用 qlib 读 OHLCV 宽表；单线程避免 Windows spawn 问题"""
    from qlib.data import D

    fields = ["$open", "$high", "$low", "$close", "$volume", "$factor"]
    # 分批，避免一次太大
    batches = [codes[i : i + 80] for i in range(0, len(codes), 80)]
    frames = []
    for i, b in enumerate(batches):
        print(f"  load batch {i+1}/{len(batches)} n={len(b)}", flush=True)
        df = D.features(b, fields, start_time=start, end_time=end)
        frames.append(df)
    import pandas as pd

    panel = pd.concat(frames, axis=0)
    return panel


def compute_factors(panel):
    """panel: MultiIndex (instrument, datetime) columns=fields"""
    import numpy as np
    import pandas as pd

    # 统一 unstack：确保 index=date, columns=instrument
    out = {}
    for col in panel.columns:
        s = panel[col]
        if isinstance(s.index, pd.MultiIndex):
            # 尝试 level 名
            names = list(s.index.names)
            if "datetime" in names or "date" in names:
                out[col] = s.unstack(level="datetime" if "datetime" in names else "date")
            else:
                # 第二层通常是日期
                out[col] = s.unstack(level=-1)
        else:
            out[col] = s
        # 若 columns 仍是日期，转置
        if out[col].shape[1] > 0 and not isinstance(out[col].columns[0], str):
            if np.issubdtype(out[col].columns.dtype, np.datetime64) or str(out[col].columns.dtype).startswith("datetime"):
                out[col] = out[col].T
        # columns 转 str
        out[col].columns = out[col].columns.astype(str)

    close = out["$close"]
    open_ = out["$open"]
    high = out["$high"]
    low = out["$low"]
    vol = out["$volume"]
    if "$factor" in out:
        adj = close * out["$factor"]
    else:
        adj = close

    ret1 = adj.pct_change()
    ret5 = adj.pct_change(5)
    ret10 = adj.pct_change(10)
    ret20 = adj.pct_change(20)
    ma5 = adj.rolling(5).mean()
    ma20 = adj.rolling(20).mean()
    ma60 = adj.rolling(60).mean()
    vol20 = ret1.rolling(20).std()
    turn = vol.rolling(5).mean() / vol.rolling(20).mean()

    # limit-up 阈值
    cols = list(adj.columns)
    lim_vals = np.array([0.198 if (c.startswith("30") or c.startswith("68") or c.startswith("BJ")) else 0.098 for c in cols])
    lim_wide = pd.DataFrame(np.tile(lim_vals, (len(adj), 1)), index=adj.index, columns=cols)
    is_zt = ret1 >= (lim_wide - 0.002)

    # 连板
    iz = is_zt.to_numpy(dtype=float, copy=True)
    sk = np.zeros_like(iz)
    for i in range(len(iz)):
        if i == 0:
            sk[i] = iz[i]
        else:
            sk[i] = np.where(iz[i] > 0, sk[i - 1] + 1.0, 0.0)
    streak = pd.DataFrame(sk, index=adj.index, columns=cols)

    # RSI
    rsi6 = adj.rolling(7).apply(lambda x: _rsi_np(x, 6), raw=True)
    rsi14 = adj.rolling(15).apply(lambda x: _rsi_np(x, 14), raw=True)

    factors = {
        "adj": adj,
        "close": close,
        "ret1": ret1,
        "ret5": ret5,
        "ret10": ret10,
        "ret20": ret20,
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "vol20": vol20,
        "turn": turn,
        "is_zt": is_zt,
        "streak": streak,
        "rsi6": rsi6,
        "rsi14": rsi14,
        "vol": vol,
        "high": high,
        "low": low,
        "open": open_,
    }
    return factors


def _rsi_np(x, n=14):
    import numpy as np

    if len(x) < n + 1:
        return np.nan
    diff = np.diff(x)
    up = np.where(diff > 0, diff, 0)
    dn = np.where(diff < 0, -diff, 0)
    rs = up[-n:].mean() / (dn[-n:].mean() + 1e-12)
    return 100 - 100 / (1 + rs)


def signal_dual_ma(f, i: int) -> dict:
    """双均线：金叉持有 topk，按 MA5-MA20 强度排序"""
    ma5, ma20, adj = f["ma5"].iloc[i], f["ma20"].iloc[i], f["adj"].iloc[i]
    if i > 0:
        prev5, prev20 = f["ma5"].iloc[i - 1], f["ma20"].iloc[i - 1]
    else:
        return {}
    cross = (prev5 <= prev20) & (ma5 > ma20)
    hold = ma5 > ma20
    score = (ma5 / ma20 - 1).where(hold)
    return {"score": score, "cross": cross}


def signal_multifactor(f, i: int) -> dict:
    """动量 + 低波 + 适度换手：综合打分"""
    import numpy as np

    m20 = f["ret20"].iloc[i]
    v20 = f["vol20"].iloc[i]
    t = f["turn"].iloc[i]
    # rank 标准化
    r_m = m20.rank(pct=True)
    r_v = (-v20).rank(pct=True)  # 低波优先
    r_t = (-np.log(t.clip(lower=0.2))).rank(pct=True)  # 换手不过高
    score = 0.5 * r_m + 0.3 * r_v + 0.2 * r_t
    return {"score": score}


def signal_limitup(f, i: int) -> dict:
    """涨停策略：昨日涨停 → 今日买入；按连板与开盘强度排序"""
    if i < 2:
        return {}
    zt_y = f["is_zt"].iloc[i - 1]
    streak_y = f["streak"].iloc[i - 1]
    prev_close = f["close"].iloc[i - 1]
    open_i = f["open"].iloc[i]
    open_pct = open_i / prev_close - 1
    cand = zt_y & (open_pct > -0.03)
    score = (streak_y.astype(float) + open_pct * 10).where(cand)
    return {"score": score}


def signal_rsi_reversal(f, i: int) -> dict:
    """RSI 超卖反转：RSI6 < 20 买，> 70 卖；按超卖程度排序"""
    rsi = f["rsi6"].iloc[i]
    score = (30 - rsi).where(rsi < 25)
    return {"score": score}


SIGNALS = {
    "dual_ma": signal_dual_ma,
    "multifactor": signal_multifactor,
    "limitup": signal_limitup,
    "rsi_reversal": signal_rsi_reversal,
}


def run_backtest(f, signal_fn, start_i: int, end_i: int, topk: int = 30,
                 cost_bps: float = 15.0, name: str = "strat"):
    """
    日频回测：T 日收盘信号 → T+1 持有，T+1 收盘计收益（近似 T+1 开盘到收盘？）
    采用：信号日 T 收盘选股，按 T+1 收盘对 T+1 开盘的收益？更常见：
    持有 T+1 全天：ret = close[t+1]/close[t] - 1（忽略滑点细节，用 cost_bps 一次）
    每日换手成本 = |w_t - w_{t-1}| * cost
    """
    import numpy as np
    import pandas as pd

    adj = f["adj"]
    dates = adj.index
    n = len(dates)
    port_ret = []
    turn_hist = []
    prev_hold = set()
    for i in range(start_i, end_i):
        sig = signal_fn(f, i)
        score = sig.get("score")
        if score is None:
            port_ret.append(0.0)
            turn_hist.append(0.0)
            continue
        score = score.dropna()
        if score.empty:
            port_ret.append(0.0)
            turn_hist.append(0.0)
            continue
        picks = list(score.sort_values(ascending=False).head(topk).index)
        hold = set(picks)
        # T+1 收益（若 i+1 存在）
        if i + 1 >= n:
            port_ret.append(0.0)
            turn_hist.append(0.0)
            prev_hold = hold
            continue
        r = adj.iloc[i + 1] / adj.iloc[i] - 1
        r = r.loc[picks].mean()
        # 换手
        if prev_hold:
            turnover = len(hold.symmetric_difference(prev_hold)) / max(len(hold), 1)
        else:
            turnover = 1.0
        cost = turnover * cost_bps / 10000.0
        port_ret.append(float(r) - cost)
        turn_hist.append(turnover)
        prev_hold = hold

    nav = np.cumprod(1 + np.array(port_ret))
    if len(nav) == 0:
        return {"name": name, "error": "empty"}
    ann_ret = nav[-1] ** (252 / len(nav)) - 1
    # max dd
    peak = np.maximum.accumulate(nav)
    dd = nav / peak - 1
    max_dd = dd.min()
    vol = float(np.std(port_ret) * np.sqrt(252))
    sharpe = float(np.mean(port_ret) / (np.std(port_ret) + 1e-12) * np.sqrt(252))
    win = float(np.mean([1 if x > 0 else 0 for x in port_ret]))
    return {
        "name": name,
        "days": len(nav),
        "nav_end": float(nav[-1]),
        "ann_ret": float(ann_ret),
        "max_dd": float(max_dd),
        "vol": vol,
        "sharpe": sharpe,
        "win_rate": win,
        "avg_turn": float(np.mean(turn_hist)) if turn_hist else 0,
        "ret_series": port_ret,
        "nav_series": list(nav),
        "dates": [str(dates[j])[:10] for j in range(start_i + 1, start_i + 1 + len(port_ret))],
    }


def layer_report(f, signal_fn, i: int, n_layers: int = 5):
    """单日分层收益（研究用）"""
    import pandas as pd

    sig = signal_fn(f, i)
    score = sig.get("score")
    if score is None or score.dropna().empty or i + 1 >= len(f["adj"]):
        return None
    score = score.dropna().sort_values(ascending=False)
    r = (f["adj"].iloc[i + 1] / f["adj"].iloc[i] - 1).reindex(score.index)
    q = pd.qcut(score.rank(method="first"), n_layers, labels=False)
    return r.groupby(q).mean().to_dict()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2026-09-11")
    ap.add_argument("--universe", type=int, default=300)
    ap.add_argument("--topk", type=int, default=30)
    ap.add_argument("--cost-bps", type=float, default=15.0)
    args = ap.parse_args()

    t0 = time.time()
    print("init qlib...", flush=True)
    init_qlib()

    print("universe...", flush=True)
    codes = load_universe(args.universe)
    print(f"  n={len(codes)}", flush=True)

    print("load panel...", flush=True)
    panel = load_panel(codes, args.start, args.end)
    print(f"  panel {panel.shape}", flush=True)

    print("factors...", flush=True)
    f = compute_factors(panel)
    adj = f["adj"]
    # 对齐回测区间
    start_i = 60  # 留出均线窗口
    end_i = len(adj) - 2
    print(f"  dates {adj.index[0]} .. {adj.index[-1]}  bt={start_i}..{end_i}", flush=True)

    results = []
    for name, fn in SIGNALS.items():
        print(f"backtest {name} ...", flush=True)
        r = run_backtest(f, fn, start_i, end_i, topk=args.topk, cost_bps=args.cost_bps, name=name)
        results.append(r)
        print(
            f"  {name}: ann={r.get('ann_ret',0):.2%} dd={r.get('max_dd',0):.2%} "
            f"sharpe={r.get('sharpe',0):.2f} nav={r.get('nav_end',0):.3f} win={r.get('win_rate',0):.1%}",
            flush=True,
        )

    # 等权基准（全市场）
    print("backtest buyhold ...", flush=True)

    def bh(f, i):
        return {"score": pd.Series(1.0, index=f["adj"].columns)}

    import pandas as pd

    bench = run_backtest(f, bh, start_i, end_i, topk=9999, cost_bps=args.cost_bps, name="buyhold")
    print(
        f"  buyhold: ann={bench.get('ann_ret',0):.2%} dd={bench.get('max_dd',0):.2%} "
        f"sharpe={bench.get('sharpe',0):.2f} nav={bench.get('nav_end',0):.3f}",
        flush=True,
    )
    results.append(bench)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    import json

    slim = []
    for r in results:
        slim.append({k: v for k, v in r.items() if k not in ("ret_series", "nav_series", "dates")} | {"dates": r.get("dates", [])[:1] + r.get("dates", [])[-1:]})
        # save full nav
        if "nav_series" in r:
            pd.DataFrame({"date": r["dates"], "nav": r["nav_series"]}).to_csv(OUT_DIR / f"nav_{r['name']}.csv", index=False)
    (OUT_DIR / "summary.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {OUT_DIR}/summary.json  total {time.time()-t0:.1f}s", flush=True)

    # markdown summary
    lines = [
        "| 策略 | 年化 | 最大回撤 | Sharpe | 胜率 | 日均换手 | NAV |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| {r['name']} | {r.get('ann_ret',0):.2%} | {r.get('max_dd',0):.2%} | {r.get('sharpe',0):.2f} | "
            f"{r.get('win_rate',0):.1%} | {r.get('avg_turn',0):.2f} | {r.get('nav_end',0):.3f} |"
        )
    md = "\n".join(lines) + "\n"
    (OUT_DIR / "summary.md").write_text(md, encoding="utf-8")
    _write_html(results, args, time.time() - t0)
    print(md, flush=True)
    print(f"saved {OUT_DIR}/summary.html", flush=True)


def _write_html(results, args, elapsed):
    rows = []
    for r in results:
        rows.append(
            f"<tr><td><b>{r['name']}</b></td>"
            f"<td class='num'>{r.get('ann_ret',0):.2%}</td>"
            f"<td class='num'>{r.get('max_dd',0):.2%}</td>"
            f"<td class='num'>{r.get('sharpe',0):.2f}</td>"
            f"<td class='num'>{r.get('win_rate',0):.1%}</td>"
            f"<td class='num'>{r.get('avg_turn',0):.2f}</td>"
            f"<td class='num'>{r.get('nav_end',0):.3f}</td></tr>"
        )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>qlib 策略回测</title>
<style>
body{{font-family:Georgia,'PingFang SC',serif;background:#fff;color:#051c2c;margin:0;padding:40px 24px;}}
.wrap{{max-width:860px;margin:0 auto;}}
h1{{font-size:28px;margin:0 0 8px;}}
.meta{{color:#8595a6;font-family:Menlo,Consolas,monospace;font-size:12px;margin-bottom:24px;}}
table{{border-collapse:collapse;width:100%;font-size:14px;}}
th{{text-align:left;font-family:Menlo,monospace;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
border-bottom:1.5px solid #051c2c;padding:8px 10px 8px 0;color:#42566a;}}
td{{padding:10px 10px 10px 0;border-bottom:1px solid #eef1f6;}}
td.num{{font-family:Menlo,monospace;font-variant-numeric:tabular-nums;}}
.note{{margin-top:28px;color:#42566a;font-size:13px;line-height:1.7;}}
</style></head><body><div class="wrap">
<h1>qlib 策略回测 · 四类信号</h1>
<p class="meta">区间 {args.start} → {args.end} · 股票池 {args.universe} · TopK {args.topk} · 成本 {args.cost_bps}bp · 耗时 {elapsed:.1f}s</p>
<table><thead><tr><th>策略</th><th>年化</th><th>最大回撤</th><th>Sharpe</th><th>胜率</th><th>日均换手</th><th>NAV</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p class="note">信号：dual_ma=MA5/MA20 金叉持有；multifactor=动量+低波+换手；limitup=昨涨停接力（连板/开盘强度）；rsi_reversal=RSI6&lt;25 超卖反转；buyhold=等权持有。<br/>
说明：T 日收盘信号 → T+1 持有收益，扣换手成本；样本池来自主板优先截断，非全市场，结果仅供研究，非投资建议。</p>
</div></body></html>
"""
    (OUT_DIR / "summary.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
