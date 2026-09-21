# -*- coding: utf-8 -*-
"""
PatternComboGate 每日 TopN 报告
  - 最后交易日收盘信号
  - 保留 2026 正收益组合（事件研究）
  - TopN（默认 10）+ 概率/评分 + 命中的形态组合名
  - 供 run_daily 写入 data.js → 复盘页
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

OUT_DIR = ROOT / "data" / "strategies"


def _qlib_last_day() -> str:
    try:
        from qlib_local import calendar_dates

        days = calendar_dates()
        return days[-1] if days else ""
    except Exception:
        return ""


def _ensure_combo_cache(force: bool = False):
    """
    读缓存；若缓存信号日 != qlib 最新交易日，则重算。
    重算很慢（全市场 ~数分钟），仅在日历前进时触发。
    """
    import json

    csv = OUT_DIR / "latest_pattern_combo_holdings.csv"
    meta = OUT_DIR / "latest_pattern_combo_meta.json"
    last = _qlib_last_day()
    if not force and csv.exists() and meta.exists():
        meta_j = json.loads(meta.read_text(encoding="utf-8"))
        sig = str(meta_j.get("signal_date") or "")
        # 缓存不早于 qlib 最新日 → 直接用（东财 Ashare 可能已刷到更近一天）
        if last and sig >= last:
            return pd.read_csv(csv), meta_j
        if last and sig < last:
            print(
                f"[pattern-gate] 缓存信号日 {sig} 早于 qlib 最新 {last}，重算 ...",
                flush=True,
            )
        elif not last:
            return pd.read_csv(csv), meta_j
    elif not last and csv.exists() and meta.exists() and not force:
        return pd.read_csv(csv), json.loads(meta.read_text(encoding="utf-8"))
    return _run_and_cache()


def _run_and_cache():
    """完整跑 latest_pattern_combo 逻辑并落盘 meta（不依赖 end 硬编码）。"""
    import json

    from run_candle_2026_maingem import (
        build_masks,
        detect_all,
        drop_st,
        event_study_range,
        init_qlib,
        list_main_gem,
        load_ohlc,
    )
    from stock_names import get_name_map, pretty
    from qlib_local import calendar_dates

    init_qlib()
    days = calendar_dates()
    end = days[-1] if days else "2026-09-11"
    print(f"[pattern-gate] 计算信号日 {end} ...", flush=True)
    codes = drop_st(list_main_gem(include_gem=True))
    data = load_ohlc(codes, "2025-11-01", end)
    pat = detect_all(data["$open"], data["$high"], data["$low"], data["$close"])
    masks = build_masks(pat)
    dates = [str(x)[:10] for x in pat["close"].index]
    start_i = next(i for i, d in enumerate(dates) if d >= "2026-01-01")
    ev = event_study_range(pat, masks, start_i, len(dates) - 1)
    keep = list(ev[(ev["mean_5d"] > 0) & (ev["hits"] >= 150)]["combo"])
    combo_stats = {
        r["combo"]: {"hits": int(r["hits"]), "mean_5d": float(r["mean_5d"])}
        for _, r in ev.iterrows()
        if r["combo"] in keep
    }

    sig_any = None
    score = None
    for name in keep:
        m = masks[name]
        sig_any = m if sig_any is None else (sig_any | m)
        score = m.astype(float) if score is None else score + m.astype(float)
    score = score.where(sig_any)

    last_idx = len(dates) - 1
    day = dates[last_idx]
    ret1 = pat["ret1"]
    red = (ret1 > 0).sum(axis=1) / ret1.notna().sum(axis=1).clip(lower=1)
    red_ma = red.rolling(5, min_periods=1).mean()
    r5 = float(red_ma.iloc[last_idx])
    if r5 < 0.35:
        g, gtxt = 0.30, "30%"
    elif r5 < 0.45:
        g, gtxt = 0.60, "60%"
    else:
        g, gtxt = 1.0, "100%"

    row = score.iloc[last_idx].dropna().sort_values(ascending=False)
    picks = list(row.head(40).index)
    nmap = get_name_map(picks)
    rows = []
    for c in picks:
        hit = [n for n in keep if bool(masks[n].iloc[last_idx].get(c, False))]
        rows.append({
            "code": c,
            "name": pretty(c, nmap),
            "combos": "；".join(hit) if hit else "—",
            "n_combo": len(hit),
            "score": float(row[c]),
            "pct": float(ret1.iloc[last_idx].get(c, np.nan)),
        })
    df = pd.DataFrame(rows)
    df.to_csv(OUT_DIR / "latest_pattern_combo_holdings.csv", index=False, encoding="utf-8-sig")
    meta = {
        "signal_date": day,
        "red_ma5": round(r5, 4),
        "gate": gtxt,
        "n_combos": len(keep),
        "combos": keep,
        "combo_stats": combo_stats,
        "n_stocks": int(data["$close"].shape[1]),
    }
    (OUT_DIR / "latest_pattern_combo_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return df, meta


def build_topn_payload(topn: int = 10, force: bool = False) -> dict:
    """
    返回复盘页用的结构：
    {
      signalDate, redMa5, gate, nCombos,
      items: [{code, name, combos, nCombo, score, pct, prob, probLabel, score100}],
      comboList: [{name, hits, mean5d}]
    }
    """
    df, meta = _ensure_combo_cache(force=force)
    if df is None or df.empty:
        return {"signalDate": None, "items": [], "comboList": []}

    combo_stats = meta.get("combo_stats") or {}
    # 概率：用该票命中组合的「历史后5日均涨幅」做启发式映射到 0–100
    # 不是严格概率，标注为 research score
    items = []
    for _, r in df.head(topn).iterrows():
        hits = [x.strip() for x in str(r["combos"]).split("；") if x.strip() and x.strip() != "—"]
        means = [combo_stats.get(h, {}).get("mean_5d", 0.0) for h in hits] or [0.0]
        avg_m = float(np.mean(means))
        # 组合数 + 均涨幅 → 评分 0–100
        score100 = int(min(100, max(0, 40 + 20 * len(hits) + 800 * max(avg_m, 0))))
        # 概率：50 + 均涨幅百分点放大，钳制 5–85
        prob = float(min(85.0, max(5.0, 50 + avg_m * 1000)))
        items.append({
            "code": str(r["code"]),
            "name": str(r["name"]),
            "combos": hits,
            "nCombo": int(r["n_combo"]),
            "score": float(r["score"]),
            "score100": score100,
            "prob": round(prob, 1),
            "probLabel": f"{prob:.0f}%",
            "pct": None if pd.isna(r["pct"]) else float(r["pct"]),
        })

    # 展示排序按 score100 降序，保证「评分」列单调可读；
    # 入选名单本身仍按命中组合数取前 topn，不改变信号强度口径。
    items.sort(key=lambda x: -x["score100"])

    combo_list = []
    for name in meta.get("combos") or []:
        st = combo_stats.get(name, {})
        combo_list.append({
            "name": name,
            "hits": int(st.get("hits", 0)),
            "mean5d": float(st.get("mean_5d", 0)),
        })

    return {
        "signalDate": meta.get("signal_date"),
        "redMa5": meta.get("red_ma5"),
        "gate": meta.get("gate"),
        "nCombos": meta.get("n_combos"),
        "items": items,
        "comboList": combo_list,
        "note": (
            "评分 = 40 + 20×命中组合数 + 800×组合历史后5日均涨幅；"
            "参考概率只由组合历史后5日均涨幅映射（不含命中数），二者不同源；"
            "入选按命中组合数取前 N，展示按评分排序"
        ),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(build_topn_payload(10), ensure_ascii=False, indent=2)[:3000])
