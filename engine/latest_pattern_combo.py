# -*- coding: utf-8 -*-
"""
PatternComboGate · 最新选股池
  与 run_candle_2026_maingem.py 同逻辑：正收益组合并集 + 信号日广度门控
  输出：最近可交易日 TopK 持仓 + 命中的形态组合名 + 当日仓位权重
"""
from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from run_candle_2026_maingem import (  # noqa: E402
    PROVIDER,
    build_masks,
    detect_all,
    drop_st,
    event_study_range,
    init_qlib,
    list_main_gem,
    load_ohlc,
)
from stock_names import get_name_map, pretty  # noqa: E402

OUT_DIR = ROOT / "data" / "strategies"


def qlib_last_day() -> str:
    from qlib_local import calendar_dates

    days = calendar_dates()
    return days[-1] if days else ""


def main():
    warm_start = "2025-11-01"
    start = "2026-01-01"
    topk = 40
    min_hits = 150

    print("init ...", flush=True)
    init_qlib()
    end = qlib_last_day()
    if not end:
        print("qlib 日历为空", flush=True)
        return
    print(f"qlib 最新交易日: {end}", flush=True)
    codes = drop_st(list_main_gem(include_gem=True))
    print(f"universe {len(codes)}", flush=True)
    print("load ...", flush=True)
    data = load_ohlc(codes, warm_start, end)
    print(f"stocks {data['$close'].shape[1]}", flush=True)

    print("patterns ...", flush=True)
    pat = detect_all(data["$open"], data["$high"], data["$low"], data["$close"])
    masks = build_masks(pat)
    dates = [str(x)[:10] for x in pat["close"].index]
    start_i = next(i for i, d in enumerate(dates) if d >= start)
    end_i = len(dates) - 2

    ev = event_study_range(pat, masks, start_i, end_i)
    keep = list(ev[(ev["mean_5d"] > 0) & (ev["hits"] >= min_hits)]["combo"])
    print(f"combos ({len(keep)}): {'、'.join(keep)}", flush=True)

    # 信号矩阵：股票 × 组合
    sig_any = None
    score = None
    combo_hits = {}  # code -> list of combo names
    for name in keep:
        m = masks[name]
        sig_any = m if sig_any is None else (sig_any | m)
        score = m.astype(float) if score is None else score + m.astype(float)
    score = score.where(sig_any)

    # 实盘/最新：用最后一根日 K 收盘出信号（不是回测用的 end_i = len-2）
    last_idx = len(dates) - 1
    if not sig_any.iloc[last_idx].fillna(False).any():
        # 最后一天无信号 → 往前找最近有信号的一天（仅供回看）
        found = None
        for i in range(last_idx, start_i - 1, -1):
            if sig_any.iloc[i].fillna(False).any():
                found = i
                break
        if found is None:
            print("最后交易日及回看均无信号", flush=True)
            return
        print(
            f"注意：最后交易日 {dates[last_idx]} 无信号；以下为最近有信号日 {dates[found]}（回看）",
            flush=True,
        )
        last_sig_idx = found
        is_latest_day = False
    else:
        last_sig_idx = last_idx
        is_latest_day = True

    day = dates[last_sig_idx]
    print(f"\n信号日: {day}{'（最新收盘）' if is_latest_day else '（回看）'}", flush=True)

    # 广度门控
    ret1 = pat["ret1"]
    red = (ret1 > 0).sum(axis=1) / ret1.notna().sum(axis=1).clip(lower=1)
    red_ma = red.rolling(5, min_periods=1).mean()
    r5 = float(red_ma.iloc[last_sig_idx])
    if r5 < 0.35:
        g, gtxt = 0.30, "30%"
    elif r5 < 0.45:
        g, gtxt = 0.60, "60%"
    else:
        g, gtxt = 1.0, "100%"
    print(f"近5日红盘率 {r5:.1%} → 信号日仓位 {gtxt}", flush=True)

    row = score.iloc[last_sig_idx].dropna().sort_values(ascending=False)
    picks = list(row.head(topk).index)
    print(f"TopK={topk} 实际选出 {len(picks)}", flush=True)

    nmap = get_name_map(picks)
    rows = []
    for c in picks:
        hit = [n for n in keep if bool(masks[n].iloc[last_sig_idx].get(c, False))]
        rows.append({
            "code": c,
            "name": pretty(c, nmap),
            "combos": "；".join(hit) if hit else "—",
            "n_combo": len(hit),
            "score": float(row[c]),
            "pct": float(ret1.iloc[last_sig_idx].get(c, np.nan)),
        })
    df = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = OUT_DIR / "latest_pattern_combo_holdings.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    # meta（供复盘页 pattern_gate 快速读取）
    import json

    combo_stats = {
        r["combo"]: {"hits": int(r["hits"]), "mean_5d": float(r["mean_5d"])}
        for _, r in ev.iterrows()
        if r["combo"] in keep
    }
    meta = {
        "signal_date": day,
        "red_ma5": round(float(r5), 4),
        "gate": gtxt,
        "n_combos": len(keep),
        "combos": keep,
        "combo_stats": combo_stats,
        "n_stocks": int(data["$close"].shape[1]),
        "hold_n": len(picks),
    }
    (OUT_DIR / "latest_pattern_combo_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nsaved {out_csv}", flush=True)
    print(df.to_string(index=False), flush=True)

    # 下一交易日提示
    nxt = dates[last_sig_idx + 1] if last_sig_idx + 1 < len(dates) else "(尚未有下一交易日数据)"
    print(f"\n【执行说明】信号日 {day} 收盘选股，对应下一交易日 {nxt} 持有；仓位 {gtxt}", flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
