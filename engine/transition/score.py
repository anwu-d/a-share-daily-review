# -*- coding: utf-8 -*-
"""两个独立分数 + 分项裸值。

设计纪律（依据 research/business-transition-pricing/REPORT.md 第七节）：

  * **只把有 A 股实证支撑的口径放进分数。**
  * **禁用**「公告日股价反应」口径的盈利惊喜（EAR3）——实测 T+2~T+60 超额≈0。
  * **机构调研频次不是 alpha**（调研前 60 日累计超额 >6%，调研后 60 日不足 2%），
    只作筛选池，不进分数。
  * **估值分位与新业务收入占比没有一手中文实证**，只作风险温度计，不进分数。
  * 一致预期修正只取**上调侧**：2018 年后负向修正已失去区分度，该因子只能做多。

缺项处理：某个分项数据缺失时**按剩余权重重新归一化**，绝不当成 0 分——
把「没拿到数据」算成「表现很差」会让排序失真。
"""
from __future__ import annotations

# 分数 A · 定价充分度（越高 = 市场定价越不充分）
WEIGHTS_A = {"revision_1m": 0.40, "sue": 0.35, "target_gap": 0.25}
# 分数 B · 转型真实性
WEIGHTS_B = {"stage": 0.60, "metric_set": 0.20, "tightness": 0.10, "traps": 0.10}

REVISION_FULL = 0.20     # 近一月一致预期上调 20% 记满分
SUE_RANGE = (-2.0, 2.0)  # 时序标准化后的惊喜值区间
TRAP_PENALTY = 12.0      # 每个陷阱类的扣分
# 缺项重新归一化会让「只拿到 1 个分项」的股票独占 100% 权重从而拿到满分，
# 实测出现过「考核目标没抽到、只剩 SUE」的标的名列第一。因此设最小可用分项数门槛，
# 未达门槛的标的仍给出分数但标记为不可排序（rankable=False）。
MIN_COMPONENTS_FOR_RANK = 2


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _merge(components: dict[str, dict], weights: dict[str, float]) -> float:
    """按**可用**分项重新归一化后加权。components 里 raw 为 None 视为缺失。"""
    usable = {k: v for k, v in components.items() if v.get("raw") is not None}
    wsum = sum(weights[k] for k in usable)
    if wsum <= 0:
        return 0.0
    total = 0.0
    for k, v in usable.items():
        eff = weights[k] / wsum
        v["weight_declared"] = weights[k]
        v["weight_effective"] = round(eff, 4)
        total += eff * v["norm"]
    return round(total * 100, 1)


def normalize_revision(rev: float | None) -> float | None:
    """一致预期月度修正 → 0-1。只取上调侧（负向修正给 0，不扣分）。"""
    if rev is None:
        return None
    return _clamp01(rev / REVISION_FULL)


def normalize_sue(sue: float | None) -> float | None:
    if sue is None:
        return None
    lo, hi = SUE_RANGE
    return _clamp01((sue - lo) / (hi - lo))


def normalize_gap(gap: float | None) -> float | None:
    """目标隐含增速 − 同期一致预期增速。正 gap = 公司目标比卖方更进取。"""
    if gap is None:
        return None
    return _clamp01((gap + 0.20) / 0.40)


def score_a(revision_1m: float | None, sue: float | None, target_gap: float | None) -> dict:
    """定价充分度 0-100。三个分项的裸值必须与分数同屏展示。"""
    comps = {
        "revision_1m": {"label": "一致预期近一月修正", "raw": revision_1m,
                        "norm": normalize_revision(revision_1m)},
        "sue": {"label": "SUE（时序口径）", "raw": sue, "norm": normalize_sue(sue)},
        "target_gap": {"label": "激励目标隐含增速 − 一致预期增速", "raw": target_gap,
                       "norm": normalize_gap(target_gap)},
    }
    for k, v in comps.items():
        if v["norm"] is None:
            v["norm"] = 0.0
            v["missing"] = True
    return {
        "score": _merge(comps, WEIGHTS_A),
        "components": comps,
        "missing": [k for k, v in comps.items() if v.get("missing")],
        "n_available": sum(1 for v in comps.values() if not v.get("missing")),
        "rankable": sum(1 for v in comps.values() if not v.get("missing")) >= MIN_COMPONENTS_FOR_RANK,
        "basis": "PFRD(90d, 仅上调侧) / TSSUE(时序) / 目标 vs 一致预期 gap；"
                 "不含估值分位与收入结构（无 A 股一手实证）",
    }


def normalize_stage(level: int) -> float:
    """里程碑阶段 0-5 → 0-1。**只有 4/5 级才算兑现**，故 1-3 级只给进度分。"""
    return _clamp01(level / 5.0)


def normalize_metric_set(metric: str, metric_set: list[str] | None) -> float:
    """含净利润类指标记满分；只含营收记 0；非净利非营收记 0。"""
    names = " ".join(metric_set or []) + " " + (metric or "")
    if "净利润" in names:
        return 1.0
    return 0.0


def normalize_tightness(tightness: str | None) -> float | None:
    """目标松紧度：目标隐含增速低于公司自身历史越多，越可能是「考核游戏」。"""
    return {"高于历史": 1.0, "接近历史": 0.7, "低于历史": 0.2}.get(tightness or "")


def score_b(*, stage_level: int, metric: str = "", metric_set: list[str] | None = None,
            tightness: str | None = None, traps: dict | None = None,
            scheme_level: str = "上市公司", connector: str = "and") -> dict:
    """转型真实性 0-100。"""
    traps = traps or {}
    trap_hits_total = sum(int(v or 0) for k, v in traps.items() if k != "非约束")
    non_binding = int(traps.get("非约束") or 0)
    penalty = min(30.0, trap_hits_total * TRAP_PENALTY / 4)
    if non_binding:
        penalty += min(20.0, non_binding * 5)
    if scheme_level == "子公司":
        penalty += 10.0
    if connector == "or":
        penalty += 5.0
    if metric in ("其他", "产量类", "分产品线收入"):
        penalty += 10.0

    comps = {
        "stage": {"label": "里程碑阶段", "raw": stage_level,
                  "norm": normalize_stage(stage_level)},
        "metric_set": {"label": "考核指标集合", "raw": f"{metric}|{','.join(metric_set or [])}",
                       "norm": normalize_metric_set(metric, metric_set)},
        "tightness": {"label": "目标松紧度", "raw": tightness,
                      "norm": normalize_tightness(tightness)},
        "traps": {"label": "陷阱扣分", "raw": round(penalty, 1),
                  "norm": _clamp01(1 - penalty / 100)},
    }
    for k, v in comps.items():
        if v["norm"] is None:
            v["norm"] = 0.0
            v["missing"] = True
    return {
        "score": _merge(comps, WEIGHTS_B),
        "components": comps,
        "penalty_detail": {"trap_hits": trap_hits_total, "non_binding": non_binding,
                           "scheme_level": scheme_level, "connector": connector,
                           "metric": metric, "total_penalty": round(penalty, 1)},
        "missing": [k for k, v in comps.items() if v.get("missing")],
        "realized": stage_level >= 4,
        "basis": "阶段枚举(量产/收入确认才算兑现) + 考核指标集合 + 目标松紧度 + 陷阱扣分",
    }
