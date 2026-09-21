# -*- coding: utf-8 -*-
"""
快速导出：close 宽表 + 最近截面 OHLC
优先保证「每日复盘」可用，全量 OHLC 可后台慢慢补。
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from qlib_local import FEATURES, CALENDAR, instrument_codes, dir_to_code6  # noqa

CACHE = ROOT / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)


def read_bin(path: Path) -> np.ndarray:
    """qlib .day.bin: [start_index float32][float32 * N]，按日历左对齐。"""
    try:
        data = path.read_bytes()
    except Exception:
        return np.array([], dtype=np.float32)
    if len(data) < 8:
        return np.array([], dtype=np.float32)
    start_f = struct.unpack("<f", data[:4])[0]
    start = int(round(start_f)) if start_f == start_f else 0  # NaN guard
    if start < 0 or start > 20000:
        start = 0
    payload = data[4:]
    n_float = len(payload) // 4
    arr = np.frombuffer(payload[: n_float * 4], dtype=np.float32)
    if start > 0:
        arr = np.concatenate([np.full(start, np.nan, dtype=np.float32), arr])
    return arr


def load_calendar() -> list[str]:
    return [ln.strip() for ln in CALENDAR.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _load_one(name: str, start_idx: int, n_days: int, fields: tuple[str, ...]):
    d = FEATURES / name
    cols = {}
    for f in fields:
        p = d / f"{f}.day.bin"
        if not p.exists():
            continue
        arr = read_bin(p)
        if start_idx:
            arr = arr[start_idx:] if len(arr) >= start_idx else np.array([], dtype=np.float32)
        if len(arr) == 0:
            continue
        out = np.full(n_days, np.nan, dtype=np.float32)
        m = min(n_days, len(arr))
        out[:m] = arr[:m]
        cols[f] = out
    if "close" not in cols:
        return None
    return name, cols


def export_closes(limit_days: int | None, include_bj: bool, workers: int = 16):
    days = load_calendar()
    start_idx = 0
    if limit_days and limit_days < len(days):
        start_idx = len(days) - limit_days
        days = days[start_idx:]
    n_days = len(days)
    codes = instrument_codes(include_bj=include_bj)
    print(f"close export: {len(codes)} stocks × {n_days} days, workers={workers}", flush=True)
    t0 = time.time()

    # 结果矩阵
    code_list = []
    close_mat = None
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_load_one, name, start_idx, n_days, ("close",)): name for name in codes}
        for fut in as_completed(futs):
            res = fut.result()
            done += 1
            if res is None:
                continue
            code, cols = res
            if close_mat is None:
                close_mat = np.full((n_days, len(codes)), np.nan, dtype=np.float32)
            code_list.append((code, cols["close"]))
            if done % 800 == 0:
                print(f"  {done}/{len(codes)}  {time.time()-t0:.1f}s", flush=True)

    # 用完整目录名（sh600000）作唯一列名，避免 sh/sz 前缀剥掉后撞车
    cmap = dict(code_list)  # key = dirname
    col_names = [n for n in codes if n in cmap]
    ordered = [cmap[n] for n in col_names]
    mat = np.column_stack(ordered) if ordered else np.zeros((n_days, 0), dtype=np.float32)
    close_df = pd.DataFrame(mat, index=pd.Index(pd.to_datetime(days), name="date"), columns=col_names)
    close_df = close_df.dropna(axis=1, how="all")
    close_path = CACHE / "close_wide.parquet"
    close_df.to_parquet(close_path, compression="zstd")
    print(f"saved {close_path} shape={close_df.shape} {close_df.index.min()}..{close_df.index.max()} "
          f"{close_path.stat().st_size/1e6:.1f}MB  {time.time()-t0:.1f}s", flush=True)

    pct = close_df.pct_change() * 100
    pct_path = CACHE / "pct_wide.parquet"
    pct.to_parquet(pct_path, compression="zstd")
    print(f"saved {pct_path}", flush=True)
    return close_df


def export_last_day_ohlc(close_df: pd.DataFrame, top_n: int = 800, include_bj: bool = False):
    """对最近一日 |pct| 最大的若干票导出 OHLC，供涨停/炸板判定"""
    if close_df is None or close_df.empty:
        return
    last = close_df.index[-1]
    prev = close_df.index[-2] if len(close_df) > 1 else None
    if prev is None:
        return
    chg = (close_df.loc[last] / close_df.loc[prev] - 1) * 100
    chg = chg.dropna()
    # 取绝对涨幅最大的 top_n
    pick = chg.reindex(chg.abs().sort_values(ascending=False).index)[:top_n]
    print(f"OHLC enrich {len(pick)} stocks on {last.date()} (top |pct|)", flush=True)

    day_str = last.strftime("%Y-%m-%d")
    # 需要日历索引
    days = load_calendar()
    # last 在 days 中的位置
    idx = None
    for i, d in enumerate(days):
        if d == day_str:
            idx = i
            break
    if idx is None:
        idx = len(days) - 1

    rows = []
    def fetch(dirname: str):
        d = FEATURES / dirname
        if not d.is_dir():
            return None
        code6 = dirname[2:] if dirname[:2] in ("sh", "sz", "bj", "SH", "SZ", "BJ") else dirname
        rec = {"code": code6, "dir": dirname, "date": day_str, "pct": float(pick[dirname])}
        for f in ("open", "high", "low", "close", "volume", "amount"):
            p = d / f"{f}.day.bin"
            if p.exists():
                arr = read_bin(p)
                rec[f] = float(arr[idx]) if idx < len(arr) else np.nan
        return rec

    with ThreadPoolExecutor(max_workers=16) as ex:
        for r in ex.map(fetch, list(pick.index)):
            if r:
                rows.append(r)
    df = pd.DataFrame(rows)
    path = CACHE / f"snap_{last.strftime('%Y%m%d')}.parquet"
    df.to_parquet(path, index=False, compression="zstd")
    print(f"saved {path} rows={len(df)}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-days", type=int, default=320)
    ap.add_argument("--include-bj", action="store_true")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--skip-ohlc", action="store_true")
    args = ap.parse_args()
    close_df = export_closes(args.limit_days, args.include_bj, args.workers)
    if not args.skip_ohlc:
        export_last_day_ohlc(close_df, top_n=1000, include_bj=args.include_bj)


if __name__ == "__main__":
    main()
