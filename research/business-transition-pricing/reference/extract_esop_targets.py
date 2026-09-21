"""
F2 证据脚本：从 cninfo 股权激励《草案》PDF 中抽取「业绩考核目标」表格数字。

实测环境：E:\\Project\\stock\\.venv（Python 3），依赖 pypdfium2（本轮用 pip --target 装到 %TEMP%\\f2libs）。
用法：
    set PYTHONPATH=%TEMP%\\f2libs
    .venv\\Scripts\\python.exe extract_esop_targets.py <pdf 路径...>

设计结论（对应 F2.md findings [5][6][7][8][9]）：
  1) 用阅读顺序全文 get_text_range()，不要按 y 坐标聚行（数字基线差 ~1pt 会被拆走）。
  2) 先删页眉页脚（含公司全称 + 「激励计划（草案）」 + 孤立页码）。
  3) 锚点用「如下表所示」引导句，其后第一个表格块才是真目标表；单搜「业绩考核目标」会误命中正文。
  4) 指标措辞不统一，必须用「正则族」覆盖：绝对值 / 基数式增速 / 期间对比式增速 / 累计 / 或条件 / 两档制。
  5) 同一目标在「摘要章」和「正文章」各出现一次 —— 必须去重。
"""
import re
import sys
import os

import pypdfium2 as pdfium

# --- 1) 页眉页脚清洗 ---------------------------------------------------------
HEADER = re.compile(r"[\u4e00-\u9fa5（）()A-Za-z0-9]{6,60}?(?:股份有限公司|集团股份有限公司|科技股份有限公司)"
                    r"20\d{2}年.{0,20}激励计划（草案）")
PAGENO = re.compile(r"^(?:第)?\d{1,3}(?:页)?$")


def pdf_text(path: str) -> str:
    pdf = pdfium.PdfDocument(path)
    raw = "".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    raw = raw.replace("\u3000", "")
    lines = []
    for ln in raw.split("\n"):
        s = ln.strip()
        if not s or PAGENO.match(s):
            continue
        s = HEADER.sub("", s)          # 去掉粘在正文里的页眉
        if s:
            lines.append(s)
    txt = "".join(lines)
    return re.sub(r"[ \t]", "", txt)


# --- 2) 正则族 ---------------------------------------------------------------
METRIC = r"(?:扣除非经常性损益后的)?(?:归属于(?:上市公司|母公司)股东的)?(?:净利润|营业收入|收入合计|[\u4e00-\u9fa5]{2,10}业务收入|[\u4e00-\u9fa5]{2,10}业务?毛利润)"
UNIT = r"(亿元|万元|元)"

PATTERNS = [
    # A. 绝对值：2026年净利润不低于2.50亿元 / 2026-2027年累计净利润不低于8.00亿元
    ("abs", re.compile(rf"(20\d{{2}}(?:-20\d{{2}})?)\s*年?(?:度|累计)*\s*({METRIC})\s*不低于\s*([0-9][0-9,\.]*)\s*{UNIT}")),
    # B. 基数式增速：以2025年净利润为基数，2026年净利润增长率不低于25%
    ("base_growth", re.compile(rf"以\s*([0-9][0-9,\.]*)\s*{UNIT}(?:为基数)?[，,]?\s*(20\d{{2}})\s*年(?:度)?\s*({METRIC})?\s*增长率不低于\s*([0-9\.]+)\s*%")),
    # C. 期间对比式增速：2026年度营业收入相较2025年度营业收入，增长率不低于15%
    ("yoy_growth", re.compile(rf"(20\d{{2}})\s*年?(?:度)?\s*({METRIC})\s*(?:相较|较)\s*(20\d{{2}})\s*年?(?:度)?\s*{METRIC}?[，,]?\s*增长率不低于\s*([0-9\.]+)\s*%")),
    # D. 无条件词裸增速：2026年净利润增长率不低于25%
    ("growth", re.compile(rf"(20\d{{2}})\s*年?(?:度)?\s*({METRIC})\s*增长率不低于\s*([0-9\.]+)\s*%")),
]
# 或条件分隔符（用于标记「收入 或 毛利润」这类二选一）
OR_MARK = re.compile(r"不低于\s*[0-9][0-9,\.]*\s*(?:亿元|万元|元)\s*或")

# --- 3) 表头驱动的「透视表」路径（覆盖 findings [7]：雄帝科技 300546 那一类）---------
# 表头把指标名写在列头上，数据行只有裸百分比，全表不含「不低于」。
ROW_LABEL = r"第[一二三四五六]个(?:行权期|归属期|解除限售期|解除限售安排|解锁期)"
# 数据行：第N个行权期 2026年 14.18%26.87%20.69%34.10%
MATRIX_ROW = re.compile(rf"({ROW_LABEL})\s*(20\d{{2}})\s*年?\s*((?:[0-9]+(?:\.[0-9]+)?%+){{2,}})")
# 表头子列标签：触发值（An） / 目标值（Am） / 触发值（Bn） / 目标值（Bm）
SUBCOL = re.compile(r"(触发值|目标值)\s*[（(]\s*([A-Za-z][a-zA-Z0-9]*)\s*[）)]")
# 表头里的「以…为基数，…增长率（X）」指标声明
METRIC_DECL = re.compile(r"([\u4e00-\u9fa5]{0,20}?(?:营业收入|净利润)[\u4e00-\u9fa5]{0,10}增长率)\s*[（(]\s*([A-Za-z])\s*[）)]")
PCT = re.compile(r"[0-9]+(?:\.[0-9]+)?%")


def extract_matrix(txt: str):
    """表头驱动：用表头列序把裸百分比映射成 (指标, 档位)。"""
    out = []
    for m in MATRIX_ROW.finditer(txt):
        pcts = PCT.findall(m.group(3))
        if len(pcts) < 2:
            continue
        # 取该数据行之前的最近一段作为表头
        head = txt[max(0, m.start() - 600):m.start()]
        subcols = SUBCOL.findall(head)          # [(触发值,An),(目标值,Am),(触发值,Bn),(目标值,Bm)]
        decls = METRIC_DECL.findall(head)       # [(以2025年度营业收入为基数…增长率, A), (…净利润…, B)]
        # 去掉重复的连续同类标签（表头跨行时会重复出现）
        subcols = list(dict.fromkeys(subcols))
        decls = list(dict.fromkeys(decls))
        labels = []
        for tier, code in subcols:
            metric = next((d[0] for d in decls if d[1] == code[0]), f"指标{code}")
            labels.append(f"{metric}[{tier}{code}]")
        if not labels:
            labels = [f"col{i + 1}" for i in range(len(pcts))]
        pairs = list(zip(labels, pcts)) if len(labels) == len(pcts) else None
        out.append({
            "kind": "matrix",
            "row": m.group(1),
            "year": m.group(2),
            "raw": m.group(0),
            "columns": labels,
            "values": pcts,
            "mapped": pairs,
            "header_ok": pairs is not None,
        })
    return out


def extract(path: str):
    txt = pdf_text(path)
    hits = []
    for kind, rx in PATTERNS:
        for m in rx.finditer(txt):
            hits.append({"kind": kind, "match": m.group(0), "span": m.span()})
    # 归并同一 span 的重复（增长率类可能被 B/D 双命中）
    uniq, seen = [], set()
    for h in sorted(hits, key=lambda x: x["span"]):
        if h["span"] in seen:
            continue
        seen.add(h["span"])
        h["or_condition"] = bool(OR_MARK.search(txt[max(0, h["span"][0] - 80):h["span"][1] + 80]))
        uniq.append(h)
    return txt, uniq, extract_matrix(txt)


def main(paths):
    for p in paths:
        txt, hits, matrix = extract(p)
        # 5) 去重：摘要章 + 正文章各一次 -> 按 match 文本计数
        counts = {}
        for h in hits:
            counts[h["match"]] = counts.get(h["match"], 0) + 1
        print(f"=== {os.path.basename(p)}  ({len(txt)} chars, {len(hits)} raw hits, "
              f"{len(counts)} distinct, {len(matrix)} matrix rows)")
        if hits:
            print(f"    [关键词路径] 出现 2 次(=摘要+正文)的条目: "
                  f"{sum(1 for v in counts.values() if v == 2)}")
            for h in hits:
                flag = " [或条件]" if h["or_condition"] else ""
                print(f"    - {h['kind']:<12} | {h['match']}{flag}")
        if matrix:
            print("    [表头驱动路径] 透视表类（行内无「不低于」）:")
            for r in matrix:
                print(f"    - {r['row']} {r['year']}年 | header_ok={r['header_ok']}")
                print(f"        列名: {r['columns']}")
                print(f"        数值: {r['values']}")
                if r["mapped"]:
                    for lab, val in r["mapped"]:
                        print(f"          => {lab} = {val}")
        if not hits and not matrix:
            print("    !! 两条路径都未命中（该布局需人工看）")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        base = os.path.dirname(os.path.abspath(__file__))
        paths = sorted(os.path.join(base, f) for f in os.listdir(base) if f.endswith(".pdf"))
    else:
        paths = sys.argv[1:]
    main(paths)
