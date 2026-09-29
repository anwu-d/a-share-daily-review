# -*- coding: utf-8 -*-
"""股票代码 → 名称（东财 clist，本地缓存）"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "stock_names.json"

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


def load_cache() -> dict[str, str]:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(m: dict[str, str]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(m, ensure_ascii=False, indent=0), encoding="utf-8")


def fetch_all_names() -> dict[str, str]:
    """全市场代码→名称，分页拉取"""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import eastmoney as em

    names = {}
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    page = 1
    while page <= 80:
        try:
            data = em._get(
                url,
                {
                    "pn": page,
                    "pz": 100,
                    "po": 0,
                    "np": 1,
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f12",
                    "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23",
                    "fields": "f12,f14",
                },
                timeout=15,
            )
        except Exception as e:
            print(f"[names] page {page} fail: {e}")
            break
        diffs = ((data.get("data") or {}).get("diff")) or []
        if not diffs:
            break
        for it in diffs:
            code = str(it.get("f12") or "").zfill(6)
            name = it.get("f14") or ""
            if code and name:
                names[code] = name
        total = int((data.get("data") or {}).get("total") or 0)
        if page % 10 == 0 or page == 1:
            print(f"[names] page {page} cached={len(names)}/{total}", flush=True)
        if page * 100 >= total:
            break
        page += 1
        time.sleep(0.05)
    print(f"[names] done {len(names)}", flush=True)
    return names


def get_name_map(codes_qlib: list[str] | None = None, force: bool = False) -> dict[str, str]:
    """
    返回 qlib 代码(如 SH600000) 与 6 位码 都能查到的名称映射。
    """
    m = load_cache()
    need = False
    if force or not m:
        need = True
    else:
        # 若传入池子且缺很多则刷新
        if codes_qlib:
            miss = sum(1 for c in codes_qlib if c[2:] not in m and c not in m)
            if miss > max(5, len(codes_qlib) * 0.1):
                need = True
    if need:
        print("[names] 从东财刷新股票名称 ...", flush=True)
        fresh = fetch_all_names()
        if fresh:
            m.update(fresh)
            save_cache(m)
            print(f"[names] 缓存 {len(m)} 只", flush=True)
        time.sleep(0.2)

    # 扩展 qlib 前缀键
    out = dict(m)
    if codes_qlib:
        for c in codes_qlib:
            c6 = c[2:] if len(c) >= 8 else c
            if c6 in m:
                out[c] = m[c6]
                out[c6] = m[c6]
    return out


def resolve_name(raw, name_map: dict[str, str] | None = None) -> str:
    """把 sh600825 / SH600825 / 600825 统一解析成中文名；查不到时退化为 6 位码。

    设计审计延伸：本地 DuckDB 梯子与部分接口给的是带市场前缀的代码，名称映射
    缓存却按 6 位码存。若不统一解析，名称解析失败（如网络不可用）时页面会直接
    显示 sh600825 这类原始代码。这里保证最坏也只是显示 600825。
    """
    s = str(raw or "")
    if not s:
        return s
    c6 = s[2:] if len(s) >= 8 else s
    if name_map is None:
        try:
            name_map = load_cache()
        except Exception:
            name_map = {}
    name = (name_map or {}).get(s) or (name_map or {}).get(c6)
    return name or c6

def pretty(code: str, name_map: dict[str, str] | None = None) -> str:
    """SH600000 → 浦发银行(600000)"""
    if name_map is None:
        name_map = get_name_map()
    c6 = code[2:] if len(code) >= 8 else code
    name = name_map.get(code) or name_map.get(c6) or ""
    return f"{name}({c6})" if name else code


if __name__ == "__main__":
    m = get_name_map(["SH600000", "SZ000001", "SZ300750", "SH688981"], force=True)
    for c in ["SH600000", "SZ300750", "SH688981"]:
        print(c, "->", pretty(c, m))
