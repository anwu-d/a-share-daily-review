# A股每日复盘 · 量化研究

从**每日结论页**到**可复现流水线**与**形态组合策略**的一体化工程。

- 前端：`index.html`（结论版复盘页，由引擎自动刷新）
- 引擎：`engine/`（取数 · 指标 · 新闻 · 策略回测）
- 数据：`data/qlib_bin`（全历史）· `data/cache`（Parquet/缓存）· `data/strategies`（回测结果）

> 全部内容仅供研究，**不构成投资建议**。

---

## 一、环境

```powershell
# 项目 venv（已含 pandas / numpy / requests / duckdb / pyqlib / lightgbm）
.venv\Scripts\python.exe -m pip install -r engine\requirements.txt
```

数据源：`chenditc/investment_data` 的 qlib release（当前解压在 `data/qlib_cn`，约 2000-01 → 2026-09）。

刷新行情：

```powershell
# 1) 下载新 release 解压覆盖 data/qlib_cn
# 2) 导出近 320 日 Parquet（复盘 DuckDB 用）
.venv\Scripts\python.exe engine\export_fast.py --limit-days 320
```

---

## 二、每日复盘流水线

```powershell
.venv\Scripts\python.exe engine\run_daily.py
# 指定日 / 强制重拉
.venv\Scripts\python.exe engine\run_daily.py --date 2026-09-11 --force
```

会生成 / 更新：

| 产物 | 说明 |
|------|------|
| `js/data.js` | 前端数据层（涨停池、情绪、主线、计划、新闻、来源） |
| `index.html` 预览 | 打开即可看当日结论页 |
| `data/cache/alpha/*` | 资金流 + 板块情绪落库（供以后因子回测） |

**数据链路**

```
qlib_bin ──export──► Parquet ──DuckDB──► 广度/梯队/晋级率
   │
   ├─ pyqlib 直读 ──► Alpha158 / 因子
   │
   └─ 东财涨停池 + 新浪/东财快讯 ──► 复盘文案与新闻
```

新闻源：新浪 7×24、东财快讯；股吧人气抽样见 `engine/sector_sentiment.py`。

---

## 三、研究加固（防过拟合）

参考一篇「vibe coding 做工业级量化框架」的实战分享，本项目补了三块基础设施，详见
[`docs/compose/spec/quant-research-hardening.md`](docs/compose/spec/quant-research-hardening.md)。

| 模块 | 作用 |
|------|------|
| `engine/constraints.py` | `TimeLock` 时间戳锁、`assert_oos`、`experiment_dir`/`record_experiment`（参数+数据截止+代码指纹） |
| `engine/factor_lab.py` | 统一 label、截面标准化、IC/ICIR、**自实现 Newey-West t**、分层净值与单调性 |
| `engine/macro_regime.py` | 利率 + 估值分位 + 指数动量 → 价值/情绪/防御三态 + 仓位（周频、死区、年换手 3.5 倍） |
| `engine/tests/` | 约束与因子的 pytest 兼容测试（也可直接 `python` 运行） |

```powershell
.venv\Scripts\python.exe engine\tests\test_constraints.py     # 7/7
.venv\Scripts\python.exe engine\tests\test_factor_lab.py      # 8/8
.venv\Scripts\python.exe engine\macro_regime.py               # 三态 + 仓位 + 换手
.venv\Scripts\python.exe engine\run_daily.py                  # 复盘页含 macroRegime
```

**加固立刻兑现的价值**：修掉形态策略在 fit 边界处的标签越界后，同一策略样本外由
**-13.8%/年 变为 -23.4%/年**（同期等权 -17.1%/年）。原先的「超额」主要来自泄漏。
宏观择时（利率+估值分位+指数动量，周频、死区 0.30）年换手 3.7 倍。

### 策略：PatternComboGate（形态组合门控）

参考《分享一些关于 k 线形态有趣的东西》中的组合统计，在 **2026 年、全市场主板+创业板非 ST** 上复现并加门控。

### 3.1 思想

1. 编码经典 2 日 / 3 日 K 线组合（刺透、南方三星、多方炮、流星…）
2. 只保留 **后 5 日均涨幅 &gt; 0** 且命中次数足够的组合（避免过拟合邪道）
3. 命中并集取 **TopK** 等权持有
4. **仅在有信号的交易日** 用「近 5 日平均红盘率」决定仓位（避免长期半仓错过反弹）

### 3.2 门控

| 近 5 日平均红盘率 | 仓位 |
|------------------|------|
| &lt; 35% | 30% |
| &lt; 45% | 60% |
| 其余 | 100% |

无信号日：不新开仓；已持仓按上次调仓权重，**不按当日广度再打折**。

### 3.3 2026 回测（主配置）

- 池：主板 `SH6*` + `SZ000/001/002/003*` + 创业板 `SZ300/301*`，滤 ST，约 **5018** 只  
- TopK **40** · 约 **3** 个交易日调仓 · 成本 15bp  
- 保留组合：事件研究 mean_5d&gt;0 且命中≥150（当前 **12** 组，以 `跳空低开→多方炮→平顶`、`跳空低开→刺透→孕线` 等为首）

| 版本 | 全年年化 | 最大回撤 | Sharpe | H1 | H2 |
|------|----------|----------|--------|-----|-----|
| 无门控 | +3.9% | -18.0% | 0.28 | +25.5% | -18.6% |
| 每天门控 | +5.1% | -15.4% | 0.36 | +32.7% | -22.1% |
| **仅信号日门控** | **+16.3%** | **-11.2%** | **0.88** | **+36.9%** | **-5.7%** |
| 池内等权 | -0.5% | — | — | +12.0% | -14.6% |

H1 / H2 以 **2026-06-01** 为界。

### 3.4 复现

```powershell
# 推荐主配置
.venv\Scripts\python.exe engine\run_candle_2026_maingem.py `
  --include-gem 1 --topk 40 --rebalance 3 --min-hits 150 --gate 1

# 不要门控
.venv\Scripts\python.exe engine\run_candle_2026_maingem.py --include-gem 1 --topk 40 --gate 0

# 仅主板
.venv\Scripts\python.exe engine\run_candle_2026_maingem.py --include-gem 0
```

**输出**

- `data/strategies/candle_combo_2026_maingem_event.csv` — 事件研究  
- `data/strategies/nav_candle_2026_maingem.csv` — 组合 vs 等权净值  

### 3.5 风险与局限

- 形态定义为近似实现，与原文作者口径可能不完全一致  
- 组合在 **2026 样本内筛选**，换年份 / 换池子需重新做事件研究  
- 全样本（跨年）上该策略长期跑不赢科技牛市等权；优势集中在 **2026 分化段**  
- 未建模：停牌、一字板无法成交、涨跌停排队、分红除权细节  

---

## 四、其他研究脚本

| 脚本 | 用途 |
|------|------|
| `run_strategies.py` | 双均线 / 多因子 / 涨停接力 / RSI（基础对照） |
| `run_sectorpulse.py` | 板块热度 + 动量选股 |
| `run_sector_rotation.py` | 三行业轮动 |
| `run_hybridcore.py` | 核心等权 + 轮动卫星 |
| `run_reversal_tilt.py` | 超跌反转倾斜 |
| `run_alpha158_lgb.py` | Alpha158 + LightGBM |
| `run_candle_combo*.py` | 形态组合（不同池 / 年份） |
| `qlib_demo.py` | qlib 取数与因子示例 |

---

## 五、目录

```
stock/
├── index.html          # 复盘页
├── css/ js/            # 前端
├── engine/             # Python 引擎与策略
├── vendor/             # MyTT · Ashare · AxData 文档
├── data/
│   ├── qlib_cn/        # qlib 全历史（investment_data）
│   ├── cache/          # Parquet · alpha 落库 · 股票名称
│   └── strategies/     # 回测净值与事件研究 CSV
└── 每日结论及其他/     # 参考 PDF（作者笔记）
```

---

## 六、后续可做

1. 资金流 / 股吧热度攒够历史后，做第二代因子  
2. 形态策略加涨跌停可交易过滤  
3. 将 PatternComboGate 的当日信号接到复盘页「明日计划」  
4. 滚动训练 Alpha158，严格 OOS 防过拟合  
