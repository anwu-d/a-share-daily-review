# -*- coding: utf-8 -*-
"""
本地 qlib_bin 读取器（不依赖 pyqlib）
数据来自 https://github.com/chenditc/investment_data release
路径: data/qlib_cn/{calendars,instruments,features}
"""
from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
QLIB_DIR = ROOT / "data" / "qlib_cn"
FEATURES = QLIB_DIR / "features"
CALENDAR = QLIB_DIR / "calendars" / "day.txt"
INSTR_ALL = QLIB_DIR / "instruments" / "all.txt"

_FIELDS = ("open", "high", "low", "close", "volume", "amount", "factor", "adjclose", "change", "vwap")


def _load_calendar() -> list[str]:
    if not CALENDAR.exists():
        return []
    return [ln.strip() for ln in CALENDAR.read_text(encoding="utf-8").splitlines() if ln.strip()]


def calendar_dates() -> list[str]:
    return _load_calendar()


def last_n_trade_days(n: int = 5) -> list[str]:
    days = _load_calendar()
    return days[-n:] if days else []


def trade_day_index(date: str) -> int:
    days = _load_calendar()
    # date: YYYY-MM-DD or YYYYMMDD
    d = date.replace("-", "")
    for i, day in enumerate(days):
        if day.replace("-", "") == d:
            return i
    # 找最后一个 <= date
    idx = -1
    for i, day in enumerate(days):
        if day.replace("-", "") <= d:
            idx = i
        else:
            break
    return idx


def load_instruments() -> pd.DataFrame:
    """all.txt: code\tstart\tend"""
    if not INSTR_ALL.exists():
        return pd.DataFrame(columns=["code", "start", "end"])
    rows = []
    for ln in INSTR_ALL.read_text(encoding="utf-8").splitlines():
        parts = ln.strip().split("\t")
        if len(parts) >= 3:
            rows.append({"code": parts[0], "start": parts[1], "end": parts[2]})
        elif len(parts) == 1:
            rows.append({"code": parts[0], "start": None, "end": None})
    return pd.DataFrame(rows)


def instrument_codes(include_bj: bool = True) -> list[str]:
    """返回如 ['SH600000','SZ000001',...] 或 qlib 原始目录名"""
    if not FEATURES.exists():
        return []
    codes = [p.name for p in FEATURES.iterdir() if p.is_dir()]
    if not include_bj:
        codes = [c for c in codes if not c.upper().startswith("BJ")]
    return sorted(codes)


def code_to_dir(code: str) -> str:
    """000001 / 600000 / SH600000 → qlib 目录名"""
    c = code.strip().upper().replace(".XSHE", "").replace(".XSHG", "")
    if c.startswith(("SH", "SZ", "BJ")):
        return c
    c = c.zfill(6)
    if c.startswith(("5", "6", "9")):
        return "SH" + c
    if c.startswith(("4", "8")):
        return "BJ" + c
    return "SZ" + c


def dir_to_code6(dirname: str) -> str:
    """SH600000 → 600000"""
    d = dirname.upper()
    if d.startswith(("SH", "SZ", "BJ")):
        return d[2:]
    return d


def _read_bin(path: Path) -> np.ndarray:
    """qlib .day.bin: [start_index float32][float32 * N]，按日历左对齐。"""
    try:
        data = path.read_bytes()
    except Exception:
        return np.array([], dtype=np.float32)
    if len(data) < 8:
        return np.array([], dtype=np.float32)
    start_f = struct.unpack("<f", data[:4])[0]
    start = int(round(start_f)) if start_f == start_f else 0
    if start < 0 or start > 20000:
        start = 0
    payload = data[4:]
    n_float = len(payload) // 4
    arr = np.frombuffer(payload[: n_float * 4], dtype=np.float32)
    if start > 0:
        arr = np.concatenate([np.full(start, np.nan, dtype=np.float32), arr])
    return arr


def load_stock(dirname: str, fields: tuple[str, ...] = ("open", "high", "low", "close", "volume", "amount", "change", "factor", "adjclose")) -> pd.DataFrame | None:
    d = FEATURES / dirname
    if not d.is_dir():
        return None
    days = _load_calendar()
    series = {}
    for f in fields:
        p = d / f"{f}.day.bin"
        if p.exists():
            series[f] = _read_bin(p)
    if not series:
        return None
    n = max(len(v) for v in series.values())
    for k, v in series.items():
        if len(v) < n:
            series[k] = np.concatenate([v, np.full(n - len(v), np.nan, dtype=np.float32)])
    m = min(n, len(days))
    df = pd.DataFrame({k: v[:m] for k, v in series.items()})
    df.index = pd.Index(days[:m], name="date")
    return df


def load_stock_window(dirname: str, end_date: str, lookback: int = 60, fields: tuple[str, ...] | None = None) -> pd.DataFrame | None:
    df = load_stock(dirname, fields=fields) if fields else load_stock(dirname)
    if df is None or df.empty:
        return None
    # 索引是 YYYY-MM-DD
    end = end_date.replace("/", "-")
    if end not in df.index:
        # 取 <= end 的最后一行
        sub = df[df.index <= end]
        if sub.empty:
            return None
        end = sub.index[-1]
    loc = df.index.get_loc(end)
    if isinstance(loc, slice):
        loc = loc.stop - 1
    start = max(0, int(loc) - lookback + 1)
    return df.iloc[start : int(loc) + 1].copy()


def snapshot_on(date: str, include_bj: bool = False) -> pd.DataFrame:
    """
    快速全市场截面：
    1) 只读 close 扫涨跌幅
    2) 仅对 |pct| 接近涨跌停或涨幅>5% 的补 OHLC/成交
    """
    idx = trade_day_index(date)
    if idx < 0:
        return pd.DataFrame()
    days = _load_calendar()
    if idx >= len(days):
        return pd.DataFrame()
    day = days[idx]
    codes = instrument_codes(include_bj=include_bj)
    if not codes:
        return pd.DataFrame()

    closes = np.full(len(codes), np.nan, dtype=np.float64)
    prevs = np.full(len(codes), np.nan, dtype=np.float64)
    for i, name in enumerate(codes):
        p = FEATURES / name / "close.day.bin"
        if not p.exists():
            continue
        try:
            arr = _read_bin(p)
            if idx < len(arr):
                closes[i] = float(arr[idx])
            if idx > 0 and idx - 1 < len(arr):
                prevs[i] = float(arr[idx - 1])
        except Exception:
            continue

    with np.errstate(divide="ignore", invalid="ignore"):
        pct = (closes / prevs - 1.0) * 100.0
    pct = np.where(np.isfinite(pct), np.round(pct, 2), np.nan)

    base = pd.DataFrame(
        {
            "code": [dir_to_code6(n) for n in codes],
            "dir": codes,
            "date": day,
            "close": closes,
            "prev_close": prevs,
            "pct": pct,
        }
    )
    base = base.dropna(subset=["close"]).reset_index(drop=True)
    for col in ("open", "high", "low", "volume", "amount"):
        base[col] = np.nan

    def _limit_of(c: str) -> float:
        if c.startswith("30") or c.startswith("68"):
            return 19.8
        if c.startswith(("4", "8", "92")):
            return 29.8
        return 9.8

    lim = base["code"].astype(str).map(_limit_of).to_numpy(dtype=np.float64)
    pctv = base["pct"].to_numpy(dtype=np.float64)
    need = (
        (pctv >= lim - 3.0)
        | (pctv <= -lim + 3.0)
        | (pctv >= 5.0)
    )
    for i in np.where(np.nan_to_num(need))[0]:
        name = base.at[i, "dir"]
        for col in ("open", "high", "low", "volume", "amount"):
            p = FEATURES / name / f"{col}.day.bin"
            if p.exists():
                try:
                    arr = _read_bin(p)
                    if idx < len(arr):
                        base.at[i, col] = float(arr[idx])
                except Exception:
                    pass
    return base


def limit_up_detect(
    snap: pd.DataFrame,
    pct_tol: float = 0.15,
) -> pd.DataFrame:
    """
    近似涨停：涨幅 >= 9.8%（主板）/ 19.8%（创业板/科创板）/ 29.8%（北交）
    收盘接近当日最高（封板质量粗判）
    """
    if snap is None or snap.empty:
        return snap
    df = snap.copy()
    code = df["code"].astype(str)

    def _limit_pct(c: str) -> float:
        if c.startswith("30") or c.startswith("68"):
            return 19.8
        if c.startswith(("4", "8", "92")):
            return 29.8
        return 9.8

    df["limit_pct"] = code.map(_limit_pct)
    high = df["high"]
    # high 可能未补全（nan）→ 仅用涨幅判定
    high_ok = high.notna()
    seal = df["close"] >= high * 0.998
    df["is_zt"] = (df["pct"] >= df["limit_pct"] - pct_tol) & (seal | ~high_ok)
    # 炸板近似：盘中接近涨停但收盘未封
    prev = df["prev_close"]
    touch = (high / prev - 1) * 100
    df["is_zb"] = (
        high_ok
        & (df["pct"] < df["limit_pct"] - pct_tol)
        & (df["pct"] >= df["limit_pct"] - 2.8)
        & (df["close"] < high * 0.995)
        & (touch >= df["limit_pct"] - 0.5)
    )
    return df


def market_stats_on(date: str, include_bj: bool = False) -> dict:
    """本地全市场：涨跌家数、涨停/跌停家数、成交额合计（亿）"""
    snap = snapshot_on(date, include_bj=include_bj)
    if snap is None or snap.empty:
        return {}
    snap = limit_up_detect(snap)
    up = int((snap["pct"] > 0).sum())
    down = int((snap["pct"] < 0).sum())
    flat = int((snap["pct"] == 0).sum())
    # 跌停近似
    def _limit_pct(c: str) -> float:
        if str(c).startswith("30") or str(c).startswith("68"):
            return -19.8
        if str(c).startswith(("4", "8", "92")):
            return -29.8
        return -9.8
    lim = snap["code"].map(_limit_pct)
    dt_n = int((snap["pct"] <= lim + 0.15).sum())
    zt_n = int(snap["is_zt"].sum())
    zb_n = int(snap["is_zb"].sum())
    denom = up + down + flat
    return {
        "date": date,
        "up": up,
        "down": down,
        "flat": flat,
        "total": denom,
        "red_pct": round(up * 100.0 / denom, 1) if denom else None,
        "zt": zt_n,
        "dt": dt_n,
        "zb": zb_n,
        "amount_yi": round(float(np.nansum(snap["amount"].values)) / 1e8, 2),
        "snap": snap,
    }


if __name__ == "__main__":
    days = calendar_dates()
    print("calendar", len(days), "first", days[0] if days else None, "last", days[-1] if days else None)
    print("instruments", len(instrument_codes(include_bj=True)))
    last = days[-1] if days else None
    if last:
        st = market_stats_on(last, include_bj=False)
        print("stats", {k: v for k, v in st.items() if k != "snap"})
