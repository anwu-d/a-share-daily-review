# -*- coding: utf-8 -*-
"""
每日 alpha 原料落库（append-only）
  data/cache/alpha/fund_flow_YYYYMMDD.parquet
  data/cache/alpha/sector_sentiment_YYYYMMDD.json
  data/cache/alpha/fund_flow_all.parquet   # 合并表（由 run 滚动重建）

用途：攒历史，供以后回测资金流/情绪因子。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
ALPHA = ROOT / "data" / "cache" / "alpha"
ALPHA.mkdir(parents=True, exist_ok=True)

FLOW_ALL = ALPHA / "fund_flow_all.parquet"


def _core_codes(n_each: int = 40) -> list[str]:
    try:
        from sector_universe import tech_pool, securities_pool, nonferrous_pool
    except Exception:
        return []
    pools = tech_pool() + securities_pool() + nonferrous_pool()
    close_p = ROOT / "data" / "cache" / "close_wide.parquet"
    if close_p.exists():
        try:
            close = pd.read_parquet(close_p)
            col_map = {str(c).upper(): str(c) for c in close.columns}
            out = []
            for pool in (tech_pool(), securities_pool(), nonferrous_pool()):
                members = [col_map[c] for c in pool if c in col_map]
                if not members:
                    continue
                avg = close[members].tail(60).mean().sort_values(ascending=False)
                for col in list(avg.head(n_each).index):
                    out.append(str(col).upper())
            if out:
                return out
        except Exception as e:
            print(f"[alpha] core from close fail: {e}")
    return pools[:120]


def collect_fund_flow(date: str, codes: list[str] | None = None) -> pd.DataFrame:
    """逐票主力净流入（东财 fflow），失败跳过。"""
    import eastmoney as em

    codes = codes or _core_codes(40)
    rows = []
    for i, c in enumerate(codes):
        c6 = c[2:] if len(c) >= 8 else c
        secid = em.secid_of(c6)
        v = em.stock_fund_flow(secid)
        rows.append({"date": date, "code": c6, "dir": c if c.startswith(("SH", "SZ")) else em.secid_of(c6), "main_inflow_yi": v})
        if (i + 1) % 40 == 0:
            print(f"  flow {i+1}/{len(codes)}", flush=True)
        time.sleep(0.06)
    df = pd.DataFrame(rows)
    return df


def collect_sector_sentiment(date: str, pool_sec_map: dict | None = None) -> dict:
    try:
        from sector_sentiment import sector_sentiment_report

        return sector_sentiment_report(date, pool_sec_map)
    except Exception as e:
        return {"date": date, "error": str(e)[:200]}


def append_flow(df: pd.DataFrame) -> None:
    """追加到总表并去重"""
    if df is None or df.empty:
        return
    if FLOW_ALL.exists():
        old = pd.read_parquet(FLOW_ALL)
        all_df = pd.concat([old, df], ignore_index=True)
        all_df = all_df.drop_duplicates(subset=["date", "code"], keep="last")
    else:
        all_df = df
    all_df.to_parquet(FLOW_ALL, index=False)


def run_daily_collect(date: str) -> dict:
    print(f"[alpha-store] fund flow {date} ...", flush=True)
    flow = collect_fund_flow(date)
    if not flow.empty:
        flow.to_parquet(ALPHA / f"fund_flow_{date.replace('-', '')}.parquet", index=False)
        append_flow(flow)
        print(f"  flow rows={len(flow)} non-null={flow['main_inflow_yi'].notna().sum()}", flush=True)

    print(f"[alpha-store] sector sentiment {date} ...", flush=True)
    # 池方向映射
    try:
        from sector_universe import tech_pool, securities_pool, nonferrous_pool

        m = {}
        for c in tech_pool():
            m[c] = "tech"
        for c in securities_pool():
            m[c] = "securities"
        for c in nonferrous_pool():
            m[c] = "nonferrous"
    except Exception:
        m = None
    sent = collect_sector_sentiment(date, m)
    (ALPHA / f"sector_sentiment_{date.replace('-', '')}.json").write_text(
        json.dumps(sent, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"  sent hits={sent.get('news', {}).get('hits')}", flush=True)
    return {"flow_rows": int(len(flow)), "sentiment_ok": "error" not in sent}


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else None
    if not day:
        from datetime import datetime

        day = datetime.now().strftime("%Y-%m-%d")
    run_daily_collect(day)
