# -*- coding: utf-8 -*-
"""转型里程碑措辞定级。

阶段枚举**硬编码且不可越级**（依据：金龙羽 002882 以「固态电解质、半固态电芯已进入
中试试验」「中试已出样品」换来 16 个交易日 +96.68%，随即收关注函 —— 中试不是产业化）：

    unknown(0) < 送样(1) < 小试/中试(2) < 小批量(3) < 量产(4) < 收入确认(5)

**判定「转型已兑现」的唯一门槛是 `量产` 或 `收入确认`。** 送样/小试/中试/小批量
只记进度分，不记兑现分。

调研实测（findings/F6.md）：
  * 60 篇调研纪要中 52% 至少含 1 个里程碑词，但早期信号极稀：
    验证 25% / 量产 18% / 产业化 10% / 中试 7% vs 送样 2% / 小批量 3% / 正式订单 2%
    → 以「送样/小批量」为主筛条件无法靠关键词预筛，必须全量下载后再计数
  * 互动易 `mainContent` 是**投资者提问**、`attachedContent` 才是**公司回复**；
    819 条样本中「送样」提问侧 12 次、回复侧 0 次 → 只统计公司侧文本
  * `收入确认`/`意向订单`/`框架协议` 三类复合词在全文检索里会被中文分词按 OR 拆词，
    只能在 PDF 文本上做精确子串计数（本模块只接受已抽取的正文文本）
"""
from __future__ import annotations

import re

STAGES = ["unknown", "送样", "小试/中试", "小批量", "量产", "收入确认"]
STAGE_LEVEL = {s: i for i, s in enumerate(STAGES)}
REALIZED_FROM = STAGE_LEVEL["量产"]      # 达到该级别才算「已兑现」

# 每个阶段的正向措辞。措辞按「客户能验证的具体动作」排序，避免把愿景词当进展。
STAGE_TERMS: dict[str, list[str]] = {
    "送样": ["送样", "样品交付", "已送样", "提交样品", "样件交付", "送检"],
    "小试/中试": ["小试", "中试", "中试线", "中试试验", "已出样品", "试产", "小试线",
                  "试验线", "中试阶段", "试制"],
    "小批量": ["小批量", "小批量试产", "小批量供货", "小批量交付", "小批量生产",
               "小批量出货", "批量试产"],
    "量产": ["量产", "批量供货", "批量交付", "批量出货", "量产线", "已量产",
             "规模量产", "开始量产", "量产阶段", "产能爬坡", "达产"],
    "收入确认": ["收入确认", "确认收入", "已确认收入", "计入当期收入", "形成收入",
                 "实现销售收入", "产生收入"],
}

# 反向/负面措辞：出现时不得据此升级阶段
NEGATIVE = ["尚未量产", "未量产", "未形成批量", "尚未形成收入", "未产生收入",
            "尚未产生收入", "不构成收入", "未实现收入", "仍在研发", "研发中",
            "不确定性", "尚在研发", "尚未签署", "无实质进展", "仅处于"]

# 陷阱词：不是阶段，但必须单独计数并进扣分项
TRAP_TERMS = {
    "框架协议": ["框架协议", "战略合作协议", "意向性协议"],
    "意向订单": ["意向采购", "意向订单", "意向性订单", "意向协议"],
    "正式订单": ["正式订单", "中标通知", "正式合同", "采购合同"],
    "非约束": ["不具有法律效力", "没有强制约束力", "不具约束力", "无强制约束力"],
}


def _own_side_text(text: str, source: str) -> str:
    """只保留公司侧文本。

    互动易的提问（`mainContent`）**不得**参与定级——投资者会把「处于小试还是中试」
    当问题抛出来，一条提问能同时含 4 个里程碑词却零披露价值。
    调用方应传 `source="irm_question"` 时被直接拒绝。
    """
    if source == "irm_question":
        raise ValueError(
            "互动易提问侧文本不得用于里程碑定级：实测「送样」在提问侧出现 12 次、"
            "回复侧 0 次，扫提问会把计数严重虚高"
        )
    return text or ""


def _strip_negations(text: str) -> str:
    """剔除「尚未量产」「未形成批量」这类否定片段，避免反向措辞被当成正向命中。"""
    out = text
    for neg in NEGATIVE:
        # 去掉「否定词 + 其后 12 字」，例如「尚未量产，仍在样品阶段」
        out = re.sub(re.escape(neg) + r"[^。；;，,]{0,12}", "　", out)
    return out


def _find_term(term: str, text: str) -> list[int]:
    """在 text 中找 term 的位置，但对「小批量」前缀做排除。

    「量产」词表里的 `批量交付` / `批量供货` / `批量出货` 是 `小批量交付` 等的子串，
    朴素的 `in` 判定会把「小批量交付」误升为「量产」= 误判为「转型已兑现」。
    因此对以「批量」开头的词加一个前缀排除。
    """
    out = []
    if term.startswith("批量"):
        for m in re.finditer(re.escape(term), text):
            i = m.start()
            if i > 0 and text[i - 1] == "小":
                continue
            out.append(i)
    else:
        out = [m.start() for m in re.finditer(re.escape(term), text)]
    return out


def classify(text: str, source: str = "announcement") -> dict:
    """返回最高阶段与该阶段的证据词。纯函数，便于单测。"""
    t = _strip_negations(_own_side_text(text, source))
    best_stage, best_terms, all_hits = "unknown", [], {}
    for stage in STAGES[1:]:
        hits = [w for w in STAGE_TERMS[stage] if _find_term(w, t)]
        if hits:
            all_hits[stage] = hits
            if STAGE_LEVEL[stage] > STAGE_LEVEL[best_stage]:
                best_stage, best_terms = stage, hits
    return {
        "stage": best_stage,
        "level": STAGE_LEVEL[best_stage],
        "terms": best_terms,
        "all_hits": all_hits,
        "realized": STAGE_LEVEL[best_stage] >= REALIZED_FROM,
    }


def stage_sentence(text: str, terms: list[str], width: int = 45) -> str:
    """取第一条命中词的上下文，作为可回溯的证据句。"""
    for w in terms:
        pos = _find_term(w, text)
        if pos:
            i = pos[0]
            return text[max(0, i - width):i + width]
    return ""


def trap_hits(text: str) -> dict:
    """复合词精确子串计数（不使用全文检索结果，那里会被分词 OR 拆词）。"""
    return {k: sum(text.count(w) for w in words) for k, words in TRAP_TERMS.items()}


def collect_evidence(records: list[dict]) -> dict:
    """records: [{'code','source','date','text','url','doc_id'}]（只应含公司侧文本）

    返回 {'stage','level','realized','latest': {...}, 'evidence': [...]}。
    证据全部保留以便回溯；当前阶段取**最新日期**的那条。
    """
    ev = []
    for r in records:
        try:
            c = classify(r.get("text", ""), r.get("source", "announcement"))
        except ValueError:
            continue
        if c["stage"] == "unknown":
            continue
        ev.append({
            "code": r.get("code"), "source": r.get("source"), "date": r.get("date"),
            "url": r.get("url"), "doc_id": r.get("doc_id"),
            "stage": c["stage"], "level": c["level"], "terms": c["terms"],
            "realized": c["realized"],
            "sentence": stage_sentence(r.get("text", ""), c["terms"]),
            "traps": trap_hits(r.get("text", "")),
        })
    if not ev:
        return {"stage": "unknown", "level": 0, "realized": False,
                "latest": None, "evidence": []}
    ev.sort(key=lambda x: (str(x.get("date") or ""), x["level"]))
    latest = ev[-1]
    return {"stage": latest["stage"], "level": latest["level"],
            "realized": latest["realized"], "latest": latest, "evidence": ev}
