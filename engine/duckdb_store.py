# -*- coding: utf-8 -*-
"""
DuckDB 查询层：直接查 data/cache 下的 Parquet，供每日复盘使用。
不建独立 .duckdb 文件，保持「文件即库」。
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
CLOSE_WIDE = CACHE / "close_wide.parquet"
PCT_WIDE = CACHE / "pct_wide.parquet"


def _con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:")


def _esc(p: Path) -> str:
    return str(p).replace("\\", "/")


def last_trade_day() -> str | None:
    if not CLOSE_WIDE.exists():
        return None
    con = _con()
    try:
        r = con.execute(
            f"SELECT max(date) FROM read_parquet('{_esc(CLOSE_WIDE)}')"
        ).fetchone()
        return r[0].strftime("%Y-%m-%d") if r and r[0] else None
    finally:
        con.close()


def calendar_tail(n: int = 20) -> list[str]:
    if not CLOSE_WIDE.exists():
        return []
    con = _con()
    try:
        df = con.execute(
            f"SELECT DISTINCT date FROM read_parquet('{_esc(CLOSE_WIDE)}') ORDER BY date DESC LIMIT {int(n)}"
        ).fetchdf()
        return [d.strftime("%Y-%m-%d") for d in df["date"]]
    finally:
        con.close()


def market_breadth(date: str | None = None) -> dict:
    """涨跌家数 / 红盘率（基于 close 宽表 pct）"""
    if not PCT_WIDE.exists():
        return {}
    con = _con()
    try:
        if date is None:
            date = last_trade_day()
        if not date:
            return {}
        # 宽表：一行一日期，列=股票；用 unpivot 后聚合
        df = con.execute(
            f"""
            SELECT * FROM read_parquet('{_esc(PCT_WIDE)}')
            WHERE date = DATE '{date}'
            """
        ).fetchdf()
        if df.empty:
            return {}
        row = df.drop(columns=["date"]).iloc[0]
        vals = pd.to_numeric(row, errors="coerce").dropna()
        up = int((vals > 0).sum())
        down = int((vals < 0).sum())
        flat = int((vals == 0).sum())
        denom = up + down + flat
        return {
            "date": date,
            "up": up,
            "down": down,
            "flat": flat,
            "total": denom,
            "red_pct": round(up * 100.0 / denom, 1) if denom else None,
        }
    finally:
        con.close()


def limit_pct(code: str) -> float:
    c = str(code)[-6:] if len(str(code)) >= 6 else str(code)
    if c.startswith("30") or c.startswith("68"):
        return 19.8
    if c.startswith(("4", "8", "92")):
        return 29.8
    return 9.8


def limit_up_list(date: str | None = None, snap_path: Path | None = None) -> pd.DataFrame:
    """
    从最近截面 OHLC 判涨停/炸板。
    优先读 data/cache/snap_YYYYMMDD.parquet；否则退化为仅用涨幅阈值。
    """
    if date is None:
        date = last_trade_day()
    if not date:
        return pd.DataFrame()
    ymd = date.replace("-", "")
    path = snap_path or (CACHE / f"snap_{ymd}.parquet")
    if not path.exists():
        # 退化：用 pct 宽表挑接近涨停的
        if not PCT_WIDE.exists():
            return pd.DataFrame()
        con = _con()
        try:
            df = con.execute(
                f"SELECT * FROM read_parquet('{_esc(PCT_WIDE)}') WHERE date = DATE '{date}'"
            ).fetchdf()
        finally:
            con.close()
        if df.empty:
            return pd.DataFrame()
        row = df.drop(columns=["date"]).iloc[0]
        items = []
        for col, pct in row.items():
            if pd.isna(pct):
                continue
            lim = limit_pct(col)
            if pct >= lim - 0.2:
                items.append({"dir": col, "code": col[-6:], "pct": float(pct), "limit_pct": lim, "is_zt": True, "is_zb": False})
            elif lim - 2.8 <= pct < lim - 0.2:
                items.append({"dir": col, "code": col[-6:], "pct": float(pct), "limit_pct": lim, "is_zt": False, "is_zb": True})
        return pd.DataFrame(items)

    snap = pd.read_parquet(path)
    if snap.empty:
        return snap
    snap = snap.copy()
    snap["limit_pct"] = snap["code"].astype(str).map(limit_pct)
    high = snap.get("high")
    close = snap["close"]
    prev = None
    if "prev_close" in snap.columns:
        prev = snap["prev_close"]
    elif "pct" in snap.columns:
        # prev = close / (1+pct/100)
        prev = close / (1 + snap["pct"] / 100.0)

    high_ok = high.notna() if high is not None else pd.Series(False, index=snap.index)
    if high is not None and prev is not None:
        seal = close >= high * 0.998
        snap["is_zt"] = (snap["pct"] >= snap["limit_pct"] - 0.15) & (seal | ~high_ok)
        touch = (high / prev - 1) * 100
        snap["is_zb"] = (
            high_ok
            & (snap["pct"] < snap["limit_pct"] - 0.15)
            & (snap["pct"] >= snap["limit_pct"] - 2.8)
            & (close < high * 0.995)
            & (touch >= snap["limit_pct"] - 0.5)
        )
    else:
        snap["is_zt"] = snap["pct"] >= snap["limit_pct"] - 0.15
        snap["is_zb"] = False
    return snap


def ladder_from_zt(zt: pd.DataFrame) -> dict:
    """
    粗连板：仅用单日涨停无法还原连板数；返回当日涨停家数。
    连板需多日滚动，见 ladder_roll。
    """
    if zt is None or zt.empty:
        return {"first": 0, "second": 0, "third": 0, "top": 0, "topName": "", "topBoards": 0, "highs": []}
    n = int(zt["is_zt"].sum()) if "is_zt" in zt.columns else len(zt)
    return {"first": n, "second": 0, "third": 0, "top": 0, "topName": "", "topBoards": 0, "highs": [], "note": "单日截面，连板需 ladder_roll"}


def ladder_roll(end_date: str | None = None, max_lookback: int = 15) -> dict:
    """
    用 pct 宽表滚动还原连板：
    某日涨停且此前连续涨停 → lbc。
    简化：pct >= limit-0.2 视为涨停（无 high 判定，略宽）。
    """
    if not PCT_WIDE.exists():
        return {"first": 0, "second": 0, "third": 0, "top": 0, "topName": "", "topBoards": 0, "highs": []}
    end = end_date or last_trade_day()
    if not end:
        return {"first": 0, "second": 0, "third": 0, "top": 0, "topName": "", "topBoards": 0, "highs": []}
    con = _con()
    try:
        df = con.execute(
            f"""
            SELECT * FROM read_parquet('{_esc(PCT_WIDE)}')
            ORDER BY date
            """
        ).fetchdf()
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"] <= pd.to_datetime(end)].tail(max_lookback + 2)
    if len(df) < 2:
        return {"first": 0, "second": 0, "third": 0, "top": 0, "topName": "", "topBoards": 0, "highs": []}

    stock_cols = [c for c in df.columns if c != "date"]
    lim = {c: limit_pct(c) for c in stock_cols}
    # 涨停布尔矩阵
    zt_mat = pd.DataFrame(index=df.index, columns=stock_cols, dtype=bool)
    for c in stock_cols:
        zt_mat[c] = df[c] >= (lim[c] - 0.2)

    # 最后一日连板：从末尾往前数连续 True
    last_i = len(df) - 1
    lbc = {}
    for c in stock_cols:
        if not zt_mat.iloc[last_i][c]:
            continue
        k = 1
        i = last_i - 1
        while i >= 0 and zt_mat.iloc[i][c]:
            k += 1
            i -= 1
        lbc[c] = k

    first = sum(1 for v in lbc.values() if v == 1)
    second = sum(1 for v in lbc.values() if v == 2)
    third = sum(1 for v in lbc.values() if v == 3)
    highs = sorted(
        [{"name": c, "code": c[-6:], "lbc": v} for c, v in lbc.items() if v >= 4],
        key=lambda x: -x["lbc"],
    )
    top = highs[0] if highs else None
    return {
        "first": first,
        "second": second,
        "third": third,
        "top": 1 if top else 0,
        "topName": top["name"] if top else "",
        "topBoards": top["lbc"] if top else 0,
        "highs": highs[:8],
        "zt_total": len(lbc),
        "date": end,
    }


def promotion_rate(end_date: str | None = None, min_lbc: int = 2) -> dict:
    """昨日 lbc>=min_lbc 的股票，今日是否仍涨停"""
    if not PCT_WIDE.exists():
        return {"rate": 0.0, "num": 0, "den": 0, "names": []}
    end = end_date or last_trade_day()
    con = _con()
    try:
        df = con.execute(
            f"SELECT * FROM read_parquet('{_esc(PCT_WIDE)}') ORDER BY date"
        ).fetchdf()
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"] <= pd.to_datetime(end)].tail(20)
    if len(df) < 2:
        return {"rate": 0.0, "num": 0, "den": 0, "names": []}
    stock_cols = [c for c in df.columns if c != "date"]
    lim = {c: limit_pct(c) for c in stock_cols}
    zt_mat = pd.DataFrame(index=df.index, columns=stock_cols, dtype=bool)
    for c in stock_cols:
        zt_mat[c] = df[c] >= (lim[c] - 0.2)

    # 昨日连板
    y_i, t_i = len(df) - 2, len(df) - 1
    cand = []
    for c in stock_cols:
        if not zt_mat.iloc[y_i][c]:
            continue
        k = 1
        i = y_i - 1
        while i >= 0 and zt_mat.iloc[i][c]:
            k += 1
            i -= 1
        if k >= min_lbc:
            cand.append((c, k))
    promoted = [c for c, _ in cand if zt_mat.iloc[t_i][c]]
    names = promoted[:]
    n, d = len(promoted), len(cand)
    return {
        "rate": round(n * 100.0 / d, 1) if d else 0.0,
        "num": n,
        "den": d,
        "names": names,
        "candidates": [{"name": c, "yest_lbc": k} for c, k in cand[:20]],
    }


def stock_window(code_or_dir: str, end_date: str | None = None, n: int = 60) -> pd.DataFrame:
    """取单票收盘序列（列名可能是 sh600000 或 600000）"""
    if not CLOSE_WIDE.exists():
        return pd.DataFrame()
    con = _con()
    try:
        df = con.execute(
            f"SELECT * FROM read_parquet('{_esc(CLOSE_WIDE)}') ORDER BY date"
        ).fetchdf()
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"])
    if end_date:
        df = df[df["date"] <= pd.to_datetime(end_date)]
    df = df.tail(n)
    col = None
    for c in df.columns:
        if c == code_or_dir or c.endswith(code_or_dir[-6:]):
            col = c
            break
    if col is None:
        return pd.DataFrame()
    out = df[["date", col]].rename(columns={col: "close"}).reset_index(drop=True)
    return out


if __name__ == "__main__":
    print("last", last_trade_day())
    print("calendar", calendar_tail(5))
    print("breadth", market_breadth())
    lad = ladder_roll()
    print("ladder", {k: lad[k] for k in ("first", "second", "third", "topBoards", "zt_total", "date")})
    print("promo", promotion_rate())
