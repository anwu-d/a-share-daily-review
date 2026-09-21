# -*- coding: utf-8 -*-
"""
从 qlib_bin 导出 Parquet（供 DuckDB 查询）
  data/cache/daily_eod.parquet   长表: date,code,open,high,low,close,volume,amount,adjclose,factor
  data/cache/instruments.parquet 代码表
  data/cache/calendar.parquet    交易日历

用法:
  python export_parquet.py
  python export_parquet.py --limit-days 250   # 只导出最近 N 日（快）
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from qlib_local import FEATURES, CALENDAR, INSTR_ALL, instrument_codes, dir_to_code6  # noqa

CACHE = ROOT / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

FIELDS = ("open", "high", "low", "close", "volume", "amount", "adjclose", "factor")


def read_bin(path: Path) -> np.ndarray:
    data = path.read_bytes()
    if len(data) < 8:
        return np.array([], dtype=np.float32)
    start, count = struct.unpack("<II", data[:8])
    arr = np.frombuffer(data[8 : 8 + count * 4], dtype=np.float32)
    if start > 0:
        arr = np.concatenate([np.full(start, np.nan, dtype=np.float32), arr])
    return arr


def load_calendar() -> list[str]:
    return [ln.strip() for ln in CALENDAR.read_text(encoding="utf-8").splitlines() if ln.strip()]


def export(limit_days: int | None = None, include_bj: bool = False, batch: int = 200):
    days = load_calendar()
    if limit_days and limit_days < len(days):
        start_idx = len(days) - limit_days
        days = days[start_idx:]
    else:
        start_idx = 0

    codes = instrument_codes(include_bj=include_bj)
    print(f"export {len(codes)} stocks × {len(days)} days (from idx {start_idx})")
    t0 = time.time()

    # 预读全部需要的字段：按股票分批写 parquet 行
    out_path = CACHE / "daily_eod.parquet"
    # 用 dataset 追加：先写多个 part 再合并
    parts_dir = CACHE / "daily_parts"
    parts_dir.mkdir(exist_ok=True)
    for old in parts_dir.glob("*.parquet"):
        old.unlink()

    buf_rows = []
    part_id = 0
    for i, name in enumerate(codes):
        code = dir_to_code6(name)
        d = FEATURES / name
        series = {}
        for f in FIELDS:
            p = d / f"{f}.day.bin"
            if p.exists():
                arr = read_bin(p)
                if start_idx:
                    arr = arr[start_idx:] if len(arr) >= start_idx else np.array([], dtype=np.float32)
                series[f] = arr
        if "close" not in series or len(series["close"]) == 0:
            continue
        n = min(len(days), max(len(v) for v in series.values()))
        if n == 0:
            continue
        date_col = days[:n]
        data = {"date": date_col, "code": code}
        for f, arr in series.items():
            m = min(n, len(arr))
            col = np.full(n, np.nan, dtype=np.float32)
            col[:m] = arr[:m]
            data[f] = col
        df = pd.DataFrame(data)
        # drop rows where close is nan
        df = df.dropna(subset=["close"])
        if not df.empty:
            buf_rows.append(df)

        if (i + 1) % batch == 0 or i + 1 == len(codes):
            if buf_rows:
                big = pd.concat(buf_rows, ignore_index=True)
                part = parts_dir / f"part_{part_id:04d}.parquet"
                big.to_parquet(part, index=False, compression="zstd")
                part_id += 1
                buf_rows = []
            print(f"  {i+1}/{len(codes)}  parts={part_id}  {time.time()-t0:.1f}s")

    # merge parts
    parts = sorted(parts_dir.glob("part_*.parquet"))
    if not parts:
        print("no data")
        return
    print(f"merging {len(parts)} parts ...")
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "code"]).reset_index(drop=True)
    df.to_parquet(out_path, index=False, compression="zstd")
    print(f"saved {out_path}  rows={len(df):,}  size={out_path.stat().st_size/1e6:.1f}MB  {time.time()-t0:.1f}s")

    # instruments
    inst = pd.DataFrame({"code": codes})
    inst.to_parquet(CACHE / "instruments.parquet", index=False)
    # calendar
    cal = pd.DataFrame({"date": pd.to_datetime(days)})
    cal.to_parquet(CACHE / "calendar.parquet", index=False)
    print("instruments/calendar saved")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-days", type=int, default=None)
    ap.add_argument("--include-bj", action="store_true")
    ap.add_argument("--batch", type=int, default=200)
    args = ap.parse_args()
    export(limit_days=args.limit_days, include_bj=args.include_bj, batch=args.batch)
