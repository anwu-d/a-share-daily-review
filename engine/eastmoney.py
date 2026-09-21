# -*- coding: utf-8 -*-
"""东方财富公开接口：涨停池 / 跌停池 / 炸板池 / 市场概况 / 个股资金流 / 龙虎榜"""
from __future__ import annotations

import json
import time
from typing import Any

import requests

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

_session = requests.Session()
_session.headers.update(UA)
# 关闭不稳定的连接复用，避免 SSL handshake 偶发失败
_session.headers.setdefault("Connection", "close")


def _get(url: str, params: dict | None = None, timeout: int = 12) -> Any:
    last_err: Exception | None = None
    hosts = [url]
    if url.startswith("https://"):
        hosts.append(url.replace("https://", "http://", 1))
        # 换备用域名
        hosts.append(url.replace("https://push2.eastmoney.com", "https://push2delay.eastmoney.com"))
        hosts.append(url.replace("https://push2ex.eastmoney.com", "https://push2ex.eastmoney.com"))
    for attempt in range(4):
        for u in hosts:
            try:
                r = _session.get(u, params=params, timeout=timeout)
                if r.status_code >= 500:
                    last_err = RuntimeError(f"HTTP {r.status_code} {u}")
                    continue
                r.raise_for_status()
                text = r.text.strip()
                if not text:
                    last_err = RuntimeError(f"empty body {u}")
                    continue
                if text.startswith("jQuery") or text.startswith("callback"):
                    text = text[text.find("(") + 1 : text.rfind(")")]
                return json.loads(text)
            except Exception as e:
                last_err = e
                continue
        time.sleep(0.4 * (attempt + 1))
    raise last_err if last_err else RuntimeError("eastmoney fetch failed")


def _date8(date_str: str) -> str:
    return date_str.replace("-", "")


# ── 涨停池 ──────────────────────────────────────────────
def zt_pool(date: str) -> list[dict]:
    """date: YYYY-MM-DD"""
    url = "https://push2ex.eastmoney.com/getTopicZTPool"
    params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "dpt": "wz.ztzt",
        "Pageindex": 0,
        "pagesize": 500,
        "sort": "fbt:asc",
        "date": _date8(date),
    }
    data = _get(url, params)
    pool = (data.get("data") or {}).get("pool") or []
    out = []
    for it in pool:
        out.append(
            {
                "code": str(it.get("c", "")).zfill(6),
                "name": it.get("n", ""),
                "price": it.get("p", 0) / 1000 if it.get("p") else 0,
                "amount": it.get("amount", 0) / 1e8 if it.get("amount") else 0,  # 亿
                "lbc": it.get("lbc", 1),  # 连板数
                "fbt": it.get("fbt"),  # 首封时间
                "lbt": it.get("lbt"),
                "hybk": it.get("hybk", ""),  # 行业板块
                "zttj": it.get("zttj") or {},
                "fund": it.get("fund", 0) / 1e4 if it.get("fund") else 0,  # 封单额(亿)
            }
        )
    return out


# ── 跌停池 ──────────────────────────────────────────────
def dt_pool(date: str) -> list[dict]:
    url = "https://push2ex.eastmoney.com/getTopicDTPool"
    params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "dpt": "wz.ztzt",
        "Pageindex": 0,
        "pagesize": 200,
        "sort": "fund:asc",
        "date": _date8(date),
    }
    data = _get(url, params)
    pool = (data.get("data") or {}).get("pool") or []
    return [
        {
            "code": str(it.get("c", "")).zfill(6),
            "name": it.get("n", ""),
            "price": it.get("p", 0) / 1000 if it.get("p") else 0,
            "hybk": it.get("hybk", ""),
        }
        for it in pool
    ]


# ── 炸板池 ──────────────────────────────────────────────
def zb_pool(date: str) -> list[dict]:
    url = "https://push2ex.eastmoney.com/getTopicZBPool"
    params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "dpt": "wz.ztzt",
        "Pageindex": 0,
        "pagesize": 200,
        "sort": "zbc:asc",
        "date": _date8(date),
    }
    data = _get(url, params)
    pool = (data.get("data") or {}).get("pool") or []
    return [
        {
            "code": str(it.get("c", "")).zfill(6),
            "name": it.get("n", ""),
            "price": it.get("p", 0) / 1000 if it.get("p") else 0,
            "hybk": it.get("hybk", ""),
        }
        for it in pool
    ]


# ── 指数行情（沪指等）────────────────────────────────────
def index_quote(secid: str = "1.000001") -> dict:
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "invt": 2,
        "fltt": 2,
        "fields": "f43,f44,f45,f46,f57,f58,f60,f170,f47,f48,f116,f117,f104,f105,f106",
        "secid": secid,
    }
    d = _get(url, params).get("data") or {}

    def _num(v, scale=1.0):
        if v is None or v == "-" or v == "":
            return None
        try:
            return float(v) / scale
        except Exception:
            return None

    def _int(v):
        if v is None or v == "-" or v == "":
            return 0
        try:
            return int(float(v))
        except Exception:
            return 0

    # fltt=2 时价格/涨跌幅已是最终小数；成交额 f48 仍为元
    amount = _num(d.get("f48"), 1e8)
    return {
        "code": d.get("f57"),
        "name": d.get("f58"),
        "close": _num(d.get("f43")),
        "high": _num(d.get("f44")),
        "low": _num(d.get("f45")),
        "open": _num(d.get("f46")),
        "pct": _num(d.get("f170")),
        "volume": d.get("f47"),
        "amount": round(amount, 2) if amount is not None else None,
        "float_mv": _num(d.get("f116"), 1e8),
        "up": _int(d.get("f104")),
        "down": _int(d.get("f105")),
        "flat": _int(d.get("f106")),
    }


# ── 全市场涨跌家数 + 全 A 成交额 ────────────────────────
# 东财 clist 接口每页实际上限为 100，传更大的 pz 会被静默截断成 100。
# 之前按 pz=500 估算页数，导致 5559 只只统计到 1200 只；又因按涨跌幅降序
# 取数，截断出来的恰好是当日最强的全红盘样本 → 红盘率被算成 100%。
PAGE_SIZE = 100


def market_breadth() -> dict:
    """翻页统计沪深全 A 涨跌家数与成交额。失败退回沪市 f104。"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    base = {
        "po": 1,
        "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23",
        "fields": "f3,f6",
    }
    try:
        probe = _get(url, {**base, "pn": 1, "pz": 1}, timeout=12)
        total = int(((probe.get("data") or {}).get("total")) or 0)
        if total <= 0:
            raise RuntimeError("total=0")
        up = down = flat = 0
        amount = 0.0
        got = 0
        page = 1
        pz = PAGE_SIZE
        # 主终止条件是 got < total；页数上限只作安全阀，不按名义 pz 推导，
        # 否则服务端每页回得比 pz 少时会提前停止（原来的 1200/5559 就是这么来的）。
        # 这里按「每页哪怕只回 10 条也要取满」留余量。
        max_page = max(64, total // 10 + 10)
        while page <= max_page and got < total:
            data = _get(url, {**base, "pn": page, "pz": pz}, timeout=20)
            diffs = ((data.get("data") or {}).get("diff")) or []
            if not diffs:
                break
            for it in diffs:
                v = it.get("f3")
                if v is None or v == "-" or v == "":
                    flat += 1
                else:
                    try:
                        fv = float(v)
                    except Exception:
                        fv = 0.0
                    if fv > 0:
                        up += 1
                    elif fv < 0:
                        down += 1
                    else:
                        flat += 1
                try:
                    amount += float(it.get("f6") or 0)
                except Exception:
                    pass
            got += len(diffs)
            page += 1
            time.sleep(0.05)
        denom = up + down + flat
        if denom:
            return {
                "up": up,
                "down": down,
                "flat": flat,
                "total": denom,
                "red_pct": round(up * 100.0 / denom, 1),
                "amount_yi": round(amount / 1e8, 2),
                "fetched": got,
                "complete": got >= total,
            }
    except Exception as e:
        print(f"[warn] 全市场涨跌家数失败: {e}")

    try:
        d = _get(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            {
                "fltt": 2,
                "secids": "1.000001",
                "fields": "f2,f3,f12,f104,f105,f106",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            },
            timeout=12,
        )
        row = (((d.get("data") or {}).get("diff")) or [{}])[0]
        up = int(float(row.get("f104") or 0))
        down = int(float(row.get("f105") or 0))
        flat = int(float(row.get("f106") or 0))
        denom = up + down + flat
        return {
            "up": up,
            "down": down,
            "flat": flat,
            "total": denom,
            "red_pct": round(up * 100.0 / denom, 1) if denom else None,
            "amount_yi": None,
            "fetched": denom,
            "complete": False,
            "note": "仅沪市 · 降级",
        }
    except Exception as e:
        print(f"[warn] 沪市涨跌家数失败: {e}")
        return {"up": 0, "down": 0, "flat": 0, "total": 0, "red_pct": None,
                "amount_yi": None, "fetched": 0, "complete": False}


# ── 个股当日资金流（主力净流入，亿）────────────────────────
def stock_fund_flow(secid: str) -> float | None:
    """secid: '0.300308' / '1.603228'"""
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        "lmt": 1,
        "klt": 101,
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }
    try:
        d = _get(url, params)
        lines = (d.get("data") or {}).get("klines") or []
        if not lines:
            return None
        parts = lines[-1].split(",")
        # f62 主力净流入（元）
        return round(float(parts[1]) / 1e8, 2) if len(parts) > 1 else None
    except Exception:
        return None


# ── 龙虎榜（当日，东财 datacenter）────────────────────────
def lhb_detail(date: str) -> list[dict]:
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "pageNumber": 1,
        "pageSize": 100,
        "sortColumns": "TURNOVERVALUE",
        "sortTypes": "-1",
        "filter": f"(TRADE_DATE='{date}')",
    }
    try:
        data = _get(url, params)
        rows = (data.get("result") or {}).get("data") or []
        return rows
    except Exception:
        return []


def secid_of(code: str) -> str:
    """A股代码 → 东财 secid"""
    c = code.zfill(6)
    if c.startswith(("6", "9", "5")):
        return f"1.{c}"
    return f"0.{c}"
