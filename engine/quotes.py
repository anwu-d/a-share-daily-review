# -*- coding: utf-8 -*-
"""K线与技术指标：Ashare 行情 + MyTT 指标"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for sub in ("MyTT", "Ashare"):
    p = str(ROOT / "vendor" / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from Ashare import get_price  # noqa: E402
except Exception:
    get_price = None

try:
    from MyTT import MA, EMA, MACD, RSI, BOLL, ATR, REF, HHV, LLV  # noqa: E402
except Exception:
    MA = EMA = MACD = RSI = BOLL = ATR = REF = HHV = LLV = None


def ashare_code(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith(("6", "9", "5")):
        return f"sh{c}"
    return f"sz{c}"


def daily_kline(code: str, count: int = 80) -> pd.DataFrame | None:
    """返回日线 DataFrame（index=date, open/close/high/low/volume）"""
    if get_price is None:
        return None
    try:
        df = get_price(ashare_code(code), count=count, frequency="1d")
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


def tech_snapshot(df: pd.DataFrame) -> dict:
    """用 MyTT 计算关键均线/动量，返回末值快照。"""
    if df is None or df.empty or MA is None:
        return {}
    c = df["close"].astype(float).values
    h = df["high"].astype(float).values
    l = df["low"].astype(float).values
    out = {}
    try:
        ma5, ma10, ma20 = MA(c, 5), MA(c, 10), MA(c, 20)
        out.update(
            {
                "close": float(c[-1]),
                "ma5": float(ma5[-1]) if len(ma5) else None,
                "ma10": float(ma10[-1]) if len(ma10) else None,
                "ma20": float(ma20[-1]) if len(ma20) else None,
                "pct_5d": round((c[-1] / c[-6] - 1) * 100, 2) if len(c) >= 6 else None,
                "pct_20d": round((c[-1] / c[-21] - 1) * 100, 2) if len(c) >= 21 else None,
            }
        )
        if ATR is not None and len(c) >= 15:
            out["atr14"] = float(ATR(h, l, c, 14)[-1])
        if MACD is not None and len(c) >= 35:
            dif, dea, hist = MACD(c, 12, 26, 9)
            out["macd_dif"] = float(dif[-1])
            out["macd_dea"] = float(dea[-1])
            out["macd_hist"] = float(hist[-1])
        if RSI is not None and len(c) >= 15:
            out["rsi6"] = float(RSI(c, 6)[-1])
            out["rsi14"] = float(RSI(c, 14)[-1])
    except Exception as e:
        out["error"] = str(e)
    return out


def index_kline(sh_code: str = "sh000001", count: int = 60) -> pd.DataFrame | None:
    return daily_kline(sh_code[2:] if sh_code.startswith("sh") else sh_code, count)
