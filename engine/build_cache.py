# -*- coding: utf-8 -*-
"""
构建/更新本地行情缓存：
  data/cache/close_matrix.parquet  — 全市场收盘价宽表 (index=date, columns=code)
  data/cache/snap_YYYYMMDD.parquet — 单日截面（含 OHLC，按需）

用法:
  python build_cache.py            # 全量构建（首次较慢）
  python build_cache.py --update   # 只补最后若干交易日
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
sys.path.insert(0, str(HERE))
from qlib_local import (  # noqa: E402
    CALENDAR,
    FEATURES,
    QLIB_DIR,
    _load_calendar,
    dir_to_code6,
    instrument_codes,
)

CACHE = QLIB_DIR.parent / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
CLOSE_MATRIX = CACHE / "close_matrix.parquet"
META_JSON = CACHE / "meta.json"


def _read_bin(path: Path) -> np.ndarray:
    data = path.read_bytes()
    if len(data) < 8:
        return np.array([], dtype=np.float32)
    _start, count = struct.unpack("<II", data[:8])
    arr = np.frombuffer(data[8 : 8 + count * 4], dtype=np.float32)
    if _start > 0:
        arr = np.concatenate([np.full(_start, np.nan, dtype=np.float32), arr])
    return arr


def build_close_matrix(include_bj: bool = False, progress: bool = True) -> pd.DataFrame:
    days = _load_calendar()
    codes = instrument_codes(include_bj=include_bj)
    cols = {dir_to_code6(c): c for c in codes}
    n = len(days)
    data = np.full((n, len(cols)), np.nan, dtype=np.float32)
    names = list(cols.keys())
    t0 = time.time()
    for j, code6 in enumerate(names):
        p = FEATURES / cols[code6] / "close.day.bin"
        if p.exists():
            try:
                arr = _read_bin(p)
                m = min(n, len(arr))
                data[:m, j] = arr[:m]
            except Exception:
                pass
        if progress and (j + 1) % 500 == 0:
            print(f"  close {j+1}/{len(names)}  {time.time()-t0:.1f}s")
    df = pd.DataFrame(data, index=pd.Index(days, name="date"), columns=names)
    return df


def build_ohlc_for_date(date: str, close_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """补全指定日截面 OHLC（仅涨跌幅靠前/靠后的票，控制 IO）"""
    from qlib_local import trade_day_index, snapshot_on

    snap = snapshot_on(date, include_bj=False)
    return snap


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="若缓存存在则只追加新日期")
    ap.add_argument("--include-bj", action="store_true")
    args = ap.parse_args()

    days = _load_calendar()
    print(f"calendar {len(days)} days, last={days[-1] if days else None}")

    if args.update and CLOSE_MATRIX.exists():
        old = pd.read_parquet(CLOSE_MATRIX)
        old_days = set(old.index.astype(str))
        new_days = [d for d in days if d not in old_days]
        if not new_days:
            print("close matrix 已是最新")
            return
        print(f"增量更新 {len(new_days)} 天: {new_days[0]} .. {new_days[-1]}")
        # 简单起见：全量重建（本地 IO 通常 1-3 分钟）
        df = build_close_matrix(include_bj=args.include_bj)
    else:
        print("全量构建 close matrix ...")
        t0 = time.time()
        df = build_close_matrix(include_bj=args.include_bj)
        print(f"done in {time.time()-t0:.1f}s  shape={df.shape}")

    CLOSE_MATRIX.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CLOSE_MATRIX)
    print(f"saved {CLOSE_MATRIX}  {CLOSE_MATRIX.stat().st_size/1e6:.1f} MB")
    # 预热最后一日截面
    if days:
        last = days[-1]
        print(f"build snap {last} ...")
        snap = build_ohlc_for_date(last, df)
        snap.to_parquet(CACHE / f"snap_{last.replace('-','')}.parquet")
        print(f"saved snap rows={len(snap)}")


if __name__ == "__main__":
    main()
