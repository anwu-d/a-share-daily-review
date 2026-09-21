# -*- coding: utf-8 -*-
"""
约束笼子测试（pytest 兼容；无 pytest 时可直接 python 运行）
    python engine/tests/test_constraints.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

from constraints import (  # noqa: E402
    LookaheadError,
    TimeLock,
    assert_oos,
    experiment_dir,
    record_experiment,
    self_check,
)


def test_lock_raises_when_extra_not_allowed():
    """面板含未来行且不允许时 → 报错；默认允许（切片时才受限）"""
    try:
        TimeLock(base="2026-05-31", data_end="2026-09-15", allow_extra=False)
    except LookaheadError:
        pass
    else:
        raise AssertionError("allow_extra=False 时应抛 LookaheadError")
    # 默认允许，不抛
    lock = TimeLock(base="2026-05-31", data_end="2026-09-15")
    assert lock.base == "2026-05-31" and lock.data_end == "2026-09-15"


def test_lock_allows_when_not_strict():
    lock = TimeLock(base="2026-05-31", data_end="2026-09-15", strict=False)
    assert lock.base == "2026-05-31"


def test_slice_only_keeps_past_rows():
    df = pd.DataFrame(
        {"v": [1, 2, 3]},
        index=pd.to_datetime(["2026-05-30", "2026-06-01", "2026-06-02"]),
    )
    cut = TimeLock(base="2026-05-31", data_end="2026-05-31").slice(df)
    assert list(cut["v"]) == [1], cut


def test_check_detects_future_rows():
    df = pd.DataFrame({"v": [1]}, index=pd.to_datetime(["2026-06-05"]))
    # 非严格：返回 False 不抛错
    lock = TimeLock(base="2026-05-31", data_end="2026-05-31", strict=False)
    assert lock.check(df) is False
    # 严格：抛 LookaheadError
    try:
        TimeLock(base="2026-05-31", strict=True).check(df)
    except LookaheadError:
        return
    raise AssertionError("check 在 strict 下应抛错")


def test_assert_oos_rejects_overlap():
    assert_oos("2026-05-31", "2026-06-01")
    try:
        assert_oos("2026-06-01", "2026-06-01")
    except LookaheadError:
        return
    raise AssertionError("oos_start <= fit_end 时应抛错")


def test_experiment_dir_and_record():
    d = experiment_dir("t2_probe")
    assert d.exists()
    p = record_experiment("t2_probe", {"topk": 40}, out_dir=d)
    text = p.read_text(encoding="utf-8")
    assert "t2_probe" in text and "topk" in text


def test_self_check_all_pass():
    r = self_check()
    assert r["lock_raises"] and r["slice_len"] == 1 and r["oos_raises"], r


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
