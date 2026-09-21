# -*- coding: utf-8 -*-
"""
板块情绪因子（论坛/快讯热度）

数据源（免费公开，尽力而为）：
1. 东财股吧热帖 / 人气（个股→板块聚合）
2. 新浪 7×24 + 东财快讯 关键词命中（政策/题材词）
3. 可选：本地 news 缓存做近几日滚动

注意：
- 「历史论坛热度」免费源很难完整回放 → 回测里用价量热度做代理
- 本模块给「当日/近 N 日」情绪，适合每日复盘与实盘信号
"""
from __future__ import annotations

import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://guba.eastmoney.com/",
}

# 板块关键词（与复盘题材词可共用，这里偏情绪词）
SECTOR_KEYWORDS = {
    "tech": [
        "半导体", "芯片", "算力", "AI", "人工智能", "光模块", "PCB", "CPO", "存储",
        "机器人", "消费电子", "软件", "信创", "数据中心", "服务器", "GPU",
    ],
    "securities": [
        "券商", "证券", "牛市", "两市成交", "融资", "北交所", "IPO", "注册制",
    ],
    "nonferrous": [
        "有色", "铜", "铝", "锂", "稀土", "黄金", "小金属", "钴", "镍", "紫金", "钼",
    ],
}


def _get(url, params=None, headers=None, timeout=12):
    h = dict(UA)
    if headers:
        h.update(headers)
    r = requests.get(url, params=params, headers=h, timeout=timeout)
    r.raise_for_status()
    return r


# ── 东财股吧：个股人气（近似）────────────────────────
def guba_popularity(code6: str) -> dict | None:
    """
    东财股吧列表页，粗取今日帖数/阅读相关字段（页面结构会变，失败返回 None）。
    """
    # 常用：https://guba.eastmoney.com/list,code.html
    url = f"https://guba.eastmoney.com/list,{code6}.html"
    try:
        r = _get(url, timeout=10)
        html = r.text
        # 粗提取「阅读」数字，格式不保证
        reads = re.findall(r"class=\"read\">(\d+)", html)
        replies = re.findall(r"class=\"reply\">(\d+)", html)
        n_read = sum(int(x) for x in reads[:30]) if reads else None
        n_reply = sum(int(x) for x in replies[:30]) if replies else None
        return {"code": code6, "read_top30": n_read, "reply_top30": n_reply, "ok": True}
    except Exception as e:
        return {"code": code6, "ok": False, "error": str(e)[:80]}


# ── 快讯关键词情绪 ─────────────────────────────────────
def news_sector_sentiment(items: list[dict], date: str | None = None) -> dict:
    """
    items: [{title, summary, time, source}, ...]
    返回各板块命中次数与占比
    """
    texts = []
    for it in items:
        t = f"{it.get('title','')}{it.get('summary','')}"
        if date and it.get("time"):
            # 宽松过滤当日或前一日
            if date not in str(it.get("time")) and date[5:] not in str(it.get("time")):
                # 仍保留，后面可再筛
                pass
        texts.append(t)
    hits = defaultdict(int)
    for t in texts:
        for sec, kws in SECTOR_KEYWORDS.items():
            if any(k in t for k in kws):
                hits[sec] += 1
    total = sum(hits.values()) or 1
    return {
        "hits": dict(hits),
        "share": {k: round(v / total, 3) for k, v in hits.items()},
        "n_items": len(texts),
        "total_hits": sum(hits.values()),
    }


# ── 汇总：价量热度 + 快讯热度 ─────────────────────────
def sector_sentiment_report(date: str, pool_sec_map: dict[str, str] | None = None) -> dict:
    """
    当日板块情绪报告（给复盘页 / 实盘信号）
    """
    import news as NEWS

    pack = NEWS.fetch_all_news(date, per_source=30)
    items = (pack.get("all") or [])
    ns = news_sector_sentiment(items, date)

    # 可选：抽样股吧人气（控制请求量）
    sample_codes = [
        "300308", "002463", "300059", "601899", "600111", "002371", "300750",
    ]
    if pool_sec_map:
        # 从池子里各取若干
        by_sec = defaultdict(list)
        for c, s in pool_sec_map.items():
            by_sec[s].append(c[2:])
        sample_codes = []
        for s, lst in by_sec.items():
            sample_codes.extend(lst[:3])

    guba = []
    for c in sample_codes[:12]:
        g = guba_popularity(c)
        if g:
            guba.append(g)
        time.sleep(0.12)

    # 按板块聚合资吧（用 pool_sec_map 反查）
    inv = {}
    if pool_sec_map:
        inv = {c[2:]: s for c, s in pool_sec_map.items()}
    guba_sec = defaultdict(list)
    for g in guba:
        sec = inv.get(g["code"], "other")
        guba_sec[sec].append(g)

    return {
        "date": date,
        "news": ns,
        "guba": guba,
        "guba_by_sector": {k: len(v) for k, v in guba_sec.items()},
        "news_top_sector": max(ns["hits"].items(), key=lambda x: x[1])[0] if ns["hits"] else None,
        "sources_ok": pack.get("sources_ok"),
        "n_news": pack.get("count"),
    }


# ── 回测用代理：价量「情绪」近似（可进策略）────────────
def price_emotion_proxy(close, is_zt, lookback=10):
    """
    价量情绪（可回测）：
      emo = 近 N 日涨停密度 + 涨幅>5% 密度  （横截面 rank）
    用于代替不可回放的论坛历史热度。
    """
    ret1 = close.pct_change()
    big = (ret1 >= 0.05).astype(float)
    zt = is_zt.astype(float)
    emo = (zt.rolling(lookback).sum() + big.rolling(lookback).sum()) / lookback
    return emo.rank(axis=1, pct=True)


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-09-11"
    r = sector_sentiment_report(day)
    import json

    print(json.dumps({k: v for k, v in r.items() if k != "guba"}, ensure_ascii=False, indent=2)[:2000])
    print("guba sample", r["guba"][:3])
