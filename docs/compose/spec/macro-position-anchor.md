---
feature: macro-position-anchor
status: delivered
updated: 2026-09-17
branch: (none — 项目无 git 仓库，直接在 E:\Project\stock 工作目录修改)
commits: (none)
---

# 宏观择时接入仓位锚（含成交额/广度口径修正）

## Report

**What was built** — 仓位锚不再只看盘面情绪：`macro_regime` 的 `position`（0–0.9 比例）
先归一到系数 `0.6 + 0.4×clamp(pos/0.9, 0, 1)` ∈ [0.6, 1.0]，再乘到盘面锚上，下限 1 成、
上限 7 成；宏观缺失、滞后 > 5 天、或时点晚于复盘日时系数取 1.0（不调整）并在 `reason`
说明。展示层统一用「成」（新增 `position_cheng`），页面给出算式
「锚 3.9 成 = 盘面 5 成 × 宏观系数 0.78；宏观 情绪 4 成」。2026-09-16 实测为 3.9 成。

两处让融合输入本身不可信的口径缺陷一并修掉：（a）成交额原取上证指数 f48 = **仅沪市**
（当天 8711 亿），而 `metrics` 的量能阈值按全 A 量级设定 —— 改为经
`pick_market_amount` 取沪深全 A（当天 **18390.09 亿**，1.84 万亿），且只在样本
`complete is True` 时采信，否则回退沪市并如实标注口径；（b）`market_breadth()` 按
`pz=500` 估算页数，但东财该接口每页实际只回 100 条，导致 5559 只只统计到 1200 只，
又因按涨跌幅降序取数，恰好取到当日全红盘样本 → 红盘率被算成 100%。修正分页后为
涨 3888 / 跌 1134 / 平 537，**红盘率 69.9%**。两处合计使情绪总分由 77 → 83（量能风偏
8→14），明日基准 67 → 73。

日缓存加了结构版本（`DAY_CACHE_VERSION`）与完整度标记（`fullDay`），避免旧口径的
截断样本被静默复用；旧结构缓存按必备字段推断是否完整，`--offline` 时允许沿用但把
`breadth.complete` 降为 False，于是成交额自动回退沪市并显示「样本不完整」。

**Verification** — 全部在 `E:\Project\stock` 下运行：

- `python engine/tests/test_constraints.py` → **PASS 7/7**
- `python engine/tests/test_factor_lab.py` → **PASS 8/8**
- `python engine/tests/test_breadth_paging.py` → **PASS 7/7**（新增；伪 `_get`，覆盖服务端截断、中途空页、口径门控）
- `python engine/tests/test_position_blend.py` → **PASS 12/12**（新增；系数值域/单调、融合算例、上下限、缺失与滞后边界、拒绝未来时点）
- `python engine/tests/test_day_cache_gate.py` → **PASS 5/5**（新增；四种缓存形态 + 不写盘）
- `python -m py_compile` 于 `eastmoney.py / metrics.py / run_daily.py / generate.py / macro_regime.py` → **PASS**
- 实盘 `em.market_breadth()` → `up 3888 / down 1134 / flat 537 / red_pct 69.9 / amount_yi 18390.09 / fetched 5559 / complete True`
- `python run_daily.py --date 2026-09-16 --macro-refresh 1` → `[anchor] 盘面 5.0 成 × 宏观系数 0.7778 → 3.9 成`，情绪 83，基准 73；`pools.amount 18390.09 亿 / amountScope 全A`；`breadth.complete True`；K5 cite 东财全市场分页求和
- Playwright（Chrome headless，1440×900）逐段检查：指标条 `18390.09 亿 | 全A 成交（较前日 —）`、`69.9% | 红盘率（涨 3888 / 跌 1134 / 平 537）`；持仓注记 `锚 3.9 成 … = 盘面 5 成 × 宏观系数 0.78；宏观 情绪 4 成`；控制台 0 error 0 warning；DOM 文本中 `null`/`None`/`undefined` 均为 0

**Journey log**

1. 用户问「8711.41 亿是什么成交额」，实测发现它是**上证指数** f48（=沪市），而全 A 是
   18390 亿。顺着这条线又查出 `market_breadth` 的分页被服务端静默截断到 100 条/页 ——
   即页面上那句「红盘率 100%（涨 1200 / 跌 0）」是假的。两个缺陷同源：**口径与样本
   完整度都没有被显式建模**。
2. 融合规则最初口头举例「5×0.9 = 4.5 成」时并未定义 0.9 从何而来；落 spec 时把映射
   写成可解释的线性归一化（0.9 为满配、系数下限 0.6），实际结果是 3.9 成。**口头算例
   与实现必须对齐**，因此把公式写进 spec 单独确认。
3. 第一轮复核抓到 critical：`run_daily.py` 选口径时只判 `amount_yi is not None`，与
   spec S2.1 要求的 `complete` 门控不符 —— 截断样本仍会被标成「全A」。修法是把选择
   抽成 `pick_market_amount` 并加护栏测试，而不是在调用点打补丁。
4. 第二轮复核指出 C3 的修法自相矛盾：`require_full` 排在 `allow_stale` 逃逸之前，而
   旧缓存根本没有 `fullDay` 字段 —— 逃逸成了死代码，`--offline` 仍会联网。改为按
   `FULL_DAY_KEYS` 推断旧结构是否完整，才同时满足「不把 zt-only 当完整日」与
   「离线可沿用旧缓存」。
5. 强制重跑时发现旧日缓存被复用（显示的还是 8711 亿），于是加了 `DAY_CACHE_VERSION`：
   结构性口径变更必须让缓存自动失效，否则改了引擎却看不到任何变化 —— 这类「改了没生效」
   比报错更难发现。

## [S1] Problem

### 1. 盘面仓位锚的输入有两处口径错误

**(a) 成交额口径错配。** `engine/run_daily.py:431` 取 `sh.get("amount")`，而 `sh` 来自
`engine/eastmoney.py:139 index_quote("1.000001")`，即**上证指数（沪市）**成交额。
但 `engine/metrics.py:198-205` 的量能阈值（≥18000 / ≥12000 / ≥8000 亿）是按**全 A**
量级设定的。用沪市值去比全 A 阈值 → 系统性低估。

2026-09-16 实测（东财全市场分页求和，5559 只）：

| 口径 | 成交额 | 标的数 |
|---|---|---|
| 上证指数 f48（页面现用值） | 8711.41 亿 | 2467 |
| 沪市主板+科创（实测和） | 8710.63 亿 | 2467 |
| 深市主板+创业 | 9679.46 亿 | 3092 |
| 沪深全 A | **18390.09 亿** | 5559 |
| 沪深+北交所 | 18525.36 亿 | 5915 |

后果：量能风偏维度实得 8/20，按全 A 值 18390 ≥ 18000 应为 10/20。页面上
「唯一折扣项 = 缩量」的判断也因此失去依据。

**(b) 涨跌家数样本被静默截断。** `engine/eastmoney.py:186 market_breadth()` 以
`pz=500` 计算页数 `max_page = ceil(total/500) = 12`，但该接口**每页实际最多返回 100 条**，
于是 5559 只只统计到 1200 只。又因请求按涨跌幅降序（`fid=f3, po=1`），这 1200 只
恰是当日最强的全红盘样本 → 返回 `up=1200, down=0, red_pct=100.0`。
函数自身算出 `sampled: false`（`eastmoney.py:241`），但 `run_daily.py` 未检查该标志。

2026-09-16 修正分页后实测：**涨 3888 / 跌 1134 / 平 537 → 红盘率 69.9%**。

后果：页面显示「红盘率 100%（涨 1200 / 跌 0）」，与实际相差极大；市场广度维度虽
仍为 20/20（69.9% 与 100% 都落在 ≥60 的同一档），但呈现的事实是错的。

两处合计使情绪总分由 77 → 79。

### 2. 宏观 position 的单位与展示不一致

`engine/macro_regime.py` 的 `position = base[state] * gate`，其中
`base: 价值0.9 / 情绪0.7 / 防御0.4`，取值域为 0–0.9 的**比例**。而页面把该值当作
「成」渲染：`index.html` 的持仓注记输出「宏观 情绪 参考 0.4 成」，实际含义是 **4 成**，
差 10 倍。

### 3. 宏观择时尚未参与仓位决策

`macro_regime` 的结果目前只写进 `payload.macroRegime` 并在页面上展示（`main.js`/
`index.html` 的 pos-note 与 sec-market dek），`metrics.position_anchor(total, has_s)`
（`engine/metrics.py:280`）完全由盘面情绪决定，二者各说各话：2026-09-16 盘面锚 5 成、
宏观 4 成，页面上并列显示却不给结论。

## [S2] Design

### 2.1 成交额口径统一为全 A

`market_breadth()` 在同一次分页遍历中同时累加 `f6`（成交额，元），返回增加
`amount_yi` 字段；请求 `fields` 由 `f3` 扩为 `f3,f6`，不增加请求数。

- 分页：`pz = 100`（服务端上限），`max_page = ceil(total / pz)`，逐页累加
  `got`；循环结束后若 `got < total` 则置 `complete: false`，且**不**把该结果当作
  完整样本使用。

  新增字段：`{up, down, flat, total, red_pct, amount_yi, fetched, complete}`。
  保留 `sampled` 字段名会造成歧义（原语义为 `got >= total`），改为语义明确的
  `complete` 与 `fetched`。

- `run_daily.py` 的成交额来源改为 `breadth["amount_yi"]`，但**必须**经一个显式门控
  函数选择口径，而不是只看 `amount_yi is not None`：

  ```
  pick_market_amount(breadth, sh_amount) -> (amount_yi, scope)
    breadth["amount_yi"] 有值 且 breadth["complete"] is True → (全A 值, "全A")
    否则 sh_amount 有值                                        → (沪指值, "沪市（回退）")
    两者都缺                                                    → (None, "不可用")
  ```

  只有 `complete is True` 才采信；`complete is False`（服务端中途空页 / 安全阀触发）
  与缺失（旧结构缓存）一律回退沪市并如实标注。这样「截断样本被当成全 A」在结构上
  不可能发生，而不是靠调用点自觉。

- 沪指 `sh["close"] / sh["pct"]` 的用途不变（仍用于指数涨跌展示）。
- `metrics.py:197-205` 阈值不变（本就按全 A 设定），补注释说明传入值必须同口径。
- 日缓存加结构版本 `DAY_CACHE_VERSION`，bump 后旧缓存自动重取。缓存分两类：
  完整日缓存（`fullDay: true`，供 `--date` 使用）与只存涨停池的「昨日」缓存
  （`fullDay: false`）；读完整日时用 `require_full=True` 排除后者，避免把
  zt-only 缓存当成完整日缓存而 `KeyError`。`--offline` 下允许沿用旧版本缓存，
  但会把其 `breadth.complete` 强制置 False，于是成交额自动回退沪市、页面显示
  「样本不完整」——不静默、也不因为断网而完全不可用。

### 2.2 宏观 position 单位统一为「成」

`macro_regime` 的 `position` 保持比例语义（0–0.9）不变，避免破坏 `regime.csv` 与
历史回测口径；在其 `run()` 输出中增加 `position_cheng = round(position * 10, 1)`。
所有展示层一律使用 `position_cheng`。

### 2.3 仓位锚融合：宏观作乘数

新增 `engine/metrics.py: macro_coefficient(macro_position) -> float`：

```
coeff = 0.6 + 0.4 * clamp(macro_position / 0.9, 0, 1)
```

- 值域 `[0.6, 1.0]`，单调递增，宏观满配（0.9）时不削减盘面锚；
  宏观越保守削减越多，但**永不低于 0.6 倍**（不否决盘面）。
- `macro_position` 为 None / 非有限值 → 返回 1.0（等价于「宏观未参与」）。
- 该系数只下调、不上调盘面锚。

新增 `engine/metrics.py: blend_position_anchor(anchor, macro_position) -> dict`，
返回 `{final, board, coeff, adjusted}`：

- `final = round(board * coeff, 1)`，下限 1.0 成、上限 7.0 成。
- `adjusted = coeff < 1.0`。

生效条件：宏观缓存存在、`position` 有效、且 `macro_as_of` 相对复盘日的**有符号**间隔
落在 `[0, 5]` 天：

- 间隔 > 5 天 → 视为过期，`coeff = 1.0`；
- 间隔 < 0（宏观时点晚于复盘日）→ 视为未来信息，`coeff = 1.0`。
  这一条是为「补跑历史复盘日」准备的：若事后刷新过宏观缓存，晚于该日的宏观态
  不得泄漏进旧日期的仓位决策。

不可用时一律 `coeff = 1.0`、`adjusted = false`，并在 `reason` 写明原因。

2026-09-16 预期结果：board 5.0、macro 0.4 → coeff = 0.6 + 0.4×(0.4/0.9) = 0.778 →
final = round(3.89, 1) = **3.9 成**。

### 2.4 payload 与前端契约

`payload` 变更（`js/data.js`）：

```jsonc
{
  "posAnchor": 3.9,                       // 改为融合后的最终值（原为盘面锚）
  "anchor": { "board": 5.0, "coeff": 0.778, "adjusted": true,
              "macroCheng": 4.0, "macroAsOf": "2026-09-16" },
  "pools": { "amount": "18390 亿", "amountScope": "全A", "amountDelta": "—" },
  "breadth": { "up": 3888, "down": 1134, "flat": 537, "total": 5559,
               "red_pct": 69.9, "complete": true }
}
```

前端（`index.html` 内联渲染 + `js/main.js`）：

- 速览指标条：成交格的键名改为「全A 成交（较前日 —）」，drill 文案带 `amountScope`。
- 速览持仓注记：`仓位：锚 3.9 成（盘面 5 成 × 宏观系数 0.78；宏观 情绪 4 成，利率 1.48%）`。
- 市场状态 dek：宏观片段改用成（`position_cheng`）。
- 来源登记 K5：`沪指 3891.6 (0.71%)；全 A 成交 18390 亿`。
- 指标条与映射表在 `breadth.complete === false` 时显示「样本不完整」提示，不静默使用。
- **下游传导（有意为之，需知悉）**：`engine/think_matrix.py` 的应变矩阵用仓位锚推导
  「仓位上限 / 压至 N 成」。融合后该值为 3.9 而非 5.0，于是矩阵文本出现 4.9 / 2.9 成
  这类一位小数。这是融合应有的传导，不再单独取整——锚本身已是小数，矩阵跟着一致。

### 2.5 测试边界

- `engine/tests/test_breadth_paging.py`：注入伪 `_get`，模拟「请求 pz=500 但服务端
  只回 100 条」，断言分页覆盖全部 total、`complete` 正确、`amount_yi` 为各页之和；
  再模拟中途空页，断言 `complete: false`。
- `engine/tests/test_position_blend.py`：覆盖 macro_position ∈
  {None, 0, 0.27, 0.4, 0.7, 0.9, 1.2, NaN} 的系数与 final 值，断言下限 1.0、
  上限 7.0、单调性，以及 5 日滞后阈值两侧的行为。
- 两个测试文件均写成 pytest 兼容但可直接 `python <file>` 运行（沿用仓库既有约定，
  避免依赖 pytest）。

## [S3] Out of Scope

- 不改宏观择时自身的模型（利率代理、估值分位、expanding 分位、rebalance/deadband
  参数一律不动）。
- 不新增 `amount_delta_yi`（前一日全 A 成交额）的计算；该字段继续为 None，
  页面显示「较前日 —（缺前一日缓存）」。
- 不引入北交所成交额（`m:0 t:81 s:2048`）；全 A 口径固定为沪深主板+创业+科创。
- 不修正 `em_volume_wide.parquet`（仅 800 只）与 `pct_wide.parquet`（止于
  2026-09-11）的本地缓存滞后问题。
- 不做宏观与盘面分歧的图形化展示，仅文本呈现。
- 不重跑除 2026-09-16 以外的历史复盘日。
- 不修 `run_daily.py --date <旧日期>` 在只有 zt-only 缓存时的其它历史行为；
  本次只保证 `require_full=True` 不再把 zt-only 缓存当成完整日缓存。
- 不修 `--offline` 的其余联网路径（`closes_for_yest_zt` / `daily_kline` / 新闻 /
  alpha_store / 宏观刷新仍会发起请求）——该 flag 实际语义是「优先用缓存」，
  并非真正离线；已由复核记录，留作后续单独处理。

## Tasks

- [x] T1: 修 `eastmoney.market_breadth()` 分页与成交额 — acceptance: 对 2026-09-16 返回 `up=3888/down=1134/flat=537/red_pct=69.9/amount_yi≈18390/complete=true`（covers: S2.1）
- [x] T2: 新增 `engine/tests/test_breadth_paging.py`（伪 `_get`，不联网）— acceptance: 覆盖「服务端截断到 100 条」与「中途空页」两种情形并全部通过（covers: S2.1, S2.5; depends: T1）
- [x] T3: `run_daily.py` 改用全 A 成交额 + 回退与 `amountScope` 标注 — acceptance: payload 的 `pools.amount` 为 18390 亿且 `amountScope` 为「全A」；模拟 absence 时回退沪市并标「沪市（回退）」（covers: S2.1）
- [x] T4: `macro_regime.run()` 输出 `position_cheng` — acceptance: 缓存中 `position=0.4` 时 `position_cheng=4.0`（covers: S2.2）
- [x] T5: `metrics.macro_coefficient` + `blend_position_anchor` — acceptance: `blend(5.0, 0.4)["final"] == 3.9`，`coeff` 落在 [0.6,1.0]，None 与滞后 >5 天返回 1.0（covers: S2.3）
- [x] T6: 新增 `engine/tests/test_position_blend.py` — acceptance: 覆盖 S2.5 列出的全部输入与边界并全部通过（covers: S2.3, S2.5; depends: T5）
- [x] T7: `run_daily.py` 写入 `posAnchor`（最终值）与 `anchor` 对象；`breadth` 补 `complete` — acceptance: data.js 中 `posAnchor` 为 3.9 且存在 `anchor.board/coeff/macroCheng`（covers: S2.3, S2.4; depends: T3, T5）
- [x] T8: 前端渲染融合结果与口径标注 — acceptance: 页面上出现「全A 成交」「锚 3.9 成（盘面 5 成 × 宏观系数 0.78；宏观 情绪 4 成）」；`breadth.complete` 为 false 时出现「样本不完整」（covers: S2.4; depends: T7）
- [x] T9: 重跑 `run_daily.py --date 2026-09-16` 并复验页面 — acceptance: 页面显示全 A 成交 18390 亿、红盘率 69.9%（涨 3888 / 跌 1134）、宏观 4 成、仓位锚 3.9 成；控制台无错误；覆盖率数字与之自洽（covers: S2.1, S2.2, S2.3, S2.4; depends: T1, T3, T4, T7, T8）

### 复核修正（第一轮 review 后追加）

- [x] T10: 成交额口径改为 `pick_market_amount` 严格门控（`complete is True` 才采信全 A）— acceptance: 截断样本（complete=False）与无 complete 字段的旧缓存都必须落到「沪市（回退）」，由 `test_breadth_paging.py::test_amount_caliber_rejects_incomplete_sample` 覆盖（covers: S2.1; depends: T3）
- [x] T11: 日缓存区分完整日（`fullDay: true`）与 zt-only（`fullDay: false`），读完整日时 `require_full=True`；`--offline` 允许沿用旧版本缓存但强制 `breadth.complete = False` — acceptance: `load_cache(zt-only, require_full=True)` 返回 None；`load_cache(legacy, allow_stale=True)` 返回的 breadth `complete` 为 False（covers: S2.1; depends: T3）
- [x] T12: `blend_position_anchor` 拒绝晚于复盘日的宏观时点 — acceptance: `blend(5.0, 0.4, "2026-09-18", "2026-09-16")` 不调整且 `reason` 含「晚于复盘日」，由 `test_position_blend.py::test_blend_rejects_macro_newer_than_review_day` 覆盖（covers: S2.3; depends: T5）
- [x] T13: 补 `metrics.py` 量能阈值口径注释；前端系数显示两位小数、成交额标签改用 `amountScope`；去掉 `test_position_blend.py` 未用 import 与取巧日期 — acceptance: 页面显示「全A 成交」与「宏观系数 0.78」，测试仍全绿（covers: S2.1, S2.4）
