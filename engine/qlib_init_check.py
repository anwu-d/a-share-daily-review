# -*- coding: utf-8 -*-
"""初始化 qlib 本地数据并做最小读取验证"""
from pathlib import Path
import qlib
from qlib.constant import REG_CN
from qlib.data import D

ROOT = Path(__file__).resolve().parent.parent
PROVIDER = str(ROOT / "data" / "qlib_cn")

def main():
    print("provider_uri =", PROVIDER)
    qlib.init(provider_uri=PROVIDER, region=REG_CN)
    # 取最近 5 日上证指数 close（qlib 内置 index 代码）
    try:
        df = D.features(["SH000001"], ["$close"], start_time="2026-09-01", end_time="2026-09-11")
        print("SH000001 close:\n", df.tail())
    except Exception as e:
        print("index read fail:", e)
    # 取一只个股
    try:
        df = D.features(["SH600000"], ["$open", "$high", "$low", "$close", "$volume"], start_time="2026-09-05", end_time="2026-09-11")
        print("SH600000:\n", df.tail())
    except Exception as e:
        print("stock read fail:", e)
    print("qlib init OK")

if __name__ == "__main__":
    main()
