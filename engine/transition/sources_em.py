# -*- coding: utf-8 -*-
"""东财结构化取数：一致预期 / 盈利预测 / 财报 / 研报 / 公告分类。

契约来自 research/business-transition-pricing/REPORT.md 第三 / 六 / 九节的直接探测，
代码里不假设任何「显然是那样」的参数：

  consensus_snapshot()     RPT_WEB_RESPREDICT —— 免密钥免 cookie，pageSize=500 × 6 页 ≈ 2916 只
  profit_forecast(code)    PC_HSF10/ProfitForecast/PageAjax —— code 须带市场前缀；
                           EPS_LASTMONTHS 是唯一可直接观测的月度修正快照
  financials(codes)        RPT_LICO_FN_CPD —— 季报累计口径；filter 必须写 `(SECURITY_CODE in (...))`
  research_reports(...)    reportapi report/list —— 4 个必填参数；pageSize 被服务端静默截到 100；
                           相对年 EPS 的基准是研报的发布年（见 absolute_eps_years 的实测证据）
  announcement_columns()   np-anotice-stock/api/security/ann —— columns[].column_name 直接是分类名

**目标价一律取 consensus_snapshot() 的 DEC_AIMPRICEMAX/MIN（实测填充率约 73%）。**
research_reports() 的 indvAimPriceT/L 只有 4.7% 的研报携带（1500 篇近月个股研报里 71 篇），
任何依赖它的目标价逻辑会产出 95% 以上空值，因此本模块只原样透传这两个字段，绝不用作目标价来源。

纪律：所有取数函数在「量级不对 / 契约变成 null」时抛 SourceError，而不是返回空壳对象——
调研已确认东财多个端点在参数非法时返回 result=null 或 0 条且 HTTP 200。
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Iterator, Sequence

import pandas as pd

from . import SourceError, TRANSITION_DIR, http

# ── 端点 ──────────────────────────────────────────────────────────────
DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HSF10_PROFIT_FORECAST_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/ProfitForecast/PageAjax"
)
REPORT_LIST_URL = "https://reportapi.eastmoney.com/report/list"
ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"

# ── 实测常量（变动即等于契约变更，必须重新探测）────────────────────────
DC_PAGE_SIZE = 500            # datacenter-web 硬上限：传 1000 / 5000 也只回 500 行
CONSENSUS_MIN_ROWS = 2500     # RPT_WEB_RESPREDICT 实测 2916 只，跌破即契约变了
CONSENSUS_PARQUET = TRANSITION_DIR / "consensus.parquet"
FINANCIALS_BATCH = 300        # 实测 500 只代码触发 HTTP 400（600 只 414），留足余量
REPORT_PAGE_SIZE = 100        # report/list 的 pageSize 被静默截到 100
REPORT_MAX_PAGES = 200        # 单股单区间不可能超过 2 万篇研报
DC_MAX_PAGES = 1200           # 分页保护：RPT_LICO_FN_CPD 全市场实测 993 页
SH_PREFIXES = ("6", "9", "5")  # 6/9/5 → 沪市，其余 → 深市（东财 F10 的 code 前缀口径）

# ── 列白名单 ──────────────────────────────────────────────────────────
CONSENSUS_COLUMNS = [
    "SECURITY_CODE", "SECURITY_NAME_ABBR",
    "YEAR1", "YEAR_MARK1", "EPS1",
    "YEAR2", "YEAR_MARK2", "EPS2",
    "YEAR3", "YEAR_MARK3", "EPS3",
    "YEAR4", "YEAR_MARK4", "EPS4",
    "RATING_ORG_NUM", "RATING_BUY_NUM",
    "DEC_AIMPRICEMAX", "DEC_AIMPRICEMIN",   # ← 唯一可用的目标价来源
    "INDUSTRY_BOARD", "CONCEPTINDEX_BOARD",  # CONCEPTINDEX_BOARD 可直接做转型题材池
]
CONSENSUS_YEAR_COLUMNS = ["YEAR1", "YEAR2", "YEAR3", "YEAR4"]
CONSENSUS_NUMERIC_COLUMNS = [
    "EPS1", "EPS2", "EPS3", "EPS4",
    "RATING_ORG_NUM", "RATING_BUY_NUM",
    "DEC_AIMPRICEMAX", "DEC_AIMPRICEMIN",
]

FINANCIALS_COLUMNS = [
    "SECURITY_CODE", "REPORTDATE", "QDATE", "DATATYPE",
    "TOTAL_OPERATE_INCOME", "PARENT_NETPROFIT",
    "YSTZ", "SJLTZ",                      # 营收 / 归母净利同比（%）
    "BASIC_EPS", "WEIGHTAVG_ROE", "NOTICE_DATE",
]
FINANCIALS_NUMERIC_COLUMNS = [
    "TOTAL_OPERATE_INCOME", "PARENT_NETPROFIT",
    "YSTZ", "SJLTZ", "BASIC_EPS", "WEIGHTAVG_ROE",
]

REPORT_COLUMNS = [
    "stockCode", "stockName", "publishDate", "orgSName", "researcher", "title",
    "emRatingName", "emRatingValue", "lastEmRatingName", "ratingChange",
    "indvAimPriceT", "indvAimPriceL",     # 覆盖率仅 4.7%，只透传、不做目标价
    "predictThisYearEps", "predictThisYearPe",
    "predictNextYearEps", "predictNextYearPe",
    "predictNextTwoYearEps", "predictNextTwoYearPe",
]
# 相对年列 → 绝对年列（用响应体的 currentYear / 发布年绝对化，见 absolute_eps_years）
REPORT_YEAR_COLUMNS = ["year_this", "year_next", "year_next_two"]
REPORT_NUMERIC_COLUMNS = [
    "predictThisYearEps", "predictThisYearPe",
    "predictNextYearEps", "predictNextYearPe",
    "predictNextTwoYearEps", "predictNextTwoYearPe",
    "indvAimPriceT", "indvAimPriceL",
]
REPORT_INT_COLUMNS = ["ratingChange", "emRatingValue"]


# ── 纯工具 ────────────────────────────────────────────────────────────
def six_digit(code: Any) -> str:
    """任意写法（`002708` / `SZ002708` / `002708.SZ`）→ 6 位代码；否则 SourceError。"""
    digits = re.sub(r"\D", "", "" if code is None else str(code))
    if len(digits) != 6:
        raise SourceError(
            f"非法股票代码 {code!r}：规范化后得到 {digits!r}，应为 6 位数字"
        )
    return digits


def _six_soft(code: Any) -> str | None:
    """行数据里的代码：非法只降级为 None，不打断整批（用户入参才用 six_digit 硬校验）。"""
    try:
        return six_digit(code)
    except SourceError:
        return None


def market_prefix(code: Any) -> str:
    """6/9/5 开头 → `SH`，其余 → `SZ`。"""
    return "SH" if six_digit(code)[0] in SH_PREFIXES else "SZ"


def market_code(code: Any) -> str:
    """东财 F10 口径的带前缀代码：`002708` → `SZ002708`。"""
    return market_prefix(code) + six_digit(code)


def in_filter(field: str, values: Sequence[Any]) -> str:
    """datacenter-web 的批量过滤串。

    实测：(SECURITY_CODE in ("000001","000002")) → 239 行；
          (SECURITY_CODE="000001,000002")       → result 为 null、0 行（静默失效，不报错）。
    空列表必须拒绝：空 filter 等价于「全市场」。
    """
    vals = [str(v).strip() for v in values if str(v).strip()]
    if not vals:
        raise SourceError(f"in_filter({field}) 收到空列表：空 filter 会静默变成全市场查询，已拒绝")
    return "(%s in (%s))" % (field, ",".join('"%s"' % v for v in vals))


def batches(items: Iterable[Any], size: int) -> list[list[Any]]:
    """定长切片（datacenter-web 单次 ≤400 只代码）。"""
    if not isinstance(size, int) or size <= 0:
        raise SourceError(f"batches 的 size 非法: {size!r}")
    seq = list(items)
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def _num(v: Any) -> float | None:
    """东财的数值字段：`""` / null / `"0.2700000000"` / 数值混合出现，统一成 float 或 None。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    s = str(v).strip()
    if s in ("", "-", "--", "null", "None", "nan", "NaN"):
        return None
    try:
        f = float(s.replace(",", ""))
    except ValueError:
        return None
    return f if f == f and abs(f) != float("inf") else None


def _int(v: Any) -> int | None:
    f = _num(v)
    return None if f is None else int(f)


def _text(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _date_text(v: Any) -> str | None:
    """`2026-09-18 00:00:00` / `2026-09-18 00:00:00.000` / `20260918` → `2026-09-18`。"""
    if v is None:
        return None
    s = str(v).strip()
    if s.lower() in ("", "none", "nan", "nat"):
        return None
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", s)
    if m:
        return "%s-%s-%s" % m.groups()
    return s


def require_date8(value: Any, label: str) -> str:
    """研报接口的必填日期参数：接受 `2026-01-01` 或 `20260101`。"""
    d = _date_text(value)
    if not d or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d or ""):
        raise SourceError(f"{label} 日期无法解析: {value!r}（应为 YYYY-MM-DD）")
    return d


def publish_year_of(publish_date: Any) -> int | None:
    d = _date_text(publish_date)
    if not d or len(d) < 4 or not d[:4].isdigit():
        return None
    return int(d[:4])


def absolute_eps_years(current_year: Any, publish_year: Any = None) -> dict:
    """相对年份（this / next / next_two）→ 绝对年份。

    基准取**研报自己的发布年**，而不是响应体的 `currentYear`：`report/list` 的
    `currentYear` 实测恒为服务端的「现在」（2026），而相对标签是按报告自身年份打的。
    证据（本机实测，7821 + 5816 篇 H2 样本）：`predictLastYearEps` 是「基准年前一年」的
    近实际值，用 2023/2024/2025 三个发布年队列分别对照年报实际 EPS，中位相对误差
    1.5% ~ 7.6%（对上「发布年-1」），而对照「发布年」本身是 17% ~ 25%——即
    2025 年发布的报告把 2025E 填在 predictThisYearEps、2024 年实际填在 predictLastYearEps。
    若一律按 currentYear(2026) 解释，2024/2025 年发布的研报会被整体平移 1~2 年。
    2026 年发布的报告两个口径一致，且已被 F10 `ycmx` 的绝对年交叉验证
    （2026-08-21 西南证券 69.83 = 2026E）。

    已知例外：2018 年队列实测基准是发布年 + 1（`predictLastYearEps` ≈ 当年）——
    2019 年及之后不再复现；引擎只做近期复盘，历史区间若要按年计算需自行校验。
    发布年不可得（或晚于 currentYear，属脏数据）时回退 currentYear；两者都没有 → SourceError，
    绝不硬编码年份。
    """
    cy, py = _int(current_year), _int(publish_year)
    if py is not None and (cy is None or py <= cy):
        base = py
    else:
        base = cy
    if base is None:
        raise SourceError("无法确定 EPS 年份基准：响应体既无 currentYear，发布日也无法解析")
    return {"this": base, "next": base + 1, "next_two": base + 2}


# ── DataFrame 归一化 ──────────────────────────────────────────────────
def normalise_records(records: Sequence[dict], columns: Sequence[str]) -> pd.DataFrame:
    """行记录 → 只含白名单列、顺序固定、缺列补 NA 的 DataFrame。"""
    cols = list(columns)
    if not records:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in cols})
    df = pd.DataFrame(list(records))
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols].copy()


def _coerce(df: pd.DataFrame, floats=(), ints=()) -> pd.DataFrame:
    for c in floats:
        df[c] = pd.to_numeric(df[c].map(_num), errors="coerce")
    for c in ints:
        df[c] = pd.to_numeric(df[c].map(_int), errors="coerce").astype("Int64")
    return df


def consensus_frame(rows: Sequence[dict]) -> pd.DataFrame:
    """一致预期原始行 → 规范 DataFrame（索引 = 6 位代码，数值列已转 float / Int64）。"""
    df = normalise_records(rows, CONSENSUS_COLUMNS)
    df["SECURITY_CODE"] = [_six_soft(c) for c in df["SECURITY_CODE"]]
    df = _coerce(df, floats=CONSENSUS_NUMERIC_COLUMNS, ints=CONSENSUS_YEAR_COLUMNS)
    return df.set_index("SECURITY_CODE")


def financials_frame(rows: Sequence[dict]) -> pd.DataFrame:
    """业绩报表原始行 → 规范 DataFrame（日期只留 YYYY-MM-DD，累计口径原样保留）。"""
    df = normalise_records(rows, FINANCIALS_COLUMNS)
    df["SECURITY_CODE"] = [_six_soft(c) for c in df["SECURITY_CODE"]]
    for c in ("REPORTDATE", "NOTICE_DATE"):
        df[c] = [_date_text(v) for v in df[c]]
    return _coerce(df, floats=FINANCIALS_NUMERIC_COLUMNS)


def research_frame(rows: Sequence[dict], current_year: Any) -> pd.DataFrame:
    """研报原始行 → 规范 DataFrame，并把相对 EPS 年份绝对化。

    `empty` 输入合法（2017-01-02 之前的区间实测返回 0 条），此时年份列留空。
    """
    df = normalise_records(rows, list(REPORT_COLUMNS) + REPORT_YEAR_COLUMNS)
    if df.empty:
        for c in REPORT_YEAR_COLUMNS:
            df[c] = pd.Series([], dtype="Int64")
        return df
    df["stockCode"] = [_six_soft(c) for c in df["stockCode"]]
    df["publishDate"] = [_date_text(v) for v in df["publishDate"]]
    df = _coerce(df, floats=REPORT_NUMERIC_COLUMNS, ints=REPORT_INT_COLUMNS)

    years = [
        absolute_eps_years(current_year, publish_year_of(d))
        for d in df["publishDate"]
    ]
    for key, col in zip(("this", "next", "next_two"), REPORT_YEAR_COLUMNS):
        df[col] = pd.Series([y[key] for y in years], dtype="Int64")
    return df


# ── datacenter-web 分页 ───────────────────────────────────────────────
def _dc_rows(report_name: str, *, filter_: str | None = None,
             page_size: int = DC_PAGE_SIZE, columns: str = "ALL",
             verbose: bool = False) -> list[dict]:
    """按页拉完一个 datacenter-web 报表。

    `result: null`（实测 `success=false` / `code=9201`「返回数据为空」）在 filter 语法写错时
    也会出现，与「这批股票真的没有数据」形状完全相同 → 这里只当作 0 行，量级自校交给调用方。
    """
    rows: list[dict] = []
    count: int | None = None
    pages: int | None = None
    page = 1
    while True:
        params = {"reportName": report_name, "columns": columns,
                  "pageNumber": page, "pageSize": page_size}
        if filter_:
            params["filter"] = filter_
        raw = http("GET", DC_URL, params=params)
        if not isinstance(raw, dict):
            raise SourceError(f"{report_name} 响应不是对象: {type(raw).__name__}")
        result = raw.get("result")
        if result is None:
            break
        if not isinstance(result, dict):
            raise SourceError(f"{report_name} 的 result 不是对象: {type(result).__name__}")
        pages = _int(result.get("pages")) if pages is None else pages
        count = _int(result.get("count")) if count is None else count
        data = result.get("data")
        if not data:
            break
        if not isinstance(data, list):
            raise SourceError(f"{report_name} 的 result.data 不是列表: {type(data).__name__}")
        rows.extend(r for r in data if isinstance(r, dict))
        if verbose and page % 50 == 0:
            print(f"  {report_name} page {page}/{pages or '?'} 累计 {len(rows)} 行")
        if pages is not None and page >= pages:
            break
        page += 1
        if page > DC_MAX_PAGES:
            raise SourceError(
                f"{report_name} 分页超过 {DC_MAX_PAGES} 页仍未结束：疑似 pages 字段异常"
            )
    # 截断自校：声明有 count 却少了一整页以上，说明翻页契约变了
    if count is not None:
        missing = count - len(rows)
        if missing >= DC_PAGE_SIZE or (count <= DC_PAGE_SIZE and missing > 0):
            raise SourceError(
                f"{report_name} 分页不完整：取到 {len(rows)} 行，服务端声明 count={count}"
                f"（页 {page}/{pages}；filter={filter_ or '无'}）"
            )
    return rows


# ── ① 一致预期 ────────────────────────────────────────────────────────
def consensus_snapshot(save: bool = False) -> pd.DataFrame:
    """全市场一致预期（RPT_WEB_RESPREDICT，免密钥免 cookie，约 6 页 / 2916 只）。

    返回按 6 位代码索引的 DataFrame；`save=True` 时另写 TRANSITION_DIR/consensus.parquet。
    目标价取 DEC_AIMPRICEMAX/MIN（填充率约 73%）——这是本模块唯一认可的目标价来源。
    """
    rows = _dc_rows("RPT_WEB_RESPREDICT", page_size=DC_PAGE_SIZE)
    if len(rows) < CONSENSUS_MIN_ROWS:
        raise SourceError(
            "一致预期行数异常：取到 %d 行 < %d —— 疑似 RPT_WEB_RESPREDICT 契约变更"
            "（分页被截断 / 字段改名），拒绝写库" % (len(rows), CONSENSUS_MIN_ROWS)
        )
    df = consensus_frame(rows)
    if save:
        TRANSITION_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(CONSENSUS_PARQUET)
    return df


# ── ② 单股盈利预测 ────────────────────────────────────────────────────
def parse_ycmx(rows: Any) -> list[dict]:
    """F10 `ycmx` 块 → 逐机构明细（维度⑤「卖方分歧」的原始数据）。"""
    out = []
    for e in rows or []:
        if not isinstance(e, dict):
            continue
        years, marks, eps, netprofit = [], [], [], []
        for i in (1, 2, 3, 4):
            y = _int(e.get(f"YEAR{i}"))
            if y is None:
                continue
            years.append(y)
            marks.append(_text(e.get(f"YEAR_MARK{i}")))
            eps.append(_num(e.get(f"EPS{i}")))
            netprofit.append(_num(e.get(f"PARENT_NETPROFIT{i}")))
        if not years:
            continue
        out.append({
            "org": _text(e.get("ORG_NAME_ABBR")) or _text(e.get("ORG_NAME")),
            "researcher": _text(e.get("RESEARCHER")),
            "rating": _text(e.get("RATING")),
            "publish_date": _date_text(e.get("PUBLISH_DATE")),
            "years": years,
            "year_marks": marks,
            "eps": eps,
            "netprofit": netprofit,
        })
    return out


def revision_1m(by_year: Sequence[dict]) -> float | None:
    """最近一个预测年的月度修正：(eps - eps_last_month) / |eps_last_month|，否则 None。

    实测 002708：2026E EPS 0.353333333333 vs EPS_LASTMONTHS 0.52 → -0.3205（一个月内下修 32%）。
    `YEAR_MARK` 缺失时退化为「第一个真正发生变化的年份」，避免契约漂移时静默返回 None。
    """
    def _pick(rows_seq, need_change):
        out = []
        for r in rows_seq or []:
            y = _int(r.get("year"))
            eps, old = _num(r.get("eps")), _num(r.get("eps_last_month"))
            if y is None or eps is None or old in (None, 0):
                continue
            if need_change and abs(eps - old) < 1e-12:
                continue
            out.append((y, eps, old))
        return out

    marked = [r for r in (by_year or [])
              if str(r.get("year_mark") or "").strip().upper() == "E"]
    cands = _pick(marked, False)
    if not cands:
        unmarked = [r for r in (by_year or [])
                    if not str(r.get("year_mark") or "").strip()]
        cands = _pick(unmarked, True)
    if not cands:
        return None
    _, eps, old = min(cands, key=lambda t: t[0])
    return (eps - old) / abs(old)


def _rating_windows(pjtj: Any) -> list[dict]:
    out = []
    for e in pjtj or []:
        if not isinstance(e, dict):
            continue
        out.append({
            "window": _text(e.get("DATE_TYPE")),
            "window_code": _int(e.get("DATE_TYPE_CODE")),
            "org_num": _int(e.get("RATING_ORG_NUM")),
            "buy_num": _int(e.get("RATING_BUY_NUM")),
            "neutral_num": _int(e.get("RATING_NEUTRAL_NUM")),
            "rating": _text(e.get("COMPRE_RATING")),
        })
    return out


def profit_forecast(code: str) -> dict:
    """单股盈利预测（东财 F10）。`code` 带或带前缀都可以，内部自行补市场前缀。

    返回：by_year（逐年 EPS/EPS_LASTMONTHS/BVPS/ROE/归母净利/营收/RANK）、
    revision_1m（最近预测年的月度修正）、ycmx（逐机构明细）、rating_org_num
    （近一月评级机构数，来自 pjtj 的「1月内」窗口）、rating_windows（五档窗口，供算覆盖广度）。
    """
    mcode = market_code(code)
    raw = http("GET", HSF10_PROFIT_FORECAST_URL, params={"code": mcode})
    if not isinstance(raw, dict):
        raise SourceError(f"盈利预测响应不是对象: {mcode} → {type(raw).__name__}")
    src = raw.get("yctj_list") or raw.get("yctj_chart") or []
    if not isinstance(src, list) or not src:
        raise SourceError(
            f"{mcode} 无盈利预测明细（yctj_list / yctj_chart 均为空或 null）"
            "：疑似去掉市场前缀、或该股确实无卖方覆盖"
        )
    by_year = []
    for e in src:
        if not isinstance(e, dict):
            continue
        y = _int(e.get("YEAR"))
        if y is None:
            continue
        by_year.append({
            "year": y,
            "year_mark": _text(e.get("YEAR_MARK")),
            "eps": _num(e.get("EPS")),
            "eps_last_month": _num(e.get("EPS_LASTMONTHS")),
            "bvps": _num(e.get("BVPS")),
            "roe": _num(e.get("ROE")),
            "parent_netprofit": _num(e.get("PARENT_NETPROFIT")),
            "total_operate_income": _num(e.get("TOTAL_OPERATE_INCOME")),
            "rank": _int(e.get("RANK")),
        })
    if not by_year:
        raise SourceError(f"{mcode} 的盈利预测条目全部缺少 YEAR，无法解析: {src[:1]!r}")

    by_year.sort(key=lambda r: r["year"])
    windows = _rating_windows(raw.get("pjtj"))
    org_num = next((w["org_num"] for w in windows if w["window_code"] == 1), None)
    if org_num is None:
        org_num = next((w["org_num"] for w in windows if w["org_num"] is not None), None)
    return {
        "code": six_digit(code),
        "market_code": mcode,
        "by_year": by_year,
        "revision_1m": revision_1m(by_year),
        "ycmx": parse_ycmx(raw.get("ycmx")),
        "rating_org_num": org_num,
        "rating_windows": windows,
    }


# ── ③ 业绩报表 ────────────────────────────────────────────────────────
def financials(codes: list[str] | None = None, *, verbose: bool = False) -> pd.DataFrame:
    """业绩报表（RPT_LICO_FN_CPD），**季报累计口径**（一季报/半年报/三季报/年报）。

    codes=None → 全市场（不带 filter；实测 496,380 行 / 993 页，耗时较长）；
    codes 给定时按 ≤FINANCIALS_BATCH 只分批，filter 写成 `(SECURITY_CODE in ("..."))`。
    写成 `(SECURITY_CODE="000001,000002")` 服务端返回 result=null、0 行且 HTTP 200，
    因此「给了代码却 0 行」在这里直接抛 SourceError，而不是返回空表。
    返回的是全部报告期，调用方按 REPORTDATE 自行取最新一期。
    """
    if codes is None:
        rows = _dc_rows("RPT_LICO_FN_CPD", page_size=DC_PAGE_SIZE, verbose=verbose)
        return financials_frame(rows)

    wanted = list(dict.fromkeys(six_digit(c) for c in codes))
    if not wanted:
        raise SourceError(
            "financials([]) 收到空列表：拒绝静默降级为全市场拉取（要全市场请显式传 codes=None）"
        )
    frames, total = [], 0
    for batch in batches(wanted, FINANCIALS_BATCH):
        rows = _dc_rows("RPT_LICO_FN_CPD", filter_=in_filter("SECURITY_CODE", batch),
                        page_size=DC_PAGE_SIZE, verbose=verbose)
        total += len(rows)
        frames.append(financials_frame(rows))
    if total == 0:
        raise SourceError(
            "RPT_LICO_FN_CPD 给了 %d 个代码却返回 0 行：优先怀疑 filter 语法（必须 "
            '`(SECURITY_CODE in ("..."))`）或代码本身不存在，而不是「这些股票没有财报」'
            % len(wanted)
        )
    return pd.concat(frames, ignore_index=True)


# ── ④ 个股研报 ────────────────────────────────────────────────────────
def research_reports(code: str, begin: str, end: str) -> pd.DataFrame:
    """个股研报明细（qType=0）。begin/end 形如 `2026-01-01`（也接受 `20260101`）。

    4 个参数 beginTime/endTime/pageNo/pageSize 缺一即 HTTP 400；pageSize 被服务端
    静默截到 100，所以按 TotalPage 翻页。历史最早只到 2017-01-02，更早的区间返回 0 条
    （合法空集，返回空 DataFrame 而不是抛错）。EPS 是相对年，用 currentYear / 发布年
    绝对化成 year_this / year_next / year_next_two。**目标价不要用 indvAimPriceT/L**
    （覆盖率 4.7%），请用 consensus_snapshot() 的 DEC_AIMPRICEMAX/MIN（约 73%）。
    """
    scode = six_digit(code)
    base_params = {
        "beginTime": require_date8(begin, "beginTime"),
        "endTime": require_date8(end, "endTime"),
        "pageSize": REPORT_PAGE_SIZE,
        "qType": 0,
        "code": scode,
    }
    rows: list[dict] = []
    page, total_pages, hits, current_year = 1, None, None, None
    while True:
        raw = http("GET", REPORT_LIST_URL, params={**base_params, "pageNo": page})
        if not isinstance(raw, dict) or ("hits" not in raw and "data" not in raw):
            raise SourceError(
                f"研报接口响应契约异常（keys={sorted(raw)[:8] if isinstance(raw, dict) else raw!r}）"
            )
        if total_pages is None:
            total_pages = _int(raw.get("TotalPage"))
        if hits is None:
            hits = _int(raw.get("hits"))
        if current_year is None:
            current_year = _int(raw.get("currentYear"))
        data = raw.get("data")
        if data is None:
            break
        if not isinstance(data, list):
            raise SourceError(f"研报接口 data 不是列表: {type(data).__name__}")
        rows.extend(r for r in data if isinstance(r, dict))
        if not data:
            break
        if total_pages is None:
            break
        if page >= total_pages:
            break
        page += 1
        if page > REPORT_MAX_PAGES:
            raise SourceError(
                f"{scode} 研报分页超过 {REPORT_MAX_PAGES} 页：疑似 TotalPage 异常"
            )
    if total_pages and page < total_pages:
        raise SourceError(
            f"{scode} 研报翻页提前结束：第 {page}/{total_pages} 页返回空，"
            f"已取 {len(rows)} 行（hits={hits}）"
        )
    if not rows and hits:
        raise SourceError(f"{scode} 研报接口声明 hits={hits} 却无 data：契约异常")
    return research_frame(rows, current_year)


# ── ⑤ 公告分类 ────────────────────────────────────────────────────────
def parse_announcements(raw: Any) -> list[dict]:
    """np-anotice-stock 响应 → [{art_code, notice_date, title, column_names}]。

    `data: null` / 缺 `list` → SourceError；公司只是没有公告时服务端给的是
    `data.list == []` 且 `total_hits == 0`，那种情况返回空列表。
    """
    if not isinstance(raw, dict):
        raise SourceError(f"公告接口响应不是对象: {type(raw).__name__}")
    if raw.get("error"):
        raise SourceError(f"公告接口返回错误: {raw.get('error')!r}")
    if raw.get("success") in (0, "0", False):
        raise SourceError(f"公告接口 success=false: {raw!r}"[:300])
    data = raw.get("data")
    if data is None:
        raise SourceError("公告接口 data 为 null：疑似参数（stock_list / ann_type）被拒")
    if not isinstance(data, dict):
        raise SourceError(f"公告接口 data 不是对象: {type(data).__name__}")
    items = data.get("list")
    if items is None:
        raise SourceError(f"公告接口 data 缺少 list 字段: keys={sorted(data)[:8]}")
    if not isinstance(items, list):
        raise SourceError(f"公告接口 data.list 不是列表: {type(items).__name__}")

    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        columns = [
            _text(c.get("column_name"))
            for c in (it.get("columns") or [])
            if isinstance(c, dict)
        ]
        out.append({
            "art_code": _text(it.get("art_code")),
            "notice_date": _date_text(it.get("notice_date")),
            "title": _text(it.get("title")) or _text(it.get("title_ch")),
            "column_names": [c for c in columns if c],
        })
    return out


def announcement_columns(code: str, page_size: int = 50) -> list[dict]:
    """单股公告（含分类名）。column_name 直接是「股权激励计划」/「股权激励计划摘要」，
    因此能精确挑出激励计划正文，而不必碰法律意见书 / 自查表 / 考核管理办法。"""
    if not isinstance(page_size, int) or isinstance(page_size, bool) or page_size <= 0:
        raise SourceError(f"announcement_columns 的 page_size 非法: {page_size!r}")
    params = {
        "page_size": page_size,
        "page_index": 1,
        "ann_type": "A",
        "client_source": "web",
        "stock_list": six_digit(code),
        "f_node": 0,
        "s_node": 0,
    }
    return parse_announcements(http("GET", ANN_URL, params=params))


# ── 自检（联网，手动执行）──────────────────────────────────────────────
if __name__ == "__main__":  # pragma: no cover
    snap = consensus_snapshot(save=True)
    print("consensus_snapshot:", snap.shape, "| 示例", snap.index[:3].tolist())
    pf = profit_forecast("002708")
    print("profit_forecast 002708 revision_1m =", pf["revision_1m"],
          "| by_year", [(r["year"], r["eps"]) for r in pf["by_year"]])
    fin = financials(["000001", "000002"])
    print("financials 2 codes:", fin.shape)
    rep = research_reports("002708", "2026-01-01", "2026-09-18")
    print("research_reports 002708:", rep.shape)
    ann = announcement_columns("300903", 50)
    print("announcement_columns 300903:", len(ann),
          "| 激励计划", [a["title"][:20] for a in ann if "股权激励" in "/".join(a["column_names"])])
