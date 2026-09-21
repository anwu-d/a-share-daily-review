# -*- coding: utf-8 -*-
"""
宏观择时：利率 + 估值 + 指数动量 → 三态（价值/情绪/防御）+ 仓位

数据（在线，失败自动降级并标注）：
  利率   : 东财 RPT_IMP_INTRESTRATEN「中国银行同业拆借市场 1年(1Y)」IR_RATE
           降级 → 十年国债 ETF 511260 价格（Ashare），以价格动量近似利率下行的权益友好
  权益   : 沪深300 指数（Ashare sh000300；东财 index_quote 补最新）
  估值   : 沪深300 价格在近 N 年分位（PE 接口不可得时用价格分位代理，输出中标注）

输出:
  data/experiments/<ts>_macro_regime/regime.csv   (date, rate, idx_close, score, state, position)
  data/experiments/<ts>_macro_regime/summary.json
  data/cache/macro/regime_latest.json             (供 run_daily 读取)

模型:
  erp_proxy = z(-利率变化20日) + z(估值分位反向)      # 越大越利好权益
  score     = 0.5*z(利率利好) + 0.5*z(估值利好)
  state     = score 三分位 → 价值 / 情绪 / 防御
  gate      = 指数20日动量 > 0 → 1.0，> -5% → 0.6，否则 0.3
  position  = base[state] * gate                     # base: 价值0.9 / 情绪0.7 / 防御0.4
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "vendor" / "Ashare"))

CACHE = ROOT / "data" / "cache" / "macro"
CACHE.mkdir(parents=True, exist_ok=True)
LATEST = CACHE / "regime_latest.json"

# 利率口径：固定单一市场（Shibor 属上海银行同业拆借市场），避免多口径拼接
RATE_MARKET = "上海银行同业拆借市场"
RATE_PERIOD = "1年(1Y)"
# 进入模型所需的最少利率观测（diff(20) + 60 日 z-score 滚动窗）
RATE_MIN_OBS = 120

BASE_POS = {"价值": 0.9, "情绪": 0.7, "防御": 0.4}


# ── 数据获取 ─────────────────────────────────────────────
def fetch_rate_latest(n_pages: int = 12, page_size: int = 500) -> pd.Series:
    """上海银行同业拆借 1 年利率（历史，倒序分页；约 12 页覆盖 20 年日频）"""
    import eastmoney as em

    rows = []
    for pn in range(1, n_pages + 1):
        try:
            d = em._get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                {
                    "reportName": "RPT_IMP_INTRESTRATEN",
                    "columns": "ALL",
                    "pageSize": page_size,
                    "pageNumber": pn,
                    "sortColumns": "REPORT_DATE",
                    "sortTypes": "-1",
                    "filter": '(CURRENCY_CODE="CNY")',
                    "source": "WEB",
                    "client": "WEB",
                },
                timeout=15,
            )
        except Exception as e:
            print(f"[macro] rate page {pn} fail: {e}", flush=True)
            break
        data = (d.get("result") or {}).get("data") or []
        if not data:
            break
        rows.extend(data)
    if not rows:
        return pd.Series(dtype=float)

    rec: dict[str, float] = {}
    for r in rows:
        if r.get("REPORT_PERIOD") != "1年(1Y)":
            continue
        # 只取单一市场，避免把「中国/上海」两个口径拼成锯齿序列
        if r.get("MARKET") != RATE_MARKET:
            continue
        dt = str(r.get("REPORT_DATE"))[:10]
        v = r.get("IR_RATE")
        if v is None:
            continue
        rec[dt] = float(v)
    if not rec:
        # 备用市场（若主市场在此接口缺失）
        for r in rows:
            if r.get("REPORT_PERIOD") != "1年(1Y)":
                continue
            if "同业拆借市场" not in str(r.get("MARKET", "")):
                continue
            dt = str(r.get("REPORT_DATE"))[:10]
            v = r.get("IR_RATE")
            if v is not None:
                rec[dt] = float(v)
    if not rec:
        return pd.Series(dtype=float)
    s = pd.Series(rec).sort_index()
    s.index = pd.to_datetime(s.index)
    # 同一日期重复 → 保留最后一条，保证单调无重复索引
    s = s[~s.index.duplicated(keep="last")]
    return s


def fetch_bond_etf(count: int = 1200) -> pd.Series:
    """降级：十年国债 ETF 收盘价（价格上行 ≈ 利率下行）"""
    try:
        from Ashare import get_price

        df = get_price("sh511260", count=count, frequency="1d")
        return df["close"].astype(float)
    except Exception as e:
        print(f"[macro] bond etf fail: {e}", flush=True)
        return pd.Series(dtype=float)


def fetch_index(count: int = 1500) -> pd.Series:
    try:
        from Ashare import get_price

        df = get_price("sh000300", count=count, frequency="1d")
        return df["close"].astype(float)
    except Exception as e:
        print(f"[macro] index fail: {e}", flush=True)
        return pd.Series(dtype=float)


def load_rate() -> tuple[pd.Series, str]:
    """
    优先在线利率（上海银行同业拆借 1Y）；观测不足则用国债 ETF 反推并标注。
    阈值按模型需求定：diff(20) + 252 日 z-score(min_periods=60) 至少需要 ~120 条。
    """
    r = fetch_rate_latest()
    print(f"[macro] interbank 1Y obs={len(r)}", flush=True)
    if len(r) >= RATE_MIN_OBS:
        return r.sort_index(), "eastmoney_interbank_1y"
    etf = fetch_bond_etf()
    if len(etf) >= 200:
        proxy = -np.log(etf.astype(float))
        print(
            f"[macro] interbank 观测 {len(r)} < {RATE_MIN_OBS}，降级国债ETF代理 obs={len(proxy)}",
            flush=True,
        )
        return proxy.sort_index(), "degraded_bond_etf_511260"
    if len(r) >= 60:
        return r.sort_index(), "eastmoney_interbank_1y_short"
    return pd.Series(dtype=float), "unavailable"


# ── 模型 ─────────────────────────────────────────────────
def _z(s: pd.Series, win: int = 252) -> pd.Series:
    mu = s.rolling(win, min_periods=60).mean()
    sd = s.rolling(win, min_periods=60).std()
    return (s - mu) / sd.replace(0, np.nan)


def build_regime(idx: pd.Series, rate: pd.Series, rate_source: str,
                 lookback_years: int = 5, rebalance_days: int = 5,
                 deadband: float = 0.30) -> pd.DataFrame:
    """
    对齐日频并计算 score / state / position。

    注意（复核修正）：
    - 只在 score 有效处计算分位；分位未就绪时 state/position 置 NaN，
      绝不让 `np.where(score >= NaN)` 把全部样本静默判成「防御」。
    - 仓位按 rebalance_days 持有（信号日决定、持有至下次调仓），
      换手与净值都用同一条 series，避免「按周测换手、按日交易」的口径错配。
    """
    df = pd.DataFrame({"idx": idx.astype(float)}).sort_index()
    r = rate.copy()
    r.index = pd.to_datetime(r.index)
    r = r[~r.index.duplicated(keep="last")].sort_index()
    df["rate"] = r.reindex(df.index, method="ffill")   # 前向填充：只用过去值
    df = df.dropna(subset=["idx"])
    if df.empty:
        return df

    # 利率利好：利率下行（20 日变化为负）→ 正值
    df["rate_chg20"] = df["rate"].diff(20)
    df["rate_bull"] = -_z(df["rate_chg20"])

    # 估值分位（价格近 N 年分位反向：低位 → 利好）
    win = int(252 * lookback_years)
    df["val_pct"] = df["idx"].rolling(win, min_periods=120).apply(
        lambda x: float((x[-1] > x).mean()), raw=True
    )
    df["val_bull"] = -(df["val_pct"] - 0.5)

    df["score"] = 0.5 * df["rate_bull"] + 0.5 * df["val_bull"]

    # 三态：**只用当期及历史**的分位（expanding），避免用全样本阈值回贴历史
    valid = df["score"].notna()
    n_valid = int(valid.sum())
    if n_valid >= 60:
        s = df["score"].where(valid)
        q1 = s.expanding(min_periods=60).quantile(0.33)
        q2 = s.expanding(min_periods=60).quantile(0.67)
        state = pd.Series(np.nan, index=df.index, dtype=object)
        ok = valid & q1.notna() & q2.notna()
        state[ok & (s >= q2)] = "价值"
        state[ok & (s < q2) & (s >= q1)] = "情绪"
        state[ok & (s < q1)] = "防御"
        df["state"] = state
        df["q33"] = q1
        df["q67"] = q2
        df["quantile_n"] = n_valid
    else:
        df["state"] = np.nan
        df["quantile_n"] = n_valid

    # 指数动量闸
    df["mom20"] = df["idx"].pct_change(20)
    gate = np.where(df["mom20"] > 0, 1.0, np.where(df["mom20"] > -0.05, 0.6, 0.3))
    df["gate"] = pd.Series(gate, index=df.index).where(df["mom20"].notna())

    # 调仓日信号 → 持有至下次调仓；带死区（变动 < deadband 不交易），控制换手
    base = df["state"].map(BASE_POS)
    pos_signal = (base * df["gate"]).astype(float)
    reb_mask = pd.Series(False, index=df.index)
    reb_mask.iloc[::rebalance_days] = True
    cur = np.nan
    applied = []
    for dt in df.index:
        sig = pos_signal.loc[dt]
        if reb_mask.loc[dt] and np.isfinite(sig):
            if not np.isfinite(cur) or abs(sig - cur) >= deadband:
                cur = float(sig)
        applied.append(cur)
    df["position"] = pd.Series(applied, index=df.index, dtype=float)
    df["rebalance_days"] = rebalance_days
    df["deadband"] = deadband
    df["rate_source"] = rate_source
    df["val_proxy"] = "price_percentile"
    return df


def turnover_annual(pos: pd.Series, periods_per_year: int = 252) -> float:
    """
    年换手（单边）= 全年仓位变动绝对值之和。
    仓位本身已按调仓周期更新，因此这里直接对实际序列取差分，口径唯一。
    """
    p = pos.dropna()
    if len(p) < 2:
        return float("nan")
    chg = float(p.diff().abs().sum())
    years = max(len(p) / periods_per_year, 1e-9)
    return chg / years


def run(save: bool = True) -> dict:
    from constraints import experiment_dir, record_experiment

    idx = fetch_index()
    rate, src = load_rate()
    if idx.empty or rate.empty:
        return {"error": "数据不可用", "rate_source": src}

    df = build_regime(idx, rate, src)
    if df.empty or "position" not in df.columns:
        return {"error": "regime 构建失败", "rate_source": src}
    d = experiment_dir("macro_regime") if save else CACHE
    if save:
        df.to_csv(d / "regime.csv", encoding="utf-8-sig")
        record_experiment(
            "macro_regime",
            {"rate_source": src, "rate_market": RATE_MARKET, "base_pos": BASE_POS},
            out_dir=d,
        )

    last = df.iloc[-1]
    to = turnover_annual(df["position"])
    # 与沪深300对照：按 position 择时 vs 满仓（只在有仓位的时段比较，避免空窗抬高结论）
    ret = df["idx"].pct_change()
    active = df["position"].notna() & df["position"].shift(1).notna()
    strat = (df["position"].shift(1) * ret)[active].dropna()
    bench = ret.loc[strat.index]
    nav_s = float((1 + strat).prod())
    nav_b = float((1 + bench).prod())
    # 分位就绪情况（避免「全样本只有一个状态」被当成结论）
    st_counts = {str(k): int(v) for k, v in df["state"].value_counts(dropna=True).items()}
    is_proxy = src.startswith("degraded")
    summary = {
        "as_of": str(df.index[-1])[:10],
        "rate_source": src,
        "rate_market": None if is_proxy else RATE_MARKET,
        "rate_is_proxy": is_proxy,
        "rate_latest": None if is_proxy else (round(float(last["rate"]), 4) if np.isfinite(last["rate"]) else None),
        "rate_proxy_latest": round(float(last["rate"]), 4) if is_proxy and np.isfinite(last["rate"]) else None,
        "val_pct": round(float(last["val_pct"]), 3) if np.isfinite(last["val_pct"]) else None,
        "score": round(float(last["score"]), 3) if last["score"] == last["score"] else None,
        "state": str(last["state"]) if isinstance(last["state"], str) else None,
        "state_counts": st_counts,
        "scored_days": int(df["score"].notna().sum()),
        "mom20": round(float(last["mom20"]), 4) if np.isfinite(last["mom20"]) else None,
        "position": round(float(last["position"]), 3) if np.isfinite(last["position"]) else None,
        # position 是 0–0.9 的「比例」语义（base 0.9/0.7/0.4 × gate）。
        # 展示层统一用「成」，避免把 0.4 直接当成 0.4 成（差 10 倍）。
        "position_cheng": round(float(last["position"]) * 10, 1) if np.isfinite(last["position"]) else None,
        "turnover_annual": round(to, 2) if np.isfinite(to) else None,
        "nav_timing": round(nav_s, 4),
        "nav_bench": round(nav_b, 4),
        "active_days": int(len(strat)),
        "days": int(len(df)),
        "rebalance_days": 5,
        "val_proxy": "price_percentile(5y)",
        "note": f"利率=上海银行间1Y（不足{RATE_MIN_OBS}条时降级国债ETF代理）；估值用价格分位；state/position 分位未就绪时为 None；分位用 expanding（因果）；仅供研究",
    }
    if save:
        (d / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        LATEST.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    print(json.dumps(run(), ensure_ascii=False, indent=2))
