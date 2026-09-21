# -*- coding: utf-8 -*-
"""
互动易 / 全景网问答取数测试（pytest 兼容；无 pytest 时可直接运行：
    python engine/tests/test_transition_irm.py

全部用例**离线**：网络 I/O 一律通过替换 ``sources_irm.http`` 打桩，
不依赖任何活接口。覆盖：
  * strip_tags 的逐字 <em> 高亮（否则「量产」计数恒为 0）
  * 毫秒 epoch -> 北京时间
  * 全景网 pid 正则解析
  * 「至少一半行属于目标代码」防静默失效断言（打桩喂假行）
  * 提问 question 与回复 answer 的分键隔离（计数只能读 answer）
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

from transition import SourceError  # noqa: E402
from transition import sources_irm as irm  # noqa: E402

PID = "0001D0D041806387470E9907700B81ED9735"
COMPANY_PAGE = (
    '<html><body><form><input type="hidden" id="companyBaseinfoId" '
    'name="companyBaseinfoId" value="%s" /></form></body></html>' % PID
)


class _Resp:
    """打桩用的最小响应对象（只有 expect_json=False 时才会用到 .text）。"""

    def __init__(self, text: str):
        self.text = text


def _stub_http(get_text: str = COMPANY_PAGE, post_json=None, record=None):
    """造一个假的 http()，按 method 分派；record 用来记录调用参数。"""

    def fake(method, url, *, data=None, params=None, timeout=25, retries=2, expect_json=True):
        if record is not None:
            record.append({"method": method.upper(), "url": url, "data": data, "params": params})
        if method.upper() == "GET":
            if not expect_json:
                return _Resp(get_text)
            return post_json if isinstance(post_json, dict) else {}
        if callable(post_json):
            return post_json(data, params)
        return post_json if post_json is not None else {}

    return fake


def _qa_row(index_id, code, question, answer, pub=1789644036000, reply=None, update=None):
    return {
        "indexId": index_id,
        "mainContent": question,
        "attachedContent": answer,
        "pubDate": pub,
        "stockCode": code,
        "attachedPubDate": reply,
        "updateDate": update,
        "qaStatus": "2",
    }


# ── strip_tags：逐字高亮 ──────────────────────────────────────────────
def test_strip_tags_per_char_highlight():
    """<em> 逐字插入时裸 count('量产') 恒为 0，去标签后才可计数。"""
    raw = "<em>量</em><em>产</em>"
    assert raw.count("量产") == 0, "样例本身就应该证明裸计数会漏"
    assert irm.strip_tags(raw) == "量产"


def test_strip_tags_p5w_inline_style():
    """全景网高亮带内联样式（<i class=... style=...>），同样要去掉。"""
    s = "<i class='ce06868' style='display:inline'>固态</i>电池<em>量</em>产<em>线</em>"
    out = irm.strip_tags(s)
    assert out == "固态电池量产线", out
    assert out.count("量产") == 1


def test_strip_tags_entities_whitespace_and_empty():
    """实体还原 + 空白折叠；None/空 -> 空串（调用方不必自己判空）。"""
    assert irm.strip_tags("a&nbsp;&amp;&nbsp;b") == "a & b"
    assert irm.strip_tags("  多\n 行\t文本  ") == "多 行 文本"
    for bad in (None, ""):
        assert irm.strip_tags(bad) == "", bad


# ── 毫秒 epoch -> 北京时间 ────────────────────────────────────────────
def test_ms_to_bj():
    """互动易的时间字段是毫秒 epoch，必须按东八区格式化（差 8 小时即错日）。"""
    assert irm.ms_to_bj(1789644036000) == "2026-09-17 19:20:36"
    assert irm.ms_to_bj(1789731543000) == "2026-09-18 19:39:03"


def test_ms_to_bj_invalid_values_are_empty():
    for bad in (None, "", 0, -1, "abc", [], {}):
        assert irm.ms_to_bj(bad) == "", bad


# ── 全景网 pid 正则 ───────────────────────────────────────────────────
def test_parse_p5w_pid_attribute_orders():
    """隐藏域属性顺序不保证，两种写法都要能解析。"""
    a = '<input id="companyBaseinfoId" name="companyBaseinfoId" type="hidden" value="%s" />' % PID
    b = "<input value='%s' type='hidden' id='companyBaseinfoId'>" % PID
    assert irm.parse_p5w_pid(a) == PID
    assert irm.parse_p5w_pid(b) == PID


def test_parse_p5w_pid_missing_raises():
    """取不到 pid 必须显式失败：空 pid 会让 companyCode 被静默忽略。"""
    for bad in ("<html>no hidden field</html>", "", None):
        try:
            irm.parse_p5w_pid(bad)
        except SourceError:
            continue
        raise AssertionError(f"应当抛 SourceError: {bad!r}")


# ── 「至少一半行属于目标代码」防静默失效 ──────────────────────────────
def test_p5w_guard_fires_on_silent_ignore():
    """只传 companyCode 时接口返回别家数据且无报错，必须被断言拦下。"""
    rows = [
        {"pid": "a", "companyCode": "300976", "content": "q", "replyContent": "r"},
        {"pid": "b", "companyCode": "300857", "content": "q", "replyContent": "r"},
        {"pid": "c", "companyCode": "600487", "content": "q", "replyContent": "r"},
        {"pid": "d", "companyCode": "002241", "content": "q", "replyContent": "r"},
    ]
    js = {"success": True, "total": 100, "rows": rows}
    orig = irm.http
    irm.http = _stub_http(post_json=js)
    try:
        try:
            irm.p5w_qa("300857", 10)
        except SourceError as e:
            assert "pid" in str(e) and "300857" in str(e), e
        else:
            raise AssertionError("静默失效未被拦下")
    finally:
        irm.http = orig


def test_p5w_guard_passes_when_all_match_and_pages_dedupe():
    """全部匹配时正常工作；重复 pid 不重复计入。"""
    page1 = [
        {"pid": "p%d" % i, "companyCode": "300857", "companyShortname": "协创数据",
         "content": "<i class='x' style='display:inline'>固态</i>电池<em>量</em><em>产</em>",
         "questionerTimeStr": "2026-09-18 19:38:24",
         "replyContent": "公司回复：<em>量</em><em>产</em>",
         "replyerTimeStr": "2026-09-18 19:39:03"}
        for i in range(10)
    ]
    page2 = [dict(page1[0])]  # 服务端忽略 page 时会重复同一页
    calls = {"n": 0}

    def post(data, params):
        calls["n"] += 1
        return {"success": True, "total": 100, "rows": page1 if calls["n"] == 1 else page2}

    orig = irm.http
    irm.http = _stub_http(post_json=post)
    try:
        out = irm.p5w_qa("300857", 100)
    finally:
        irm.http = orig
    assert len(out) == 10, len(out)          # 去重后不会因重复页膨胀
    assert all(r["code"] == "300857" for r in out)
    assert out[0]["question"] == "固态电池量产", out[0]["question"]
    assert out[0]["answer"] == "公司回复：量产", out[0]["answer"]
    assert out[0]["question_date"] == "2026-09-18 19:38:24"


# ── 提问 / 回复分键隔离 ───────────────────────────────────────────────
def test_company_qa_separates_question_and_answer():
    """核心陷阱：提问里的里程碑词不是披露信号，两段文本必须分键。"""
    rows = [
        _qa_row("2361442039876571136", "300857",
                "请问公司产品处于<em>送</em><em>样</em>还是<em>中</em><em>试</em>阶段？",
                "公司 <em>量</em><em>产</em> 产能按计划推进。",
                pub=1789644036000, reply=None, update=1789731543000),
        _qa_row("2360792219976318976", "300857",
                "定增进度如何？", None,
                pub=1789568387000, reply=None, update=1789699935000),
    ]
    listing = {"total": 2, "pageSize": 1000, "rows": rows}
    org = {"data": [{"stockCode": "300857", "secid": "9900039793"}]}

    orig_http, orig_cache = irm.http, irm.ORG_CACHE
    tmp = Path(tempfile.mkdtemp()) / "irm_org.json"
    irm.ORG_CACHE = tmp
    calls = []

    def fake(method, url, *, data=None, params=None, timeout=25, retries=2, expect_json=True):
        calls.append((method.upper(), url, data, params))
        if "queryKeyboardInfo" in url:
            return org
        if "company/question" in url:
            return listing
        raise AssertionError("未预期的 URL: %s" % url)

    irm.http = fake
    try:
        out = irm.company_qa("300857")
    finally:
        irm.http, irm.ORG_CACHE = orig_http, orig_cache

    assert len(out) == 2
    r0 = out[0]
    # 分键：提问侧词不出现在 answer，回复侧词不出现在 question
    assert "送样" in r0["question"] and "送样" not in r0["answer"], r0
    assert "量产" in r0["answer"] and "量产" not in r0["question"], r0
    assert r0["answer"].count("量产") == 1      # 逐字高亮已被剥掉
    assert r0["question_id"] == "2361442039876571136"
    assert r0["code"] == "300857"
    assert r0["question_date"] == "2026-09-17 19:20:36"
    assert r0["answer_date"] == "2026-09-18 19:39:03"   # attachedPubDate 为 null -> updateDate
    assert r0["qa_status"] == "2"
    # 未回复：answer 为空串而不是 None，且时间回落到 updateDate
    assert out[1]["answer"] == ""
    assert out[1]["answer_date"] == "2026-09-18 10:52:15", out[1]["answer_date"]
    # 参数契约：互动易参数必须走 query string，body 只承载 keyWord
    q_call = [c for c in calls if "company/question" in c[1]][0]
    assert q_call[2] is None and q_call[3]["orgId"] == "9900039793", q_call
    assert q_call[3]["pageSize"] == 1000 and q_call[3]["pageNum"] == 1, q_call[3]
    k_call = [c for c in calls if "queryKeyboardInfo" in c[1]][0]
    assert k_call[2] == {"keyWord": "300857"}, k_call


def test_answer_date_falls_back_to_update_date():
    """attachedPubDate 实测几乎恒为 null，必须回落到 updateDate。"""
    rows = [_qa_row("1", "300857", "q", "a", pub=1789644036000, reply=None,
                    update=1789731543000)]
    listing = {"total": 1, "pageSize": 1000, "rows": rows}
    orig_http, orig_cache = irm.http, irm.ORG_CACHE
    irm.ORG_CACHE = Path(tempfile.mkdtemp()) / "irm_org.json"

    def fake(method, url, *, data=None, params=None, timeout=25, retries=2, expect_json=True):
        if "queryKeyboardInfo" in url:
            return {"data": [{"stockCode": "300857", "secid": "9900039793"}]}
        return listing

    irm.http = fake
    try:
        out = irm.company_qa("300857")
    finally:
        irm.http, irm.ORG_CACHE = orig_http, orig_cache
    assert out[0]["answer_date"] == "2026-09-18 19:39:03", out[0]

    # 有 attachedPubDate 时优先用它
    rows[0]["attachedPubDate"] = 1789644036000
    irm.http = fake
    irm.ORG_CACHE = Path(tempfile.mkdtemp()) / "irm_org.json"
    try:
        out2 = irm.company_qa("300857")
    finally:
        irm.http, irm.ORG_CACHE = orig_http, orig_cache
    assert out2[0]["answer_date"] == "2026-09-17 19:20:36", out2[0]


def test_question_detail_maps_reply_date():
    """详情端点给出权威 replyDate；未回复时缺失字段回落空串。"""
    answered = {"statusCode": 200, "data": {
        "questionContent": "<em>问</em>题", "questioner": "irm1",
        "questionDate": 1789644036000, "replyContent": "<em>回</em>复",
        "replyDate": 1789731543000, "stockCode": "300857"}}
    unanswered = {"statusCode": 200, "data": {
        "questionContent": "问题", "questioner": "irm1",
        "questionDate": 1789568387000, "stockCode": "300857"}}

    orig = irm.http
    irm.http = _stub_http(post_json=answered)
    try:
        d = irm.question_detail("1")
    finally:
        irm.http = orig
    assert d == {"question": "问题", "questioner": "irm1",
                 "question_date": "2026-09-17 19:20:36", "answer": "回复",
                 "answer_date": "2026-09-18 19:39:03", "code": "300857"}, d

    irm.http = _stub_http(post_json=unanswered)
    try:
        d = irm.question_detail("2")
    finally:
        irm.http = orig
    assert d["answer"] == "" and d["answer_date"] == "", d

    irm.http = _stub_http(post_json={"statusCode": 500, "message": "boom", "data": None})
    try:
        try:
            irm.question_detail("3")
        except SourceError:
            pass
        else:
            raise AssertionError("空详情应当抛 SourceError")
    finally:
        irm.http = orig


def test_org_id_caches_and_rejects_mismatch():
    """orgId 落盘缓存；返回的 stockCode 不匹配时不能猜着用。"""
    tmp = Path(tempfile.mkdtemp()) / "irm_org.json"
    orig = irm.http
    orig_cache = irm.ORG_CACHE
    irm.ORG_CACHE = tmp
    n = {"calls": 0}

    def fake(method, url, *, data=None, params=None, timeout=25, retries=2, expect_json=True):
        n["calls"] += 1
        return {"data": [{"stockCode": "300857", "secid": "9900039793"}]}

    irm.http = fake
    try:
        assert irm.org_id("300857") == "9900039793"
        assert irm.org_id("300857") == "9900039793"
        assert n["calls"] == 1, "第二次应命中缓存"
        assert tmp.exists()
    finally:
        irm.http = orig

    irm.http = _stub_http(post_json={"data": [{"stockCode": "300976", "secid": "x"}]})
    irm.ORG_CACHE = Path(tempfile.mkdtemp()) / "irm_org_miss.json"  # 空缓存，避免命中上一段
    try:
        try:
            irm.org_id("300857")
        except SourceError:
            pass
        else:
            raise AssertionError("stockCode 不匹配时应当抛 SourceError")
    finally:
        irm.http, irm.ORG_CACHE = orig, orig_cache


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
