# -*- coding: utf-8 -*-
"""
行业股票池：科技 / 证券 / 有色
- 证券/有色：手工名单
- 科技：主板龙头 + 从 qlib features 自动扫创业板(300/301)与科创(688)
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEAT = ROOT / "data" / "qlib_cn" / "features"

SECURITIES = [
    "SH600030", "SH601688", "SH601211", "SH600837", "SH600999", "SH601066",
    "SH601377", "SH601788", "SH601881", "SH601995", "SZ000776", "SZ000166",
    "SZ002736", "SZ300059", "SH600958", "SH601198", "SH601878", "SZ002926",
    "SH601375", "SH600369", "SH601901", "SH601108", "SH601059", "SH601555",
]

NONFERROUS = [
    "SH601899", "SH600111", "SH603993", "SH601600", "SH600362", "SH601168",
    "SH600547", "SH600489", "SH600988", "SZ000807", "SZ000878", "SZ002466",
    "SZ002460", "SH603799", "SZ002340", "SH600219", "SH601677", "SZ000630",
    "SZ000060", "SH600331", "SH601958", "SH600497", "SZ002155", "SZ002378",
    "SZ000960", "SH600259", "SH600531", "SH600549", "SH600711", "SH600961",
    "SH601137", "SH601388", "SH601618", "SH601969", "SH603399", "SZ000603",
    "SZ000655", "SZ000751", "SZ000758", "SZ000762", "SZ000933", "SZ000962",
    "SZ000970", "SZ002114", "SZ002149", "SZ002155", "SZ002167", "SZ002182",
    "SZ002203", "SZ002237", "SZ002295", "SZ002378", "SZ002428", "SZ002460",
    "SZ002466", "SZ002501", "SZ002540", "SZ002716", "SZ002738", "SZ002842",
]

TECH_CORE = [
    "SH688981", "SH688012", "SH688036", "SH688111", "SH688008", "SH688041",
    "SH688256", "SH688052", "SH688521", "SH688728", "SH688396", "SH688188",
    "SH688169", "SH603501", "SH603986", "SH600588", "SH600183", "SH600745",
    "SZ002371", "SZ002049", "SZ300661", "SZ300782", "SZ300223", "SZ300496",
    "SZ300454", "SZ002415", "SZ002230", "SZ300308", "SZ300502", "SZ300394",
    "SZ002463", "SZ002916", "SH603228", "SZ300476", "SZ002475", "SZ002241",
    "SZ300750", "SZ300124", "SZ000063", "SZ300760", "SZ002938", "SZ300687",
    "SZ300339", "SH603160", "SH600845", "SH600570", "SH601138", "SZ002236",
]


def _exists(name: str) -> bool:
    return (FEAT / name.lower()).is_dir()


def _uniq(seq):
    seen, out = set(), []
    for x in seq:
        x = str(x).upper()
        if x in seen:
            continue
        seen.add(x)
        if _exists(x):
            out.append(x)
    return out


def scan_gem_star(limit_gem: int = 180, limit_star: int = 120) -> list[str]:
    """从 qlib 目录扫描创业板 SZ300/SZ301 与科创 SH688"""
    gem, star = [], []
    if not FEAT.exists():
        return []
    for p in sorted(FEAT.iterdir()):
        n = p.name.upper()
        if n.startswith("SZ300") or n.startswith("SZ301"):
            gem.append(n)
        elif n.startswith("SH688"):
            star.append(n)
    # 取前 N（目录序近似代码序，足够做研究池）
    return gem[:limit_gem] + star[:limit_star]


def _load_name_map() -> dict[str, str]:
    try:
        import json

        p = ROOT / "data" / "cache" / "stock_names.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def filter_st(codes: list[str]) -> list[str]:
    """剔除 ST / *ST / 退 / PT（名称含关键字）"""
    names = _load_name_map()
    out = []
    dropped = []
    for c in codes:
        c6 = c[2:] if len(c) >= 8 else c
        name = names.get(c6) or names.get(c) or ""
        up = name.upper()
        if any(k in up for k in ("ST", "退", "PT")) or name.startswith("*"):
            dropped.append((c, name))
            continue
        out.append(c)
    if dropped:
        print(f"[pool] 剔除 ST/退 {len(dropped)} 只，例: {dropped[:5]}", flush=True)
    return out


def tech_pool(auto_scan: bool = True, limit_gem: int = 180, limit_star: int = 120, drop_st: bool = True):
    core = _uniq(TECH_CORE)
    if not auto_scan:
        pool = core
    else:
        scan = _uniq(scan_gem_star(limit_gem, limit_star))
        pool = _uniq(core + scan)
    return filter_st(pool) if drop_st else pool


def securities_pool(drop_st: bool = True):
    p = _uniq(SECURITIES)
    return filter_st(p) if drop_st else p


def nonferrous_pool(drop_st: bool = True):
    p = _uniq(NONFERROUS)
    return filter_st(p) if drop_st else p


def combined_pool(auto_scan: bool = True, drop_st: bool = True):
    p = _uniq(tech_pool(auto_scan, drop_st=drop_st) + securities_pool(drop_st) + nonferrous_pool(drop_st))
    return filter_st(p) if drop_st else p


if __name__ == "__main__":
    t, s, n = tech_pool(), securities_pool(), nonferrous_pool()
    c = combined_pool()
    print(f"科技 {len(t)}  证券 {len(s)}  有色 {len(n)}  合并 {len(c)}")
