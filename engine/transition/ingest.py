# -*- coding: utf-8 -*-
"""全市场采集（可断点续跑）。

设计取舍：
  * **PDF 不入库长期保存**，只落抽取结果与 PDF 深链——全量回填会下载上万份 PDF，
    保留原文既不必要也占空间。已下载的 PDF 存在 `data/transition/pdf/` 下，
    可用 `--purge-pdf` 清理。
  * 状态文件 `data/transition/state.json` 记录已完成的公告 id，
    中断后重跑不会重复下载（spec T11 的验收点）。
  * 切片规则来自实测：巨潮 `hisAnnouncement` 有 3000 条/次硬墙 → 按**月**切；
    `fulltextSearch` 有 offset 20000 墙 → 按**季**切。
  * 全量首次回填是重活（数千份 PDF），因此提供 `limit` 以便小批量验证与分批推进。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import TRANSITION_DIR, SourceError, read_json, write_json
from . import sources_cninfo as C

PDF_CACHE = TRANSITION_DIR / "pdf"
STATE_PATH = TRANSITION_DIR / "state.json"


def _state() -> dict:
    return read_json(STATE_PATH, {"esop_done": [], "milestone_done": [], "runs": 0})


def _save_state(st: dict) -> None:
    write_json(STATE_PATH, st)


def _slug(code: str, ann_id: str) -> str:
    return f"{code}_{ann_id}"


# ── 激励草案（维度 ①）────────────────────────────────────────────────────
DRAFT_EXCLUDE = ("法律意见", "核查意见", "自查表", "摘要", "独立财务顾问", "监事会",
                 "薪酬与考核委员会", "获授", "授予结果", "授予公告", "实施考核管理",
                 "考核管理办法", "更正", "问询")


def is_draft(title: str) -> bool:
    """`category_gqjl_szsh` 混着法律意见/核查意见/自查表，必须靠标题二次过滤。

    实测教训：只判「草案 in title」会把「…激励计划（草案）的法律意见」当成草案本身。
    """
    if any(x in title for x in DRAFT_EXCLUDE):
        return False
    import re
    return bool(re.search(r"激励计划\s*[（(]\s*草案\s*[）)]", title))


def discover_drafts(start: str, end: str) -> list[dict]:
    """发现草案。

    早先用 fulltextSearch 只翻 5 页（500 行）就停，全年范围下会漏掉大量早期草案
    （实测全年只报 150 份，真值 632 份）。改用 announcements(category=category_gqjl_szsh)
    ——它已按月切片、正确翻页、并做 totalAnnouncement 量级自校验。"""
    out, seen = [], set()
    for it in C.announcements("category_gqjl_szsh", start, end, expect_min=100):
        code = str(it.get("secCode") or "")
        title = C.strip_em(it.get("announcementTitle"))
        if len(code) != 6 or not code.isdigit():
            continue
        if not is_draft(title) or not it.get("adjunctUrl"):
            continue
        key = (code, str(it.get("announcementId")))
        if key in seen:
            continue
        seen.add(key)
        out.append({"code": code, "name": it.get("secName"), "title": title,
                    "announcement_id": str(it.get("announcementId")),
                    "announcement_time": it.get("announcementTime"),
                    "adjunct_url": it["adjunctUrl"]})
    return out


def ingest_esop(start: str, end: str, limit: int | None = None,
                progress_every: int = 10) -> dict:
    """下载并抽取草案的考核目标，落 `esop.parquet` + `coverage.json`。"""
    from . import esop_extract as E

    st = _state()
    done = set(st.get("esop_done") or [])
    drafts = discover_drafts(start, end)
    todo = [d for d in drafts if _slug(d["code"], d["announcement_id"]) not in done]
    if limit:
        todo = todo[:limit]
    print(f"[esop] 发现 {len(drafts)} 份草案，待处理 {len(todo)}（已完成 {len(done)}）")

    rows: list[dict] = []
    for i, d in enumerate(todo, 1):
        slug = _slug(d["code"], d["announcement_id"])
        pdf = PDF_CACHE / f"{slug}.pdf"
        try:
            C.download_pdf(d["adjunct_url"], pdf)
            res = E.extract(pdf)
        except SourceError as e:
            res = None
            d["reason"] = f"下载失败：{e}"
        except Exception as e:  # noqa: BLE001
            res = None
            d["reason"] = f"抽取异常：{type(e).__name__}"
        row = {**{k: d[k] for k in ("code", "name", "announcement_id", "adjunct_url")},
               "pdf_url": C.pdf_url(d["adjunct_url"]), "extracted": res is not None,
               "reason": d.get("reason", "")}
        if res:
            row.update({k: v for k, v in res.items() if k != "periods"})
            row["n_periods"] = len(res["periods"])
            row["periods_json"] = json.dumps(res["periods"], ensure_ascii=False)
            row["footnote_metric_def"] = res["footnote_metric_def"]
        rows.append(row)
        done.add(slug)
        if i % progress_every == 0:
            print(f"    {i}/{len(todo)} 已处理（命中 {sum(1 for r in rows if r['extracted'])}）")

    df = pd.DataFrame(rows)
    df.to_parquet(TRANSITION_DIR / "esop.parquet", index=False)
    # 注意：命中标记必须是**非空** dict —— coverage() 用 `not result` 判缺失，
    # 传 {} 会把抽取成功的行也算成未命中。
    cov = E.coverage([{"code": r["code"], "name": r.get("name"),
                       "pdf_url": r.get("pdf_url"),
                       "result": ({"path": r.get("extraction_path"),
                                   "metric": r.get("metric")} if r["extracted"] else None),
                       "reason": r.get("reason")} for r in rows])
    prev = read_json(TRANSITION_DIR / "coverage.json", {})
    write_json(TRANSITION_DIR / "coverage.json",
               {"esop": {**cov, "window": f"{start}~{end}", "runs": (prev.get("esop", {}).get("runs", 0) + 1)}})
    st["esop_done"] = sorted(done)
    st["runs"] = st.get("runs", 0) + 1
    _save_state(st)
    print(f"[esop] 命中 {cov['extracted']}/{cov['total']} = {100 * cov['rate']:.0f}%")
    return cov


# ── 调研纪要（维度 ④）────────────────────────────────────────────────────
def discover_minutes(start: str, end: str) -> list[dict]:
    """调研纪要列表：深市走 `tabName=relation`，沪市走全文检索（实测该 tab 是深交所专属）。"""
    out, seen = [], set()
    for r in C.relation_minutes(start, end):
        code = str(r.get("secCode") or "")
        if len(code) == 6 and code.isdigit() and code not in seen and r.get("adjunctUrl"):
            seen.add(code)
            out.append({"code": code, "name": r.get("secName"),
                        "announcement_id": str(r.get("announcementId")),
                        "adjunct_url": r["adjunctUrl"]})
    for r in C.fulltext("投资者关系活动记录表", start, end):
        code = str(r.get("secCode") or "")
        if len(code) == 6 and code.isdigit() and code not in seen and r.get("adjunctUrl"):
            seen.add(code)
            out.append({"code": code, "name": r.get("secName"),
                        "announcement_id": str(r.get("announcementId")),
                        "adjunct_url": r["adjunctUrl"]})
    return out


def ingest_milestones(start: str, end: str, limit: int | None = None) -> dict:
    """抽取调研纪要正文里的里程碑措辞，落 `milestone.parquet`。"""
    from . import milestone_extract as M

    st = _state()
    done = set(st.get("milestone_done") or [])
    docs = discover_minutes(start, end)
    todo = [d for d in docs if _slug(d["code"], d["announcement_id"]) not in done]
    if limit:
        todo = todo[:limit]
    print(f"[milestone] 发现 {len(docs)} 篇调研纪要，待处理 {len(todo)}")

    rows, hits = [], 0
    for d in todo:
        slug = _slug(d["code"], d["announcement_id"])
        pdf = PDF_CACHE / f"{slug}.pdf"
        try:
            C.download_pdf(d["adjunct_url"], pdf)
            txt = C.pdf_text(pdf)
        except Exception:  # noqa: BLE001
            st.setdefault("milestone_done", []).append(slug)
            continue
        c = M.classify(txt, "announcement")
        traps = M.trap_hits(txt)
        row = {"code": d["code"], "name": d.get("name"), "source": "relation",
               "doc_id": slug, "url": C.pdf_url(d["adjunct_url"]),
               "date": str(pd.to_datetime(int(pd.Timestamp(start).timestamp())))[:10],
               "stage": c["stage"], "level": c["level"], "realized": c["realized"],
               "terms": ",".join(c["terms"]),
               "sentence": M.stage_sentence(txt, c["terms"]),
               "traps_json": json.dumps(traps, ensure_ascii=False)}
        rows.append(row)
        if c["stage"] != "unknown":
            hits += 1
        done.add(slug)
    df = pd.DataFrame(rows)
    df.to_parquet(TRANSITION_DIR / "milestone.parquet", index=False)
    st["milestone_done"] = sorted(done)
    _save_state(st)
    print(f"[milestone] {hits}/{len(rows)} 篇含里程碑措辞")
    return {"total": len(rows), "with_stage": hits}


# ── 互动易（维度 ③）───────────────────────────────────────────────────────
def ingest_irm(codes: list[str]) -> int:
    """只落**公司回复侧**文本。提问侧会严重虚高里程碑计数（实测送样 12 vs 0）。"""
    from . import sources_irm as I

    rows = []
    for code in codes:
        try:
            qa = I.company_qa(code)
        except Exception as e:  # noqa: BLE001
            print(f"  [irm] {code} 失败：{type(e).__name__}")
            continue
        for q in qa:
            if not (q.get("answer") or "").strip():
                continue
            rows.append({"code": code, "source": "irm", "doc_id": q.get("question_id"),
                         "url": "https://irm.cninfo.com.cn/", "date": q.get("answer_date") or "",
                         "text": q["answer"]})
    df = pd.DataFrame(rows)
    df.to_parquet(TRANSITION_DIR / "irm.parquet", index=False)
    print(f"[irm] {len(rows)} 条公司回复（{len(codes)} 只）")
    return len(rows)


# ── 一致预期与财报（维度 ②⑤ 的量化输入）────────────────────────────────
def ingest_consensus(limit_pages: int | None = None) -> int:
    """全市场一致预期快照（keyless，2916 只有覆盖）。"""
    from . import sources_em as EM

    df = EM.consensus_snapshot(save=True)
    if limit_pages:
        df = df.head(limit_pages)
    print(f"[consensus] {len(df)} 只")
    return len(df)


def ingest_financials(codes: list[str], parquet_name: str = "financials") -> int:
    """批量取中报/季报（含 YSTZ/SJLTZ 同比）。单次批量上限 400 只。"""
    from . import sources_em as EM

    frames = []
    for batch in [codes[i:i + 350] for i in range(0, len(codes), 350)]:
        try:
            frames.append(EM.financials(batch))
        except SourceError as e:
            print(f"  [financials] 批次失败：{e}")
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    df.to_parquet(TRANSITION_DIR / f"{parquet_name}.parquet", index=False)
    print(f"[financials] {len(df)} 行（{len(codes)} 只）")
    return len(df)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--esop", nargs=2, metavar=("START", "END"))
    ap.add_argument("--minutes", nargs=2, metavar=("START", "END"))
    ap.add_argument("--consensus", action="store_true")
    ap.add_argument("--financials-from-esop", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    if a.esop:
        ingest_esop(a.esop[0], a.esop[1], limit=a.limit)
    if a.minutes:
        ingest_milestones(a.minutes[0], a.minutes[1], limit=a.limit)
    if a.consensus:
        ingest_consensus()
    if a.financials_from_esop:
        df = _load_parquet("esop")
        if df is not None and "code" in df.columns:
            ingest_financials(sorted(set(df["code"].astype(str))))
