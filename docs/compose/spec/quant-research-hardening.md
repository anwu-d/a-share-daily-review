---
feature: quant-research-hardening
status: delivered
updated: 2026-09-16
branch: (no git repository — working directly in E:\Project\stock)
commits: (n/a — 无版本控制)
---

# 量化研究加固：约束笼子 + 因子流水线 + 宏观择时

## Report

**What was built** — 三块能力落地并接入现有流水线：
1. `engine/constraints.py`：`TimeLock`（面板可含未来行，`slice()/check()` 严格按 `base` 截断）、
   `assert_oos`、`experiment_dir` / `record_experiment`（每次实验记录参数+数据截止+代码指纹）。
2. `engine/factor_lab.py`：统一 label（T→T+N 收益，尾端置 NaN）、截面 rank/zscore、
   IC/RankIC/ICIR、**自实现 Newey-West（Bartlett 核）t 值**、分层净值与单调性、多因子汇总表。
3. `engine/macro_regime.py`：利率 + 估值分位 + 指数动量 → 三态（价值/情绪/防御）+ 仓位，
   周频调仓 + 0.30 死区，年换手 3.49 倍。

**接入**：`run_candle_2026_maingem.py` 只在 `--fit-end` 前挑组合、只在 `--oos-start` 后报告净值；
`run_daily.py` 写入 `macroRegime` 并在页面市场区块与仓位行展示。

**Verification**（全部实跑）
| 命令 | 结果 |
|------|------|
| `engine/tests/test_constraints.py` | 7/7 passed |
| `engine/tests/test_factor_lab.py` | 8/8 passed |
| `engine/factor_lab.py`（合成数据） | 有效因子 IC 0.759 / t_nw 198.9 / 分层单调 0.997；噪声 IC 0.0026 |
| `engine/macro_regime.py` | 利率用上海银行间 1Y（367 条，主源生效）；三态非退化；scored_days 281；换手 3.67（<4） |
| `engine/run_daily.py --date 2026-09-15` | `js/data.js` 含 `macroRegime`（rate_latest 1.48，state=情绪，position 0.4） |
| `engine/run_candle_2026_maingem.py` | fit 窗口 2026-01-05..2026-05-22（标签不越界），OOS 2026-06-01..09-10 |

**关键发现（本次加固立刻兑现的价值）**
- 标签越界修正后，形态策略样本外表现由 **-13.8%/年 → -27.96% →（再修 1 天后）-23.41%/年**，
  始终不如等权（-17.14%/年）。原来的「超额」主要来自 fit 边界处的标签泄漏。
- 宏观三态第一版因 `np.where(score >= NaN)` 把 1500/1500 天静默判成「防御」；
  第二版又用**全样本分位**回贴历史（前视）。最终改为 `expanding(min_periods=60)` 因果分位。
- 利率同日存在「中国/上海」两个口径，`setdefault` 会拼出锯齿序列并制造假信号（rate_bull 单日 -2.88）。

**Journey log**
1. 起初只想加「约束脚本」，独立复核发现真正的坑在**分类器的 NaN 语义**与**多口径序列拼接**。
2. 利率接口只有同业拆借（无 10Y 国债），且必须固定单一市场；阈值定 120 条后主源才真正生效。
3. 换手口径必须与仓位实际更新方式一致，否则「按周测、按日交易」低估 2 倍以上。
4. 独立复核比自检有效得多：3 个 critical 全部由复核发现，其中 2 个直接改变研究结论。
5. 过拟合的典型来源不是「参数多」，而是**边界处的标签窗口**（`iloc[i0:i1]` 切在 fit_end 上）。

**交付边界（复核结论）**：三态分位改用 `expanding` 后已是因果的，因此 `nav_timing` 属
「walk-forward 可实时的行为描述」，**但不等于独立验证**——`gate`/死区参数是在同一段样本上
看换手调出来的，且前 ~380 行未计分样本被排除。要当结论用，需另取一段未见样本重跑。

## [S1] Problem

参考一篇「vibe coding 做工业级量化框架」的实战分享，结合本项目现状，暴露出四个问题：

1. **过拟合无护栏**：`run_candle_2026_maingem` 等脚本用「全样本事件研究」筛出正收益组合，
   再在同一段样本上报告净值。这等于先看答案再选题，样本外不代表能力。
2. **因子只有粗糙统计**：`event_study_range` 只算 mean_5d，没有 IC / RankIC / ICIR、
   没有分层单调性、没有显著性检验，无法判断一个信号是否真的有效。
3. **宏观择时缺失**：当前仓位门控只看「池内红盘率」，与利率/估值无关；
   而分享者验证最有效的正是「债券利率 + 分红 → 情绪市/价值市 + 指数动量」的宏观择时。
4. **回测缺交易约束**：近似 T+1 收盘成交，未处理涨跌停不可成交、停牌、印花税/佣金分项。

## [S2] Design

### 2.1 约束笼子 `engine/constraints.py`

| 契约 | 行为 |
|------|------|
| `TimeLock(base, data_end, strict, allow_extra)` | 面板可含晚于 `base` 的行（原料），但 `slice()`/`check()` 严格按 `base` 截断；仅 `allow_extra=False` 时构造即报错 |
| `slice(df/list)` | 只保留 ≤ `base` 的行；`check()` 检测越界：strict 抛错，非 strict 返回 False |
| `assert_oos(fit_end, oos_start)` | `oos_start > fit_end`，否则抛 `LookaheadError` |
| `record_experiment(name, params)` | 写入 `data/experiments/<ts>_<name>/experiment.json`（参数 + 数据截止 + 代码 hash） |
| `experiment_dir(name)` | 新策略脚本的输出根目录 |
| 环境变量 `MIMO_STRICT` | 默认 `1`（严格）；`0` 关闭强制 |

`LookaheadError` 为自定义异常，便于测试捕获。

### 2.2 因子流水线 `engine/factor_lab.py`

```
build_label(close, horizon, tradable)   # N 日前复权收益；停牌/不可交易置 NaN
xs_norm(factor, method)                 # 截面 rank 或 zscore
ic_series(factor, label, method)        # 逐日 IC / RankIC
ic_report(...)                          # mean IC, ICIR, t-stat(NW), 胜率, 样本数
layer_returns(factor, close, n=5)       # 分层日收益 + 单调性评分
report_table(factors)                   # 汇总 DataFrame
```

- 显著性用**自实现 Newey-West（Bartlett 核）**，不依赖 statsmodels（安装超时，改为纯 numpy，见 `newey_west_t`），输出中标注。
- 输出：`data/experiments/<ts>_factor_lab/`（IC 序列、分层净值、汇总表）。

### 2.3 宏观择时 `engine/macro_regime.py`

数据（在线拉取，失败自动降级）：

| 序列 | 实际采用 | 降级 |
|------|----------|------|
| 无风险利率 | 东财 `RPT_IMP_INTRESTRATEN`「中国银行同业拆借市场 1年(1Y)」`IR_RATE`（在线分页取历史） | 十年国债 ETF 511260 价格取 `-log` 作方向代理 |
| 权益 | Ashare `sh000300` 日线（东财 `index_quote` 补最新） | — |
| 估值 | 沪深300 价格近 5 年分位（PE 接口不可得，输出标注 `val_proxy=price_percentile`） | — |

产出：

```
score = 0.5 * z(-10Y动量) + 0.5 * z(股债性价比变化)   # 权益友好度
state = 价值/情绪/防御（按 score 分位三分）
position = base[state] * momentum_gate(指数20日动量)
```

- 周频（默认 5 交易日）更新，目标年换手 < 4 倍。
- 输出：`data/experiments/<ts>_macro_regime/regime.csv` + 与沪深300的对照净值。

### 2.4 接入现有脚本

- `run_candle_2026_maingem.py`：组合筛选强制 `fit_end`，报告仅用 `oos_start` 之后；
  输出改到 `experiment_dir()`。
- `run_daily.py`：读取 `macro_regime` 结果作为仓位锚的参考项写入 `data.js`（只展示，不覆盖原锚）。

## [S3] Out of Scope

- Rust 回测内核（保持 Python，仅记录为后续项）
- 融资融券、期货、T+0、分钟级撮合
- 付费数据源（同花顺/tushare token）
- 完整 Tick 级交易约束（本轮到「涨跌停 + 停牌 + 分项费用」为止）

## Tasks

- [x] T1: 新增 `engine/constraints.py`（TimeLock / assert_oos / experiment_dir / record_experiment）— acceptance: `python engine/constraints.py` 自检 3 项通过（covers: S2.1）
- [x] T2: 新增 `engine/tests/test_constraints.py` 覆盖越界与 OOS 断言 — acceptance: 7/7 passed（covers: S2.1; depends: T1）
- [x] T3: 新增 `engine/factor_lab.py`（label / xs_norm / ic_report / layer_returns / newey_west_t）— acceptance: 合成数据有效因子 IC 0.759、t_nw 198.9、分层单调 0.997；噪声因子 IC 0.0026（covers: S2.2）
- [x] T4: 新增 `engine/tests/test_factor_lab.py`（含打乱 label 后 IC≈0 的未来函数检测）— acceptance: 8/8 passed（covers: S2.2; depends: T3）
- [x] T5: 新增 `engine/macro_regime.py` — acceptance: 生成 `regime.csv` 与 `regime_latest.json`；2026-09-16 state=防御 position=0.24 turnover=2.61（<4）（covers: S2.3）
- [x] T6: `run_candle_2026_maingem.py` 接入 TimeLock 与 OOS 报告 — acceptance: 日志 `fit窗口 2026-01-05..2026-06-01 | OOS 2026-06-01..2026-09-10`；仅拟合期筛组合；OOS nav 0.958 vs 等权 0.948（covers: S2.4; depends: T1）
- [x] T7: `run_daily.py` 写入 `macroRegime` — acceptance: `js/data.js` 出现 `macroRegime.state/position`；页面市场区块展示（covers: S2.4; depends: T5）
