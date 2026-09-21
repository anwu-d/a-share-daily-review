# -*- coding: utf-8 -*-
"""投资者问答取数：互动易（irm.cninfo.com.cn）+ 全景网（ir.p5w.net）。

========================================================================
契约核心：提问（investor question）与回复（company answer）必须分开
========================================================================

互动易单公司问答接口的返回体里：

  * ``mainContent``    = **投资者提问**
  * ``attachedContent`` = **公司回复**（只有这一侧是披露级口径）

这不是命名偏好问题，是实测出来的致命陷阱：819 条样本逐词对照，
``送样`` 在**提问侧出现 12 次、回复侧 0 次**（``量产`` 24/19、``验证`` 18/9、
``小批量`` 7/5）。典型噪声是投资者把「目前处于小试/中试/送样哪个阶段」
当**问题**抛出去（豪鹏科技 001283 一条提问同时含 4 个里程碑词，零披露价值）。
**因此本模块返回的 dict 把两段文本放在互不相同的键里（``question`` / ``answer``），
任何计数、定级、热度统计只允许读 ``answer``；把提问侧当披露信号会得到纯噪声。**
调用方若把 ``question`` 塞进里程碑计数，等于自造信号。

配套的两个坑：

1. ``<em>`` 高亮是**逐字**插入的（``<em>量</em><em>产</em>``），裸
   ``str.count("量产")`` 恒为 0。必须先 :func:`strip_tags` 去标签再计数。
   全景网同样插内联样式高亮（``<i class='ce06868' style='display:inline'>固态</i>``）。
2. 全景网按公司过滤**必须**传隐藏域 ``companyBaseinfoId``(pid)；只传
   ``companyCode`` 会被**静默忽略**并返回别家公司的数据（实测传
   ``companyCode=300857`` 不带 pid 时，10 条里只有 2 条属于 300857）。
   所以 :func:`p5w_qa` 会在每页上做「至少一半属于目标代码」的断言，
   静默失效一律升级成 :class:`SourceError`。

其他已验证的口径（不要凭直觉加参数）：

* 互动易两类端点（单公司问答 / 详情）**不支持任何日期过滤**，
  ``beginTime``/``startDate``/``pubDateStart`` 全部被忽略。结果按最新活动倒序，
  要回溯到某个日期边界只能翻页。
* 只读端点全部免登录、免 token、免 Cookie、免 Referer（实测），本模块不加。
* 互动易问答**零沪市覆盖**：``org_id('600519')`` 能换到 ``gssh0600519``，
  但 ``company_qa('600519')`` 返回 ``total=0``。跨公司全文检索命中的 198 个代码
  首字符只有 0/3。沪市只能靠公告/调研纪要，全景网可作部分补充。
* 全景网 ``interaction/getNewSearchR.shtml``（关键词检索变体）把 ``total``
  硬顶在 100，**不能用来取全量历史**，故本模块不采用。
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime, timedelta, timezone

try:  # 作为包导入：engine 在 sys.path 上
    from . import SourceError, TRANSITION_DIR, http, read_json, write_json
except ImportError:  # pragma: no cover - 兜底：engine 在 sys.path 上按顶层包导入
    from transition import (  # type: ignore
        SourceError,
        TRANSITION_DIR,
        http,
        read_json,
        write_json,
    )
# ── 端点 ──────────────────────────────────────────────────────────────
IRM_BASE = "https://irm.cninfo.com.cn/newircs"
P5W_BASE = "https://ir.p5w.net"

# 代码 -> orgId 缓存（orgId 永不变，见 org_id 文档串）
ORG_CACHE = TRANSITION_DIR / "irm_org.json"

BJ = timezone(timedelta(hours=8))

# 全景网 getNewR.shtml 实测单页上限：rows=4 -> 4 条，rows=20/100/1000 -> 10 条。
# 即 ``rows`` 被服务端钳到 ≤10，且 ``page`` 是 **0 基**页码（page=0 才是最新一页，
# 传 page=1 会跳过最新 10 条）。因此 p5w_qa 逐页翻并去重，而不是指望一次取满。
P5W_PAGE_MAX = 10
P5W_MAX_PAGES = 200  # 防御性上限（total 本身也被顶在 100）

_TAG_RE = re.compile(r"<[^>]*>")
# pid 隐藏域：属性顺序不保证，两个方向都认
_PID_RE = re.compile(r'''id=["']companyBaseinfoId["'][^>]*?\bvalue=["']([^"']*)["']''')
_PID_RE_ALT = re.compile(r'''value=["']([^"']*)["'][^>]*?\bid=["']companyBaseinfoId["']''')


# ── 纯函数小工具（可离线单测）─────────────────────────────────────────
def strip_tags(s) -> str:
    """去掉 HTML 标签并还原实体，返回可安全计数的纯文本。

    逐字高亮的 ``'<em>量</em><em>产</em>'`` -> ``'量产'``（去标签前
    ``str.count('量产')`` 恒为 0，这是本模块存在的第一个理由）。
    全景网的内联样式高亮同理。None / 空值 -> ``''``。
    """
    if s is None:
        return ""
    t = _TAG_RE.sub("", str(s))
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def ms_to_bj(ms) -> str:
    """毫秒 epoch -> 北京时间的 ``YYYY-MM-DD HH:MM:SS``；无效值 -> ``''``。

    互动易所有时间字段（``pubDate`` / ``attachedPubDate`` / ``updateDate`` /
    ``replyDate``）都是毫秒时间戳，且原样用本地时区格式化会差 8 小时。
    """
    if ms is None or ms == "":
        return ""
    try:
        v = int(ms)
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    try:
        return datetime.fromtimestamp(v / 1000, tz=BJ).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def parse_p5w_pid(page_html: str) -> str:
    """从 ``ir.p5w.net/c/<code>.shtml`` 里抠出隐藏域 ``companyBaseinfoId``。

    取不到就抛 :class:`SourceError`——不能返回空 pid，否则下游请求会被
    服务端静默忽略并返回别家公司的数据。
    """
    text = page_html or ""
    for rx in (_PID_RE, _PID_RE_ALT):
        m = rx.search(text)
        if m and m.group(1).strip():
            return m.group(1).strip()
    raise SourceError(
        "全景网公司页未找到隐藏域 companyBaseinfoId(pid)；"
        "没有 pid 时 companyCode 会被静默忽略并返回无关公司数据，故直接失败"
    )


def _assert_code_majority(rows, code: str) -> None:
    """防「pid 静默失效」：本页至少一半的行必须属于目标代码。

    实测只传 ``companyCode`` 不传 pid 时接口仍返回 200 + 10 行，
    但只有 2 行属于目标公司——没有任何报错，纯静默失效。
    """
    if not rows:
        return
    hit = sum(1 for r in rows if str((r or {}).get("companyCode") or "").strip() == code)
    if hit * 2 < len(rows):
        raise SourceError(
            f"全景网 pid 过滤未生效：请求 {code}，本页 {len(rows)} 条里只有 {hit} 条"
            f"属于该公司（companyCode 被静默忽略的典型症状），结果不可用"
        )


# ── 互动易 ────────────────────────────────────────────────────────────
def org_id(code: str) -> str:
    """代码 -> 互动易 ``secid``(orgId)，结果落盘缓存 ``irm_org.json``。

    组织 id 与代码一一对应且从不变化，所以缓存是安全的；``keyWord`` 必须是
    **form-urlencoded body**（JSON body 会被静默丢弃）。取不到匹配项即抛错，
    不返回空串——空 orgId 会让单公司问答端点退回「全市场」语义而不报错。
    """
    code = str(code).strip()
    if not code:
        raise SourceError("org_id 需要非空股票代码")

    cache = read_json(ORG_CACHE, default={}) or {}
    if isinstance(cache, dict) and cache.get(code):
        return str(cache[code])

    js = http("POST", f"{IRM_BASE}/index/queryKeyboardInfo", data={"keyWord": code})
    items = (js.get("data") if isinstance(js, dict) else js) or []
    hit = next((x for x in items if str(x.get("stockCode") or "").strip() == code), None)
    if hit is None:
        raise SourceError(
            f"互动易未能把代码 {code} 换成 orgId：返回 {len(items)} 条且无一条 stockCode 匹配"
        )
    oid = str(hit.get("secid") or "").strip()
    if not oid:
        raise SourceError(f"互动易对代码 {code} 返回了空 secid，拒绝继续（会静默退回全市场）")

    if not isinstance(cache, dict):
        cache = {}
    cache[code] = oid
    write_json(ORG_CACHE, cache)
    return oid


def company_qa(code: str, page_size: int = 1000) -> list[dict]:
    """单公司全量问答（互动易）。返回**逐条 QA**，提问与回复分键存放。

    参数全在 **query string** 里（放 body 会被静默丢弃）；
    ``page_size=1000`` 实测一次取回全部（300857 -> 170 条）。

    返回每项的键：

      ``question_id``    ``indexId``，可喂给 :func:`question_detail`
      ``code``           ``stockCode``
      ``question``       投资者提问（**非披露口径，勿计数**）
      ``question_date``  ``pubDate`` -> 北京时间
      ``answer``         公司回复（**唯一披露级口径**）
      ``answer_date``    列表现场的 ``attachedPubDate`` 实测**几乎恒为 null**
                         （300857: 170/170 为 null），故回落到 ``updateDate``
                         （它等于该条的最近活动时间，通常等于回复时间但不是权威值）。
                         需要权威 ``replyDate`` 时用 :func:`question_detail`。
      ``qa_status``      ``qaStatus`` 原样透传（实测 0/2 混用，不是「已回复」标志）

    未获回复的提问 ``answer`` 为 ``''``（300857 有 16/170），调用方自行按需过滤。
    """
    code = str(code).strip()
    if not code:
        raise SourceError("company_qa 需要非空股票代码")
    oid = org_id(code)

    params = {
        "_t": int(time.time() * 1000),
        "stockcode": code,
        "orgId": oid,
        "pageSize": int(page_size),
        "pageNum": 1,
        "keyWord": "",
        "startDay": "",
        "endDay": "",
    }
    js = http("POST", f"{IRM_BASE}/company/question", params=params)
    rows = (js or {}).get("rows") or []
    try:
        total = int((js or {}).get("total") or 0)
    except (TypeError, ValueError):
        total = 0

    if not rows and total:
        raise SourceError(
            f"company_qa({code}) 声称 total={total} 却返回 0 行，疑似参数被静默忽略"
        )
    if 0 < total <= int(page_size) and len(rows) != total:
        raise SourceError(
            f"company_qa({code}) total={total} 但 rows={len(rows)}（pageSize 足够却截断），拒绝使用"
        )

    return [
        {
            "question_id": str(r.get("indexId") or ""),
            "code": str(r.get("stockCode") or code),
            "question": strip_tags(r.get("mainContent")),
            "question_date": ms_to_bj(r.get("pubDate")),
            "answer": strip_tags(r.get("attachedContent")),
            "answer_date": ms_to_bj(r.get("attachedPubDate") or r.get("updateDate")),
            "qa_status": str(r.get("qaStatus") or ""),
        }
        for r in rows
    ]


def question_detail(question_id: str) -> dict:
    """单条问答详情（唯一能拿到权威回复时间的入口）。

    列表端点的 ``attachedPubDate`` 不可信，这里的 ``replyDate`` 才是公司回复时间。
    返回值键：``question`` / ``questioner`` / ``question_date`` /
    ``answer`` / ``answer_date``(=``replyDate``) / ``code``。
    未获回复的提问没有 ``replyContent``/``replyDate`` 字段，回落为空串。
    """
    qid = str(question_id).strip()
    if not qid:
        raise SourceError("question_detail 需要非空 questionId")
    js = http("GET", f"{IRM_BASE}/question/getQuestionDetail", params={"questionId": qid})
    d = (js or {}).get("data") if isinstance(js, dict) else None
    if not d:
        msg = (js or {}).get("message") if isinstance(js, dict) else ""
        raise SourceError(f"问答详情为空：questionId={qid} message={msg!r}")
    return {
        "question": strip_tags(d.get("questionContent")),
        "questioner": str(d.get("questioner") or ""),
        "question_date": ms_to_bj(d.get("questionDate")),
        "answer": strip_tags(d.get("replyContent")),
        "answer_date": ms_to_bj(d.get("replyDate")),
        "code": str(d.get("stockCode") or ""),
    }


# ── 全景网（补充源）───────────────────────────────────────────────────
def p5w_pid(code: str) -> str:
    """抓公司页并解析隐藏域 ``companyBaseinfoId``（不传它就无法按公司过滤）。"""
    code = str(code).strip()
    if not code:
        raise SourceError("p5w_pid 需要非空股票代码")
    r = http("GET", f"{P5W_BASE}/c/{code}.shtml", expect_json=False)
    text = r.text if getattr(r, "text", None) else ""
    if not text:
        raise SourceError(f"全景网公司页 {code} 返回空文本，无法解析 pid")
    return parse_p5w_pid(text)


def p5w_qa(code: str, rows: int = 100) -> list[dict]:
    """全景网问答（互动易的补充源，含深市并转发来的互动易内容）。

    返回每项：``question_id``(``pid``) / ``code`` / ``question``(``content``) /
    ``question_date``(``questionerTimeStr``) / ``answer``(``replyContent``) /
    ``answer_date``(``replyerTimeStr``) / ``company``(``companyShortname``)。
    两个时间串本身就是 ``YYYY-MM-DD HH:MM:SS``，不做时区换算。

    两点实测与直觉不符，别照字面改回去：

    * ``page`` 是 **0 基**页码（page=0 为最新一页）；``rows`` 被服务端钳到 ≤10。
      所以这里按页翻、按 ``pid`` 去重，攒够 ``rows`` 条或翻到末页为止
      （若某页没有新增行，立即停止，避免服务端忽略 ``page`` 时死循环）。
    * 每页都做「至少一半属于目标代码」的断言：只传 ``companyCode`` 时接口会
      静默返回别家公司数据（实测 10 条里仅 2 条匹配），必须变成显式错误。

    注：关键词检索变体 ``interaction/getNewSearchR.shtml`` 的 ``total`` 被硬顶在
    100，无法用于全量历史，故不使用。
    """
    code = str(code).strip()
    if not code:
        raise SourceError("p5w_qa 需要非空股票代码")
    pid = p5w_pid(code)
    want = max(1, int(rows))

    out: list[dict] = []
    seen: set[str] = set()
    body_rows = str(want)
    for page in range(P5W_MAX_PAGES):
        data = {
            "isPagination": "1",
            "page": str(page),
            "rows": body_rows,
            "companyBaseinfoId": pid,
            "companyCode": code,
        }
        js = http("POST", f"{P5W_BASE}/interaction/getNewR.shtml", data=data)
        chunk = (js or {}).get("rows") or []
        if not chunk:
            break
        _assert_code_majority(chunk, code)

        fresh = 0
        for r in chunk:
            key = str(r.get("pid") or "")
            if key:
                if key in seen:
                    continue
                seen.add(key)
            fresh += 1
            out.append(
                {
                    "question_id": key,
                    "code": str(r.get("companyCode") or code),
                    "question": strip_tags(r.get("content")),
                    "question_date": strip_tags(r.get("questionerTimeStr")),
                    "answer": strip_tags(r.get("replyContent")),
                    "answer_date": strip_tags(r.get("replyerTimeStr")),
                    "company": strip_tags(r.get("companyShortname")),
                }
            )
        if len(out) >= want:
            break
        if fresh == 0:  # 服务端忽略 page，再翻也是同一页
            break
        if len(chunk) < min(want, P5W_PAGE_MAX):  # 短页 = 末页
            break

    return out[:want]


if __name__ == "__main__":  # 手工探针：python -m transition.sources_irm
    import sys

    c = sys.argv[1] if len(sys.argv) > 1 else "300857"
    print("org_id:", org_id(c))
    qa = company_qa(c)
    print(f"company_qa: {len(qa)} rows; answer 含'量产' 的条数 =",
          sum(1 for x in qa if "量产" in x["answer"]))
    print("first:", qa[0] if qa else None)
    print("p5w_qa:", len(p5w_qa(c, 30)), "rows")
