# 每日复盘引擎（可复用）

静态结论页 → **每个交易日可复现**流水线。

**策略总览与 PatternComboGate 详见项目根目录 [`README.md`](../README.md)。**

## 架构

```
qlib_bin (investment_data release)
    │
    ├─► pyqlib 直接读 ──► 因子 / 回测（engine/qlib_demo.py）
    │
    └─► export_fast.py ──► data/cache/*.parquet
                              │
                              └─ DuckDB 查询（engine/duckdb_store.py）
                                       │
                                       └─ run_daily.py ──► js/data.js
                                              │
                                              ├─ 东财涨停池（名称/板块）
                                              ├─ 新浪7×24 + 东财快讯（新闻）
                                              └─ MyTT（自选池技术位）
```

| 层 | 路径 | 用途 |
|----|------|------|
| qlib 源 | `data/qlib_cn/` | 2000→2026-09-11 全历史，量化 |
| Parquet | `data/cache/close_wide.parquet` | 近 320 日收盘宽表 |
| Parquet | `data/cache/pct_wide.parquet` | 涨跌幅宽表（复盘主用） |
| Parquet | `data/cache/snap_YYYYMMDD.parquet` | 最近一日 OHLC 截面 |
| DuckDB | 内存查询上述 parquet | 截面/梯队/晋级率 |

## 一键生成

```powershell
# 首次：装依赖 + 导出 parquet
.venv\Scripts\python.exe -m pip install -r engine\requirements.txt
.venv\Scripts\python.exe engine\export_fast.py --limit-days 320

# 每日复盘
.venv\Scripts\python.exe engine\run_daily.py
.venv\Scripts\python.exe engine\run_daily.py --date 2026-09-11
.venv\Scripts\python.exe engine\run_daily.py --force

# qlib 量化示例
.venv\Scripts\python.exe engine\qlib_demo.py
```

生成物：`js/data.js`（`index.html` 读 `window.RPT`）

## 新闻源

- 新浪 7×24（主）
- 东财快讯
- 财联社电报（需签名则跳过）
- 可选：部署 [TrendRadar](https://github.com/sansan0/TrendRadar) 做热点聚合，再对接其输出

## 刷新行情

1. **当日增量**：`run_daily` 东财涨停池 + DuckDB 本地日线
2. **qlib_bin 整包**：下载 [investment_data](https://github.com/chenditc/investment_data) 最新 release，解压到 `data/qlib_cn`，再跑 `export_fast.py`

## 自选池

改 `engine/config.py`：`WATCH_POOL` / `SECTOR_KEYWORDS` / `DIM_WEIGHTS`

## 免责

盘面数据自动汇总与策略骨架，**非投资建议**。
