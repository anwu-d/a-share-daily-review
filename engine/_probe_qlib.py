# -*- coding: utf-8 -*-
"""最小 qlib 探针：只 init + 读一只票"""
import sys
from pathlib import Path
import qlib
from qlib.constant import REG_CN

print("init...", flush=True)
qlib.init(provider_uri=str(Path("E:/Project/stock/data/qlib_cn")), region=REG_CN)
print("ok", flush=True)
from qlib.data import D

df = D.features(["SH600000", "SZ000001"], ["$close"], start_time="2026-09-01", end_time="2026-09-11")
print("shape", df.shape, flush=True)
print(df.tail(), flush=True)

# instruments dict style
try:
    ins = D.instruments(market="csi300")
    print("csi300 ins", type(ins), flush=True)
    codes = D.list_instruments(instruments=ins, as_list=True)
    print("n", len(codes), codes[:3], flush=True)
except Exception as e:
    print("ins fail", e, flush=True)
