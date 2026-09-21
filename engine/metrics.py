# -*- coding: utf-8 -*-
"""核心指标：连板梯队 / 炸板率 / 晋级率 / 情绪五维 / 主线归类"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

import math

from config import DIM_WEIGHTS, SECTOR_KEYWORDS


def _dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def prev_trade_day(date: str, max_back: int = 12) -> str:
    """简单回退：跳过周末。不含节假日日历（可后续接交易日历）。"""
    d = _dt(date)
    for _ in range(max_back):
        d -= timedelta(days=1)
        if d.weekday() < 5:
            return d.strftime("%Y-%m-%d")
    return d.strftime("%Y-%m-%d")


def ladder(zt: list[dict]) -> dict:
    """连板梯队：first/second/third/top"""
    first = sum(1 for x in zt if x.get("lbc", 1) == 1)
    second = sum(1 for x in zt if x.get("lbc", 1) == 2)
    third = sum(1 for x in zt if x.get("lbc", 1) == 3)
    highs = [x for x in zt if x.get("lbc", 1) >= 4]
    top = max(highs, key=lambda x: x.get("lbc", 0)) if highs else None
    return {
        "first": first,
        "second": second,
        "third": third,
        "top": 1 if top else 0,
        "topName": top["name"] if top else "",
        "topBoards": top["lbc"] if top else 0,
        "highs": [
            {"name": h["name"], "code": h["code"], "lbc": h["lbc"]} for h in sorted(highs, key=lambda x: -x["lbc"])
        ][:8],
    }


def zb_rate(zt_n: int, zb_n: int) -> float:
    denom = zt_n + zb_n
    return round(zb_n * 100.0 / denom, 1) if denom else 0.0


def promotion_rate(today_zt: list[dict], yest_zt: list[dict]) -> dict:
    """
    晋级率：昨日连板数>=2 的股票，今日是否继续涨停。
    分母 = 昨日 lbc>=2 的全部；分子 = 今日仍在涨停池。
    """
    cand = [x for x in yest_zt if x.get("lbc", 1) >= 2]
    today_codes = {x["code"] for x in today_zt}
    promoted = [x for x in cand if x["code"] in today_codes]
    n, d = len(promoted), len(cand)
    return {
        "rate": round(n * 100.0 / d, 1) if d else 0.0,
        "num": n,
        "den": d,
        "names": [x["name"] for x in promoted],
    }


def yest_premium(today_map: dict, yest_zt: list[dict], closes: dict) -> dict:
    """昨涨停溢价：昨日涨停股今日涨跌幅均值 / 中位 / 大面数"""
    import statistics

    chgs = []
    worst = []
    for x in yest_zt:
        c = x["code"]
        # 今日涨跌幅需外部 closes[code] = {pct}
        pct = (closes or {}).get(c)
        if pct is None:
            continue
        chgs.append(pct)
        if pct < -5:
            worst.append({"name": x["name"], "pct": pct})
    if not chgs:
        return {"avg": None, "median": None, "big_face": 0, "worst": []}
    return {
        "avg": round(sum(chgs) / len(chgs), 2),
        "median": round(statistics.median(chgs), 2),
        "big_face": len(worst),
        "worst": sorted(worst, key=lambda x: x["pct"])[:5],
        "sample": len(chgs),
    }


def classify_sector(name: str, hybk: str = "") -> str:
    for sec, kws in SECTOR_KEYWORDS.items():
        for kw in kws:
            if kw in name:
                return sec
    if hybk:
        return hybk[:8]
    return "其他"


def sector_counts(zt: list[dict]) -> list[dict]:
    c: Counter = Counter()
    for x in zt:
        c[classify_sector(x["name"], x.get("hybk", ""))] += 1
    return [{"name": k, "n": v} for k, v in c.most_common(12)]


def emotion_score(
    *,
    zt_n: int,
    dt_n: int,
    zb_rate_pct: float,
    promo: dict,
    red_pct: float | None,
    amount_yi: float | None,
    amount_delta_yi: float | None,
    sectors: list[dict],
    ladder_info: dict,
) -> dict:
    """
    五维情绪分（0-100）。规则可解释、可复算。
    """
    # 1 涨跌停结构 (25)
    s1 = 0
    if zt_n >= 80:
        s1 += 12
    elif zt_n >= 50:
        s1 += 8
    elif zt_n >= 30:
        s1 += 5
    else:
        s1 += 2
    if dt_n <= 3:
        s1 += 8
    elif dt_n <= 10:
        s1 += 4
    # 梯队完整
    lad = ladder_info
    if lad["topBoards"] >= 5 and lad["third"] >= 2 and lad["second"] >= 5:
        s1 += 5
    elif lad["topBoards"] >= 3 and lad["second"] >= 3:
        s1 += 3
    s1 = min(s1, DIM_WEIGHTS["涨跌停结构"])

    # 2 赚钱效应 (25)
    s2 = 0
    rate = promo.get("rate") or 0
    if rate >= 50:
        s2 += 12
    elif rate >= 35:
        s2 += 9
    elif rate >= 20:
        s2 += 5
    else:
        s2 += 2
    avg = (promo.get("premium") or {}).get("avg")
    if avg is not None:
        if avg >= 3:
            s2 += 8
        elif avg >= 1:
            s2 += 5
        elif avg >= -1:
            s2 += 2
    bf = (promo.get("premium") or {}).get("big_face") or 0
    if bf == 0:
        s2 += 5
    elif bf <= 3:
        s2 += 2
    if zb_rate_pct < 20:
        s2 += 0  # 不触发减半，给满分空间已在上面
    elif zb_rate_pct >= 35:
        s2 = max(0, s2 - 6)
    s2 = min(s2, DIM_WEIGHTS["赚钱效应延续性"])

    # 3 市场广度 (20)
    s3 = 0
    if red_pct is not None:
        if red_pct >= 60:
            s3 += 12
        elif red_pct >= 50:
            s3 += 8
        elif red_pct >= 40:
            s3 += 4
        else:
            s3 += 1
    if zt_n > max(dt_n, 1) * 8:
        s3 += 8
    elif zt_n > max(dt_n, 1) * 3:
        s3 += 5
    s3 = min(s3, DIM_WEIGHTS["市场广度"])

    # 4 量能风偏 (20)
    # 阈值按**沪深全 A** 量级设定（1.8万亿/1.2万亿/8000亿），
    # 传入的 amount_yi 必须同口径；沪市单一市场值会系统性低估本项。
    s4 = 0
    if amount_yi is not None:
        if amount_yi >= 18000:
            s4 += 10
        elif amount_yi >= 12000:
            s4 += 7
        elif amount_yi >= 8000:
            s4 += 4
        else:
            s4 += 2
    if amount_delta_yi is not None:
        if amount_delta_yi > 500:
            s4 += 8
        elif amount_delta_yi > 0:
            s4 += 5
        elif amount_delta_yi > -800:
            s4 += 3
        else:
            s4 += 0
    else:
        s4 += 4
    s4 = min(s4, DIM_WEIGHTS["量能风偏"])

    # 5 主线健康度 (10)
    s5 = 0
    top_sec = sectors[0]["n"] if sectors else 0
    if top_sec >= 8:
        s5 += 6
    elif top_sec >= 5:
        s5 += 4
    elif top_sec >= 3:
        s5 += 2
    if len(sectors) >= 4:
        s5 += 4
    elif len(sectors) >= 2:
        s5 += 2
    s5 = min(s5, DIM_WEIGHTS["主线健康度"])

    total = s1 + s2 + s3 + s4 + s5
    dims = [
        {"name": "涨跌停结构", "w": 25, "s": s1, "note": f"涨停 {zt_n} / 跌停 {dt_n} / 最高板 {lad['topBoards']}", "src": "K1/K3"},
        {
            "name": "赚钱效应延续性",
            "w": 25,
            "s": s2,
            "note": f"晋级率 {rate}%（{promo.get('num')}/{promo.get('den')}）· 昨涨停溢价 {avg}% · 炸板率 {zb_rate_pct}%",
            "src": "K2/K4",
        },
        {"name": "市场广度", "w": 20, "s": s3, "note": f"红盘率 {red_pct}%", "src": "K5/K6"},
        {
            "name": "量能风偏",
            "w": 20,
            "s": s4,
            "note": f"成交 {amount_yi} 亿 · 较前日 {amount_delta_yi} 亿" if amount_delta_yi is not None else f"成交 {amount_yi} 亿 · 较前日 —（缺前一日缓存）",
            "src": "K5",
        },
        {
            "name": "主线健康度",
            "w": 10,
            "s": s5,
            "note": "主线家数：" + "、".join(f"{x['name']} {x['n']}" for x in sectors[:4]),
            "src": "K1/K8",
        },
    ]
    base = max(40, min(75, total - 10))
    return {
        "dims": dims,
        "score": {"today": total, "yesterday": None, "base36": base},
        "total": total,
    }


def market_regime(total: int, has_s: bool) -> str:
    if total >= 75 and has_s:
        return "进攻态"
    if total >= 65:
        return "混沌态（偏强）"
    if total >= 50:
        return "混沌态"
    if total >= 35:
        return "退潮观察"
    return "防御态"


def position_anchor(total: int, has_s: bool) -> float:
    """盘面仓位上限（成）"""
    if total >= 75 and has_s:
        return 7.0
    if total >= 70:
        return 5.0
    if total >= 60:
        return 4.0 if has_s else 3.0
    if total >= 45:
        return 2.0
    return 1.0


def pick_market_amount(breadth: dict | None, sh_amount) -> tuple[float | None, str]:
    """选成交额口径。

    只有样本**明确完整**（complete is True）的全 A 值才可用；
    截断样本、旧结构缓存（无 complete 字段）一律回退到沪指，并如实标注口径。
    返回 (amount_yi, scope)。
    """
    b = breadth or {}
    if b.get("amount_yi") is not None and b.get("complete") is True:
        return b["amount_yi"], "全A"
    if sh_amount is not None:
        return sh_amount, "沪市（回退）"
    return None, "不可用"


# ── 宏观择时 → 仓位系数 ───────────────────────────────────
# macro_regime.position 是 0–0.9 的比例（base 0.9/0.7/0.4 × gate）。
# 映射到 [0.6, 1.0] 的乘数：
#   macro=0.9（宏观满配）→ 1.0，完全不削减盘面锚
#   macro=0.4（2026-09-16）→ 0.778
#   macro=0     → 0.6，最多砍到六成，永不完全否决盘面
MACRO_POS_FULL = 0.9      # 该策略 position 的理论上限
MACRO_COEF_MIN = 0.6      # 系数下限：宏观再悲观也保留盘面锚的六成
MACRO_LAG_MAX_DAYS = 5    # 宏观 as_of 与复盘日间隔超过该天数即视为不可用


def macro_coefficient(macro_position) -> float:
    """宏观 position（0–0.9 比例）→ 盘面锚乘数，值域 [MACRO_COEF_MIN, 1.0]。

    None / NaN / 非正数一律返回 1.0（等价于「宏观未参与」，不做任何削减）。
    """
    try:
        p = float(macro_position)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(p):
        return 1.0
    frac = min(max(p / MACRO_POS_FULL, 0.0), 1.0)
    return round(MACRO_COEF_MIN + (1.0 - MACRO_COEF_MIN) * frac, 4)


def _lag_days(as_of: str, review_day: str) -> int | None:
    """宏观时点相对复盘日的**有符号**间隔：正值=宏观更旧，负值=宏观更新。"""
    try:
        from datetime import date as _d

        da = _d.fromisoformat(str(as_of)[:10])
        db = _d.fromisoformat(str(review_day)[:10])
        return (db - da).days
    except Exception:
        return None


def blend_position_anchor(anchor: float, macro_position, macro_as_of=None,
                          review_day=None) -> dict:
    """盘面锚 × 宏观系数 → 最终仓位上限（成）。

    返回 {final, board, coeff, adjusted, reason}。
    宏观缺失、滞后超过 MACRO_LAG_MAX_DAYS 天、或时点晚于复盘日（复盘旧日期时
    会引入未来信息）时一律不调整（coeff=1.0）。
    """
    board = float(anchor)
    coeff = 1.0
    reason = ""

    if macro_position is None:
        reason = "宏观不可用，未调整"
    else:
        lag = None
        if macro_as_of and review_day:
            lag = _lag_days(macro_as_of, review_day)
        if lag is not None and lag < 0:
            reason = f"宏观时点晚于复盘日 {-lag} 天，未调整"
        elif lag is not None and lag > MACRO_LAG_MAX_DAYS:
            reason = f"宏观滞后 {lag} 天（>{MACRO_LAG_MAX_DAYS}），未调整"
        else:
            coeff = macro_coefficient(macro_position)
            if coeff >= 1.0:
                reason = "宏观未削减"

    final = round(board * coeff, 1)
    final = min(max(final, 1.0), 7.0)
    return {
        "final": final,
        "board": round(board, 1),
        "coeff": coeff,
        "adjusted": coeff < 1.0,
        "reason": reason,
    }
