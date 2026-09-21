# -*- coding: utf-8 -*-
"""东财取数层测试（pytest 兼容；无 pytest 时可直接运行：
    python engine/tests/test_transition_em.py

全部离线：只测纯函数（代码前缀 / in 过滤器 / 相对年绝对化 / 月度修正算术 / 归一化）
以及用 stub 顶替 `sources_em.http` 的解析与自校分支，绝不真的发请求。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

import pandas as pd  # noqa: E402

from transition import SourceError  # noqa: E402
from transition import sources_em as em  # noqa: E402

EPS = 1e-12


class _stub_http:
    """把 sources_em.http 临时换成离线假实现（上下文管理器，退出即还原）。"""

    def __init__(self, responder):
        self.responder = responder

    def __enter__(self):
        self.orig = em.http
        em.http = self.responder
        return self.responder

    def __exit__(self, *exc):
        em.http = self.orig
        return False


def _assert_raises(fn, *needles):
    try:
        fn()
    except SourceError as e:
        msg = str(e)
        for needle in needles:
            assert needle in msg, f"错误信息缺少 {needle!r}: {msg}"
        return msg
    raise AssertionError("本应抛 SourceError，但没有抛")


# ── 代码 → 市场前缀 ───────────────────────────────────────────────────
def test_six_digit_and_market_prefix():
    assert em.six_digit("002708") == "002708"
    assert em.six_digit("SZ002708") == "002708"
    assert em.six_digit("002708.SZ") == "002708"
    assert em.six_digit("sh600519") == "600519"

    assert em.market_prefix("600519") == "SH"
    assert em.market_prefix("688981") == "SH"    # 科创板
    assert em.market_prefix("900901") == "SH"    # 9 开头
    assert em.market_prefix("500001") == "SH"    # 5 开头
    assert em.market_prefix("002708") == "SZ"
    assert em.market_prefix("300903") == "SZ"
    assert em.market_prefix("000001.SZ") == "SZ"
    assert em.market_prefix("sz300903") == "SZ"
    assert em.market_code("002708") == "SZ002708"
    assert em.market_code("600519.SH") == "SH600519"

    for bad in ("2708", "0027081", "", None, "ABCDEF", "0000011"):
        _assert_raises(lambda b=bad: em.market_prefix(b), "非法股票代码")


# ── in (...) 过滤器 ───────────────────────────────────────────────────
def test_in_filter_syntax():
    # 实测口径：`in (...)` 返回数据，字面量逗号列表返回 0 行
    assert em.in_filter("SECURITY_CODE", ["000001", "000002"]) == \
        '(SECURITY_CODE in ("000001","000002"))'
    assert em.in_filter("SECURITY_CODE", ["000001"]) == '(SECURITY_CODE in ("000001"))'
    _assert_raises(lambda: em.in_filter("SECURITY_CODE", []), "空列表")
    _assert_raises(lambda: em.in_filter("SECURITY_CODE", ["", "  "]), "空列表")


def test_batches():
    assert [len(b) for b in em.batches(range(700), 300)] == [300, 300, 100]
    assert em.batches([], 300) == []
    assert em.FINANCIALS_BATCH < 400      # 实测 500 只代码触发 HTTP 400
    _assert_raises(lambda: em.batches([1], 0), "size 非法")


# ── 相对年 → 绝对年（currentYear 打桩）────────────────────────────────
def test_absolute_eps_years_uses_current_year():
    # 题目要求：用响应体的 currentYear 绝对化，不许硬编码年份
    assert em.absolute_eps_years(2026) == {"this": 2026, "next": 2027, "next_two": 2028}
    assert em.absolute_eps_years("2025") == {"this": 2025, "next": 2026, "next_two": 2027}
    # 早于 currentYear 发布：卖方当年的「今年」是发布年
    assert em.absolute_eps_years(2026, 2025) == {"this": 2025, "next": 2026, "next_two": 2027}
    # 发布年晚于 currentYear（脏数据）→ 仍以 currentYear 为准
    assert em.absolute_eps_years(2026, 2027)["this"] == 2026
    assert em.absolute_eps_years(None, 2025)["this"] == 2025
    _assert_raises(lambda: em.absolute_eps_years(None), "无法确定 EPS 年份基准")
    _assert_raises(lambda: em.absolute_eps_years("", ""), "无法确定 EPS 年份基准")

    assert em.publish_year_of("2026-09-17 00:00:00.000") == 2026
    assert em.publish_year_of("20260917") == 2026
    assert em.publish_year_of(None) is None
    assert em.publish_year_of("") is None


# ── revision_1m 算术 ─────────────────────────────────────────────────
def test_revision_1m_arithmetic():
    # 002708 实测：2026E EPS 0.353333333333 vs EPS_LASTMONTHS 0.52 → 一个月下修约 32%
    by_year = [
        {"year": 2025, "year_mark": "A", "eps": 0.17964489989, "eps_last_month": 0.17964489989},
        {"year": 2026, "year_mark": "E", "eps": 0.353333333333, "eps_last_month": 0.52},
        {"year": 2027, "year_mark": "E", "eps": 0.483333333333, "eps_last_month": 0.74},
    ]
    r = em.revision_1m(by_year)
    assert abs(r - (0.353333333333 - 0.52) / 0.52) < EPS
    assert -0.33 < r < -0.32, r

    # 只取最近的一个 E 年，且实际年份（A）不参与
    assert abs(em.revision_1m([
        {"year": 2027, "year_mark": "E", "eps": 0.75, "eps_last_month": 0.50},
        {"year": 2026, "year_mark": "E", "eps": 0.30, "eps_last_month": 0.20},
        {"year": 2025, "year_mark": "A", "eps": 9.0, "eps_last_month": 1.0},
    ]) - 0.5) < EPS

    # 上月值相同 → 0.0（是「无修正」，不是缺失）
    assert em.revision_1m([{"year": 2026, "year_mark": "E", "eps": 0.74, "eps_last_month": 0.74}]) == 0.0

    # 缺值 / 上月为 0 / 无预测年 → None
    assert em.revision_1m([]) is None
    assert em.revision_1m(None) is None
    assert em.revision_1m([{"year": 2026, "year_mark": "E", "eps": None, "eps_last_month": 0.52}]) is None
    assert em.revision_1m([{"year": 2026, "year_mark": "E", "eps": 0.3, "eps_last_month": 0}]) is None
    assert em.revision_1m([{"year": 2025, "year_mark": "A", "eps": 0.1, "eps_last_month": 0.1}]) is None

    # YEAR_MARK 整列缺失（契约漂移）时退化为「第一个真正变化的年份」，不静默返回 None
    drift = em.revision_1m([
        {"year": 2025, "eps": 0.2, "eps_last_month": 0.2},
        {"year": 2026, "eps": 0.3533, "eps_last_month": 0.52},
    ])
    assert drift is not None and abs(drift - (0.3533 - 0.52) / 0.52) < 1e-9


# ── DataFrame 归一化 ─────────────────────────────────────────────────
def test_normalise_records_whitelist_and_fill():
    df = em.normalise_records([{"A": 1, "B": 2}], ["B", "C"])
    assert list(df.columns) == ["B", "C"]          # 白名单列 + 固定顺序，多余列丢弃
    assert df.loc[0, "B"] == 2 and pd.isna(df.loc[0, "C"])
    assert em.normalise_records([], ["B", "C"]).empty


def test_consensus_frame_normalisation():
    rows = [{
        "SECURITY_CODE": "000001", "SECURITY_NAME_ABBR": "平安银行",
        "YEAR1": 2025, "YEAR_MARK1": "A", "EPS1": 2.196907127249,
        "YEAR2": 2026, "YEAR_MARK2": "E", "EPS2": 2.1776,
        "RATING_ORG_NUM": 25, "RATING_BUY_NUM": 13,
        "DEC_AIMPRICEMAX": 15.1, "DEC_AIMPRICEMIN": "",     # 空串 → NaN，不能变成 0
        "INDUSTRY_BOARD": "银行Ⅱ", "CONCEPTINDEX_BOARD": "HS300_,区块链",
    }]
    df = em.consensus_frame(rows)
    assert df.index.name == "SECURITY_CODE" and list(df.index) == ["000001"]
    assert set(em.CONSENSUS_COLUMNS) - {"SECURITY_CODE"} <= set(df.columns)
    assert df.loc["000001", "EPS1"] == 2.196907127249
    assert df.loc["000001", "DEC_AIMPRICEMAX"] == 15.1
    assert pd.isna(df.loc["000001", "DEC_AIMPRICEMIN"])
    assert pd.isna(df.loc["000001", "EPS3"])                # 接口没给的列必须补齐
    assert str(df["YEAR1"].dtype) == "Int64"
    assert str(df["EPS1"].dtype) == "float64"
    assert df.loc["000001", "SECURITY_NAME_ABBR"] == "平安银行"

    # 带前缀的代码也要归一到 6 位
    assert list(em.consensus_frame([{"SECURITY_CODE": "600519.SH"}]).index) == ["600519"]

    empty = em.consensus_frame([])                          # 空输入形状稳定
    assert empty.empty and empty.index.name == "SECURITY_CODE"
    assert set(em.CONSENSUS_COLUMNS) - {"SECURITY_CODE"} <= set(empty.columns)


def test_financials_frame_normalisation():
    rows = [{
        "SECURITY_CODE": "000001", "REPORTDATE": "2025-06-30 00:00:00", "QDATE": "2025Q2",
        "DATATYPE": "2025年 半年报", "TOTAL_OPERATE_INCOME": 69385000000,
        "PARENT_NETPROFIT": 24870000000, "YSTZ": -10.0438209822, "SJLTZ": -3.9,
        "BASIC_EPS": 1.18, "WEIGHTAVG_ROE": 5.25, "NOTICE_DATE": "2025-08-23 00:00:00",
        "XSMLL": None, "BOARD_NAME": "银行Ⅱ",
    }]
    df = em.financials_frame(rows)
    assert list(df.columns) == em.FINANCIALS_COLUMNS        # 累计口径字段白名单
    assert df.loc[0, "REPORTDATE"] == "2025-06-30"
    assert df.loc[0, "NOTICE_DATE"] == "2025-08-23"
    assert df.loc[0, "QDATE"] == "2025Q2"
    assert abs(df.loc[0, "YSTZ"] - (-10.0438209822)) < EPS
    assert df.loc[0, "PARENT_NETPROFIT"] == 24870000000.0
    assert em.financials_frame([]).empty


def test_research_frame_years_and_dates():
    row = {
        "stockCode": "002708", "stockName": "光洋股份",
        "publishDate": "2026-09-17 00:00:00.000", "orgSName": "东吴证券",
        "researcher": "黄细里,郭雨蒙", "title": "轴承龙头内生外延",
        "emRatingName": "买入", "emRatingValue": "3", "lastEmRatingName": "买入",
        "ratingChange": 3, "indvAimPriceT": "", "indvAimPriceL": "",
        "predictThisYearEps": "0.2700000000", "predictThisYearPe": "63.8500000000",
        "predictNextYearEps": "0.3400000000", "predictNextYearPe": "51.0800000000",
        "predictNextTwoYearEps": "0.6000000000", "predictNextTwoYearPe": "28.9400000000",
    }
    df = em.research_frame([row], 2026)                      # currentYear 打桩
    r = df.iloc[0]
    assert (r["year_this"], r["year_next"], r["year_next_two"]) == (2026, 2027, 2028)
    assert r["publishDate"] == "2026-09-17"
    assert abs(r["predictThisYearEps"] - 0.27) < EPS          # 字符串 → float
    assert r["ratingChange"] == 3 and str(df["ratingChange"].dtype) == "Int64"
    assert pd.isna(r["indvAimPriceT"])                        # 4.7% 覆盖率 → 空值留 NaN
    assert set(em.REPORT_COLUMNS) <= set(df.columns)

    # 早于 currentYear 发布的研报：以发布年为基准
    older = em.research_frame([dict(row, publishDate="2025-05-26 00:00:00.000")], 2026)
    assert older.iloc[0]["year_this"] == 2025

    # 2017-01-02 之前的区间：合法空集，不抛错
    empty = em.research_frame([], None)
    assert empty.empty and "year_this" in empty.columns
    assert str(empty["year_this"].dtype) == "Int64"

    # currentYear 缺失但发布日可解析 → 仍能绝对化（不硬编码年份）
    assert em.research_frame([row], None).iloc[0]["year_this"] == 2026

    # 两个基准都拿不到 → 显式报错
    _assert_raises(lambda: em.research_frame([dict(row, publishDate=None)], None),
                   "无法确定 EPS 年份基准")


# ── stub 掉的接口分支 ────────────────────────────────────────────────
def test_consensus_guards():
    def few(method, url, **kw):
        return {"result": {"pages": 1, "count": 1, "data": [{"SECURITY_CODE": "000001"}]}}

    with _stub_http(few):
        _assert_raises(em.consensus_snapshot, "一致预期行数异常", "契约变更")

    # result 为 null（真实观测：success=false / code 9201 「返回数据为空」）→ 仍是 0 行，量级自校报错
    with _stub_http(lambda m, u, **kw: {"result": None, "success": False, "code": 9201}):
        _assert_raises(em.consensus_snapshot, "一致预期行数异常")

    def truncated(method, url, **kw):
        # 声明 3000 行 / 6 页，却每页只回 1 行 → 分页自校必须报错
        return {"result": {"pages": 6, "count": 3000, "data": [{"SECURITY_CODE": "000001"}]}}

    with _stub_http(truncated):
        _assert_raises(em.consensus_snapshot, "分页不完整")


def test_profit_forecast_stub():
    payload = {
        "pjtj": [
            {"DATE_TYPE": "1月内", "DATE_TYPE_CODE": 1, "RATING_ORG_NUM": 2,
             "RATING_BUY_NUM": 2, "COMPRE_RATING": "买入"},
            {"DATE_TYPE": "1年内", "DATE_TYPE_CODE": 5, "RATING_ORG_NUM": 3,
             "RATING_BUY_NUM": 3, "COMPRE_RATING": "买入"},
        ],
        "yctj_list": [
            {"YEAR": 2026, "YEAR_MARK": "E", "EPS": 0.353333333333,
             "EPS_LASTMONTHS": 0.52, "BVPS": 3.48, "ROE": 10.34,
             "PARENT_NETPROFIT": 199000000, "TOTAL_OPERATE_INCOME": 3419333333.33, "RANK": 4},
            {"YEAR": 2025, "YEAR_MARK": "A", "EPS": 0.17964489989,
             "EPS_LASTMONTHS": 0.17964489989, "BVPS": 3.08, "ROE": 6,
             "PARENT_NETPROFIT": 100978033.01, "TOTAL_OPERATE_INCOME": 2716372918.5, "RANK": 3},
            {"YEAR": 2027, "YEAR_MARK": "E", "EPS": 0.483333333333,
             "EPS_LASTMONTHS": 0.74, "BVPS": 4.05, "ROE": 12.46,
             "PARENT_NETPROFIT": 271333333.33, "TOTAL_OPERATE_INCOME": 4413000000, "RANK": 5},
        ],
        "ycmx": [{
            "ORG_NAME_ABBR": "东吴证券", "RESEARCHER": "黄细里,郭雨蒙", "RATING": "买入",
            "PUBLISH_DATE": "2026-09-17 00:00:00",
            "YEAR1": 2025, "YEAR_MARK1": "A", "EPS1": 0.17964489989, "PARENT_NETPROFIT1": 100978033.01,
            "YEAR2": 2026, "YEAR_MARK2": "E", "EPS2": 0.27, "PARENT_NETPROFIT2": 152000000,
            "YEAR3": None, "YEAR_MARK3": None, "EPS3": None, "PARENT_NETPROFIT3": None,
        }],
    }
    seen = {}

    def responder(method, url, **kw):
        assert "ProfitForecast" in url
        seen.update(kw.get("params") or {})
        return payload

    with _stub_http(responder):
        pf = em.profit_forecast("002708")

    assert seen["code"] == "SZ002708"                 # 市场前缀由模块自己补
    assert pf["code"] == "002708" and pf["market_code"] == "SZ002708"
    assert [r["year"] for r in pf["by_year"]] == [2025, 2026, 2027]   # 按年升序
    assert abs(pf["by_year"][1]["eps"] - 0.353333333333) < EPS
    assert abs(pf["revision_1m"] - (0.353333333333 - 0.52) / 0.52) < EPS
    assert pf["rating_org_num"] == 2                  # 取「1月内」窗口
    assert [w["window"] for w in pf["rating_windows"]] == ["1月内", "1年内"]
    assert pf["ycmx"][0]["org"] == "东吴证券"
    assert pf["ycmx"][0]["publish_date"] == "2026-09-17"
    assert pf["ycmx"][0]["eps"] == [0.17964489989, 0.27]        # 缺 YEAR 的槽位被跳过
    assert pf["ycmx"][0]["years"] == [2025, 2026]

    for bogus in ({"yctj_list": None, "yctj_chart": []}, {"yctj_list": [], "yctj_chart": None},
                  {"yctj_list": None, "yctj_chart": None}):
        with _stub_http(lambda m, u, _b=bogus, **kw: _b):
            _assert_raises(lambda: em.profit_forecast("002708"), "无盈利预测明细")


def test_financials_filter_and_empty_guard():
    seen = []

    def empty_result(method, url, **kw):
        seen.append((kw.get("params") or {}).get("filter"))
        return {"version": None, "result": None, "success": False,
                "message": "返回数据为空", "code": 9201}

    with _stub_http(empty_result):
        # 给了代码却 0 行 → 显式报错，而不是返回空表
        _assert_raises(lambda: em.financials(["000001", "000002"]), "0 行", "SECURITY_CODE in")
    assert seen and seen[0] == '(SECURITY_CODE in ("000001","000002"))'

    # 空列表不得静默降级为全市场
    _assert_raises(lambda: em.financials([]), "空列表")

    def full_market(method, url, **kw):
        assert "filter" not in (kw.get("params") or {})       # codes=None 全程不带 filter
        return {"result": {"pages": 1, "count": 1, "data": [
            {"SECURITY_CODE": "000001", "REPORTDATE": "2026-06-30 00:00:00", "QDATE": "2026Q2",
             "DATATYPE": "2026年 半年报", "TOTAL_OPERATE_INCOME": 1.0, "PARENT_NETPROFIT": 2.0,
             "YSTZ": 20.87, "SJLTZ": 89.3, "BASIC_EPS": 1.5, "WEIGHTAVG_ROE": 10.0,
             "NOTICE_DATE": "2026-08-15 00:00:00"}]}}

    with _stub_http(full_market):
        df = em.financials()
    assert len(df) == 1 and df.loc[0, "SECURITY_CODE"] == "000001"

    # 分批：700 个代码 → 3 次请求，且每批都走 in (...)
    filters = []

    def batched(method, url, **kw):
        filters.append((kw["params"])["filter"])
        return {"result": {"pages": 1, "count": 1, "data": [
            {"SECURITY_CODE": "000001", "REPORTDATE": "2026-06-30 00:00:00"}]}}

    codes = ["%06d" % (i + 1) for i in range(700)]
    with _stub_http(batched):
        em.financials(codes)
    assert len(filters) == 3 and all(" in (" in f for f in filters)
    assert all(f.count('"') // 2 <= em.FINANCIALS_BATCH for f in filters)


def test_research_reports_pagination_stub():
    calls = []
    rec = {"stockCode": "002708", "publishDate": "2026-09-17 00:00:00.000",
           "orgSName": "东吴证券", "emRatingValue": "3", "ratingChange": 3}

    def responder(method, url, **kw):
        p = kw["params"]
        calls.append(p["pageNo"])
        assert p["qType"] == 0 and p["pageSize"] == em.REPORT_PAGE_SIZE
        assert p["beginTime"] == "2026-01-01" and p["endTime"] == "2026-09-18"
        data = [rec, rec] if p["pageNo"] == 1 else [rec]
        return {"hits": 3, "TotalPage": 2, "currentYear": 2026, "data": data}

    with _stub_http(responder):
        df = em.research_reports("002708", "2026-01-01", "2026-09-18")
    assert calls == [1, 2]                            # pageSize 被截到 100 → 必须翻页
    assert len(df) == 3 and list(df["year_this"]) == [2026] * 3

    # 2017-01-02 之前的区间：合法空集（实测返回 0 条），不抛错
    def empty_range(method, url, **kw):
        assert kw["params"]["beginTime"] == "2015-01-01"
        return {"hits": 0, "TotalPage": 0, "currentYear": 2026, "data": []}

    with _stub_http(empty_range):
        df0 = em.research_reports("002708", "20150101", "2016-12-31")
    assert df0.empty and "year_this" in df0.columns

    # 声明 hits>0 却无 data → 契约异常
    def broken(method, url, **kw):
        return {"hits": 5, "TotalPage": 1, "currentYear": 2026, "data": []}

    with _stub_http(broken):
        _assert_raises(lambda: em.research_reports("002708", "2026-01-01", "2026-09-18"), "契约异常")


def test_parse_announcements():
    raw = {"data": {"list": [
        {"art_code": "AN1", "notice_date": "2026-09-17 00:00:00",
         "title": "科翔股份:2026年限制性股票激励计划(草案)",
         "columns": [{"column_name": "股权激励计划"}]},
        {"art_code": "AN2", "notice_date": "2026-09-17 00:00:00", "title": "法律意见书",
         "columns": [{"column_name": "法律意见书"}, {"column_name": None}]},
    ], "page_index": 1, "page_size": 50, "total_hits": 2}, "error": "", "success": 1}

    out = em.parse_announcements(raw)
    assert [o["art_code"] for o in out] == ["AN1", "AN2"]
    assert out[0]["notice_date"] == "2026-09-17"
    assert out[0]["column_names"] == ["股权激励计划"]      # 精确分类，滤得掉法律意见书
    assert out[1]["column_names"] == ["法律意见书"]        # 空分类名被剔除

    # 公司只是没有公告：空列表是合法结果
    assert em.parse_announcements({"data": {"list": [], "total_hits": 0}, "success": 1}) == []

    for bad in ({"data": None, "success": 0},
                {"data": {"page_index": 1}, "success": 1},
                {"data": {"list": None}, "success": 1},
                {"data": {"list": []}, "error": "boom", "success": 1},
                "not-a-dict"):
        _assert_raises(lambda b=bad: em.parse_announcements(b), "公告接口")


def test_announcement_columns_request_shape():
    seen = {}

    def responder(method, url, **kw):
        assert "security/ann" in url
        seen.update(kw["params"])
        return {"data": {"list": [], "total_hits": 0}, "success": 1}

    with _stub_http(responder):
        assert em.announcement_columns("SZ300903", 50) == []
    assert seen == {"page_size": 50, "page_index": 1, "ann_type": "A",
                    "client_source": "web", "stock_list": "300903",
                    "f_node": 0, "s_node": 0}
    _assert_raises(lambda: em.announcement_columns("300903", 0), "page_size 非法")


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
