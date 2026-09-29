# -*- coding: utf-8 -*-
"""设计审计修复的引擎侧回归测试（离线，不联网）。

    python engine/tests/test_audit_fixes.py

覆盖 spec design-audit-fixes.md 的 T2/T3/T4：
  * 封单单位 元→亿（此前 /1e4 标「亿」放大了 1 万倍）
  * 龙头风险分级（替代原来 5 行重复的模板结论）
  * 新闻摘要回退 / 同主题去重 / 晚于复盘日的条目剔除
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

import eastmoney as em  # noqa: E402
import metrics as M  # noqa: E402
import news as NEWS  # noqa: E402

# 实测锚点：2026-09-16 有研硅 涨停池 fund 原值
YANSHA_FUND_RAW = 214910502


# ── T2 封单单位 ─────────────────────────────────────────────────────────────
def test_fund_yuan_to_yi():
    """有研硅 raw=214910502 元 → 2.15 亿（此前页面显示 21491.05 亿）。"""
    assert round(em.yi(YANSHA_FUND_RAW), 2) == 2.15


def test_fund_not_the_old_1e4_scale():
    """回归护栏：绝不能退回 /1e4。"""
    v = em.yi(YANSHA_FUND_RAW)
    assert v != YANSHA_FUND_RAW / 1e4
    assert abs(v - YANSHA_FUND_RAW / 1e4) > 1000


def test_fund_zero_and_junk():
    assert em.yi(0) == 0.0
    assert em.yi(None) == 0.0
    assert em.yi("") == 0.0
    assert em.yi("abc") == 0.0
    assert em.yi(1e8) == 1.0


# ── T4 龙头风险分级 ─────────────────────────────────────────────────────────
def test_leader_risk_top_board_always_high():
    assert M.leader_risk(6, 99.0, 1.0, is_top=True) == "高"
    assert M.leader_risk(1, 99.0, 1.0, is_top=True) == "高"


def test_leader_risk_seal_ratio():
    """封单/成交 < 0.3 说明承接弱 → 高；否则 中。"""
    assert M.leader_risk(2, 2.0, 39.0) == "高"     # 2.15/39 ≈ 0.05
    assert M.leader_risk(2, 20.0, 30.0) == "中"    # 0.67


def test_leader_risk_missing_turnover_is_unknown():
    """成交缺失时不猜测，返回 —。"""
    assert M.leader_risk(2, 2.0, 0) == "—"
    assert M.leader_risk(2, 2.0, None) == "—"
    assert M.leader_risk(2, 2.0, "x") == "—"


# ── T3 新闻清洗 ─────────────────────────────────────────────────────────────
def _pack(items):
    return {"policy": list(items), "global": [], "event": [], "risk": [],
            "count": len(items), "sources_ok": ["sina"]}


def test_news_clears_summary_when_same_as_title():
    """无独立摘要时 summary 必须置空 —— 否则前端会把标题渲染两遍。"""
    t = "美国财政部召集全球金融机构推进经济弃儿行动"
    p = NEWS.clean_pack(_pack([{"time": "2026-09-16 01:00:00", "title": t,
                                "summary": t, "source": "新浪"}]), "2026-09-16")
    assert p["policy"][0]["summary"] == "", p["policy"][0]


def test_news_keeps_distinct_summary():
    t = "某公司公告重大合同"
    p = NEWS.clean_pack(_pack([{"time": "2026-09-16 01:00:00", "title": t,
                                "summary": "合同金额约 3 亿元，不具法律约束力", "source": "东财"}]), "2026-09-16")
    assert "3 亿元" in p["policy"][0]["summary"]


def test_news_dedups_same_topic_headlines():
    """同主题条目（归一化标题前 12 字相同）只保留最早一条。"""
    items = [
        {"time": "2026-09-16 03:00:00", "title": "加拿大央行会议纪要显示，委员们同意重申立场", "summary": "a", "source": "新浪"},
        {"time": "2026-09-16 02:00:00", "title": "加拿大央行会议纪要显示，委员认为通胀蔓延", "summary": "b", "source": "新浪"},
        {"time": "2026-09-16 01:00:00", "title": "完全不同的标题：A股半导体板块异动", "summary": "c", "source": "东财"},
    ]
    p = NEWS.clean_pack(_pack(items), "2026-09-16")
    titles = [x["title"] for x in p["policy"]]
    assert len(titles) == 2, titles
    assert any("半导体" in t for t in titles)
    # 两条加拿大纪要只留一条
    assert sum(1 for t in titles if "加拿大" in t) == 1


def test_news_drops_entries_after_review_day():
    """复盘页只呈现复盘日及以前的信息。"""
    items = [
        {"time": "2026-09-17 00:29:30", "title": "复盘日之后的快讯", "summary": "", "source": "新浪"},
        {"time": "2026-09-16 23:00:00", "title": "复盘日当天的快讯", "summary": "", "source": "新浪"},
        {"time": "09-17 01:33:00", "title": "无年份但晚于复盘日", "summary": "", "source": "新浪"},
    ]
    p = NEWS.clean_pack(_pack(items), "2026-09-16")
    titles = [x["title"] for x in p["policy"]]
    assert titles == ["复盘日当天的快讯"], titles


def test_news_dedup_keeps_earliest_not_first_in_list():
    """源列表常为最新在前；去重必须取最早一条，不是列表首条。"""
    items = [
        {"time": "2026-09-16 18:00:00", "title": "加拿大央行会议纪要显示，较新的那条", "summary": "new", "source": "s"},
        {"time": "2026-09-16 02:00:00", "title": "加拿大央行会议纪要显示，较早的那条", "summary": "old", "source": "s"},
    ]
    p = NEWS.clean_pack(_pack(items), "2026-09-16")
    assert len(p["policy"]) == 1
    assert p["policy"][0]["summary"] == "old", p["policy"][0]


def test_news_late_entries_dropped_by_default():
    """默认口径是「剔除」，不是「标注」；标注只是显式逃生口。"""
    items = [{"time": "2026-09-17 01:00:00", "title": "复盘日之后", "summary": "", "source": "s"}]
    assert NEWS.clean_pack(_pack(items), "2026-09-16")["policy"] == []
    kept = NEWS.clean_pack(_pack(items), "2026-09-16", allow_late=True)["policy"]
    assert len(kept) == 1 and "复盘日后" in kept[0]["summary"]


def test_resolve_name_strips_market_prefix():
    """本地梯子给 sh600825 这类带前缀代码，名称缓存按 6 位码存。

    解析失败时必须退化为 6 位码而不是把 sh600825 直接显示到页面上。
    """
    nm = {"600825": "新华传媒", "000001": "平安银行"}
    from stock_names import resolve_name

    assert resolve_name("sh600825", nm) == "新华传媒"
    assert resolve_name("SH600825", nm) == "新华传媒"
    assert resolve_name("600825", nm) == "新华传媒"
    assert resolve_name("sz000001", nm) == "平安银行"
    # 查不到名字时退化为 6 位码，绝不返回带前缀的原始串
    assert resolve_name("sh999999", nm) == "999999"
    assert resolve_name("999999", nm) == "999999"
    assert resolve_name(None, nm) == ""
    assert resolve_name("", nm) == ""


def test_news_count_recomputed():
    items = [{"time": "2026-09-16 01:00:00", "title": "标题一", "summary": "s", "source": "a"},
             {"time": "2026-09-17 01:00:00", "title": "标题二", "summary": "s", "source": "a"}]
    p = NEWS.clean_pack(_pack(items), "2026-09-16")
    assert p["count"] == 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
