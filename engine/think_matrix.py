# -*- coding: utf-8 -*-
"""
应变矩阵 + 核心评分（规则引擎）
  - 应变矩阵：由情绪/广度/最高板/最强题材 自动生成场景与应对
  - 四维评分：对自选/高位票打 题材/地位/合力/韧性 → 综合/弹性/行动
可选：另一模型只做文案润色（不改变数字结论）
"""
from __future__ import annotations

from typing import Any


def _grade(score: float) -> str:
    if score >= 90:
        return "S"
    if score >= 80:
        return "A+"
    if score >= 70:
        return "A"
    if score >= 60:
        return "A-"
    if score >= 50:
        return "B+"
    if score >= 40:
        return "B"
    if score >= 30:
        return "B-"
    if score >= 20:
        return "C"
    return "D"


def is_gem(code: str) -> bool:
    c = str(code).zfill(6) if str(code).isdigit() else str(code)[-6:]
    return c.startswith("30") or c.startswith("68")


def build_core_roles(ctx: dict) -> dict:
    """
    自动识别三类角色（规则，非人工点名）：
      leader  龙头：最高连板 → 次高板+封单/成交靠前
      mid     中军：最强行业里成交额/封单最大的非一字大票（含自选中大成交）
      elastic 弹性：20cm + 当日强势 + 量能/涨幅靠前
    """
    zt = list(ctx.get("zt") or [])
    sectors = ctx.get("sectors") or []
    top_sec = sectors[0]["name"] if sectors else ""
    watch = ctx.get("watch_tech") or []

    def _amt(x):
        try:
            return float(x.get("amount") or 0)
        except Exception:
            return 0.0

    def _fund(x):
        try:
            return float(x.get("fund") or 0)
        except Exception:
            return 0.0

    def _lbc(x):
        try:
            return int(x.get("lbc") or 1)
        except Exception:
            return 1

    def _sec(x):
        return x.get("hybk") or M_classify(x.get("name") or "")

    # —— 龙头 ——
    leaders = []
    by_lbc = sorted(zt, key=lambda z: (_lbc(z), _fund(z), _amt(z)), reverse=True)
    for z in by_lbc[:6]:
        leaders.append({
            "name": z.get("name"),
            "code": z.get("code"),
            "h": f"{_lbc(z)} 板",
            "lbc": _lbc(z),
            "sector": _sec(z),
            "fund": round(_fund(z), 2),
            "amount": round(_amt(z), 2),
            "role": "leader",
            "verdict": f"{_sec(z)} · 连板 {_lbc(z)} · 封单 {_fund(z):.2f} 亿 · 成交 {_amt(z):.2f} 亿；接力风险高，只作温度计",
            "tone": "hot" if _lbc(z) >= 2 else "mid",
        })
    # 去重
    seen = set()
    leaders = [x for x in leaders if not (x["name"] in seen or seen.add(x["name"]))][:5]

    # —— 中军：最强行业涨停里成交额最大；再补自选里成交/市值感（用 close*amount 粗代理）——
    mids = [z for z in zt if _sec(z) == top_sec]
    mids = sorted(mids, key=_amt, reverse=True)[:4]
    mid_list = []
    for z in mids:
        mid_list.append({
            "name": z.get("name"),
            "code": z.get("code"),
            "role": "mid",
            "sector": _sec(z),
            "amount": round(_amt(z), 2),
            "fund": round(_fund(z), 2),
            "note": f"{_sec(z)} · 成交 {_amt(z):.2f} 亿 · 封单 {_fund(z):.2f} 亿；中军原则：低吸可、追高不可",
        })
    # 自选池补充（若有技术位且站上 MA20）
    for w in watch:
        tech = w.get("tech") or {}
        close, ma20 = tech.get("close"), tech.get("ma20")
        if not (close and ma20 and close >= ma20):
            continue
        if any(m.get("name") == w.get("name") for m in mid_list):
            continue
        if w.get("dir") and top_sec and (top_sec[:2] in w["dir"] or w["dir"][:2] in top_sec):
            mid_list.append({
                "name": w.get("name"),
                "code": w.get("code"),
                "role": "mid",
                "sector": w.get("dir"),
                "amount": None,
                "fund": None,
                "note": f"自选站上 MA20（收 {close}）；中军/趋势参考",
            })
        if len(mid_list) >= 5:
            break

    # —— 弹性：20cm + 当日涨幅高 + 成交不低 ——
    elastic = []
    for z in zt:
        if not is_gem(z.get("code") or z.get("name")):
            continue
        elastic.append(z)
    # 无 20cm 涨停则用涨幅榜代理（ctx 可带）
    extra = ctx.get("elastic_candidates") or []
    for z in extra:
        if is_gem(z.get("code")):
            elastic.append(z)
    elastic = sorted(elastic, key=_amt, reverse=True)[:5]
    el_list = []
    for z in elastic:
        el_list.append({
            "name": z.get("name"),
            "code": z.get("code"),
            "role": "elastic",
            "sector": _sec(z),
            "amount": round(_amt(z), 2),
            "note": f"20cm 弹性 · 成交 {_amt(z):.2f} 亿；波动大，仓位减半/短持",
        })

    return {"leader": leaders, "mid": mid_list, "elastic": el_list, "topSector": top_sec}


def M_classify(name: str) -> str:
    try:
        import metrics as M

        return M.classify_sector(name or "")
    except Exception:
        return ""


def build_scenarios(ctx: dict) -> list[dict]:
    """盘中应变矩阵（7 档，数据驱动）"""
    emotion = (ctx.get("emotion") or {}).get("total", 50)
    red = (ctx.get("breadth") or {}).get("red_pct") or ctx.get("pools", {}).get("redPct") or 50
    zt = ctx.get("zt_n") or 0
    zb_rate = ctx.get("zb_rate") or 20
    lad = ctx.get("ladder_info") or {}
    top_boards = lad.get("topBoards") or 0
    top_name = lad.get("topName") or "最高标"
    sectors = ctx.get("sectors") or []
    top_sec = sectors[0]["name"] if sectors else "最强题材"
    top_n = sectors[0]["n"] if sectors else 0
    regime = ctx.get("regime") or "混沌态"
    pos = ctx.get("pos_anchor") or 2

    return [
        {
            "s": f"{top_sec}集体高开/涨停开",
            "t": f"竞价 {top_sec} 前排 ≥3 家高开 >5% 或涨停开；{top_sec} 涨停 ≥{max(top_n,5)} 家",
            "a": f"不追一致；只跟换手后活口。仓位上限 {min(pos+1,5)} 成，等 10:00 分歧再定",
        },
        {
            "s": f"{top_sec}竞价核按钮",
            "t": "前排低开 <-3% 或开盘 5 分钟翻绿；炸板率快速上行",
            "a": f"该链 A/B 路径作废；只保留 C 回踩。仓位压至 {max(pos-1,0)} 成以内",
        },
        {
            "s": f"{top_name}（{top_boards} 板）被核",
            "t": "竞价低开 <-3% 或封单骤降/开板不回封",
            "a": "高位情绪降温；不接力不撬板；全场买点降一档",
        },
        {
            "s": "指数跳水 / 红盘率崩溃",
            "t": f"红盘率 <35%（当前 {red}%）或沪指 -1% 以上无承接",
            "a": f"门控减仓（信号日广度门控）；不新开仓，持仓去弱",
        },
        {
            "s": "缩量反弹（假修复）",
            "t": f"红盘率回升但成交萎缩；炸板率仍 >{zb_rate:.0f}%",
            "a": "不加仓；等 10:30 是否放量确认，否则反弹卖",
        },
        {
            "s": "放量普涨（真修复）",
            "t": f"红盘率 >60%（当前 {red}%）且涨停家数回升；情绪分上修",
            "a": f"可升至仓位锚 {pos} 成；仍不满仓，只做确认型买点",
        },
        {
            "s": "外盘黑天鹅",
            "t": "隔夜美股/港股大跌、地缘或政策突变",
            "a": f"竞价观望；低开不核=错杀可看，低开核=全天不买。状态={regime}",
        },
    ]


def _score_one(
    *,
    theme_score: float,
    pos_score: float,
    flow_score: float,
    hold_score: float,
    emotion_pen: float = 0.0,
) -> dict:
    """四维 0–100 → 综合/弹性/行动"""
    g = 0.35 * theme_score + 0.30 * pos_score + 0.20 * flow_score + 0.15 * hold_score
    g = max(0.0, min(100.0, g - emotion_pen))
    # 弹性：题材+地位高则弹性高
    e = 0.5 * theme_score + 0.5 * pos_score
    e = max(0.0, min(100.0, e))
    if g >= 75:
        act = "核心/条件单可跟，仓位优先"
    elif g >= 60:
        act = "观察偏多，等回踩/承接确认"
    elif g >= 45:
        act = "只看不追；持仓可持不可加"
    else:
        act = "回避或去弱"
    return {
        "theme": _grade(theme_score),
        "pos": _grade(pos_score),
        "flow": _grade(flow_score),
        "hold": _grade(hold_score),
        "grade": _grade(g),
        "elastic": _grade(e),
        "act": act,
        "raw": round(g, 1),
    }


def build_scores(ctx: dict, watch_tech: list[dict] | None = None) -> list[dict]:
    """
    对自选池 + 连板高位打分（启发式）
    theme: 最强题材/涨停热度
    pos: 连板高度 / 是否最高标
    flow: 自选技术位（MA 位置、动量）
    hold: 韧性（距 MA20 / 近 5 日抗跌）
    """
    sectors = ctx.get("sectors") or []
    sec_map = {s["name"]: s["n"] for s in sectors}
    top_sec = sectors[0]["name"] if sectors else ""
    top_n = sectors[0]["n"] if sectors else 0
    emotion = (ctx.get("emotion") or {}).get("total", 50)
    pen = 8 if emotion < 50 else (4 if emotion < 65 else 0)

    zt_codes = {z.get("code"): z for z in (ctx.get("zt") or [])}
    leaders = ctx.get("leaders") or []
    lad = ctx.get("ladder_info") or {}

    rows = []
    # 高位 / 最高板
    if lad.get("topName"):
        rows.append(
            {
                "name": lad["topName"],
                **_score_one(
                    theme_score=70,
                    pos_score=85,
                    flow_score=40,
                    hold_score=55,
                    emotion_pen=pen,
                ),
                "note": f"最高板 {lad.get('topBoards')}；监管/断板风险",
            }
        )
    for L in leaders[:4]:
        name = L.get("name")
        if any(r["name"] == name for r in rows):
            continue
        h = str(L.get("h") or "")
        boards = 0
        try:
            boards = float("".join(ch for ch in h if ch.isdigit() or ch == ".") or 0)
        except Exception:
            boards = 0
        theme = 60 if top_sec and top_sec in str(L.get("verdict") or "") else 45
        if top_n >= 8:
            theme = min(90, theme + 10)
        pos_s = min(90, 35 + boards * 12)
        rows.append(
            {
                "name": name,
                **_score_one(theme_score=theme, pos_score=pos_s, flow_score=45, hold_score=50, emotion_pen=pen),
                "note": f"高位/梯队 {h}",
            }
        )

    # 自选池
    for w in watch_tech or []:
        tech = w.get("tech") or {}
        close = tech.get("close")
        ma20 = tech.get("ma20")
        mom5 = tech.get("pct_5d")
        theme = 50
        sec_name = w.get("dir") or ""
        for s_name, n in sec_map.items():
            if s_name and (s_name[:2] in sec_name or sec_name[:2] in s_name):
                theme = min(90, 40 + n * 5)
                break
        if w.get("code") in zt_codes:
            theme = min(95, theme + 20)
        pos_s = 55 if w.get("code") in zt_codes else 40
        flow_s = 50
        hold_s = 50
        if close and ma20:
            if close >= ma20:
                flow_s += 15
                hold_s += 15
            else:
                flow_s -= 10
        if mom5 is not None:
            if mom5 > 3:
                flow_s += 10
            elif mom5 < -5:
                hold_s -= 10
        rows.append(
            {
                "name": w.get("name"),
                **_score_one(
                    theme_score=max(0, min(100, theme)),
                    pos_score=max(0, min(100, pos_s)),
                    flow_score=max(0, min(100, flow_s)),
                    hold_score=max(0, min(100, hold_s)),
                    emotion_pen=pen,
                ),
                "note": f"{w.get('dir') or ''}；技术位参考",
            }
        )

    # 去重保序
    seen = set()
    out = []
    for r in rows:
        if not r.get("name") or r["name"] in seen:
            continue
        seen.add(r["name"])
        out.append(r)
    return out[:16]


def build_report(ctx: dict, watch_tech: list | None = None) -> dict:
    ctx = dict(ctx or {})
    if watch_tech:
        ctx["watch_tech"] = watch_tech
    roles = build_core_roles(ctx)
    return {
        "scenarios": build_scenarios(ctx),
        "scores": build_scores(ctx, watch_tech),
        "roles": roles,
        "method": "规则引擎：情绪/广度/连板/题材热度/MA 位 + 龙头/中军/弹性自动识别；非人工四维定义，可再叠 LLM 文案",
    }
