# -*- coding: utf-8 -*-
"""transition 包核心逻辑的离线测试（不联网）。

    python engine/tests/test_transition_core.py

覆盖三块纯逻辑：
  * esop_extract   —— 指标归类、数值解析、摘要/正文重复去重、覆盖率算术、表头驱动路径
  * milestone_extract —— 阶段枚举不可越级、否定措辞、提问侧拒绝、陷阱计数
  * score          —— 权重和为 1、缺项重新归一化、只取上调侧、兑现门槛
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

from transition import esop_extract as E  # noqa: E402
from transition import milestone_extract as M  # noqa: E402
from transition import score as S  # noqa: E402


# ── esop_extract ────────────────────────────────────────────────────────────
def test_metric_alias_classification():
    assert E.classify_metric("归属于上市公司股东的净利润") == "净利润"
    assert E.classify_metric("扣非归母净利润") == "扣非净利润"
    assert E.classify_metric("营业收入") == "营业收入"
    assert E.classify_metric("矿产资源业务毛利润") == "毛利润"
    assert E.classify_metric("产出片数") == "产量类"
    assert E.classify_metric("完全不相干") == "其他"


def test_parse_num_handles_commas_and_junk():
    assert E.parse_num("2,500.00") == 2500.0
    assert E.parse_num("15") == 15.0
    assert E.parse_num("") is None
    assert E.parse_num(None) is None


def test_duplicate_target_across_summary_and_body_is_deduped():
    """同一目标在摘要章与正文章各出现一次是正常现象，不得落成两条。"""
    one = "本激励计划2026年净利润不低于2.50亿元。"
    txt = one + "…（正文章重复）…" + one
    res = E.extract_from_text(txt)
    got = [p for p in res["periods"] if p["metric_raw"] == "净利润"]
    assert len(got) == 1, res["periods"]


def test_growth_without_the_word_lv_still_matches():
    """`增长不低于`（无「率」）是样本外最大的单词失败源，必须命中。"""
    res = E.extract_from_text("公司2026年营业收入相比考核基数增长不低于15%。")
    assert any(p["unit"] == "%" and p["value"] == 15.0 for p in res["periods"]), res["periods"]
    assert "regex" in res["extraction_path"]


def test_renminbi_quantifier_between_threshold_and_number():
    """`净利润不低于人民币5,000万元` —— 量词会打断朴素正则。"""
    res = E.extract_from_text("2027年度净利润不低于人民币5,000万元。")
    p = [x for x in res["periods"] if x["unit"] == "万元"]
    assert p and p[0]["value"] == 5000.0, res["periods"]


def test_cumulative_marker_after_metric():
    """`2026-2027年营业收入累计不低于70.00亿元` —— 累计在指标之后。"""
    res = E.extract_from_text("2026-2027年营业收入累计不低于70.00亿元。")
    assert any(x["value"] == 70.0 and x["unit"] == "亿元" for x in res["periods"]), res["periods"]


def test_matrix_path_reads_bare_percentages_by_header_order():
    """透视表：表头写指标名、数据行只有裸百分比，行内没有任何文字标签。"""
    txt = ("业绩考核目标如下表所示："
           "行权期考核年度营业收入增长率（A）净利润增长率（B）触发值（An）目标值（Am）"
           "触发值（Bn）目标值（Bm）第一个行权期2026年14.18%26.87%20.69%34.10%")
    res = E.extract_from_text(txt)
    assert "matrix" in res["extraction_path"], res
    vals = sorted(p["value"] for p in res["periods"] if p["kind"] == "matrix")
    assert vals == [14.18, 20.69, 26.87, 34.10], vals


def test_or_connector_detected():
    res = E.extract_from_text("至少满足下列两个条件之一：2026年营业收入不低于10亿元。")
    assert res["connector"] == "or"


def test_footnote_share_payment_forces_interval_gap():
    txt = ("2026年净利润不低于5.00亿元。上述“净利润”指经审计的归属于上市公司股东的净利润，"
           "并剔除本次及其它股权激励计划股份支付费用的影响。")
    res = E.extract_from_text(txt)
    assert res["share_pay_adjusted"] is True


def test_coverage_arithmetic_and_miss_list():
    rows = [
        {"code": "A", "name": "甲", "pdf_url": "u1", "result": {"extraction_path": "regex", "metric": "净利润"}},
        {"code": "B", "name": "乙", "pdf_url": "u2", "result": None, "reason": "无锚点"},
    ]
    cov = E.coverage(rows)
    assert cov["total"] == 2 and cov["extracted"] == 1
    assert cov["total"] == cov["extracted"] + len(cov["misses"])
    assert cov["misses"][0]["pdf_url"] == "u2", "未命中必须带 PDF 深链"


# ── milestone_extract ───────────────────────────────────────────────────────
def test_pilot_sample_is_not_mass_production():
    """金龙羽案：把「中试已出样品」当产业化是典型阶段幻觉。"""
    c = M.classify("公司固态电解质、半固态电芯已进入中试试验，中试试验已出样品。")
    assert c["stage"] == "小试/中试", c
    assert c["realized"] is False


def test_negative_wording_does_not_upgrade_stage():
    c = M.classify("公司相关产品主要面向光学成像领域客户送样，未形成批量订单。")
    assert c["stage"] == "送样", c


def test_realized_requires_mass_production_or_revenue():
    assert M.classify("公司已实现批量供货，产能爬坡中。")["realized"] is True
    assert M.classify("已确认收入并计入当期。")["realized"] is True
    assert M.classify("已小批量交付。")["stage"] == "小批量"
    assert M.classify("已小批量交付。")["realized"] is False
    assert M.classify("尚在研发中，存在不确定性。")["stage"] == "unknown"


def test_small_batch_is_not_substring_of_mass_production():
    """回归护栏：`批量交付` 是 `小批量交付` 的子串，朴素 in 判定会误升为量产。"""
    c = M.classify("公司已实现小批量供货。")
    assert c["stage"] == "小批量" and c["level"] == 3, c
    c2 = M.classify("公司已实现批量供货。")
    assert c2["stage"] == "量产" and c2["level"] == 4, c2


def test_investor_question_side_is_rejected():
    """互动易提问侧不得参与定级：实测「送样」提问侧 12 次、回复侧 0 次。"""
    try:
        M.classify("公司产品处于小试还是中试？", "irm_question")
    except ValueError:
        return
    raise AssertionError("提问侧文本必须被拒绝")


def test_latest_evidence_wins_and_history_is_kept():
    ev = M.collect_evidence([
        {"code": "X", "source": "relation", "date": "2026-01-05", "text": "已送样。", "url": "a"},
        {"code": "X", "source": "relation", "date": "2026-08-05", "text": "已实现批量供货。", "url": "b"},
    ])
    assert ev["stage"] == "量产" and ev["realized"] is True
    assert len(ev["evidence"]) == 2, "全部证据行必须保留以便回溯"


def test_trap_counter_is_exact_substring():
    t = M.trap_hits("本协议为框架协议，不具有法律效力，没有强制约束力。")
    assert t["框架协议"] == 1, t
    # 该句同时含两条「非约束」表述，故计 2
    assert t["非约束"] == 2, t


# ── score ───────────────────────────────────────────────────────────────────
def test_declared_weights_sum_to_one():
    assert abs(sum(S.WEIGHTS_A.values()) - 1.0) < 1e-9
    assert abs(sum(S.WEIGHTS_B.values()) - 1.0) < 1e-9


def test_missing_component_is_renormalised_not_zeroed():
    """把「没拿到数据」当成「表现很差」会让排序失真。"""
    full = S.score_a(revision_1m=0.2, sue=2.0, target_gap=0.2)
    part = S.score_a(revision_1m=None, sue=2.0, target_gap=0.2)
    assert part["missing"] == ["revision_1m"]
    assert part["score"] == full["score"] == 100.0, (part["score"], full["score"])


def test_revision_is_one_sided_upside_only():
    """2018 年后负向修正失去区分度，该因子只能做多 → 下修不给负分。"""
    assert S.normalize_revision(-0.5) == 0.0
    assert S.normalize_revision(0.2) == 1.0
    assert S.normalize_revision(0.4) == 1.0


def test_score_a_bounds():
    assert S.score_a(None, None, None)["score"] == 0.0
    assert 0.0 <= S.score_a(0.35, 5.0, 1.0)["score"] <= 100.0
    assert S.score_a(0.35, 5.0, 1.0)["score"] <= 100.0


def test_score_b_penalises_only_revenue_target():
    both = S.score_b(stage_level=4, metric="净利润", metric_set=["净利润", "营业收入"],
                     tightness="高于历史", traps={})
    rev_only = S.score_b(stage_level=4, metric="营业收入", metric_set=["营业收入"],
                         tightness="高于历史", traps={})
    assert rev_only["score"] < both["score"], (rev_only["score"], both["score"])


def test_score_b_penalises_non_binding_agreements():
    clean = S.score_b(stage_level=3, metric="净利润", metric_set=["净利润"], tightness="接近历史", traps={})
    dirty = S.score_b(stage_level=3, metric="净利润", metric_set=["净利润"], tightness="接近历史",
                      traps={"框架协议": 3, "非约束": 2})
    assert dirty["penalty_detail"]["total_penalty"] > clean["penalty_detail"]["total_penalty"]
    assert dirty["score"] < clean["score"]


def test_sparse_coverage_stock_is_not_rankable():
    """回归护栏：缺项重新归一化会让「只剩 1 个分项」的股票独占权重拿满分。

    实测出现过「考核目标没抽到、只剩 SUE」的标的 rank 第一（A=100）——
    必须标记为不可排序，而不是直接霸榜。
    """
    only_sue = S.score_a(None, 3.22, None)
    assert only_sue["score"] == 100.0, only_sue["score"]      # 归一化后确实是满分
    assert only_sue["n_available"] == 1
    assert only_sue["rankable"] is False, "只有 1 个可用分项时不得参与排序"

    two = S.score_a(0.2, 1.0, None)
    assert two["n_available"] == 2 and two["rankable"] is True


def test_valuation_percentile_absent_from_scores():
    """估值分位与收入结构迁移无 A 股一手实证，不得进分数（只看分项，不看解释性文案）。"""
    a = S.score_a(0.1, 1.0, 0.0)
    b = S.score_b(stage_level=2)
    keys = list(a["components"]) + list(b["components"])
    blob = " ".join(keys).lower()
    for banned in ("valuation", "percentile", "估值", "收入占比", "收入结构"):
        assert banned not in blob, banned
    assert set(a["components"]) == {"revision_1m", "sue", "target_gap"}
    assert "valuation" not in blob


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
