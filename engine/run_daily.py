# -*- coding: utf-8 -*-
"""
每日复盘一键流水线
用法:
  python run_daily.py                 # 用最近交易日（今天或上一交易日）
  python run_daily.py --date 2026-09-07
  python run_daily.py --offline       # 只用 data/ 缓存（若有）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import eastmoney as em  # noqa: E402
import metrics as M  # noqa: E402
import news as NEWS  # noqa: E402
import quotes as Q  # noqa: E402
import duckdb_store as DS  # noqa: E402
from config import WATCH_POOL, DATA_DIR  # noqa: E402
from generate import build_payload, write_data_js  # noqa: E402


def _is_weekend(s: str) -> bool:
    return datetime.strptime(s, "%Y-%m-%d").weekday() >= 5


def pick_review_day(date_arg: str | None) -> str:
    if date_arg:
        return date_arg
    d = datetime.now()
    # 若今天是周末或未收盘（简单：15:05 前），回退到上一交易日
    if d.weekday() >= 5 or (d.hour < 15):
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def next_day(date: str) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def cache_path(date: str) -> Path:
    d = DATA_DIR / date.replace("-", "")
    d.mkdir(parents=True, exist_ok=True)
    return d / "raw.json"


# 结构版本：bump 后旧缓存自动失效重取。
# 2 → breadth 增加 amount_yi / fetched / complete（成交额与涨跌家数口径修正）
DAY_CACHE_VERSION = 2


# 完整日缓存必须含有的字段（旧结构没有 fullDay 标记时用它来判定）
FULL_DAY_KEYS = ("zt", "dt", "zb", "sh", "breadth")


def _is_full_day(d: dict) -> bool:
    """是否完整日缓存。

    新结构看 fullDay；旧结构（没有该字段）按必备字段推断——
    只存涨停池的「昨日」缓存只有 zt，会被正确排除。
    """
    if "fullDay" in d:
        return bool(d["fullDay"])
    return all(k in d for k in FULL_DAY_KEYS)


def load_cache(date: str, require_full: bool = False, allow_stale: bool = False) -> dict | None:
    """读日缓存。

    版本不符默认忽略并重取；allow_stale（仅 --offline 用）时仍返回旧缓存，
    但把 breadth 标记为 complete=False，避免把旧口径的截断样本当成全 A 使用。
    require_full 用于排除只存了涨停池的「昨日」缓存。
    """
    p = cache_path(date)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    stale = d.get("cacheVersion") != DAY_CACHE_VERSION
    if stale and not allow_stale:
        print(f"[cache] {date} 结构版本 {d.get('cacheVersion')} != {DAY_CACHE_VERSION}，忽略并重取")
        return None
    if require_full and not _is_full_day(d):
        print(f"[cache] {date} 非完整日缓存，忽略")
        return None
    if stale:
        print(f"[cache] {date} 结构版本过旧，离线沿用；广度/成交额标记为不可信")
        b = d.get("breadth")
        if isinstance(b, dict):
            d["breadth"] = {**b, "complete": False}
    return d


def save_cache(date: str, data: dict, full_day: bool = True) -> None:
    cache_path(date).write_text(
        json.dumps(
            {**data, "cacheVersion": DAY_CACHE_VERSION, "fullDay": bool(full_day)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def fetch_day(date: str) -> dict:
    print(f"[fetch] 涨停池 {date} ...")
    zt = em.zt_pool(date)
    time.sleep(0.15)
    print(f"[fetch] 跌停池 {date} ...")
    dt = em.dt_pool(date)
    time.sleep(0.15)
    print(f"[fetch] 炸板池 {date} ...")
    zb = em.zb_pool(date)
    time.sleep(0.15)
    print(f"[fetch] 指数 ...")
    sh = em.index_quote("1.000001")
    time.sleep(0.1)
    print(f"[fetch] 涨跌家数 ...")
    breadth = em.market_breadth()
    # 全市场分页失败、退回沪指 f104/f105 时，必须保留「样本不完整」标记，
    # 否则页面既不显示 ⚠，又会把沪市红盘率当成全市场口径来引用。
    if sh.get("up") and (breadth.get("up") or 0) == 0:
        denom = (sh.get("up") or 0) + (sh.get("down") or 0) + (sh.get("flat") or 0)
        breadth = {
            "up": sh.get("up") or 0,
            "down": sh.get("down") or 0,
            "flat": sh.get("flat") or 0,
            "total": denom,
            "red_pct": round((sh.get("up") or 0) * 100.0 / denom, 1) if denom else None,
            "amount_yi": None,
            "fetched": denom,
            "complete": False,
            "scope": "仅沪市 · 降级",
        }
    return {"zt": zt, "dt": dt, "zb": zb, "sh": sh, "breadth": breadth}


def closes_for_yest_zt(yest_zt: list[dict], limit: int = 80) -> dict:
    """拉全市场涨跌幅，按昨日涨停代码匹配。"""
    try:
        data = em._get(
            "https://push2.eastmoney.com/api/qt/clist/get",
            {
                "pn": 1,
                "pz": 6000,
                "po": 1,
                "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23",
                "fields": "f12,f3",
            },
            timeout=15,
        )
        diffs = ((data.get("data") or {}).get("diff")) or []
        m = {str(it.get("f12", "")).zfill(6): it.get("f3") for it in diffs if it.get("f3") is not None}
        return {x["code"]: m.get(x["code"]) for x in yest_zt if m.get(x["code"]) is not None}
    except Exception as e:
        print(f"[warn] 全市场涨跌幅失败: {e}")
        return {}


def build_narrative(date: str, plan_date: str, ctx: dict) -> dict:
    """数据驱动的文案骨架（可人工覆写）。"""
    zt_n, dt_n, zb_n = ctx["zt_n"], ctx["dt_n"], ctx["zb_n"]
    rate = ctx["promo"]["rate"]
    total = ctx["emotion"]["total"]
    regime = ctx["regime"]
    top_sec = ctx["sectors"][0] if ctx["sectors"] else {"name": "无", "n": 0}
    lad = ctx["ladder_info"]
    top_sec_name = top_sec.get("name") or "最强题材"
    top_name = lad.get("topName") or "最高标"
    top_boards = lad.get("topBoards") or 0
    # 名称可能是代码
    try:
        if str(top_name)[:2].upper() in ("SH", "SZ"):
            from stock_names import get_name_map, pretty

            top_name = pretty(str(top_name), get_name_map([str(top_name)]))
    except Exception:
        pass

    tone = "修复日" if total >= 60 else ("退潮日" if total < 45 else "震荡日")
    title_main = tone
    title_num = f"情绪 {total} → 基准 {ctx['emotion']['score']['base36']}"
    sub = (
        f"{top_sec['name']} {top_sec['n']} 家涨停 · 晋级率 {rate}% · "
        f"炸板率 {ctx['zb_rate']}% · 缩量/放量待确认"
    )

    lines = []
    for s in ctx["sectors"][:6]:
        lv = "A-" if s["n"] >= 8 else ("B+" if s["n"] >= 5 else ("B" if s["n"] >= 3 else "观察"))
        tone_css = "hot" if lv in ("A-", "B+") else ("mid" if lv == "B" else "mute")
        if s["n"] >= 8:
            act = "高潮次日不追 A/B 竞价，只做 C 回踩条件单"
        elif s["n"] >= 5:
            act = "跟踪身位股；一字后开板分歧日不接力"
        else:
            act = "只观察，不预支买点"
        lines.append(
            {
                "lv": lv,
                "name": s["name"],
                "stage": f"涨停 {s['n']} 家（自动题材归类）",
                "act": act,
                "tone": tone_css,
            }
        )
    lines.insert(
        0,
        {
            "lv": "S" if ctx["has_s"] else "（空缺）",
            "name": top_sec["name"] if ctx["has_s"] else "（空缺）",
            "stage": f"最强候选 {top_sec['name']} {top_sec['n']} 家；升 S 需前排批量涨停开 + 复核",
            "act": "无唯一主线不重仓" if not ctx["has_s"] else "主线内只做 C 回踩",
            "mute": "mute",
            "tone": "mute" if not ctx["has_s"] else "hot",
        },
    )

    # 龙头：最高板 + 涨停封单额前三 + 涨停家数最多板块内身位股
    leaders = []
    if lad.get("highs"):
        h0 = lad["highs"][0]
        leaders.append(
            {
                "name": h0["name"],
                "h": f"{h0['lbc']} 板",
                "verdict": f"名义最高连板 {h0['lbc']}；监管/断板信号需人工复核，自动模式不给出接力买点",
                "tone": "mid",
            }
        )
    for x in sorted(ctx["zt"], key=lambda z: -(z.get("lbc") or 0))[:4]:
        if leaders and x["name"] == leaders[0]["name"]:
            continue
        leaders.append(
            {
                "name": x["name"],
                "h": f"{x.get('lbc',1)} 板",
                "verdict": f"板块 {x.get('hybk') or M.classify_sector(x['name'])}；封单 {x.get('fund') or 0:.2f} 亿（自动）",
                "tone": "hot" if (x.get("lbc") or 0) >= 2 else "mid",
            }
        )

    # 计划：自选池里今日有涨停/大涨的 → 生成 C 回踩条件单
    plan2 = []
    zt_map = {z["code"]: z for z in ctx["zt"]}
    for w in WATCH_POOL:
        z = zt_map.get(w["code"])
        if not z:
            continue
        plan2.append(
            {
                "pri": f"P{len(plan2)+1}",
                "name": w["name"],
                "path": "C 盘中分歧低吸（自动）",
                "trig": f"竞价无核按钮 + 回踩 5 日线/涨停日均价企稳；连板 {z.get('lbc',1)}；板块 {z.get('hybk') or w['dir']}",
                "kill": "前排集体低开 <-3% / 破 5 日线 30 分钟收不回 / 板块涨停家数腰斩",
                "pos": "≤0.5 成",
                "stop": "10:30 前未企稳回升即走；止损 = 买入日最低下方 2%",
            }
        )
    plan1 = [
        {
            "pri": "表1·P1",
            "name": "—",
            "path": "核心趋势仓（自动模式默认空）",
            "trig": f"情绪 {total} · {regime}；仓位锚 {ctx['pos_anchor']} 成。趋势仓需人工指定中军",
            "kill": "指数破位 / 主线证伪",
            "pos": "0",
            "stop": "—",
        }
    ]

    bans = [
        f"{top_sec_name}链 A/B 追高（只做 C 回踩）",
        f"{top_name}（最高板 {top_boards}）接力/撬板",
        "一字独苗无承接高位接力",
        "连续 2 日主力净流出的独苗",
        "缩量市中军主升追高",
        "竞价高开 >8%（20cm）/>6%（10cm）则 A/B 路径失效",
        "外盘决议/数据公布前一日追高",
        "两表以外的临场单（纪律）",
    ]

    risks = [
        {
            "lv": "🟠 中高",
            "name": f"{top_sec['name']}高潮次日分化",
            "t": f"触发=前排竞价低开 <-3% 或 10:00 前炸板潮；证伪=前排 ≥3 家涨停开",
            "a": "该链 A/B 竞价路径停用，只做 C 回踩",
        },
        {
            "lv": "🟡 中",
            "name": "高位连板监管/断板",
            "t": f"最高板 {lad.get('topName') or '—'} {lad.get('topBoards') or 0} 板；触发=竞价低开或封单骤降",
            "a": "不撬板不低吸不接力；情绪温度计",
        },
        {
            "lv": "🟡 中",
            "name": "缩量修复证伪",
            "t": f"今日成交 {ctx['amount_yi']} 亿；若继续缩量且红盘率 <50%",
            "a": "仓位下修一档，买点全部加时间止损",
        },
    ]

    branches = [
        {
            "key": "基准",
            "score": ctx["emotion"]["score"]["base36"],
            "prob": 55,
            "dir": "震荡分化",
            "cond": "前排溢价收窄 + 板块涨停家数回落 + 资金轮动",
            "base": True,
        },
        {
            "key": "乐观",
            "score": min(90, total + 5),
            "prob": 25,
            "dir": "修复延续",
            "cond": "主线前排批量涨停开 + 容量中军红开 + 量能回升",
        },
        {
            "key": "悲观",
            "score": max(30, total - 25),
            "prob": 20,
            "dir": "高低切/退潮",
            "cond": "前排低开 <-3% + 最高板被核 + 指数破位无承接",
        },
    ]

    pool = []
    for w in WATCH_POOL:
        z = zt_map.get(w["code"])
        st = "观察·条件单" if z else "观察"
        note = (
            f"今日涨停 {z.get('lbc',1)} 板；除条件单外不开仓"
            if z
            else f"自选 {w['dir']}；今日未涨停，按技术位跟踪"
        )
        pool.append({"dir": w["dir"], "name": w["name"], "st": st, "note": note})

    watch = [
        {
            "name": w["name"],
            "d": "可参与（条件单）" if w["code"] in zt_map else "观察",
            "note": f"{w['dir']}；资金/技术位需 MyTT 复核" ,
        }
        for w in WATCH_POOL
    ]

    sources = [
        {"id": "K1", "cat": "kimi", "fact": f"涨停 {zt_n} 家；连板 {sum(1 for x in ctx['zt'] if x.get('lbc',1)>=2)} 只", "cite": f"东财涨停池 · {date}"},
        {"id": "K2", "cat": "kimi", "fact": f"炸板 {zb_n} 家、炸板率 {ctx['zb_rate']}%", "cite": f"东财炸板池 · {date}"},
        {"id": "K3", "cat": "kimi", "fact": f"跌停 {dt_n} 家", "cite": f"东财跌停池 · {date}"},
        {"id": "K4", "cat": "kimi", "fact": f"晋级率 {rate}%（{ctx['promo']['num']}/{ctx['promo']['den']}）", "cite": f"涨停池两日交叉 · {date}"},
        {"id": "K5", "cat": "kimi", "fact": f"沪指 {ctx['sh'].get('close')}（{ctx['sh'].get('pct')}%）；成交约 {ctx['amount_yi'] if ctx.get('amount_yi') is not None else '—'} 亿（口径 {ctx.get('amount_scope', '—')}）", "cite": f"{'东财全市场分页求和' if ctx.get('amount_scope') == '全A' else '东财指数（沪市）'} · {date}"},
        {"id": "K6", "cat": "kimi", "fact": f"涨 {ctx['breadth'].get('up')} / 跌 {ctx['breadth'].get('down')} / 平 {ctx['breadth'].get('flat')}，红盘率 {ctx['breadth'].get('red_pct')}%（样本 {ctx['breadth'].get('fetched', '—')}，完整 {ctx['breadth'].get('complete', '—')}）", "cite": f"东财全市场 · {date}"},
        {"id": "K8", "cat": "kimi", "fact": "题材归类：" + "、".join(f"{s['name']} {s['n']}" for s in ctx["sectors"][:6]), "cite": f"自动关键词归类 · {date}"},
        {"id": "L1", "cat": "local", "fact": f"本地行情 close/pct 宽表（近 320 日，约 5260 只）；广度/梯队/晋级可复算", "cite": "data/cache/*.parquet · DuckDB 查询"},
        {"id": "L2", "cat": "local", "fact": "全历史 qlib_bin（2000→数据截止），供因子与回测", "cite": "data/qlib_cn · investment_data release"},
        {"id": "L3", "cat": "local", "fact": f"情绪五维合计 {ctx['emotion']['total']}；状态 {ctx['regime']}；仓位锚 {ctx['pos_anchor']} 成（盘面 {ctx['anchor']['board']} 成 × 宏观系数 {ctx['anchor']['coeff']}）", "cite": "engine/metrics.py 规则自算"},
    ]

    news = ctx.get("news") or {}
    for i, item in enumerate(news.get("policy", [])[:4], start=1):
        sources.append(
            {
                "id": f"N{i}",
                "cat": "industry",
                "fact": f"政策快讯：{item.get('title','')[:60]}",
                "cite": f"{item.get('source','')} · {item.get('time', date)}",
            }
        )
    for i, item in enumerate(news.get("global", [])[:4], start=1):
        sources.append(
            {
                "id": f"G{i}",
                "cat": "broker",
                "fact": f"外围：{item.get('title','')[:60]}",
                "cite": f"{item.get('source','')} · {item.get('time', date)}",
            }
        )

    return {
        "title_main": title_main,
        "title_num": title_num,
        "sub": sub,
        "lines": lines,
        "leaders": leaders[:6],
        "plan1": plan1,
        "plan2": plan2,
        "bans": bans,
        "risks": risks,
        "pool": pool,
        "watch": watch,
        "branches": branches,
        "sources": sources,
        "news": news,
    }


def local_day_stats(date: str) -> dict:
    """仅当本地最新日 == 复盘日时才用 DuckDB/Parquet，避免旧日覆盖新日。"""
    out = {}
    try:
        last = DS.last_trade_day()
        out["local_last"] = last
        if last and date and last == date:
            out["date"] = last
            out["breadth"] = DS.market_breadth(last)
            out["ladder"] = DS.ladder_roll(last)
            out["promo"] = DS.promotion_rate(last)
            out["ok"] = True
        else:
            out["ok"] = False
            out["reason"] = f"local last={last} vs requested={date}（不同日则用东财全量）"
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)
        print(f"[warn] local stats fail: {e}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="复盘日 YYYY-MM-DD（默认自动）")
    ap.add_argument("--offline", action="store_true", help="只用本地缓存")
    ap.add_argument("--force", action="store_true", help="忽略缓存重新拉取")
    ap.add_argument("--macro-refresh", type=int, default=0, help="1=强制重算宏观择时")
    args = ap.parse_args()

    date = pick_review_day(args.date)
    plan_date = next_day(date)
    print(f"复盘日={date}  计划日={plan_date}")

    cached = None if args.force else load_cache(
        date, require_full=True, allow_stale=args.offline
    )
    if cached and args.offline:
        print("[cache] 命中", cache_path(date))
        day = cached
    elif cached and not args.force:
        print("[cache] 命中", cache_path(date))
        day = cached
    else:
        day = fetch_day(date)
        save_cache(date, day)

    yest = M.prev_trade_day(date)
    ycached = load_cache(yest, allow_stale=args.offline)
    if ycached:
        print(f"[cache] 昨日 {yest} 命中")
        yday = ycached
    else:
        print(f"[fetch] 昨日涨停池 {yest} ...")
        yday = {"zt": em.zt_pool(yest)}
        save_cache(yest, yday, full_day=False)

    zt, dt, zb = day["zt"], day["dt"], day["zb"]
    sh, breadth = day["sh"], day["breadth"]
    yest_zt = yday.get("zt") or []

    zt_n, dt_n, zb_n = len(zt), len(dt), len(zb)
    zb_rate = M.zb_rate(zt_n, zb_n)
    lad = M.ladder(zt)
    promo = M.promotion_rate(zt, yest_zt)
    closes = closes_for_yest_zt(yest_zt)
    premium = M.yest_premium(zt, yest_zt, closes)
    promo["premium"] = premium
    sectors = M.sector_counts(zt)
    # 口径修正：成交额优先用全 A（market_breadth 同轮累加 f6），
    # 但只有样本完整时才采信；否则回退沪指并标注。
    # 原来固定取上证指数 f48 = 仅沪市（当天 8711 亿），而 metrics 的量能阈值是按全 A
    # 量级设的（≥18000/≥12000/≥8000），属于口径错配 → 系统性低估量能分。
    amount_yi, amount_scope = M.pick_market_amount(breadth, sh.get("amount"))
    amount_delta_yi = None
    breadth_source = dict(breadth)  # 本地 Parquet 覆盖后仍要保留东财口径与完整度标记

    # ── 本地 DuckDB/Parquet 优先 ──
    loc = local_day_stats(date)
    if loc.get("ok"):
        print(f"[local] 使用 DuckDB/Parquet  {loc.get('date')}")
        if loc.get("breadth"):
            b = loc["breadth"]
            breadth = {
                "up": b.get("up"),
                "down": b.get("down"),
                "flat": b.get("flat"),
                "total": b.get("total"),
                "red_pct": b.get("red_pct"),
                "amount_yi": breadth_source.get("amount_yi"),
                "fetched": breadth_source.get("fetched"),
                "complete": breadth_source.get("complete"),
                "scope": "本地 Parquet（复权前收盘）",
            }
        if loc.get("ladder"):
            L = loc["ladder"]
            # 保留东财名单若有，否则用本地
            if not lad.get("topBoards") and L.get("topBoards"):
                lad = L
            elif L.get("topBoards"):
                lad = {**lad, **{k: L[k] for k in ("first", "second", "third", "top", "topName", "topBoards", "highs") if k in L}}
        if loc.get("promo") and loc["promo"].get("den"):
            P = loc["promo"]
            promo = {
                "rate": P.get("rate"),
                "num": P.get("num"),
                "den": P.get("den"),
                "names": P.get("names") or [],
                "premium": premium,
            }
        if not zt_n and loc.get("ladder"):
            zt_n = loc["ladder"].get("zt_total") or zt_n
    else:
        print(f"[local] 跳过 ({loc.get('reason') or loc.get('error')})")

    emotion = M.emotion_score(
        zt_n=zt_n,
        dt_n=dt_n,
        zb_rate_pct=zb_rate,
        promo=promo,
        red_pct=breadth.get("red_pct"),
        amount_yi=amount_yi,
        amount_delta_yi=amount_delta_yi,
        sectors=sectors,
        ladder_info=lad,
    )
    emotion["score"]["yesterday"] = None  # 可接昨日缓存
    has_s = sectors and sectors[0]["n"] >= 10 and emotion["total"] >= 75
    regime = M.market_regime(emotion["total"], bool(has_s))
    pos_anchor = M.position_anchor(emotion["total"], bool(has_s))

    # 自选池技术快照（MyTT）
    watch_tech = []
    for w in WATCH_POOL:
        df = Q.daily_kline(w["code"], count=60)
        snap = Q.tech_snapshot(df) if df is not None else {}
        watch_tech.append({**w, "tech": snap})
        time.sleep(0.2)

    # ── 新闻层 ──
    print("[news] 拉取政策/外围/事件快讯 ...")
    news_pack = NEWS.fetch_all_news(date, per_source=35)

    # ── alpha 原料落库（资金流 + 板块情绪，供以后回测）──
    alpha_info = {}
    try:
        import alpha_store

        print("[alpha-store] 落库资金流/情绪 ...")
        alpha_info = alpha_store.run_daily_collect(date)
    except Exception as e:
        print(f"[alpha-store] skip: {e}")
        alpha_info = {"error": str(e)[:120]}

    # ── 宏观择时（利率 + 估值 + 指数动量）──
    macro_regime = {}
    try:
        import json as _json
        from pathlib import Path as _P

        latest_p = _P(__file__).resolve().parent.parent / "data" / "cache" / "macro" / "regime_latest.json"
        if latest_p.exists() and not getattr(args, "macro_refresh", 0):
            macro_regime = _json.loads(latest_p.read_text(encoding="utf-8"))
            print(
                f"[macro] {macro_regime.get('as_of')} state={macro_regime.get('state')} "
                f"pos={macro_regime.get('position')} rate={macro_regime.get('rate_latest')}",
                flush=True,
            )
        else:
            import macro_regime as MR

            print("[macro] 重新计算宏观择时 ...", flush=True)
            macro_regime = MR.run(save=True)
            print(
                f"[macro] {macro_regime.get('as_of')} state={macro_regime.get('state')} "
                f"pos={macro_regime.get('position')}",
                flush=True,
            )
    except Exception as e:
        print(f"[macro] skip: {e}")
        macro_regime = {"error": str(e)[:120]}

    # ── 仓位锚 = 盘面锚 × 宏观系数（宏观只下调、不上调；缺失/滞后则不变）──
    anchor_info = M.blend_position_anchor(
        pos_anchor,
        (macro_regime or {}).get("position"),
        (macro_regime or {}).get("as_of"),
        date,
    )
    anchor_board = float(pos_anchor)
    pos_anchor = anchor_info["final"]
    print(
        f"[anchor] 盘面 {anchor_board} 成 × 宏观系数 {anchor_info['coeff']} → {pos_anchor} 成"
        + (f"（{anchor_info['reason']}）" if anchor_info.get("reason") else ""),
        flush=True,
    )

    # ── PatternComboGate Top10 ──
    pattern_gate = {}
    try:
        import pattern_gate as PG

        print("[pattern-gate] 读取 Top10 形态信号 ...")
        pattern_gate = PG.build_topn_payload(topn=10)
        print(
            f"  信号日 {pattern_gate.get('signalDate')} 仓位 {pattern_gate.get('gate')} "
            f"Top {len(pattern_gate.get('items') or [])}",
            flush=True,
        )
    except Exception as e:
        print(f"[pattern-gate] skip: {e}")
        pattern_gate = {"error": str(e)[:160], "items": []}

    ctx = {
        "zt": zt,
        "dt": dt,
        "zb": zb,
        "zt_n": zt_n,
        "dt_n": dt_n,
        "zb_n": zb_n,
        "zb_rate": zb_rate,
        "ladder_info": lad,
        "promo": promo,
        "premium": premium,
        "breadth": breadth,
        "sh": sh,
        "amount_yi": amount_yi,
        "amount_delta_yi": amount_delta_yi,
        "amount_scope": amount_scope,
        "sectors": sectors,
        "emotion": emotion,
        "regime": regime,
        "pos_anchor": pos_anchor,
        "anchor": anchor_info,
        "has_s": bool(has_s),
        "watch_tech": watch_tech,
        "news": news_pack,
    }

    nar = build_narrative(date, plan_date, ctx)

    # ── 应变矩阵 + 四维评分 + 角色（规则引擎）──
    think = {}
    try:
        import think_matrix as TM

        think = TM.build_report(ctx, watch_tech)
        nar["scenarios"] = think["scenarios"]
        nar["scores"] = think["scores"]
        roles = think.get("roles") or {}
        if roles.get("leader"):
            nar["leaders"] = [
                {
                    "name": x["name"],
                    "h": x.get("h") or "",
                    "verdict": x.get("verdict") or "",
                    "tone": x.get("tone") or "mid",
                }
                for x in roles["leader"]
            ]
        have = {s.get("name") for s in nar["scores"]}
        for x in (roles.get("elastic") or []):
            if x.get("name") in have:
                continue
            nar["scores"].append(
                {
                    "name": x.get("name"),
                    **TM._score_one(theme_score=55, pos_score=50, flow_score=60, hold_score=40),
                    "note": x.get("note") or "20cm 弹性",
                }
            )
        print(
            f"[think] scenarios={len(nar['scenarios'])} scores={len(nar['scores'])} "
            f"roles L/M/E={len(roles.get('leader') or [])}/{len(roles.get('mid') or [])}/{len(roles.get('elastic') or [])}",
            flush=True,
        )
        # 代码→名称
        try:
            from stock_names import get_name_map, pretty

            codes = [s["name"] for s in nar["scores"] if s.get("name")]
            codes += [x["name"] for x in roles.get("leader") or []]
            codes += [x["name"] for x in roles.get("mid") or []]
            nmap = get_name_map([c for c in codes if c])
            for s in nar["scores"]:
                n = s.get("name") or ""
                if n and (n.startswith(("SH", "SZ", "sh", "sz")) or str(n)[:6].isdigit()):
                    s["name"] = pretty(n if n[:2].upper() in ("SH", "SZ") else n.upper(), nmap)
            for L in nar.get("leaders") or []:
                n = L.get("name") or ""
                if n and (n.startswith(("SH", "SZ", "sh", "sz")) or str(n)[:6].isdigit()):
                    L["name"] = pretty(n if n[:2].upper() in ("SH", "SZ") else n.upper(), nmap)
        except Exception:
            pass
    except Exception as e:
        print(f"[think] skip: {e}")

    # 角色落到 leaders / 表1（中军）——放在 try 外，避免中途异常丢更新
    try:
        roles = (think or {}).get("roles") or {}
        if roles.get("leader"):
            from stock_names import get_name_map, pretty as _p

            codes = [x["name"] for x in roles["leader"] if x.get("name")]
            nmap_r = get_name_map(codes)
            nar["leaders"] = [
                {
                    "name": _p(x["name"], nmap_r) if x.get("name") else x.get("name"),
                    "h": x.get("h") or "",
                    "verdict": x.get("verdict") or "",
                    "tone": x.get("tone") or "mid",
                }
                for x in roles["leader"]
            ]
        if roles.get("mid"):
            m0 = roles["mid"][0]
            from stock_names import get_name_map, pretty as _p

            nm = m0.get("name")
            if nm:
                nm = _p(nm, get_name_map([nm]))
            nar["plan1"] = [
                {
                    "pri": "表1·P1",
                    "name": nm,
                    "path": "中军趋势仓（自动识别）",
                    "trig": f"{m0.get('note') or ''}；情绪 {(emotion or {}).get('total')} · {regime}",
                    "kill": "破 MA20 / 主力净流出放大 / 板块涨停家数腰斩",
                    "pos": "≤1 成（中军原则）",
                    "stop": "跌破 MA20 且 30 分钟收不回则减仓",
                }
            ]
        print(
            f"[roles] leader={len(roles.get('leader') or [])} mid={len(roles.get('mid') or [])} "
            f"elastic={len(roles.get('elastic') or [])} plan1={nar['plan1'][0].get('name')}",
            flush=True,
        )
    except Exception as e:
        print(f"[roles] skip: {e}")

    # ── 用 MyTT 技术位覆盖 watch / leaders 名称 ──
    try:
        from stock_names import get_name_map, pretty

        def _pretty_name(n):
            if not n:
                return n
            s = str(n)
            if s[:2].upper() in ("SH", "SZ", "BJ") or s[:6].isdigit():
                return pretty(s if s[:2].upper() in ("SH", "SZ") else s.upper(), nmap)
            return s

        all_codes = []
        for L in nar.get("leaders") or []:
            all_codes.append(L.get("name"))
        for p in nar.get("pool") or []:
            pass
        for w in nar.get("watch") or []:
            all_codes.append(w.get("name"))
        nmap = get_name_map([c for c in all_codes if c])

        for L in nar.get("leaders") or []:
            L["name"] = _pretty_name(L.get("name"))
        # leaders 去重（名称或去掉括号代码后相同）
        import re as _re

        def _key(n):
            return _re.sub(r"（.*?）|\(.*?\)", "", str(n or "")).strip()

        seen_l = set()
        uniq_l = []
        for L in nar.get("leaders") or []:
            k = _key(L.get("name"))
            if k in seen_l:
                continue
            seen_l.add(k)
            uniq_l.append(L)
        nar["leaders"] = uniq_l
        for p in nar.get("pool") or []:
            if p.get("name") and str(p["name"])[:2].upper() in ("SH", "SZ"):
                p["name"] = _pretty_name(p["name"])

        # watch：用 watch_tech 写真实技术位
        tech_map = {w["code"]: w for w in (watch_tech or [])}
        new_watch = []
        for w in nar.get("watch") or []:
            code = None
            for w0 in WATCH_POOL:
                if w0["name"] == w.get("name") or w0["code"] == w.get("name"):
                    code = w0["code"]
                    break
            t = tech_map.get(code) if code else None
            tech = (t or {}).get("tech") or {}
            name = t["name"] if t else w.get("name")
            if tech:
                def _f(x, n=2):
                    try:
                        return f"{float(x):.{n}f}"
                    except Exception:
                        return "—"
                close, ma5, ma20 = tech.get("close"), tech.get("ma5"), tech.get("ma20")
                mom5 = tech.get("pct_5d")
                if close and ma20:
                    pos = "站上MA20" if close >= ma20 else "MA20下方"
                else:
                    pos = "—"
                note = f"收 {_f(close)} · MA5 {_f(ma5)} · MA20 {_f(ma20)} · 5日 {_f(mom5)}% · {pos}"
                det = "可参与（技术位）" if (close and ma20 and close >= ma20) else "观察"
                new_watch.append({"name": name, "d": det, "note": note})
            else:
                new_watch.append({"name": _pretty_name(name), "d": w.get("d"), "note": w.get("note")})
        if new_watch:
            nar["watch"] = new_watch
    except Exception as e:
        print(f"[watch-nap] skip: {e}")

    pools = {
        "zt": zt_n,
        "zb": zb_n,
        "dt": dt_n,
        "zbRate": zb_rate,
        "sealRate": None,
        "jinji": promo["rate"],
        "redPct": breadth.get("red_pct"),
        "amount": f"{amount_yi} 亿" if amount_yi is not None else "—",
        "amountDelta": f"{amount_delta_yi} 亿" if amount_delta_yi is not None else "—",
        "amountScope": amount_scope,
        "shClose": str(sh.get("close") or ""),
        "shChg": f"{sh.get('pct')}%" if sh.get("pct") is not None else "",
        "shLow": str(sh.get("low") or ""),
        "kc50": "",
    }

    payload = build_payload(
        review_day=f"{date}",
        plan_day=f"{plan_date}",
        pools=pools,
        ladder_info=lad,
        promo={k: promo[k] for k in ("rate", "num", "den", "names")},
        premium=premium,
        breadth=breadth,
        amount_yi=amount_yi,
        amount_delta_yi=amount_delta_yi,
        sh_quote=sh,
        sectors=sectors,
        emotion=emotion,
        regime=regime,
        pos_anchor=pos_anchor,
        lines=nar["lines"],
        leaders=nar["leaders"],
        plan1=nar["plan1"],
        plan2=nar["plan2"],
        bans=nar["bans"],
        risks=nar["risks"],
        pool=nar["pool"],
        watch=nar["watch"],
        branches=nar["branches"],
        sources=nar["sources"],
        meta={"promptVer": "auto-v1", "regime": regime, "watch_tech": watch_tech},
    )

    # 封面标题同步（写到 data 里，index 可读）
    payload["cover"] = {
        "kicker": f"A股短线每日复盘 · 自动引擎 · 数据截至 {date}",
        "title": nar["title_main"],
        "titleNum": nar["title_num"],
        "sub": nar["sub"],
        "regime": regime,
        "posAnchor": pos_anchor,
    }
    # 本地 qlib 是否跟上复盘日
    try:
        from qlib_local import calendar_dates

        qlib_last = calendar_dates()[-1] if calendar_dates() else ""
        if qlib_last and qlib_last != date:
            print(
                f"[lag] qlib/Parquet 最新={qlib_last}，复盘日={date}；"
                f"形态信号与本地广度可能滞后，更新 investment_data 后重跑 export_fast/latest_pattern_combo",
                flush=True,
            )
            payload_lag = qlib_last
        else:
            payload_lag = None
    except Exception:
        payload_lag = None

    payload["news"] = news_pack
    payload["alphaStore"] = alpha_info
    payload["patternGate"] = pattern_gate
    payload["scenarios"] = nar.get("scenarios") or []
    payload["scores"] = nar.get("scores") or []
    payload["coreRoles"] = think.get("roles") if isinstance(think, dict) else None
    payload["macroRegime"] = macro_regime
    # 旧的宏观缓存可能没有 position_cheng（比例 → 成）
    _mr = macro_regime or {}
    _cheng = _mr.get("position_cheng")
    if _cheng is None and _mr.get("position") is not None:
        try:
            _cheng = round(float(_mr["position"]) * 10, 1)
        except (TypeError, ValueError):
            _cheng = None
    payload["anchor"] = {
        "board": anchor_info["board"],
        "coeff": anchor_info["coeff"],
        "adjusted": anchor_info["adjusted"],
        "reason": anchor_info.get("reason") or "",
        "macroCheng": _cheng,
        "macroState": _mr.get("state"),
        "macroAsOf": _mr.get("as_of"),
    }
    if payload_lag:
        payload["dataLag"] = {
            "reviewDay": date,
            "qlibLast": payload_lag,
            "note": "形态组合信号基于 qlib 最新交易日，可能早于复盘日",
        }

    out = write_data_js(payload)
    print(f"[done] 写入 {out}")
    print(
        f"  涨停 {zt_n} 炸板 {zb_n}({zb_rate}%) 跌停 {dt_n}  "
        f"晋级 {promo['rate']}%  情绪 {emotion['total']}  {regime}  仓位锚 {pos_anchor} 成"
    )
    if alpha_info:
        print(f"  alpha-store: {alpha_info}")
    print(
        f"  新闻 {news_pack.get('count',0)} 条 "
        f"(政策 {len(news_pack.get('policy',[]))} / 外围 {len(news_pack.get('global',[]))} "
        f"/ 事件 {len(news_pack.get('event',[]))} / 风险 {len(news_pack.get('risk',[]))}) "
        f"源={news_pack.get('sources_ok')}"
    )


if __name__ == "__main__":
    main()
