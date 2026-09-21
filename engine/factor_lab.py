# -*- coding: utf-8 -*-
"""
因子研究流水线：统一 label → 截面标准化 → IC/RankIC/ICIR → 分层 → Newey-West t

不依赖 statsmodels（自实现 HAC 标准误），只依赖 numpy / pandas。

用法:
    from factor_lab import build_label, xs_norm, ic_report, layer_returns, report_table

    label  = build_label(close, horizon=5, tradable=tradable)
    f      = xs_norm(mom20, method="rank")
    rep    = ic_report(f, label, method="spearman")
    layers = layer_returns(f, close, n=5)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


# ── label ───────────────────────────────────────────────
def build_label(close: pd.DataFrame, horizon: int = 5, tradable: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    未来 horizon 日收益（简单收益，等价于复权后收益若 close 已复权）。
    tradable=False 或价格缺失 → NaN（该样本不进 IC / 分层）。
    label 只在研究阶段使用，禁止进入交易信号。
    """
    c = close.sort_index()
    fwd = c.shift(-horizon) / c - 1.0
    if tradable is not None:
        t = tradable.reindex_like(fwd).fillna(False).astype(bool)
        fwd = fwd.where(t)
    # 数据尾端不足 horizon 的样本无效
    fwd.iloc[-horizon:] = np.nan
    return fwd


def tradable_mask(close: pd.DataFrame, volume: pd.DataFrame | None = None, min_amount: float | None = None,
                  limit_up: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    可交易掩码：价格与成交量有效；可选剔除涨跌停（limit_up=True 表示当日封板不可买入）。
    """
    m = close.notna()
    if volume is not None:
        m &= volume.reindex_like(close).notna()
    if min_amount is not None and volume is not None:
        m &= volume.reindex_like(close) >= min_amount
    if limit_up is not None:
        m &= ~limit_up.reindex_like(close).fillna(False).astype(bool)
    return m


# ── 截面标准化 ───────────────────────────────────────────
def xs_norm(factor: pd.DataFrame, method: str = "rank") -> pd.DataFrame:
    """横截面标准化：rank → [0,1]（再中心化到 -0.5..0.5）；zscore → 均值0 标准差1"""
    f = factor.replace([np.inf, -np.inf], np.nan)
    if method == "rank":
        return f.rank(axis=1, pct=True) - 0.5
    if method == "zscore":
        mu = f.mean(axis=1)
        sd = f.std(axis=1).replace(0, np.nan)
        return f.sub(mu, axis=0).div(sd, axis=0)
    raise ValueError(f"unknown method {method}")


# ── IC ──────────────────────────────────────────────────
def ic_series(factor: pd.DataFrame, label: pd.DataFrame, method: str = "spearman",
              min_names: int = 20) -> pd.Series:
    """逐日横截面相关；参与股票数不足 min_names 的日子置 NaN"""
    f = xs_norm(factor, "rank") if method == "spearman" else factor
    l = xs_norm(label, "rank") if method == "spearman" else label
    common_idx = f.index.intersection(l.index)
    f, l = f.loc[common_idx], l.loc[common_idx]
    out = {}
    for dt in common_idx:
        a, b = f.loc[dt], l.loc[dt]
        both = a.notna() & b.notna()
        if int(both.sum()) < min_names:
            out[dt] = np.nan
            continue
        out[dt] = float(np.corrcoef(a[both].to_numpy(float), b[both].to_numpy(float))[0, 1])
    return pd.Series(out, name=f"ic_{method}")


def newey_west_t(x: np.ndarray, lag: int | None = None) -> float:
    """
    mean 的 HAC(Newey-West) t 值。lag 默认 floor(4*(n/100)^(2/9))。
    """
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = x.size
    if n < 5:
        return float("nan")
    if lag is None:
        lag = int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    lag = max(0, min(lag, n - 1))
    mu = x.mean()
    d = x - mu
    gamma0 = float((d * d).sum() / n)
    var = gamma0
    for k in range(1, lag + 1):
        w = 1.0 - k / (lag + 1.0)          # Bartlett
        gk = float((d[k:] * d[:-k]).sum() / n)
        var += 2.0 * w * gk
    var /= n
    if var <= 0 or not np.isfinite(var):
        return float("nan")
    return float(mu / np.sqrt(var))


def ic_report(factor: pd.DataFrame, label: pd.DataFrame, method: str = "spearman",
              min_names: int = 20, horizon: int = 5) -> dict:
    """IC 均值 / 标准差 / ICIR / NW t / 胜率 / 有效天数"""
    ics = ic_series(factor, label, method=method, min_names=min_names).dropna()
    if ics.empty:
        return {"method": method, "n_days": 0, "ic_mean": np.nan, "ic_std": np.nan,
                "icir": np.nan, "t_nw": np.nan, "win_rate": np.nan}
    mu, sd = float(ics.mean()), float(ics.std(ddof=1)) if len(ics) > 1 else float("nan")
    icir = float(mu / sd) if sd and np.isfinite(sd) and sd > 0 else float("nan")
    return {
        "method": method,
        "n_days": int(ics.size),
        "ic_mean": round(mu, 4),
        "ic_std": round(sd, 4) if np.isfinite(sd) else np.nan,
        "icir": round(icir, 4) if np.isfinite(icir) else np.nan,
        "t_nw": round(newey_west_t(ics.to_numpy(), lag=horizon), 3),
        "win_rate": round(float((ics > 0).mean()), 4),
        "ic_series": ics,
    }


# ── 分层 ────────────────────────────────────────────────
def layer_returns(factor: pd.DataFrame, close: pd.DataFrame, n: int = 5,
                  min_names: int = 20) -> dict:
    """
    按因子分位分层，计算各层下一期等权收益。
    返回 {layers: DataFrame(层×日均收益), monotonic: 相关系数, spread: 多头-空头}
    注：使用 T 日因子 → T+1 收益，无未来函数。
    """
    f = xs_norm(factor, "rank")
    ret1 = close.sort_index().pct_change().shift(-1)   # T → T+1
    idx = f.index.intersection(ret1.index)
    f, ret1 = f.loc[idx], ret1.loc[idx]
    rows = []
    for dt in idx:
        a = f.loc[dt]
        r = ret1.loc[dt]
        both = a.notna() & r.notna()
        if int(both.sum()) < min_names:
            continue
        q = pd.qcut(a[both].rank(method="first"), n, labels=False)
        rows.append(r[both].groupby(q).mean().rename(dt))
    if not rows:
        return {"layers": pd.DataFrame(), "monotonic": np.nan, "spread": pd.Series(dtype=float)}
    layers = pd.DataFrame(rows).sort_index()
    layers.columns = [f"Q{int(c) + 1}" for c in layers.columns]
    mono = float(np.corrcoef(np.arange(layers.shape[1]), layers.mean().to_numpy(float))[0, 1]) \
        if layers.shape[1] > 1 else np.nan
    spread = layers[layers.columns[-1]] - layers[layers.columns[0]]
    return {"layers": layers, "monotonic": mono, "spread": spread,
            "layer_mean": layers.mean().round(5).to_dict()}


def report_table(factors: dict[str, pd.DataFrame], label: pd.DataFrame, close: pd.DataFrame,
                 horizon: int = 5, n_layers: int = 5) -> pd.DataFrame:
    """多因子汇总：IC / ICIR / NW t / 胜率 / 分层单调 / 多空年化"""
    rows = []
    for name, f in factors.items():
        rep = ic_report(f, label, horizon=horizon)
        lay = layer_returns(f, close, n=n_layers)
        spread = lay["spread"]
        ann = float((1 + spread.mean()) ** 252 - 1) if len(spread) else np.nan
        rows.append({
            "factor": name,
            "ic": rep.get("ic_mean"),
            "icir": rep.get("icir"),
            "t_nw": rep.get("t_nw"),
            "win": rep.get("win_rate"),
            "n_days": rep.get("n_days"),
            "mono": round(lay["monotonic"], 3) if np.isfinite(lay.get("monotonic", np.nan)) else np.nan,
            "ls_ann": round(ann, 4) if np.isfinite(ann) else np.nan,
        })
    return pd.DataFrame(rows).sort_values("icir", ascending=False, na_position="last")


# ── 便捷：合成数据自检 ───────────────────────────────────
def _synth(seed: int = 7, n_days: int = 250, n_stocks: int = 120):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-01", periods=n_days)
    cols = [f"S{i:03d}" for i in range(n_stocks)]
    eps = rng.normal(0, 0.02, size=(n_days, n_stocks))
    close = pd.DataFrame(100 * np.cumprod(1 + eps, axis=0), index=dates, columns=cols)
    # 已知有效因子：未来 5 日收益的领先信号（只用于验证流水线是否正确）
    fwd5 = close.shift(-5) / close - 1
    mom20 = close.pct_change(20)
    good = fwd5.shift(1) + rng.normal(0, 0.01, size=(n_days, n_stocks))  # 有信息
    noise = pd.DataFrame(rng.normal(size=(n_days, n_stocks)), index=dates, columns=cols)
    return close, mom20, good, noise, fwd5


def self_check() -> dict:
    close, mom20, good, noise, fwd5 = _synth()
    label = build_label(close, horizon=5)
    r_good = ic_report(good, label, horizon=5)
    r_noise = ic_report(noise, label, horizon=5)
    lay_good = layer_returns(good, close, n=5)
    return {
        "ic_good": r_good["ic_mean"],
        "t_good": r_good["t_nw"],
        "ic_noise": r_noise["ic_mean"],
        "mono_good": round(lay_good["monotonic"], 3),
        "layer_mean": lay_good.get("layer_mean"),
    }


if __name__ == "__main__":
    print(json.dumps(self_check(), ensure_ascii=False, indent=2, default=str))
