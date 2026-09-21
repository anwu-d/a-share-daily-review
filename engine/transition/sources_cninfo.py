# -*- coding: utf-8 -*-
"""巨潮资讯网取数：公告检索、正文全文检索、PDF 文本。

本模块的核心职责不是「把请求发出去」，而是**把巨潮的静默失效变成显式错误**。
调研实测（research/business-transition-pricing/findings/F1.md、F6.md）：

  * 全部端点强制浏览器 UA，否则 403（Referer / Cookie 不需要）
  * `hisAnnouncement/query` 的 `pageSize` 服务端硬限 30，传更大只回 30
  * 同端点 `pageNum >= 101` **静默返回第 1 页**（不是空、不是报错）→ 会静默重复采集
  * 非法 `category` / `plate` **返回全市场数据而不报错**（实测 category_gqjl_bf 与不筛同值 153372）
  * `column` 留空会让 `tabName` 过滤整体失效（退回全市场 11100 条）
  * `totalAnnouncement` 本身不可靠（`机器人` 返回 0，与不存在的词无法区分）
  * `fulltextSearch/full` 的 `type` 非空静默返回 0 条；offset >= 20000 后恒空
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pandas as pd

from . import BROWSER_UA, TRANSITION_DIR, SourceError, http, read_json, write_json

CNINFO = "http://www.cninfo.com.cn"
QUERY_URL = f"{CNINFO}/new/hisAnnouncement/query"
FULLTEXT_URL = f"{CNINFO}/new/fulltextSearch/full"
STOCK_MAP_URL = f"{CNINFO}/new/data/szse_stock.json"
PDF_BASE = "http://static.cninfo.com.cn/"

PAGE_SIZE_LIMIT = 30          # hisAnnouncement 服务端硬限
PAGE_NUM_WRAP = 101           # >= 该值会静默回卷第 1 页
FULLTEXT_PAGE_LIMIT = 100
FULLTEXT_OFFSET_WALL = 20000

# 只登记本模块实际使用且已实测有效的分类；未知分类一律拒绝，
# 因为巨潮对非法分类不报错、直接回退全市场。
KNOWN_CATEGORIES = {
    "category_gqjl_szsh": "股权激励",
    "category_gqbd_szsh": "股权变动",
    "category_rcjy_szsh": "日常经营",
    "category_fxts_szsh": "风险提示",
    "category_bndbg_szsh": "半年报",
    "category_ndbg_szsh": "年报",
}
KNOWN_TABS = {"fulltext", "relation"}
KNOWN_COLUMNS = {"szse", "hke", "fund", "bond", ""}
_EM = re.compile(r"</?em>")


def strip_em(s: str | None) -> str:
    """isHLtitle=true 会在标题里插入 <em>，入库前必须剥离。"""
    return _EM.sub("", s or "").strip()


# ── 代码 → orgId ────────────────────────────────────────────────────────────
def stock_map(refresh: bool = False) -> dict[str, str]:
    """6 位代码 → orgId 映射（6255 条）。按个股取公告必须用 "代码,orgId"。"""
    cache = TRANSITION_DIR / "cninfo_stock_map.json"
    if not refresh and cache.exists():
        d = read_json(cache, {})
        if len(d) > 3000:
            return d
    js = http("GET", STOCK_MAP_URL)
    rows = js.get("stockList") or []
    m = {r["code"]: r["orgId"] for r in rows if r.get("code") and r.get("orgId")}
    if len(m) < 3000:
        raise SourceError(f"szse_stock.json 只解析出 {len(m)} 条，契约疑似变更")
    write_json(cache, m)
    return m


def _validate(category: str, tab_name: str, column: str) -> None:
    if category and category not in KNOWN_CATEGORIES:
        raise SourceError(
            f"未知 category={category!r}；巨潮对非法分类会静默返回全市场，故直接拒绝"
        )
    if tab_name and tab_name not in KNOWN_TABS:
        raise SourceError(f"未知 tabName={tab_name!r}（合法值只有 {sorted(KNOWN_TABS)}）")
    if column not in KNOWN_COLUMNS:
        raise SourceError(f"未知 column={column!r}（合法值 {sorted(KNOWN_COLUMNS)}）")
    if tab_name and not column:
        raise SourceError("column 留空会让 tabName 过滤整体失效并返回全市场，必须指定")


# ── 公告检索 ───────────────────────────────────────────────────────────────
def query_page(page_num: int, *, category: str = "", tab_name: str = "fulltext",
               column: str = "szse", search_key: str = "", se_date: str = "",
               stock: str = "", page_size: int = PAGE_SIZE_LIMIT,
               _validate_args: bool = True) -> dict:
    """单页查询。返回巨潮原始响应（含 announcements / totalAnnouncement / hasMore）。"""
    if _validate_args:
        _validate(category, tab_name, column)
    if page_num >= PAGE_NUM_WRAP:
        raise SourceError(
            f"pageNum={page_num} >= {PAGE_NUM_WRAP} 会静默回卷第 1 页，"
            "请改用更细的日期切片"
        )
    body = {
        "pageNum": page_num,
        "pageSize": min(page_size, PAGE_SIZE_LIMIT),
        "column": column,
        "tabName": tab_name,
        "category": category,
        "searchkey": search_key,
        "seDate": se_date,
        "stock": stock,
        "isHLtitle": "false",
    }
    return http("POST", QUERY_URL, data=body)


def _rows_of(js: dict) -> list[dict]:
    rows = js.get("announcements")
    return rows if isinstance(rows, list) else []


def query_all(*, category: str = "", tab_name: str = "fulltext", column: str = "szse",
              search_key: str = "", se_date: str = "", stock: str = "",
              max_pages: int = 100, expect_min: int = 0) -> list[dict]:
    """分页取完整列表。

    两道防护：
      1) `pageNum` 回退防护——若某页首条 id 与第 1 页相同，说明已触墙，立即停止并报错。
      2) 量级自校验——`totalAnnouncement` 低于 `expect_min` 说明过滤器失效（常见于非法参数
         被静默忽略），此时报错而不是把全市场数据当成查询结果。
    """
    first = query_page(1, category=category, tab_name=tab_name, column=column,
                       search_key=search_key, se_date=se_date, stock=stock)
    total = int(first.get("totalAnnouncement") or 0)
    if expect_min and total < expect_min:
        raise SourceError(
            f"totalAnnouncement={total} < expect_min={expect_min}；"
            f"过滤器疑似被静默忽略（category={category!r} tab={tab_name!r} "
            f"key={search_key!r} date={se_date!r}）"
        )
    out: list[dict] = []
    page_no = 1
    first_id = (_rows_of(first) or [{}])[0].get("announcementId")
    while True:
        js = first if page_no == 1 else query_page(
            page_no, category=category, tab_name=tab_name, column=column,
            search_key=search_key, se_date=se_date, stock=stock)
        rows = _rows_of(js)
        if not rows:
            break
        if page_no > 1 and rows[0].get("announcementId") == first_id:
            raise SourceError(
                f"第 {page_no} 页与第 1 页首条相同 → 已触发 pageNum 静默回卷；"
                f"请把 {se_date!r} 切成更细的日期区间"
            )
        out.extend(rows)
        if not js.get("hasMore", True) or page_no >= min(max_pages, PAGE_NUM_WRAP - 1):
            break
        page_no += 1
        time.sleep(0.05)
    return out


def announcements(category: str, start: str, end: str, *, column: str = "szse",
                  search_key: str = "", tab_name: str = "fulltext",
                  expect_min: int = 1) -> list[dict]:
    """按分类+日期区间取公告（自动按「月」切片，规避 3000 条/次上限）。

    `category` 允许为空——`tabName=relation`（调研纪要）下 category 不起筛选作用。
    """
    if category and category not in KNOWN_CATEGORIES:
        raise SourceError(f"未知 category={category!r}")
    out: list[dict] = []
    for a, b in _months(start, end):
        out.extend(query_all(category=category, tab_name=tab_name, column=column,
                             search_key=search_key, se_date=f"{a}~{b}",
                             expect_min=expect_min))
    return _dedupe(out)


def relation_minutes(start: str, end: str) -> list[dict]:
    """投资者关系活动记录表（调研纪要）。

    注意：`tabName=relation` 是**深交所专属**（实测 180 篇抽样中 0 个 6xxxxx），
    沪市必须走 `fulltext(keyword="投资者关系活动记录表")`。
    """
    return announcements("", start, end, tab_name="relation", column="szse",
                         expect_min=1)


def _months(start: str, end: str) -> list[tuple[str, str]]:
    """按自然月切片。调研实测：按季切片会超过 3000 条/次上限（2026Q2 达 4824 条）。"""
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    out = []
    cur = s
    while cur <= e:
        nxt = (cur + pd.offsets.MonthBegin(1)) - pd.Timedelta(days=1)
        seg_end = min(nxt, e)
        out.append((cur.strftime("%Y-%m-%d"), seg_end.strftime("%Y-%m-%d")))
        cur = seg_end + pd.Timedelta(days=1)
    return out


def _dedupe(rows: list[dict]) -> list[dict]:
    """同一公告可能有两条记录（announcementId 不同、PDF 内容相同）。"""
    seen, out = set(), []
    for r in rows:
        key = (r.get("secCode"), str(r.get("announcementTime"))[:10],
               strip_em(r.get("announcementTitle")))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def pdf_url(adjunct_url: str) -> str:
    return PDF_BASE + str(adjunct_url).lstrip("/")


# ── 正文级全文检索 ─────────────────────────────────────────────────────────
def fulltext_page(search_key: str, page_num: int = 1, *, start: str = "", end: str = "",
                  page_size: int = FULLTEXT_PAGE_LIMIT, is_fulltext: bool = True) -> dict:
    """正文级检索。

    `isfulltext=true` 才是正文级（实测「量产」205906 条 vs 仅标题 63 条）。
    `type` 必须留空——任何非空值静默返回 0 条。
    """
    if page_num * page_size >= FULLTEXT_OFFSET_WALL:
        raise SourceError(
            f"offset={page_num * page_size} >= {FULLTEXT_OFFSET_WALL} 后恒返回 0 条；"
            "请按日期切片"
        )
    data = {
        "searchkey": search_key,
        "isfulltext": "true" if is_fulltext else "false",
        "sortName": "nothing",
        "sortType": "desc",
        "pageNum": page_num,
        "pageSize": min(page_size, FULLTEXT_PAGE_LIMIT),
        "type": "",            # 必须留空
        "isHLtitle": "false",
    }
    if start:
        data["sdate"] = start
    if end:
        data["edate"] = end
    return http("POST", FULLTEXT_URL, data=data)


def fulltext(search_key: str, start: str, end: str, *, max_records: int = 5000) -> list[dict]:
    """按季度切片取正文级命中（已验证季度求和无损），并剔除港股（代码非 6 位）。"""
    out: list[dict] = []
    for a, b in _quarters(start, end):
        page_no = 1
        while True:
            js = fulltext_page(search_key, page_no, start=a, end=b)
            rows = js.get("announcements") or []
            if not rows:
                break
            out.extend(rows)
            if not js.get("hasMore", False) or len(out) >= max_records:
                break
            page_no += 1
            if page_no * FULLTEXT_PAGE_LIMIT >= FULLTEXT_OFFSET_WALL:
                break
    return [r for r in out if _is_a_share(r)]


def _is_a_share(row: dict) -> bool:
    """港股（5 位代码）会混进结果，按 secCode 长度为 6 过滤。"""
    code = str(row.get("secCode") or "")
    return len(code) == 6 and code.isdigit()


def _quarters(start: str, end: str) -> list[tuple[str, str]]:
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    out, cur = [], s
    while cur <= e:
        q_end = cur + pd.offsets.QuarterEnd(1)
        seg_end = min(q_end, e)
        out.append((cur.strftime("%Y-%m-%d"), seg_end.strftime("%Y-%m-%d")))
        cur = seg_end + pd.Timedelta(days=1)
    return out


# ── PDF ────────────────────────────────────────────────────────────────────
_PAGENO = re.compile(r"^(?:第)?\d{1,3}(?:页)?$")


def download_pdf(adjunct_url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1024:
        return dest
    r = http("GET", pdf_url(adjunct_url), expect_json=False, timeout=90)
    if not r.content.startswith(b"%PDF"):
        raise SourceError(f"下载到的不是 PDF（magic 不符）: {adjunct_url}")
    dest.write_bytes(r.content)
    return dest


def pdf_text(path: Path, drop_page_numbers: bool = True) -> str:
    """阅读顺序全文。

    实测要点：**不要**按 y 坐标聚行——数字与汉字基线相差约 1pt，
    朴素取整会把数字整段踢出该行（`y=497.0 | 20262.5020262025`）。
    且阅读顺序文本不保证单元格分隔，会整段粘住，因此这里同时去掉空白，
    调用方的正则不能依赖空白或行边界。
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        raw = "".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    finally:
        pdf.close()
    lines = []
    for ln in raw.replace("\u3000", "").split("\n"):
        s = ln.strip()
        if not s or (drop_page_numbers and _PAGENO.match(s)):
            continue
        lines.append(s)
    return re.sub(r"[ \t\r]", "", "".join(lines))
