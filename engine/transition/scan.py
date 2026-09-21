# -*- coding: utf-8 -*-
"""把采集结果合成复盘页需要的 `payload["transition"]` 段。

两条纪律：
  1. **缺源要降级，不能崩。** 一致预期/财报缺失时分项置 None，由 score 层按剩余权重
     重新归一化——「没拿到数据」不等于「表现很差」。
  2. **覆盖率横幅是必出项。** 「未抽取到考核目标」必须与「没有考核目标」在视觉上分开，
     因此 coverage 段在任何情况下都要写进 payload，且带上未命中清单与 PDF 深链。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import TRANSITION_DIR, read_json
from . import score as S

TOP_N_DEFAULT = 20


def _load(name: str) -> pd.DataFrame | None:
    p = TRANSITION_DIR / f"{name}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        return df if len(df) else None
    except Exception:  # noqa: BLE001
        return None


def _consensus_growth(c: pd.DataFrame, code: str, year: int) -> float | None:
    """一致预期隐含增速：(EPS_year − EPS_{year−1}) / |EPS_{year−1}|。"""
    try:
        row = c.loc[code]
    except KeyError:
        return None
    eps: dict[int, float] = {}
    for i in (1, 2, 3, 4):
        y, e = row.get(f"YEAR{i}"), row.get(f"EPS{i}")
        if pd.notna(y) and pd.notna(e):
            eps[int(y)] = float(e)
    if year not in eps or (year - 1) not in eps or not eps[year - 1]:
        return None
    return (eps[year] - eps[year - 1]) / abs(eps[year - 1])


def _implied_growth(periods: list[dict]) -> tuple[int | None, float | None]:
    """把考核目标反解成「隐含增速」。只处理增速类目标（单位 %）。"""
    pcts = [p for p in periods if p.get("unit") == "%" and p.get("value") is not None]
    if not pcts:
        return None, None
    p = sorted(pcts, key=lambda x: x["year"])[0]
    return int(p["year"]), float(p["value"]) / 100.0


def _sue_proxy(fin: pd.DataFrame, code: str, min_obs: int = 6) -> float | None:
    """时序口径 SUE 代理：最近一期净利同比相对该公司自身历史的 z 值。

    刻意**不用**「公告日股价反应」口径（EAR3）——实测 T+2~T+60 超额≈0，是死因子。
    """
    try:
        sub = fin[fin["SECURITY_CODE"] == code].sort_values("REPORTDATE")
    except Exception:  # noqa: BLE001
        return None
    if "SJLTZ" not in sub or len(sub) < min_obs:
        return None
    s = pd.to_numeric(sub["SJLTZ"], errors="coerce").dropna()
    if len(s) < min_obs:
        return None
    hist, last = s.iloc[:-1], s.iloc[-1]
    sd = float(hist.std())
    if not sd or pd.isna(sd):
        return None
    return float((last - hist.mean()) / sd)


def _tightness(implied: float | None, fin: pd.DataFrame, code: str) -> str | None:
    """目标隐含增速 vs 公司自身近两年实际增速。"""
    if implied is None:
        return None
    try:
        sub = fin[fin["SECURITY_CODE"] == code].sort_values("REPORTDATE")
        hist = pd.to_numeric(sub["SJLTZ"], errors="coerce").dropna().iloc[-2:]
    except Exception:  # noqa: BLE001
        return None
    if len(hist) < 1:
        return None
    avg = float(hist.mean()) / 100.0
    if implied > avg * 1.15:
        return "高于历史"
    if implied < avg * 0.75:
        return "低于历史"
    return "接近历史"


def enrich_revision(items: list[dict], limit: int = 40) -> int:
    """对候选做逐股一致预期月度修正补取，并重算分数 A。

    全市场跑 `profit_forecast` 是 1 请求/股，成本高；因此只对**排序靠前的候选**补取
    （spec 的「全市场结构化初筛 → Top N 深挖」）。补不到就保持缺失，由 score 层
    重新归一化，而不是当 0 分。
    """
    from . import sources_em as EM

    filled = 0
    for it in items[:limit]:
        try:
            pf = EM.profit_forecast(it["code"])
        except Exception:  # noqa: BLE001
            continue
        rev = pf.get("revision_1m")
        if rev is None:
            continue
        filled += 1
        it["revision_1m"] = rev
        a = S.score_a(rev, it.pop("_sue", None), it.pop("_gap", None))
        it["scoreA"] = a["score"]
        it["componentsA"] = {k: {"label": v["label"], "raw": v["raw"],
                                 "weight": v.get("weight_effective"),
                                 "missing": v.get("missing", False)}
                             for k, v in a["components"].items()}
        it["missingA"] = a["missing"]
        it["rankable"] = a["rankable"]
        it["nAvailableA"] = a["n_available"]
    return filled


def build(top_n: int = TOP_N_DEFAULT, as_of: str | None = None,
          enrich: bool = True, enrich_limit: int = 40) -> dict:
    esop = _load("esop")
    mile = _load("milestone")
    cons = _load("consensus")
    fin = _load("financials")
    if cons is not None and "SECURITY_CODE" in cons.columns:
        cons = cons.set_index("SECURITY_CODE", drop=False)

    codes: list[str] = []
    for df in (esop, mile):
        if df is not None and "code" in df.columns:
            codes.extend(df["code"].astype(str).tolist())
    codes = sorted(set(codes))
    if not codes:
        return {"asOf": as_of, "coverage": read_json(TRANSITION_DIR / "coverage.json", {}),
                "items": [], "note": "尚无采集数据（先跑 transition.ingest）"}

    # 只保留抽到目标的草案；未抽到的进入「需人工」清单而不是被当成没有目标
    esop_by_code: dict[str, dict] = {}
    if esop is not None:
        for _, r in esop.iterrows():
            if not r.get("extracted"):
                continue
            periods = json.loads(r.get("periods_json") or "[]")
            esop_by_code[str(r["code"])] = {
                "metric": r.get("metric"), "metric_raw": r.get("metric_raw"),
                "scheme_level": r.get("scheme_level"), "periods": periods,
                "path": r.get("extraction_path"), "gap_kind": r.get("gap_kind"),
                "connector": r.get("connector"),
                "requires_manual": bool(r.get("requires_manual")),
                "share_pay_adjusted": bool(r.get("share_pay_adjusted")),
                "footnote": r.get("footnote_metric_def") or "",
                "pdf_url": r.get("pdf_url"),
            }

    mile_by_code: dict[str, dict] = {}
    if mile is not None:
        for code, sub in mile.groupby("code"):
            sub = sub.sort_values("date")
            last = sub.iloc[-1]
            mile_by_code[str(code)] = {
                "stage": last.get("stage"), "level": int(last.get("level") or 0),
                "date": last.get("date"), "url": last.get("url"),
                "sentence": last.get("sentence"),
                "realized": bool(last.get("realized")),
                "traps": json.loads(last.get("traps_json") or "{}"),
                "evidence": len(sub),
            }

    items = []
    for code in codes:
        e = esop_by_code.get(code)
        m = mile_by_code.get(code, {})
        year, implied = _implied_growth(e["periods"]) if e else (None, None)
        cg = _consensus_growth(cons, code, year) if (cons is not None and year) else None

        revision = None
        if cons is not None:
            try:
                _r = cons.loc[code]
                # 快照只用于判断是否被覆盖；月度修正值由 enrich_revision 逐股补取。
                revision = None if pd.isna(_r.get("EPS1")) else None
            except KeyError:
                revision = None

        sue = _sue_proxy(fin, code) if fin is not None else None
        gap = (implied - cg) if (implied is not None and cg is not None) else None

        a = S.score_a(revision, sue, gap)
        metric_one = (e or {}).get("metric") or ""
        metric_set = [metric_one] if metric_one else []
        b = S.score_b(
            stage_level=m.get("level", 0),
            metric=metric_one,
            metric_set=metric_set,
            tightness=_tightness(implied, fin, code) if fin is not None else None,
            traps=m.get("traps") or {},
            scheme_level=(e or {}).get("scheme_level") or "上市公司",
            connector=(e or {}).get("connector") or "and",
        )

        manual = []
        if e is None:
            manual.append("考核目标未抽取到，需看草案原文")
        if m.get("level", 0) == 0:
            manual.append("无里程碑证据（调研纪要/互动易未见进展措辞）")
        if (e or {}).get("requires_manual"):
            manual.append(f"考核指标为「{(e or {}).get('metric')}」，无法倒算净利润缺口")

        items.append({
            "code": code, "name": _name_of(code, esop, mile),
            "scoreA": a["score"], "scoreB": b["score"],
            "componentsA": {k: {"label": v["label"], "raw": v["raw"],
                                "weight": v.get("weight_effective"), "missing": v.get("missing", False)}
                            for k, v in a["components"].items()},
            "componentsB": {k: {"label": v["label"], "raw": v["raw"],
                                "weight": v.get("weight_effective"), "missing": v.get("missing", False)}
                            for k, v in b["components"].items()},
            "missingA": a["missing"], "missingB": b["missing"],
            "rankable": a["rankable"], "nAvailableA": a["n_available"],
            "_sue": sue, "_gap": gap,
            "milestone": m or None,
            "esop": e,
            "impliedGrowth": implied, "consensusGrowth": cg,
            "scoreBRealized": b["realized"],
            "manual": manual,
        })

    # 排序纪律：先按「可用分项是否够」分组，再按分数。稀疏覆盖的标的不得霸榜。
    items.sort(key=lambda x: (not x["rankable"], -x["scoreA"], -x["scoreB"]))
    if enrich:
        filled = enrich_revision(items, limit=enrich_limit)
        print(f"[scan] 补取一致预期月度修正 {filled} 只")
        items.sort(key=lambda x: (not x["rankable"], -x["scoreA"], -x["scoreB"]))
    for it in items:
        it.pop("_sue", None)
        it.pop("_gap", None)
    unrankable = [i["code"] for i in items if not i["rankable"]]
    return {
        "asOf": as_of,
        "coverage": read_json(TRANSITION_DIR / "coverage.json", {}),
        "universe": len(codes),
        "items": items[:top_n],
        "topN": top_n,
        "rankNote": ("分项可用数不足的标的不参与排序（排在末尾并在页面标注）"
                     if unrankable else ""),
    }


def _name_of(code: str, *frames) -> str:
    for df in frames:
        if df is None or "code" not in df.columns:
            continue
        sub = df[df["code"].astype(str) == code]
        if len(sub) and pd.notna(sub.iloc[0].get("name")):
            return str(sub.iloc[0]["name"])
    return code


if __name__ == "__main__":
    import json as _j

    from . import write_json
    out = build()
    write_json(TRANSITION_DIR / "transition_payload.json", out)
    print(_j.dumps({k: v for k, v in out.items() if k != "items"}, ensure_ascii=False, indent=2))
    for it in out["items"][:8]:
        print(f"  {it['code']} {it['name']}  A={it['scoreA']:<5} B={it['scoreB']:<5} "
              f"stage={((it['milestone'] or {}).get('stage'))} manual={len(it['manual'])}")
