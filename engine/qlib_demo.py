# -*- coding: utf-8 -*-
"""
qlib 本地量化最小示例
数据: data/qlib_cn (investment_data release 2026-09-12)

用法:
  python qlib_demo.py
"""
from __future__ import annotations

from pathlib import Path

import qlib
import pandas as pd
from qlib.constant import REG_CN
from qlib.data import D

ROOT = Path(__file__).resolve().parent.parent
PROVIDER = str(ROOT / "data" / "qlib_cn")


def main():
    qlib.init(provider_uri=PROVIDER, region=REG_CN)

    instruments = ["SH600000", "SZ000001", "SH601318", "SZ300750"]
    fields = ["$open", "$high", "$low", "$close", "$volume"]
    df = D.features(instruments, fields, start_time="2026-08-01", end_time="2026-09-11")
    print("features tail:\n", df.tail(8))

    # 简单因子：5日动量 / 20日波动
    close = df["$close"].unstack()
    mom5 = close.pct_change(5)
    vol20 = close.pct_change().rolling(20).std()
    print("\nmom5 last:\n", mom5.tail(3))
    print("\nvol20 last:\n", vol20.tail(3))

    # 自定义股票池查询
    pool = D.instruments("csi300")
    print("\ncsi300 instruments sample:", pool[:5] if hasattr(pool, "__getitem__") else type(pool))

    print("\nqlib demo OK")


if __name__ == "__main__":
    main()
