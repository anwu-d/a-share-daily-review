# -*- coding: utf-8 -*-
"""
约束笼子：时间戳锁 / 样本外断言 / 实验隔离
目的：防止「先看全样本再挑参数」的过拟合，并让每次实验可追溯。

用法:
    from constraints import TimeLock, assert_oos, experiment_dir, record_experiment

    lock = TimeLock(base="2026-05-31", data_end=df.index.max())
    fit = lock.slice(panel)                 # 只保留 <= base 的行
    assert_oos(fit_end="2026-05-31", oos_start="2026-06-01")
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS = ROOT / "data" / "experiments"

STRICT = os.environ.get("MIMO_STRICT", "1") not in ("0", "false", "False")


class LookaheadError(RuntimeError):
    """使用了超出允许截止日的数据（未来函数）。"""


def _as_ts(x) -> pd.Timestamp:
    return pd.Timestamp(str(x)[:10])


def _norm(d) -> str:
    return _as_ts(d).strftime("%Y-%m-%d")


@dataclass
class TimeLock:
    """
    把可用数据硬性截断到 base（含）。

    base        : 允许看到数据的最后一天（通常是 fit_end / valid_end）
    data_end    : 面板实际最后一天。允许 > base（面板是原料，切片才是使用）；
                  仅当 allow_extra=False 时视为错误。
    strict      : 是否强制（默认取环境变量 MIMO_STRICT）
    allow_extra : True=面板可含未来行，但 slice()/check() 仍受 base 约束
    """

    base: str
    data_end: str | None = None
    strict: bool | None = None
    allow_extra: bool = True
    _checked: bool = field(default=False, init=False)

    def __post_init__(self):
        self.base = _norm(self.base)
        self.strict = STRICT if self.strict is None else self.strict
        if self.data_end is not None:
            self.data_end = _norm(self.data_end)
            if self.data_end > self.base:
                msg = (
                    f"面板数据截止 {self.data_end} 晚于 base {self.base}；"
                    f"仅允许经 slice()/check() 使用 base 之前的行"
                )
                if not self.allow_extra and self.strict:
                    raise LookaheadError(msg)
                print(f"[constraint] note: {msg}", flush=True)

    def slice(self, obj, date_index: pd.Index | None = None):
        """按行截断 DataFrame / Series / list[date]"""
        b = _as_ts(self.base)
        if isinstance(obj, (pd.DataFrame, pd.Series)):
            idx = pd.to_datetime(date_index if date_index is not None else obj.index)
            mask = idx <= b
            return obj.loc[np.asarray(mask)]
        if isinstance(obj, (list, tuple)):
            return type(obj)(x for x in obj if _as_ts(x) <= b)
        raise TypeError(f"TimeLock.slice 不支持 {type(obj)}")

    def check(self, obj) -> bool:
        """校验对象中不存在晚于 base 的数据；返回 True/False，strict 时直接抛错"""
        b = _as_ts(self.base)
        latest = None
        if isinstance(obj, (pd.DataFrame, pd.Series)):
            idx = pd.to_datetime(obj.index)
            if len(idx):
                latest = idx.max()
        elif isinstance(obj, (list, tuple)) and obj:
            latest = max(_as_ts(x) for x in obj)
        ok = latest is None or latest <= b
        if not ok and self.strict:
            raise LookaheadError(f"发现 {latest} > base {self.base}")
        return ok

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def assert_oos(fit_end: str, oos_start: str, strict: bool | None = None) -> None:
    """样本外必须晚于拟合/筛选期的最后一天（strict=False 时只提示不抛错）"""
    strict = STRICT if strict is None else strict
    fe, os_ = _norm(fit_end), _norm(oos_start)
    if os_ <= fe:
        msg = f"oos_start {os_} 必须晚于 fit_end {fe}；否则筛选期与报告期重叠"
        if strict:
            raise LookaheadError(msg)
        print(f"[constraint] warn: {msg}", flush=True)


def experiment_dir(name: str | None = None, create: bool = True) -> Path:
    """实验输出目录：data/experiments/<ts>_<name>/"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"{ts}_{name}" if name else ts
    p = EXPERIMENTS / tag
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def _code_fingerprint(files: Iterable[str | Path] | None = None) -> str:
    """对 engine 关键脚本取 hash，便于追溯「哪一版代码产生的结论」"""
    h = hashlib.sha256()
    files = list(files) if files else sorted((ROOT / "engine").glob("*.py"))
    for f in files:
        p = Path(f)
        if not p.is_absolute():
            p = ROOT / "engine" / p
        if p.exists():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def record_experiment(name: str, params: dict[str, Any] | None = None, out_dir: Path | None = None) -> Path:
    """落盘一次实验：参数 + 数据截止 + 代码指纹"""
    d = out_dir or experiment_dir(name)
    meta = {
        "name": name,
        "created": datetime.now().isoformat(timespec="seconds"),
        "params": params or {},
        "engine_fingerprint": _code_fingerprint(),
        "python": sys.version.split()[0],
        "strict": STRICT,
    }
    p = d / "experiment.json"
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def self_check() -> dict:
    """自检：返回各项是否按预期工作（供测试与启动时打印）"""
    out = {}
    # 1) allow_extra=False 时含未来行应报错
    try:
        TimeLock(base="2026-05-31", data_end="2026-09-15", allow_extra=False)
        out["lock_raises"] = False
    except LookaheadError:
        out["lock_raises"] = True
    # 2) 截断生效
    df = pd.DataFrame({"v": [1, 2, 3]}, index=pd.to_datetime(["2026-05-30", "2026-06-01", "2026-06-02"]))
    cut = TimeLock(base="2026-05-31", data_end="2026-05-31").slice(df)
    out["slice_len"] = int(len(cut))
    # 3) OOS 断言
    try:
        assert_oos("2026-05-31", "2026-05-31")
        out["oos_raises"] = False
    except LookaheadError:
        out["oos_raises"] = True
    return out


if __name__ == "__main__":
    import json as _j

    print(_j.dumps(self_check(), ensure_ascii=False))
