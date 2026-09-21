# -*- coding: utf-8 -*-
"""
用东财日K把「qlib 之后缺失的交易日」补进本地宽表，并重算 PatternComboGate。
比整包下载 qlib_bin 快得多。
"""
from __future__ import annotations

import json
import multiprocessing
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

CACHE = ROOT / "data" / "cache"
OUT_DIR = ROOT / "data" / "strategies"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

# qlib 目录名 sh600000 → 东财 secid 1.600000
def secid_of(dirname: str) -> str:
    d = dirname.upper()
    if d.startswith("SH"):
        return "1." + d[2:]
    if d.startswith("SZ"):
        return "0." + d[2:]
    if d.startswith("BJ"):
        return "0." + d[2:]  # 北交部分用 0.
    return "0." + d[-6:]


def fetch_kline(dirname: str, beg: str, end: str, retries: int = 2) -> list[tuple]:
    """优先新浪/腾讯（Ashare）；东财兜底"""
    try:
        sys.path.insert(0, str(ROOT / "vendor" / "Ashare"))
        from Ashare import get_price

        df = get_price(dirname.lower(), end_date=end, count=120, frequency="1d")
        if df is not None and not df.empty:
            out = []
            for dt, row in df.iterrows():
                ds = str(dt)[:10]
                if beg and ds < beg:
                    continue
                out.append(
                    (
                        ds,
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        float(row["volume"]),
                        0.0,
                    )
                )
            if out:
                return out
    except Exception:
        pass
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid_of(dirname),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": 101,
        "fqt": 1,
        "beg": beg.replace("-", ""),
        "end": end.replace("-", ""),
    }
    for i in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=12)
            data = r.json()
            kl = ((data.get("data") or {}).get("klines")) or []
            out = []
            for line in kl:
                p = line.split(",")
                if len(p) < 7:
                    continue
                out.append(
                    (
                        p[0],
                        float(p[1]),
                        float(p[2]),
                        float(p[3]),
                        float(p[4]),
                        float(p[5]),
                        float(p[6]),
                    )
                )
            return out
        except Exception:
            time.sleep(0.3 * (i + 1))
    return []


def list_main_gem() -> list[str]:
    from run_candle_2026_maingem import drop_st, list_main_gem as _lm

    codes = drop_st(_lm(include_gem=True))
    close_p = CACHE / "close_wide.parquet"
    if close_p.exists():
        try:
            close = pd.read_parquet(close_p)
            cmap = {str(c).upper(): c for c in close.columns}
            members = [cmap[c] for c in codes if c in cmap]
            if members:
                avg = close[members].tail(60).mean().dropna().sort_values(ascending=False)
                picked = [str(x).upper() for x in avg.head(800).index]
                print(f"liquidity core {len(picked)}", flush=True)
                return picked
        except Exception as e:
            print(f"core fail {e}", flush=True)
    return codes


def build_panel(codes, beg: str, end: str, workers: int = 16) -> dict[str, pd.DataFrame]:
    """{field: wide df} index=date"""
    fields = ["open", "high", "low", "close", "volume"]
    cols: dict[str, list] = {f: [] for f in fields}
    cols["dir"] = []
    dates_set = set()

    def one(name):
        return name, fetch_kline(name, beg, end)

    print(f"fetch {len(codes)} stocks via Ashare ...", flush=True)
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, c) for c in codes]
        for fut in as_completed(futs):
            name, rows = fut.result()
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(codes)} {time.time()-t0:.0f}s", flush=True)
            if not rows:
                continue
            rec = {f: {} for f in fields}
            for dt, o, h, l, c, v, amt in rows:
                rec["open"][dt] = o
                rec["high"][dt] = h
                rec["low"][dt] = l
                rec["close"][dt] = c
                rec["volume"][dt] = v
                dates_set.update(rec["open"].keys())
            cols["dir"].append(name)
            for f in fields:
                cols[f].append(rec[f])

    dates = sorted(dates_set)
    out = {}
    for f in fields:
        df = pd.DataFrame(index=pd.Index(dates, name="date"), columns=cols["dir"], dtype=float)
        for i, name in enumerate(cols["dir"]):
            series = cols[f][i]
            for dt, v in series.items():
                df.at[dt, name] = v
        out[f] = df
    print(f"panel ready {out['close'].shape} {out['close'].index.min()}..{out['close'].index[-1]} {time.time()-t0:.0f}s", flush=True)
    return out


def main():
    from run_candle_2026_maingem import detect_all, build_masks, event_study_range
    from stock_names import get_name_map, pretty
    import pattern_gate as PG

    codes = list_main_gem()
    print(f"universe {len(codes)}", flush=True)
    # 近 80 自然日足够算 MA20 / 组合
    end = time.strftime("%Y-%m-%d")
    beg = (pd.Timestamp(end) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    data = build_panel(codes, beg, end, workers=8)
    last_day = str(data["close"].index[-1])[:10]
    print(f"last trade day {last_day}", flush=True)

    # 缓存到 parquet，便于 DuckDB/复盘复用
    for f, df in data.items():
        df.to_parquet(CACHE / f"em_{f}_wide.parquet", compression="zstd")
    print("saved em_*_wide.parquet", flush=True)

    print("patterns ...", flush=True)
    pat = detect_all(data["open"], data["high"], data["low"], data["close"])
    masks = build_masks(pat)
    dates = [str(x)[:10] for x in pat["close"].index]
    start_i = next((i for i, d in enumerate(dates) if d >= "2026-01-01"), 20)
    ev = event_study_range(pat, masks, start_i, len(dates) - 1)
    keep = list(ev[(ev["mean_5d"] > 0) & (ev["hits"] >= 80)]["combo"])
    if not keep:
        keep = list(ev[(ev["mean_5d"] > 0) & (ev["hits"] >= 20)]["combo"])
    if not keep:
        print("无正收益组合，退出", flush=True)
        print(ev.to_string(index=False), flush=True)
        return
    combo_stats = {
        r["combo"]: {"hits": int(r["hits"]), "mean_5d": float(r["mean_5d"])}
        for _, r in ev.iterrows()
        if r["combo"] in keep
    }
    print(f"keep {len(keep)} combos", flush=True)

    sig_any = None
    score = None
    for name in keep:
        m = masks[name]
        sig_any = m if sig_any is None else (sig_any | m)
        score = m.astype(float) if score is None else score + m.astype(float)
    score = score.where(sig_any)

    last_idx = len(dates) - 1
    ret1 = pat["ret1"]
    red = (ret1 > 0).sum(axis=1) / ret1.notna().sum(axis=1).clip(lower=1)
    red_ma = red.rolling(5, min_periods=1).mean()
    r5 = float(red_ma.iloc[last_idx])
    if r5 < 0.35:
        gtxt = "30%"
    elif r5 < 0.45:
        gtxt = "60%"
    else:
        gtxt = "100%"

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
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / "latest_pattern_combo_holdings.csv", index=False, encoding="utf-8-sig")
    meta = {
        "signal_date": last_day,
        "red_ma5": round(r5, 4),
        "gate": gtxt,
        "n_combos": len(keep),
        "combos": keep,
        "combo_stats": combo_stats,
        "n_stocks": int(data["close"].shape[1]),
        "hold_n": len(picks),
        "source": "eastmoney_kline",
    }
    (OUT_DIR / "latest_pattern_combo_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"signal day {last_day} gate {gtxt} red5 {r5:.1%} top {len(picks)}", flush=True)
    print(df.head(12).to_string(index=False), flush=True)
    print(f"saved {OUT_DIR}/latest_pattern_combo_*", flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
