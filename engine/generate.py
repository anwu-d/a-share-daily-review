# -*- coding: utf-8 -*-
"""把计算结果写成 js/data.js，并可选更新 index 标题/封面文案"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
JS_DATA = ROOT / "js" / "data.js"


def _js(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def write_data_js(payload: dict, out: Path | None = None) -> Path:
    out = out or JS_DATA
    out.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "// 数据层 · 由 engine/run_daily.py 自动生成\n"
        f"// 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"window.RPT = {_js(payload)};\n"
    )
    out.write_text(body, encoding="utf-8")
    return out


def build_payload(
    *,
    review_day: str,
    plan_day: str,
    pools: dict,
    ladder_info: dict,
    promo: dict,
    premium: dict,
    breadth: dict,
    amount_yi: float | None,
    amount_delta_yi: float | None,
    sh_quote: dict,
    sectors: list[dict],
    emotion: dict,
    regime: str,
    pos_anchor: float,
    lines: list[dict],
    leaders: list[dict],
    plan1: list[dict],
    plan2: list[dict],
    bans: list[dict],
    risks: list[dict],
    pool: list[dict],
    watch: list[dict],
    branches: list[dict],
    sources: list[dict],
    meta: dict,
) -> dict:
    return {
        "meta": {
            "reviewDay": review_day,
            "planDay": plan_day,
            "genTime": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "promptVer": meta.get("promptVer", "auto-v1"),
            "auto": True,
        },
        "pools": pools,
        "ladder": ladder_info,
        "promo": promo,
        "premium": premium,
        "breadth": breadth,
        "sectors": sectors,
        "dims": emotion["dims"],
        "score": emotion["score"],
        "regime": regime,
        "posAnchor": pos_anchor,
        "branches": branches,
        "lines": lines,
        "leaders": leaders,
        "plan1": plan1,
        "plan2": plan2,
        "bans": bans,
        "risks": risks,
        "pool": pool,
        "watch": watch,
        "sources": sources,
        "poolDelta": meta.get(
            "poolDelta",
            {
                "out": "移出规则未配置（自动模式）",
                "keep": f"保留 {len(pool)} 只",
                "add": "新增规则未配置（自动模式）",
                "check": "仍是核心需人工复核；自动引擎只输出数据骨架与条件单框架。",
            },
        ),
    }
