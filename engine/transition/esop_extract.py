# -*- coding: utf-8 -*-
"""股权激励《草案》的业绩考核目标抽取。

**覆盖率是产出的一部分，不是调试信息。** orchestrator 在 24 份未参与模式拟合的
草案上实测（research/business-transition-pricing/REPORT.md 见 S1）：参考抽取器原样
泛化失败 0/2，扩模式族后 46%，再修 `人民币` 量词 / `扣非归母` 别名 / `累计` 位置
并补表头驱动路径后 75%。剩余约 25% 是**结构性**障碍而非正则缺陷：

  * 考核指标不是净利润/营收（格科微用「产品线收入 + 产出片数（万片）」）
  * 考核对象是收购标的或子公司（康希通信考核知融科技）
  * 非数值目标（雷科防务「2026年度净利润扭亏为盈」）
  * 连锚点句都没有（*ST东智）

因此本模块的契约是：**抽不到就返回 None，由调用方在页面上明确标「需人工」并给出
PDF 深链；绝不允许「抽不到」被读成「没有考核目标」。**

两条抽取路径必须都跑并合并：
  regex  —— 模式族，覆盖绝对值 / 基数式 / 期间对比式 / 裸增速 / 相比考核基数 / 含「人民币」
  matrix —— 表头驱动，覆盖「表头写指标名、数据行只有裸百分比」的透视表
            （行内没有任何文字标签，纯正则会整类漏检）
"""
from __future__ import annotations

import re
from pathlib import Path

# ── 模式族 ────────────────────────────────────────────────────────────────
# 「率」必须可选：实测样本外最大的单词失败源就是 `增长不低于`（无「率」）。
GW = r"(?:增长率|增长)"
REL = r"(?:相较|相比|相比于|相较于|较)于?"
UNIT = r"(?:人民币)?(?:亿元|万元|元)"
UNIT_C = r"((?:人民币)?(?:亿元|万元|元))"   # 需要把单位一起落库时用捕获版
NUM = r"([0-9][0-9,\.]*)"
PCT = r"([0-9]+(?:\.[0-9]+)?)\s*%"
METRIC = (r"(?:扣除非经常性损益后|扣非)?(?:归属于(?:上市公司|母公司)股东的|归母)?"
          r"(?:净利润|营业收入|收入合计|[\u4e00-\u9fa5]{2,10}(?:业务)?(?:收入|毛利润|毛利))")

PATTERNS = [
    ("abs",        re.compile(rf"(20\d{{2}}(?:-20\d{{2}})?)\s*年?(?:度|累计)*\s*({METRIC})"
                              rf"\s*(?:累计)?\s*(?:不低于|不少于)\s*{UNIT}?\s*{NUM}\s*{UNIT_C}")),
    ("abs_company", re.compile(rf"(20\d{{2}})\s*年?(?:度)?\s*(?:公司)?\s*({METRIC})\s*"
                               rf"(?:不低于|不少于)\s*{UNIT}?\s*{NUM}\s*{UNIT_C}")),
    ("base_growth", re.compile(rf"以\s*{NUM}\s*{UNIT}(?:为基数|作为基数)?[，,]?\s*"
                               rf"(20\d{{2}})\s*年?(?:度)?\s*({METRIC})?\s*{GW}不低于\s*{PCT}")),
    ("rel_year",   re.compile(rf"(20\d{{2}})\s*年?(?:度)?\s*({METRIC})\s*{REL}\s*"
                              rf"(20\d{{2}})\s*年?(?:度)?(?:{METRIC})?[，,]?\s*{GW}不低于\s*{PCT}")),
    ("rel_named",  re.compile(rf"(20\d{{2}})\s*年?(?:度)?\s*({METRIC})\s*{REL}\s*"
                              rf"(?:考核基数|基期|基准年度?|上年(?:度)?)[，,]?\s*{GW}不低于\s*{PCT}")),
    ("bare_growth", re.compile(rf"(20\d{{2}})\s*年?(?:度)?\s*({METRIC})\s*{GW}不低于\s*{PCT}")),
]

# ── 表头驱动（透视表）路径 ────────────────────────────────────────────────
ROW_LABEL = r"第[一二三四五六七八九十]个(?:归属期|行权期|解除限售期|解除限售安排|解锁期)"
MATRIX_ROW = re.compile(rf"({ROW_LABEL})\s*(20\d{{2}})\s*年?\s*((?:[0-9]+(?:\.[0-9]+)?%+){{2,}})")
PCT_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?%")
TIER = re.compile(r"(触发值|目标值|门槛值|考核值|行权系数)\s*[（(]?\s*([A-Za-z]{0,3})\s*[）)]?")
DECL = re.compile(r"([\u4e00-\u9fa5]{2,30}?(?:增长率|收入|片数|毛利|净利润)"
                  r"[\u4e00-\u9fa5（）()A-Za-z0-9]{0,12}?)\s*[（(]\s*([A-Za-z]{1,2})\s*[）)]")

ANCHOR = "业绩考核目标"
GUIDE = "如下表所示"
OR_MARK = re.compile(r"至少满足下列两个条件之一|满足其中之一|或者|；或|;或")
FOOTNOTE = re.compile(r"上述[“\"]?(净利润|营业收入)[”\"]?[^。]{0,400}?。")
SHARE_PAY = re.compile(r"剔除[^。]{0,60}股份支付|股份支付费用")
SUBSIDIARY = re.compile(r"子公司层面|控股子公司|下属子公司")

METRIC_ALIASES = [
    (r"扣非|扣除非经常性损益", "扣非净利润"),
    (r"归母净利润|归属于(?:上市公司|母公司)股东的净利润", "净利润"),
    (r"净利润", "净利润"),
    (r"营业收入|收入合计", "营业收入"),
    (r"毛利", "毛利润"),
    (r"片数|产能|出货量", "产量类"),
    (r"业务收入", "分产品线收入"),
]


def classify_metric(raw: str) -> str:
    for pat, name in METRIC_ALIASES:
        if re.search(pat, raw):
            return name
    return "其他"


def parse_num(s: str) -> float | None:
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


LEVEL_SUB = re.compile(r"子公司层面|子公司业绩考核|子公司考核目标")
LEVEL_MAIN = re.compile(r"公司层面|上市公司层面|公司业绩考核|公司层面业绩考核")


def _scope(txt: str, tight: int = 80, wide: int = 400) -> tuple[str, str]:
    """判定考核层级，并返回判定来源。

    只在**锚点紧前**的窗口找显式层级词——用 500 字宽窗会捞到无关的「控股子公司」，
    实测把朗科智能 300543、科翔股份 300903 这类「公司层面」的方案误判成子公司层面。
    没有显式层级词时退化为启发式，并把来源标出来，避免把猜测当断言。
    """
    sub = main = 0
    heuristic = False
    for m in re.finditer(ANCHOR, txt):
        seg = txt[max(0, m.start() - tight):m.start()]
        if LEVEL_SUB.search(seg):
            sub += 1
        elif LEVEL_MAIN.search(seg):
            main += 1
        else:
            wide_seg = txt[max(0, m.start() - wide):m.start()]
            if re.search(r"控股子公司[^。]{0,20}(业绩考核目标|考核目标)", wide_seg):
                sub += 1
                heuristic = True
            else:
                main += 1
    if sub == 0 and main == 0:
        return "上市公司", "默认"
    if sub > main:
        return "子公司", "启发式" if heuristic else "显式层级词"
    return "上市公司", "显式层级词"


def extract_from_text(txt: str) -> dict:
    """从已清洗的 PDF 全文里抽考核目标。txt 应为阅读顺序、无空白。"""
    periods: list[dict] = []
    paths: set[str] = set()
    metric_raws: set[str] = set()

    # 路径 1：模式族
    for kind, rx in PATTERNS:
        for m in rx.finditer(txt):
            g = m.groups()
            paths.add("regex")
            if kind in ("abs", "abs_company"):
                # 两个 abs 模式都是 4 组：年 / 指标 / 数值 / 单位
                year, raw, val, unit = g[0], g[1], g[2], g[3]
                periods.append({"year": int(str(year)[:4]), "metric_raw": raw, "op": ">=",
                                "value": parse_num(val), "unit": unit, "kind": kind})
                metric_raws.add(raw)
            else:
                if kind == "base_growth":
                    year, raw, pct = g[1], (g[2] or ""), g[3]
                    basis = {"kind": "amount", "amount": parse_num(g[0])}
                elif kind == "rel_year":
                    year, raw, pct = g[0], g[1], g[3]
                    basis = {"kind": "year", "year": int(g[2])}
                elif kind == "rel_named":
                    year, raw, pct = g[0], g[1], g[2]
                    basis = {"kind": "基数"}
                else:
                    year, raw, pct = g[0], g[1], g[2]
                    basis = {"kind": "year", "year": int(year) - 1}
                periods.append({"year": int(year), "metric_raw": raw, "op": ">=",
                                "value": parse_num(pct), "unit": "%", "kind": kind,
                                "basis": basis})
                metric_raws.add(raw)

    # 路径 2：表头驱动（透视表）
    tiers: list[dict] = []
    for m in MATRIX_ROW.finditer(txt):
        pcts = PCT_RE.findall(m.group(3))
        if len(pcts) < 2:
            continue
        head = txt[max(0, m.start() - 700):m.start()]
        decls = DECL.findall(head)
        cols = TIER.findall(head)
        if not decls and not cols:
            continue
        paths.add("matrix")
        labels = []
        for i, (tier, code) in enumerate(cols):
            metric = next((d[0] for d in decls if d[1] == code), None)
            labels.append({"tier": tier, "code": code, "metric": metric})
        for i, pct in enumerate(pcts):
            lab = labels[i] if i < len(labels) else {"tier": "col", "code": str(i + 1), "metric": None}
            periods.append({"year": int(m.group(2)), "metric_raw": lab.get("metric") or "透视表列",
                            "op": ">=", "value": parse_num(pct.rstrip("%")), "unit": "%",
                            "kind": "matrix", "tier": lab.get("tier")})
            if lab.get("metric"):
                metric_raws.add(lab["metric"])
        tiers = labels

    # 去重：同一目标在「摘要章」与「正文章」各出现一次是正常的
    seen, uniq = set(), []
    for p in periods:
        key = (p.get("year"), p.get("metric_raw"), p.get("op"), p.get("value"), p.get("unit"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)

    primary_raw = ""
    for raw in metric_raws:
        if "净利润" in raw:
            primary_raw = raw
            break
    if not primary_raw and metric_raws:
        primary_raw = sorted(metric_raws, key=len)[0]

    fn = FOOTNOTE.search(txt)
    footnote = fn.group(0) if fn else ""
    or_cond = bool(OR_MARK.search(txt))
    level, level_src = _scope(txt)

    return {
        "metric": classify_metric(primary_raw) if primary_raw else "其他",
        "metric_raw": primary_raw,
        "periods": sorted(uniq, key=lambda p: (p.get("year") or 0, p.get("value") or 0)),
        "tiers": tiers,
        "connector": "or" if or_cond else "and",
        "scheme_level": level,
        "scheme_level_source": level_src,
        "footnote_metric_def": footnote,
        "share_pay_adjusted": bool(SHARE_PAY.search(footnote or txt)),
        "extraction_path": "+".join(sorted(paths)) or "",
    }


def extract(pdf_path: str | Path) -> dict | None:
    """抽一份草案。返回 None 表示两条路径都没命中 → 调用方必须标「需人工」。"""
    from .sources_cninfo import pdf_text

    try:
        txt = pdf_text(Path(pdf_path))
    except Exception:  # noqa: BLE001
        return None
    if ANCHOR not in txt and GUIDE not in txt:
        return None
    res = extract_from_text(txt)
    if not res["periods"]:
        return None
    res["confidence"] = "high" if res["extraction_path"] == "regex" else "medium"
    # 口径含股份支付剔除时，缺口只能给区间，不能给点值
    res["gap_kind"] = "interval" if res["share_pay_adjusted"] else "point"
    res["requires_manual"] = res["metric"] in ("其他", "产量类", "分产品线收入")
    return res


def coverage(rows: list[dict]) -> dict:
    """rows: [{'code','name','pdf_url','result': extract() 的返回值／精简标记／None}]

    `result` 允许是精简标记（只含 path/metric），因为 ingest 落库后再回调本函数时
    不必重放完整 periods。
    """
    total = len(rows)
    misses = [{"code": r["code"], "name": r.get("name"), "pdf_url": r.get("pdf_url"),
               "reason": r.get("reason") or "两条路径均未命中"}
              for r in rows if not r.get("result")]
    extracted = total - len(misses)
    by_path: dict[str, int] = {}
    by_metric: dict[str, int] = {}
    for r in rows:
        res = r.get("result")
        if not res:
            continue
        path = res.get("extraction_path") or res.get("path") or "未知"
        metric = res.get("metric") or "未知"
        by_path[path] = by_path.get(path, 0) + 1
        by_metric[metric] = by_metric.get(metric, 0) + 1
    return {
        "total": total,
        "extracted": extracted,
        "rate": round(extracted / total, 4) if total else 0.0,
        "by_path": by_path,
        "by_metric": by_metric,
        "misses": misses,
    }
