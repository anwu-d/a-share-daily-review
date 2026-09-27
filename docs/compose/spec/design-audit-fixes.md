---
feature: design-audit-fixes
status: delivered
updated: 2026-09-22
branch: design-audit-fixes
commits: 99c04b9..<uncommitted>  # 复核范围为基线之后的全部工作区改动
---

# 设计审计修复（结论页）

## Report

**What was built** — 依据 `output/design-audit/AUDIT.md` 的 17 条 UX/无障碍发现与 6 条引擎观察，对结论页做了一轮完整的可读性与可信度修复。三类核心问题被根治：读者文案里的内部代号（`PATTERNCOMBOGATE` / `Prompt v4` / `plan2` / `R 编号`）全部清除；章节名此前在导航、封面 chips、`sec-no` 三处各自为政，现在由 `section[data-name]` 单一来源生成；「结论」列原本 10 行/5 行全是同一句模板，现已换成真实字段（自选池：方向/标的/状态/技术位/今日表现；龙头：标的/板块/等效高度/封单/成交/接力风险）。

最要紧的是封单单位：`eastmoney.zt_pool` 的 `fund` 原按 `/1e4` 处理后标注「亿」，把封单放大了一万倍（有研硅显示 21491.05 亿，真实 2.15 亿）。改为 `yi()` 统一 `/1e8` 后，页面显示 0.43/0.68/1.56 亿的合理量级；同时给**离线旧缓存**补了折算路径（`cacheVersion == 2` 折算、未知版本置 None），并把 `DAY_CACHE_VERSION` 升到 3 让旧口径缓存自动失效。新闻侧则按 spec 严格剔除晚于复盘日的条目（`--allow-late-news` 为显式逃生口），同主题去重改为「按时间升序取最早」而非「列表首条」。

无障碍侧补齐：指标格改为 `<button>`、两张 canvas 加 `role/tabindex/aria-label` 并配视觉隐藏文字表、drill 弹窗具备 `role="dialog"`/`aria-modal`/Esc 关闭/焦点还原/关闭按钮 `aria-label`、11 张表加视觉隐藏 caption、焦点环自定义、`scroll-padding-top:64px` 让锚点不再靠巧合的内边距、灰色 token 由 `#8595a6`（3.07:1）改为 `#5a6676`（**5.84:1**，colophon 5.54:1）。封面连板图重画为按台阶序位递增的阶梯（此前 77 个首板把 1 板台阶顶得最高，视觉与「阶梯」相反），标签去重叠、炸板簇改为可读实心块，窄屏下表格可横滚、指标条 2 列、标题 40px。

**Verification** —
- `python engine/tests/test_audit_fixes.py` → **13/13**；`test_day_cache_gate.py` 7/7；`test_constraints.py` 7/7；`test_factor_lab.py` 8/8；`test_breadth_paging.py` 7/7；`test_position_blend.py` 12/12；`test_transition_core.py` 25/25（合计 **79 项**）
- `py_compile` 于 `engine/news.py` / `engine/run_daily.py` / `engine/metrics.py` / `engine/eastmoney.py` → PASS
- `node --check` 于 `js/utils.js` `js/cover.js` `js/charts.js` `js/main.js` → PASS
- `run_daily.py --date 2026-09-16` → `leaders[0].sealFund = 0.43`、`news.count = 0`（严格剔除后）、`anchor = 5.0 × 0.7778 → 3.9`
- 浏览器复验（1440×900 与 390×844）：内部代号在渲染文本中为 0；导航/chips/sec-no 十个名称完全一致；6/6 指标格为 `<button>`；两张 canvas 均有含真实数字的 aria-label，封面图文字表 6 行、五维图 5 行；drill 为 `role=dialog`+`aria-modal`+`aria-label=关闭` 且 **Esc 可关闭**；点导航后 `.sec-no` 位于 169px（导航底 50px，未遮挡）；对比度 `.muted` 5.84 / `.colophon` 5.54；390px 下 `scrollWidth == clientWidth`、指标条 2 列、表格可横滚
- 修复项在进程内复测：`load_cache` 对 v2 缓存 `fund=21491.0502 → 2.15`、重复加载不双重折算、未知版本置 `None`；`clean_pack` 最新在前的输入去重后保留 `02:00` 的最早条；严格模式晚于复盘日条目为 0，`allow_late` 模式为 1 且带「【复盘日后】」标记

**Journey log**

1. **标签 ≠ 合规。** 复核指出：我把 spec 的「剔除晚于复盘日的快讯」擅自改成了「保留但标注」，用披露换掉了验收。诚实的标注是对的，但不能当成满足了「剔除」。最终改为**默认严格剔除**，逃生口做成显式 `--allow-late-news` 开关。
2. **版本门禁不等于口径安全。** `DAY_CACHE_VERSION` 只挡在线缓存；`--offline` 放行的旧缓存里封单仍是旧单位。根治要同时处理「版本 bump」与「陈旧读取方」——v2 折算、未知版本置 None，宁可显示 `—` 也不猜。
3. **用「行数」还是「层级」定高度是语义选择。** 封面图按方块行数定台阶高度时，77 个首板会把 1 板顶得最高，视觉与「连板阶梯」相反；改成按序位递增后，`topBoards` 为 1/2/3/20 时都单调上升（已按极端值验算）。
4. **去重要「最早一条」就不能按列表顺序。** 源常为最新在前，先排序再取首条才落实「最早」。
5. **canvas 文字与 DOM 文本不是一回事。** `昨日 无缓存` 画在 canvas 上，用 `innerText` 复验永远测不到——复验手段必须和渲染手段同源（读源码 / 读 data.js）。


## [S1] Problem

`output/design-audit/AUDIT.md` 对结论页做了截图级审计，产出 17 条 UX/无障碍发现（R1–R10、A1–A7）与 6 条引擎观察（E1–E6）。用户决定**全部修复**，包括封面图表重画与窄屏重排。

审计已确认的两条事实性问题必须先解决，因为它们决定页面其余数字是否可信：

1. **封单单位错误（R4 / E3）。** `engine/eastmoney.py:84` 把东财涨停池的 `fund` 字段 `÷1e4` 后标注为「亿」。实测 2026-09-16：有研硅 `fund=21491.0502`，页面显示「封单 21491.05 亿」；按元计真实值是 **2.15 亿元**，页面放大了 10,000 倍。通鼎互联显示「15622.26 亿」同理。
2. **新闻板块（R5 / E4）。** 每条标题被渲染两遍（源数据无独立摘要时前端回退成标题再截断）；政策/外围栏出现连续 6 条加拿大央行会议纪要（同主题未去重）；时间戳为 `09-17`，与页头「数据截至 2026-09-16」冲突。

其余问题分三类：

- **读者文案里出现内部代号**（R1）：板块 8 眉标 `PATTERNCOMBOGATE`、来源板块 `Prompt v4.14` 与 `R4/R35` 编号、表 2 空态「（可人工补充 plan2）」、龙头 dek 里的开发者备注。
- **同一板块三个名字**（R2 / E1）：导航、封面 chips、`p.sec-no` 三处各自为政（如「风险 / 特殊提醒 / 半导体高潮次日分化」、「股池 / 自建股池 / 自选池」）。
- **结论列全重复**（R3 / E2）：自选池 10 行、龙头表 5 行的「结论」列除模板变量外完全相同；根因是数据模型里没有逐票结论字段，模板只能插常量。

无障碍侧的结构性缺口：6 个指标格与两张 canvas 图只能鼠标操作（无 `tabindex`/`role`/`aria-label`）；drill 弹窗不能用 Esc 关闭、无对话框语义、关闭按钮无可访问名称；两张 canvas 图无文字替代；`.muted` 与 `.colophon` 的灰色对比度分别为 **3.07:1** 与 **2.91:1**（AA 要求 4.5:1）。

## [S2] Design

### 2.1 章节命名单一来源（R2 / E1）

每个 `section[id]` 增加 `data-name="正名"`，三个展示位置都从它派生，禁止各自写死：

| id | 正名 |
|---|---|
| `sec-glance` | 速览 |
| `sec-market` | 市场状态 |
| `sec-lines` | 主线推演 |
| `sec-leader` | 龙头 |
| `sec-plan` | 明日计划 |
| `sec-risk` | 风险提示 |
| `sec-pool` | 自选池 |
| `sec-pattern` | 形态组合信号 |
| `sec-news` | 新闻快讯 |
| `sec-sources` | 来源与方法 |

- 吸顶导航 `#sec-nav` 与封面 chips 的文案由 `main.js` 从 `data-name` 生成。
- `p.sec-no` 由 JS 改写为 `板块 N · <data-name>`（`sec-sources` 用 `来源与方法`）。
- H2 仍可由数据覆写（动态结论），但**不参与**命名一致性约束。

### 2.2 内部代号清除（R1）

读者可见文案中禁止出现：算法类名、提示词版本号、引擎变量名、报告章节编号。

| 现文案 | 改为 |
|---|---|
| `板块 8 · PATTERNCOMBOGATE` | `板块 8 · 形态组合信号`（由 data-name 生成） |
| `…（Prompt v4.14）` | 删除该括号，保留「完整推演见 Markdown 报告」 |
| `R4 口径自算` / `R35 四步核验` | `自算口径` / `四步核验`（去掉 R 编号） |
| `（可人工补充 plan2）` | `（引擎未生成，需人工补充）` |
| 龙头 dek 括号内「（具体标的一律见下表，由引擎按当日数据生成。）」 | 删除该括号 |

### 2.3 结论列替换为真实字段（R3 / E2）

**自选池表**（`#sec-pool`）：删「结论」列，列改为 `方向 / 标的 / 状态 / 技术位 / 今日表现`。

- `状态` ← `pool[].st`
- `技术位` ← `watch[]` 中同名标的的 `d`（`可参与（技术位）` / `观察`）；匹配不到时显示 `—`
- `今日表现` ← `watch[].note`（含 MA5/MA20/5日涨跌的完整一句）；匹配不到时显示 `—`

**龙头表**（`#sec-leader`）：删「结论」列，列改为 `标的 / 板块 / 等效高度 / 封单(亿) / 成交(亿) / 接力风险`。

引擎需在 `payload.leaders[]` 增加结构化字段（不再从 `verdict` 字符串里拆）：

```jsonc
{ "name", "h", "sector", "sealFund",  "turnover",  "risk",
     // 亿      // 亿        // "高"/"中"/"低"
  "tone" }
```

`risk` 由规则给出：最高板（`h` 最大）→ `高`；封单/成交比 < 0.3 → `高`；否则 `中`。文案不再在表格里重复。

### 2.4 封单单位（R4 / E3）

`engine/eastmoney.py:84`：

```
-  "fund": it.get("fund", 0) / 1e4 if it.get("fund") else 0,  # 封单额(亿)
+  "fund": it.get("fund", 0) / 1e8 if it.get("fund") else 0,  # 封单额（亿）
```

所有展示封单的地方随数据自动修正（龙头 verdict、明日计划表、应变矩阵、龙头表）。

单元测试：`raw fund = 214910502` → `2.15` 亿（保留两位小数）；`raw = 0` → `0`。

**离线旧缓存路径（复核发现的残留漏洞）**：`DAY_CACHE_VERSION` 只挡在线缓存；`--offline` 下 `load_cache(allow_stale=True)` 仍会放行版本 2 的缓存，而那里面的 `zt[].fund` 是 `/1e4` 旧口径。因此在 stale 分支必须处理：`cacheVersion == 2` 时把 `fund` 除以 `1e4` 折算；其它未知版本一律置 `None`（页面显示 `—`），绝不猜测单位。

### 2.5 新闻去重与日期（R5 / E4）

引擎侧（`engine/news.py` + `run_daily.py` 的新闻打包）：

- 每条增加 `summary` 字段；源数据无独立摘要时 `summary = ""`（**不要回退成标题**）。
- 同一分类内按归一化标题去重：去标点后取前 12 字作键；**先按 `time` 升序排序**再取首条，确保是「最早一条」而不是「列表首条」（源常为最新在前）。
- 过滤：`time` 晚于复盘日 `reviewDay` 的条目**剔除**（复盘页只呈现复盘日及以前的信息）。这是**默认且唯一**的口径；历史日重跑若确实需要看晚于复盘日的快讯，必须显式传 `--allow-late-news 1`，此时条目保留但 summary 前缀「【复盘日后】」。把「标注」当成满足了「剔除」的验收是不允许的。
- 上述过滤后若某分类条目数为 0，前端显示该分类下的占位文案（已存在）。

前端侧（`index.html` 内联脚本）：

```
- `${(n.summary||"").slice(0,120)}` 无条件渲染
+ n.summary ? `<span class="s-cite">…${n.summary.slice(0,120)}</span>` : ""
```

### 2.6 空态（R6 / R10）

- **空表**：`tbody` 无行时**不渲染 `thead`**，改为单行占位，`colspan` 等于原列数，文案用现有兜底句。适用 `lines-tbody / leaders-tbody / plan1-tbody / plan2-tbody / scen-tbody / scores-tbody / watch-tbody / risks-tbody / pool-tbody / glance-plan-tbody / pg-tbody / pg-combos`。
  - 实现方式：`fill()` 渲染后由 JS 检查 `tbody.children.length`，若为 0 则隐藏同级 `thead`；占位行始终渲染（已是现状）。
- **缺失值文案**：`昨日 —（未接缓存）` → `昨日 无缓存`。全站缺失值统一为 `—`，解释性说明只出现在 drill 或 `Source` 行里，不塞进数值行。

### 2.7 截断文案展开（R7）

`ban-note`（不做什么）超过 90 字时：

- 默认渲染前 90 字 + `…`
- 紧随其后一个 `<button class="expand" aria-expanded="false">展开全文</button>`
- 点击切换全文/收起，`aria-expanded` 同步

### 2.8 封面连板图重画（R8）

`js/cover.js` 的 `build()` / `drawPlaque()` 改动：

1. **阶梯高度按台阶序位（rank=1..4）**，不再按方块行数：`h = baseH + rank * stepH`。高度必须**恒定单调递增**——若用「最高板数」作为高度系数，当最高板只有 1~3 板时末级会反而变矮、阶梯不再上升。标签仍显示真实板数（`lv = topBoards`）。方块仍按家数排布在各自台阶上方。
2. **标签去重叠**：`⚠ 监管警示` 与 `<最高板名> · N 板` 分两行，y 至少错开 14px；两行都限制在画布右边界内。
3. **炸板簇可读性**：虚线+对角线改为「实心描边小方块」；簇上方固定渲染文字标签 `炸板 <n>`，下方 `跌停 <n>`（与现有 y 位置一致，仅样式改）。
4. **说明文案**：`cover-lede` 下方的画布说明由 `右侧画布 = …` 改为 `连板图 = …`（去掉方位词，窄屏下方才不会与事实冲突）。
5. **窄屏堆叠**：`W < 980` 时 `stage` 改为纵向排列——炸板簇在上、四级台阶横向压缩但**保持层级递增**，`scale` 下限从 `0.34` 提到 `0.42`，确保标签仍可读。

### 2.9 drill 弹窗（R9 / A2）

`js/utils.js` 的 `showDrill()` / `hideDrill()` 与 `css/style.css`：

- 弹窗加 `role="dialog" aria-modal="true" aria-label="<title>"`。
- 打开时保存 `document.activeElement`，把焦点移到弹窗；关闭时焦点还原。
- 全局监听 `keydown`：`Escape` → `hideDrill()`。
- 关闭按钮加 `aria-label="关闭"`。
- 定位：优先放触发物**右侧**（`x + triggerWidth + 12`）；若越出视口则改放触发物**下方**；仍越界再回退到现有逻辑。弹窗不得覆盖触发物。
- 视觉：深蓝底改为 `#ffffff` + 1px `#dbe2ea` 边框 + 与 ANALYST NOTE 一致的左侧色条，减少与页面的割裂感。

### 2.10 无障碍补齐（A1 / A3 / A4 / A5 / A6 / A7）

- **A1 指标格可键盘操作**：`#metric-strip` 的 6 个 `.m` 由 `div` 改为 `<button type="button" class="m" data-drill="…">`，保持现有样式（按钮重置）。
- **A1 canvas 可达**：
  - `#cover-canvas`：`role="img"` + `tabindex="0"` + `aria-label="2026-09-16 连板梯队：首板 77 家、2 板 9 家、3 板 1 家、最高板 闽东电力 6 板、炸板 11 家、跌停 4 家"`（由 `D.ladder`/`D.pools` 拼装）。
  - 五维图 canvas：`role="img"` + `tabindex="0"` + `aria-label` = 图表标题文字。
- **A3 视觉隐藏文字表**：两张 canvas 各配一个 `<div class="visually-hidden">` 内含 `<table>`：
  - 连板图：`梯队 / 家数` 6 行（首板/2板/3板/最高板/炸板/跌停）。
  - 五维图：`维度 / 得分 / 满分` 5 行。
- **A4 对比度**：`css/style.css` 与 `js/utils.js` 的 `PAL.inkLo` 由 `rgb(133,149,166)`（3.07:1）改为 `#5a6676`（rgb 90,102,118；对白 5.84:1，对 `#f7f9fc` 5.59:1）。`.muted` 与 `.colophon` 随 token 变化。
- **A5 表格 caption**：每张 `table.dt` 加 `<caption class="visually-hidden">`，内容取所属 section 的 `data-name` + 表标题（如「形态信号 · 形态组合信号 Top10」）。
- **A6 焦点环**：
  ```css
  :focus-visible { outline: 2px solid #2251ff; outline-offset: 2px; }
  ```
- **A7 锚点偏移**：
  ```css
  html { scroll-padding-top: 64px; }
  section[id], footer[id] { scroll-margin-top: 16px; }
  ```
  验收：点击导航任一链接后，该 section 的 `.sec-no` 可见（top ≥ 64px）。

### 2.11 页面数据契约（E5）

`js/data.js` 缺失或 `window.RPT` 为空时：

- `body` 顶部插入一条全宽提示：`数据文件缺失或为空，请先运行 engine/run_daily.py`。
- 现有各板块仍渲染，但不显示 `—` 堆叠，而是保留标题 + 该提示。
- 实现：`main.js` / 内联脚本在 `if (!D || !D.meta)` 分支中注入提示条。

### 2.12 窄屏重排（R8 mobile）

- `css/style.css` 增加 `@media (max-width: 720px)`：
  - 表格改为横向可滚动容器（`display:block; overflow-x:auto`），不压缩列宽到不可读。
  - 指标条 `#metric-strip` 由 3 列改 2 列。
  - 封面 chips 保持换行；`cover-title` 字号降到 `40px`。
  - 章节内边距减半，减少无效留白。

## [S3] Out of Scope

- 不新建 `sec-transition` 板块（`transition-underpricing` spec 的 T13/T14 另行推进）。
- 不改宏观择时、K 线形态、资金流、板块情绪的任何**算法**；本轮只改展示口径与单位。
- 不重跑历史复盘日；`run_daily.py --date 2026-09-16` 只在验证需要时运行。
- 不做深色主题、不做国际化。
- 不引入外部图标库；所有图形仍由现有 canvas 与 CSS 绘制。
- E6（覆盖率横幅借鉴）不适用于本页——那条建议是给 transition 模块的。

## Tasks

- [x] T1: 写 `.gitignore` 并在 `design-audit-fixes` 分支提交代码基线 — acceptance: `git log` 显示基线提交；`git status` 不含 `.venv/`/`data/`（covers: S2 preamble）
- [x] T2: `eastmoney.py` 封单单位 `÷1e4 → ÷1e8` + 单元测试 — acceptance: `test_fund_unit.py` 中 `raw=214910502 → 2.15`，测试全绿（covers: S2.4; depends: T1）
- [x] T3: 引擎 `news.py`/`run_daily.py` 新闻摘要回退、同主题去重、剔除晚于复盘日的条目 — acceptance: 构造含重复标题与未来日期的输入，输出中重复项只留一条、未来项被剔除、无摘要时 `summary==""`（covers: S2.5; depends: T1）
- [x] T4: `run_daily.py` 为 `leaders[]` 增加 `sector/sealFund/turnover/risk` 结构化字段 — acceptance: `data.js` 的 `leaders[0]` 含全部四个键，`sealFund` 为两位小数且量级正确（覆盖 R4 单位修正）（covers: S2.3, S2.4; depends: T2）
- [x] T5: `index.html` 章节 `data-name` + JS 从它生成导航/chips/sec-no — acceptance: 三处文案完全一致，页面上不再出现 `PATTERNCOMBOGATE`/`特殊提醒`/`自建股池` 等旧名（covers: S2.1, S2.2; depends: T1）
- [x] T6: 清除其余内部代号（Prompt v4.14 / R 编号 / plan2 / 龙头 dek 括号） — acceptance: 在 `index.html` 全文中 grep 不到 `Prompt v4`、`plan2`、`R35`、`R4 口径`、`由引擎按当日数据生成`（covers: S2.2; depends: T5）
- [x] T7: 自选池表列改为 `方向/标的/状态/技术位/今日表现` — acceptance: 表头五列与 `data.js` 的 `watch` 字段对得上，匹配不到 watch 的标的显示 `—`（covers: S2.3; depends: T4）
- [x] T8: 龙头表列改为 `标的/板块/等效高度/封单/成交/接力风险` — acceptance: 封单数量级为「亿」且与 `leaders[].sealFund` 一致（covers: S2.3; depends: T4）
- [x] T9: 空表隐藏 thead + 缺失值文案统一 — acceptance: 手动清空 `data.js` 的 `plan2` 后渲染出无表头的单行占位；`昨日 无缓存` 替换原括号文案（covers: S2.6; depends: T5）
- [x] T10: 不做清单展开/收起按钮 — acceptance: 文案 >90 字时出现 `展开全文`，点击后 `aria-expanded` 变为 `true` 且全文可见（covers: S2.7; depends: T5）
- [x] T11: drill 弹窗语义与定位 — acceptance: 弹窗带 `role="dialog"`/`aria-modal`/`aria-label`；Esc 可关闭且焦点还原；关闭按钮有 `aria-label`；弹窗不覆盖触发物（covers: S2.9; depends: T1）
- [x] T12: 指标格改 `<button>` + 两张 canvas 加 `role`/`tabindex`/`aria-label` — acceptance: Tab 可聚焦到全部 6 个指标格与两张 canvas；canvas 的 `aria-label` 含真实数字（covers: S2.10; depends: T5）
- [x] T13: 两张 canvas 配视觉隐藏文字表 — acceptance: 页面 DOM 中存在两个 `.visually-hidden` 表格，行数与图表维度一致（covers: S2.10; depends: T12）
- [x] T14: 对比度 token `#5a6676` + `:focus-visible` + `scroll-padding-top` — acceptance: `.muted` 与 `.colophon` 计算样式对比度 ≥4.5:1；点击导航后目标 `.sec-no` 的 top ≥64px（covers: S2.10; depends: T1）
- [x] T15: 所有 `table.dt` 加视觉隐藏 `<caption>` — acceptance: 11 张表均有 caption，内容含所属 section 的 data-name（covers: S2.10; depends: T5）
- [x] T16: `data.js` 缺失时的降级提示 — acceptance: 删除 `js/data.js` 后刷新页面，顶部出现提示条而非空白堆 `—`（covers: S2.11; depends: T5）
- [x] T17: 窄屏 `@media (max-width:720px)` 重排 — acceptance: 390px 视口下表格可横向滚动、指标条 2 列、无横向溢出（`scrollWidth == clientWidth`）（covers: S2.12; depends: T14）
- [x] T18: 封面连板图重画（层级递增 + 标签去重叠 + 炸板样式 + 说明文案 + 窄屏堆叠） — acceptance: 1440px 下台阶从左到右上升；`⚠监管警示` 与最高板标签 y 错开 ≥14px；说明文案为「连板图 = …」；390px 下标签仍可读（covers: S2.8; depends: T1, T12）
- [x] T19: 重跑 `run_daily.py --date 2026-09-16` 刷新 `js/data.js` — acceptance: 页面显示封单为亿级（有研硅 ≈2.15 亿）、新闻无重复标题且无 09-17 条目、`leaders[]` 含新字段（covers: S2.3, S2.4, S2.5; depends: T2, T3, T4）
- [x] T20: 全量测试 + Playwright 复验审计 14 步 — acceptance: 引擎测试全绿；浏览器控制台 0 error；对比度/键盘/Esc/锚点/窄屏五项断言全部通过（covers: S2.1–S2.12; depends: T5–T19）
