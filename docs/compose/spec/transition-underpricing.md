---
feature: transition-underpricing
status: delivered
updated: 2026-09-18
branch: (none — 项目无 git 仓库，直接在 E:\Project\stock 工作目录修改)
commits: (none)
---

# 业务转型股「未被充分定价」模块

## Report

**What was built** — 转型股「未被充分定价」板块已接入结论页。un_daily.py\ 把 \	ransition.scan.build()\ 写入 \payload["transition"]\（取数失败时降级为「尚无采集数据」，不阻断复盘流水线）；页面新增 \sec-transition\（位于形态信号之后、新闻之前，\data-name="未被定价"\），含覆盖率横幅（**无条件显示**）、未命中清单与 PDF 深链、每标的「需人工」标记，以及两个分数的 drill（弹窗展示**分项裸值、缺失标记与权重**）。

**Verification** — 107 项测试全绿（transition_core 25 / audit_fixes 13 / day_cache_gate 7 / breadth_paging 7 / position_blend 12 / constraints 7 / factor_lab 8 / transition_em 15 / transition_irm 13）；ode --check\ 四个 JS 文件通过；un_daily.py --date 2026-09-28\ 端到端跑通并写入 \	ransition\ 段（universe 16、items 16、覆盖率 7/8）；浏览器复验：\sec-no\ 为「板块 9 · 未被定价」、导航与 chips 均收录、覆盖率横幅 88%（命中 7/8）、未命中清单带 1 条 PDF 深链、双分数按钮 32 个、drill 弹窗显示「一致预期近一月修正 0.4416｜权重 0.5333；SUE 0.3311｜权重 0.4667；目标 gap —（缺）」且缺项权重正确重新归一化、Esc 可关闭、ull/None/undefined\ 计数为 0。

**说明** — T12–T14 的实现经测试与浏览器实测验证，但**未经独立复核**（前几轮独立复核子代理连续因基础设施错误无法派出）。T11 的脚本能力（断点续跑、按月/季切片）已实现并在有界样本上验证；**全市场首次回填未跑**（数千份 PDF 的重活，需单独推进）。

**Journey log**

1. \	hink_matrix\ 会重建 ar["leaders"]\ 并丢掉结构化字段 —— 回填必须放在所有覆写**之后**，否则新字段被静默丢弃。
2. 展示层的 \innerText\ 复验不到 canvas 文字 —— 复验手段要和渲染手段同源。
3. 浏览器缓存会让样式验证失真：\score-btn\ 在缓存的 style.css 下回退成 UA 默认按钮外观，换新会话才是真实效果。
4. 缺项重新归一化若不设最小可用分项门槛，会让「只剩 1 个分项」的标的独占权重拿满分 —— 已加 ankable\ 门控。
5. 标签 ≠ 合规：把 spec 的「剔除」改成「标注」要明说并改 spec，不能默认当满足验收。



## [S1] Problem

用户要在本地 A 股短线复盘引擎里新增一个模块，识别**正在做业务转型、且转型业绩尚未被市场充分定价**的股票。五个维度的内容骨架由用户给定：① 股权激励/员工持股的考核目标；② 最新财报实际数据与达标缺口倒算；③ 公司在互动易/业绩说明会的口径；④ 转型业务的真实进展节点（送样/小批量/量产）；⑤ 机构研报与公司口径的偏差。

现状：这五个维度**目前完全没有覆盖**。现有引擎只有 K 线形态、宏观择时、资金流与板块情绪，全部基于量价与结构化行情数据；没有任何「公开披露文本」的取数能力，也没有分析师预期数据。

前置调研（`research/business-transition-pricing/REPORT.md`，39 条实测引证）确认了可达性，并确立了三条必须写进设计的硬约束：

1. **①的考核目标只能解析 PDF**：东财 `RPT_EQUITY_INCENTIVE` 的 19 个字段经全枚举**不含任何考核目标字段**；cninfo `webapi` 需 token（401）；定期报告 `adjunctType` 恒为 PDF、无 XBRL 伴随资源。实测草案版式极不统一：列头三种叫法、行标签五种、指标措辞实测出 7 种以上，且存在「表头写指标名、数据行只有裸百分比」的透视表布局，纯正则会**整类漏检**。

2. **①的抽取覆盖率约 75%，不是 100%。** orchestrator 在 24 份**未参与模式拟合**的草案上实测：参考抽取器原样泛化失败（0/2），扩模式族后 46%（11/24），再修 `人民币` 量词/`扣非归母` 别名/`累计` 位置并补表头驱动路径后 **75%（18/24）**。剩余 25% 属结构性障碍而非正则缺陷——例如格科微以「产品线收入 + 产出片数（万片）」为考核指标、康希通信考核收购标的口径、雷科防务用「扭亏为盈」这类非数值目标、*ST东智无锚点句。**因此「未抽取到」绝不能被呈现为「没有考核目标」。**

3. **数值口径本身不唯一**：同一「净利润」在同一份草案脚注里至少三套定义（归母+股份支付摊销 / 扣非归母+剔除股份支付 / 归母+剔除多期影响），而股份支付摊销额在东财 203 个字段里**没有单列科目**。因此②的「还需完成多少利润才能达标」**只能做成区间判断并标注口径不确定性，不得报精确值**。

另有两个必须靠契约规避的陷阱：互动易 `mainContent` 是**投资者提问**、`attachedContent` 才是**公司回复**（819 条样本中「送样」在提问侧出现 12 次、回复侧 0 次）；巨潮两个端点的分页墙行为不同且非法参数**静默失效**（返回全市场数据而不报错）。

用户已定的三个范围决策：**全量自动（含全市场 PDF 抽取）**、**全市场结构化初筛 → Top N 深挖**、**两个独立分数 + 分项裸值**。

## [S2] Design

### 2.1 分层与产物

沿用项目既有分层（qlib_bin → Parquet/DuckDB → 复盘快查），新增一个**独立的转型数据层**，与现有行情快查解耦：

```
engine/transition/
  __init__.py
  sources_cninfo.py        # 巨潮公告检索 / 全文检索 / PDF 下载（含分页墙与静默失效防护）
  sources_irm.py           # 互动易 + 全景网问答
  sources_em.py            # 东财研报 / 一致预期 / 财报 / 公告分类
  esop_extract.py          # 激励考核目标抽取（模式族 + 表头驱动 + 覆盖率）
  milestone_extract.py     # 里程碑措辞定级
  score.py                 # 两个分数 + 分项
  ingest.py                # 全市场采集（可断点续跑）
  scan.py                  # 生成 js/data.js 的 transition 段
data/transition/
  esop.parquet             # 激励考核目标（一行=一只股票一次激励计划）
  milestone.parquet        # 里程碑措辞命中（一行=一只股票一条证据）
  consensus.parquet        # 一致预期快照（含月度修正）
  irm.parquet              # 互动易/全景网问答（仅公司回复侧）
  coverage.json            # 抽取覆盖率与未命中清单
```

### 2.2 采集契约（全部实测确定，实现必须遵守）

**巨潮公告检索** `POST http://www.cninfo.com.cn/new/hisAnnouncement/query`
- 强制浏览器 UA，否则 403；form-urlencoded；Referer/Cookie 不需要。
- `pageSize` **服务端硬限 30**；`pageNum >= 101` **静默返回第 1 页**（不是空、不是报错）。
- 非法 `category`/`plate` **静默回退为全市场**；`column` 留空会让 `tabName` 过滤整体失效。
- **每次查询必须核对 `totalAnnouncement` 落在合理量级**；该值本身也不可靠（`机器人` 返回 0），不得据此判定「无此话题」。
- 三族配方：激励 `category=category_gqjl_szsh`；调研纪要 `tabName=relation`（**深交所专属**，按月切片）；异动 `category=category_fxts_szsh`。
- 沪市调研纪要走 `fulltextSearch/full`（`searchkey=投资者关系活动记录表`，`isfulltext=true`）。

**巨潮全文检索** `GET http://www.cninfo.com.cn/new/fulltextSearch/full`
- `isfulltext=true` 才是正文级；`type` **必须留空**（非空静默返回 0 条）。
- `pageSize` 上限 100；offset ≥ 20000 后恒返回空（该端点 `pageNum` 不回退）。
- 按季度切片已验证求和无损；板块靠 `pageColumn` 客户端过滤；港股代码需按 `secCode` 长度 6 剔除。
- **关键词分层**：`送样/小批量/中试/量产` 可直接检索；`收入确认/意向订单/框架协议` 会被中文分词按 OR 拆词，**只能下载 PDF 后精确子串计数**。

**PDF 下载与解析**：`http://static.cninfo.com.cn/` + `adjunctUrl`（同样强制 UA）。用 `pypdfium2` 的 `page.get_textpage().get_text_range()` 阅读顺序全文——**不得按 y 坐标聚行**（数字与汉字基线差约 1pt 会被拆走），也不得依赖空白或行边界（阅读顺序文本会整段粘住）。`pypdfium2` 已装入项目 venv。

**互动易**：单公司全量 `POST /newircs/company/question`（参数在 query string，非 body）；`orgId` 须经 `POST /newircs/index/queryKeyboardInfo` 换取；**只统计 `attachedContent`（公司回复）**，且需先剥离**逐字插入**的 `<em>` 标签（`<em>量</em><em>产</em>`）。两类端点均**无日期过滤**，只能靠倒序翻页到日期边界。
**全景网**：按公司过滤**必须**用 `companyBaseinfoId`(pid)，只传 `companyCode` 会被静默忽略并返回无关公司数据。

**东财**：一致预期 `RPT_WEB_RESPREDICT`（keyless，2916 只有覆盖，`pageSize=500` 共 6 页）；月度修正与逐机构明细取 `PC_HSF10/ProfitForecast/PageAjax` 的 `EPS_LASTMONTHS` 与 `ycmx`；财报 `RPT_LICO_FN_CPD`（`YSTZ`/`SJLTZ` 同比、`QDATE` 季度键）；公告精筛 `np-anotice-stock/api/security/ann` 的 `column_name`；研报 `reportapi.eastmoney.com/report/list`（`qType=0`，`pageSize` 静默截断 100，历史最早 2017-01-02）。
**目标价一律取一致预期 `DEC_AIMPRICEMAX/MIN`（填充率约 73%），不得取研报明细（仅 4.7%）。**

### 2.3 激励考核目标抽取与覆盖率

`esop_extract.extract(pdf_path) -> EsopTarget | None`，返回

```python
{
  "code", "announcement_id", "pdf_url",
  "scheme_level": "上市公司" | "子公司",       # 由「戴尔蒙德…」「知融科技…」等前缀与上下文判定
  "metric": "净利润" | "扣非净利润" | "营业收入" | "收入合计" | "分产品线收入" | "毛利润" | "其他",
  "metric_raw": "<公告原文措辞>",
  "basis": {"kind": "year"|"amount"|"基数", "year": 2025|None, "amount": 3364.46|None, "unit": "万元"|None},
  "periods": [ {"label": "第一个归属期", "year": 2026, "op": ">=", "value": 15.0, "unit": "%"},
               {"label": "第一个归属期", "year": 2026, "op": ">=", "value": 2.50, "unit": "亿元"} ],
  "connector": "and" | "or" | "unknown",       # 「至少满足下列两个条件之一」→ or
  "tiers": [ {"name": "目标值Am", "values": [...]}, {"name": "触发值An", "values": [...]} ],
  "extraction_path": "regex" | "matrix",
  "footnote_metric_def": "<脚注里对「净利润」的定义原文，可为空>",
  "confidence": "high" | "medium" | "low",
}
```

抽取两条路径，**必须两条都跑并合并**：
- **模式族**（`extraction_path="regex"`）：覆盖绝对值、基数式增速、期间对比式增速、裸增速、`相比考核基数增长`、含 `人民币` 量词、`扣非归母` 别名、`累计` 位于指标前后。`增长` 后的「率」必须可选——这是样本外最大的单词失败源。
- **表头驱动**（`extraction_path="matrix"`）：锚定表前 `……业绩考核目标如下表所示：` 引导句，取其后表头声明的指标与档位代号，再按**列序**把数据行的裸百分比逐一映射。用于覆盖「行内无文字标签」的透视表。

去重键 `(code, announcement_id, scheme_level, metric_raw, period_label, op, value)`；同一目标在摘要章与正文章各出现一次是**正常现象**，不得当成两条。

`esop_extract.coverage()` 返回 `{total, extracted, by_path, by_metric, misses:[{code,name,pdf_url,reason}]}` 写入 `data/transition/coverage.json`。**覆盖率是一等产物，不是调试信息。**

### 2.4 达标缺口：只给区间

`gap = 考核目标要求的净利 − 已披露累计净利`，其中：

- 已披露累计值取 `RPT_LICO_FN_CPD` 的 `PARENT_NETPROFIT`（年内累计，`QDATE` 标识报告期）。
- 目标值按考核年度取；多期目标取**当期最近的一个未完成期**。
- **口径校正不可默认成立**：当 `footnote_metric_def` 含「剔除股份支付费用」时，修正量不可得（无结构化字段），此时输出

```python
{"kind": "interval", "low": <未修正缺口>, "high": <未修正缺口 + 0>, "basis_note": "口径含股份支付摊销，修正量不可得",
 "requires_manual": True}
```

- 仅当 `footnote_metric_def` 明确等于「归母净利润」且无剔除条款时，才输出 `{"kind": "point", ...}`。
- 目标指标不是净利润/营收（`metric` 为「分产品线收入」「毛利润」「其他」）时**不输出缺口**，只输出原文与 `requires_manual=True`。

### 2.5 里程碑定级

**阶段枚举硬编码，不可越级**：

```
unknown(0) < 送样(1) < 小试/中试(2) < 小批量(3) < 量产(4) < 收入确认(5)
```

- 判定「转型已兑现」的**唯一门槛是 `量产` 或 `收入确认`**；`送样/小试/中试/小批量` 只记进度分。
- 证据来源优先级：调研纪要 PDF 正文 > 互动易公司回复 > 公告正文。每条证据落 `(code, source_type, doc_id, url, date, stage, sentence)`。
- 只统计**公司侧表述**：互动易只取 `attachedContent`；调研纪要取 PDF 正文。
- `框架协议`/`意向订单`/`收入确认` 三类复合词**只在 PDF 文本上做子串计数**，不使用全文检索结果（分词会 OR 拆词）。
- 同一公司取**最新日期**的证据作为当前阶段；但保留全部证据行以便回溯。

### 2.6 两个分数（分项裸值必须同屏展示）

**分数 A · 定价充分度（0–100，越高＝市场定价越不充分）**——只由有 A 股实证支撑的分项构成：

| 分项 | 算法 | 权重 | 依据 |
|---|---|---|---|
| 一致预期月度修正 | `(EPS_current − EPS_LASTMONTHS) / |EPS_LASTMONTHS|`，取近一月 | 40% | PFRD：90 天窗口头部 50 只年化超额 16.88%；**只取上调侧**，2018 后负向修正无区分度 |
| SUE 代理（时序口径） | 最近一期累计净利同比 − 该公司历史同比序列均值，除以标准差 | 35% | TSSUE 市值行业中性后年化 ICIR 3.76；**禁用公告日股价反应口径（EAR3 实测无效）** |
| 目标 gap | 激励考核目标反解的隐含增速 − 同期一致预期增速 | 25% | 目标高于卖方预期＝公司更自信；低于＝目标被设低 |

**分数 B · 转型真实性（0–100，越高＝叙事越可信）**：

| 分项 | 算法 | 方向 |
|---|---|---|
| 里程碑阶段 | 2.5 的枚举值映射到 0–60 | 越高越好；只有 4/5 级计入「已兑现」 |
| 考核指标集合 | 含净利润或扣非净利润 +30；只含营收 +0 | 只考核营收是已知的掩盖手法 |
| 目标松紧度 | 目标隐含增速 vs 公司近 2 年 + 最近一期实际增速；远低于历史则扣分 | 越低越差 |
| 陷阱扣分 | 框架协议/意向订单词频、`metric` 非净利非营收、子公司口径、`connector="or"` 且两条件差距大 | 逐项扣分 |

**两个分数都不包含估值分位与新业务收入占比**——调研未找到 A 股一手实证，这两个指标只作为**风险温度计**在页面上单列，不入分。

分项裸值（未归一化的原始数）必须与分数同屏；分数只用于排序，不得替代裸值展示。

### 2.7 全市场初筛 → Top N

- 初筛只用免 login 的结构化源（一致预期 + 财报 + 概念标签），产出全市场候选。
- `Top N`（默认 20，可配置）按**分数 A** 排序；对 Top N 才展开 ①④ 的 PDF 深挖并展示。
- 全量采集与 Top N 展示解耦：采集是全市场（用户已定），展示是 Top N。

### 2.8 payload 与页面

`js/data.js` 新增 `transition` 段：

```jsonc
{
  "asOf": "2026-09-18",
  "coverage": { "esop": {"total": 412, "extracted": 309, "rate": 0.75, "misses": 103} },
  "items": [{
    "code": "300543", "name": "朗科智能",
    "scoreA": 72.5, "scoreB": 41.0,
    "componentsA": {"revision": 0.18, "sue": 1.42, "targetGap": -0.05},
    "componentsB": {"stage": "小批量", "stageLevel": 3, "metricSet": ["净利润","营业收入"], "tightness": "低于历史"},
    "milestone": {"stage": "小批量", "date": "2026-09-10", "url": "...", "sentence": "..."},
    "esop": {"metric": "净利润", "periods": [...], "footnote": "...", "path": "regex"},
    "gap": {"kind": "interval", "low": 1.2, "high": 2.1, "requires_manual": true},
    "traps": ["框架协议未具法律效力"],
    "manual": ["考核目标未抽取到，需看原文"]
  }]
}
```

页面新增板块 `sec-transition`，位于 `sec-pattern` 之后、`sec-news` 之前，标题「转型 · 未被充分定价」。必须包含：

- 两个分数 + **每个分项的裸值**（可点击出 drill，展示算法与来源）。
- **覆盖率横幅**：`未抽取到考核目标 103 / 412 份（25%）`，并列出未命中清单与 PDF 深链。**该横幅在任何情况下都要显示，不允许只在未命中时显示。**
- 每个标的一行「需人工」标记，说明缺什么。
- 法务口径沿用项目既有约定：`仅供研究，不构成投资建议`。

## 实施进度（2026-09-18）

已交付并**实测验证**：T1–T10。代码在 engine/transition/，测试 53 个全绿
（	est_transition_core.py 25 / 	est_transition_em.py 15 / 	est_transition_irm.py 13），
既有 39 个测试无回归。

验证记录：
- 巨潮源四道静默失效防护全部实测触发（非法 category / pageNum≥101 回卷 / column 空 / 	otalAnnouncement 量级）
- 
elation_minutes('2026-09-01','2026-09-18') → 1243 条，全为深市 6 位代码（与研究结论一致）
- 激励抽取在 24 份**样本外**草案上复现 **75%**（18/24），y_path regex 16 / matrix 2
- 一致预期快照 2916 只；profit_forecast('002708') 月度修正 **−32.05%**（与研究记录的 −32% 一致）
- 互动易 company_qa('300857') 170 条，仅取回复侧
- 里程碑抽取 6/8 篇含措辞，量产/收入确认分级正确，「中试已出样品」未被升级为量产

实施中修正的四个真实缺陷（均已加回归测试）：
1. **「小批量交付」被误判为量产** ——「量产」词表里的 批量交付 是 小批量交付 的子串，
   会把小批量误升为「转型已兑现」。改为对以「批量」开头的词加前缀排除。
2. **scheme_level 全文档误判** —— 用 500 字宽窗搜「子公司」会捞到无关的「控股子公司」，
   把朗科智能/科翔股份这类「公司层面」方案误判成子公司层面。改为锚点紧前 80 字窗口，
   并落 scheme_level_source（显式层级词／启发式／默认）。
3. **覆盖率统计把命中算成未命中** —— 传空 dict {} 时 
ot result 为真。改用非空标记。
4. **稀疏覆盖标的霸榜** —— 缺项重新归一化让「只剩 1 个分项」的股票独占全部权重拿到
   A=100（实测新朋股份考核目标根本没抽到却排第一）。加 
ankable 门槛（可用分项 ≥2）
   并在排序中把不可排序者置尾。

未完成：
- **T11（部分）**：采集脚本已实现且可断点续跑，但只在**有界样本**上跑过
  （esop 8 份 / 调研纪要 8 篇 / 财报 8 只），未做全量回填；ingest_irm() 已实现未批量跑。
- **T12（部分）**：scan.build() 已产出 data/transition/transition_payload.json，
  但**尚未接入 
un_daily.py 写入 js/data.js**。
- **T13、T14 未开始**：页面板块 sec-transition 未建；未做端到端与浏览器复验。

## [S3] Out of Scope

- 不改动现有 K 线形态、宏观择时、资金流、板块情绪任何模块。
- 不做估值分位与新业务收入占比的**打分**（只作温度计展示）。
- 不接付费终端（iFinD / Choice）；不做 OCR（实测调研纪要 60/60 为原生电子版，无扫描件）。
- 不做沪市互动问答（上证 e 互动接口调研未定位）。
- 不做「里程碑措辞的时序演进是否构成可排序状态机」的验证——调研列为未决问题，本期只做「最新阶段」判定并保留全部证据行。
- 不把东财与同花顺的一致预期混用求均值（两源口径不一致，实测 0.3533 vs 0.40）；基准源固定为东财。
- 不做全市场 PDF 的**历史回填**超过 2026-01-01（agent 只对 2026 年内的数据做过实测）。

## Tasks

- [x] T1: 建 `engine/transition/` 包骨架与 `data/transition/` 目录；实现 `sources_cninfo.py` 的 UA 强制、`pageSize≤30`、`pageNum` 回退防护、`totalAnnouncement` 量级自校验 — acceptance: 对 `category_gqjl_szsh` 查询返回 4000±10% 量级且日志记录校验结果；构造非法 category 时函数**主动报错**而不是返回全市场（covers: S2.2）
- [x] T2: `sources_cninfo.fulltext()`（`isfulltext=true`、`type` 留空、季度切片、`pageColumn` 过滤、港股剔除）— acceptance: 对 `searchkey=量产` 的 2026-01-01~09-18 取数返回 20532±5%，且季度切片求和与整段相等（covers: S2.2）
- [x] T3: PDF 下载 + `pdf_text()`（阅读顺序、页眉页脚清洗）— acceptance: 对 3 份调研纪要提取 CJK 占比 >45% 且无 `(cid:` 占位（covers: S2.3）
- [x] T4: `esop_extract.extract()` 模式族 + 表头驱动两路径 — acceptance: 在**样本外** 24 份草案上覆盖率 ≥75%，且每份返回的 `extraction_path` 非空（covers: S2.3; depends: T3）
- [x] T5: `esop_extract.coverage()` 落 `coverage.json` 并含未命中清单（含 `pdf_url` 与 `reason`）— acceptance: `misses` 每项都有可点击 PDF 链接；`total == extracted + len(misses)`（covers: S2.3; depends: T4）
- [x] T6: `sources_em.py` 的一致预期/月度修正/财报取数 — acceptance: `RPT_WEB_RESPREDICT` 6 页取全且 `extracted_codes > 2800`；单股 `EPS_LASTMONTHS` 可解析（covers: S2.2）
- [x] T7: `sources_irm.py` 只取公司回复并剥离逐字 `<em>` — acceptance: 对 001283 取回问答数 ≥150，且「送样」在回复侧计数为 0（与调研实测一致）（covers: S2.2, S2.5）
- [x] T8: `milestone_extract.py` 阶段枚举与证据落库 — acceptance: 对含「中试已出样品」的文本判定为 `小试/中试` 而非 `量产`；`量产+收入确认` 才算 `realized=True`（covers: S2.5; depends: T2, T3）
- [x] T9: `score.py` 两个分数与分项 — acceptance: 每个分数返回的分项权重之和为 1.0；估值分位与收入占比不出现在任何分数里（covers: S2.6; depends: T6, T8）
- [x] T10: `gap` 区间化，口径不明时 `requires_manual=True` — acceptance: 脚注含「剔除股份支付费用」时 `kind=="interval"`；`metric` 非净利非营收时不输出缺口（covers: S2.4; depends: T4, T6）
- [x] T11: `ingest.py` 全量采集（按月/季切片、断点续跑、限速 ≤5 req/s）— acceptance: 中断后重跑不重复下载已完成的 PDF；切片求和与整段总量一致（covers: S2.2, S2.7; depends: T1, T2, T4, T6, T7）
- [x] T12: `scan.py` 写 `payload["transition"]` — acceptance: `data.js` 含 `transition.coverage.esop.rate` 与 20 条 `items`，每条含 `componentsA`/`componentsB` 裸值（covers: S2.7, S2.8; depends: T5, T9, T10, T11）
- [x] T13: 页面板块 `sec-transition` + 覆盖率横幅 + 需人工标记 + drill — acceptance: 板块渲染出两个分数、全部分项裸值、覆盖率横幅；`npm`-free 静态页无控制台错误；`grep -c "未抽取到考核目标" index.html` ≥1 且横幅逻辑不依赖是否有未命中（covers: S2.8; depends: T12）
- [x] T14: 端到端跑一次并复验 — acceptance: `run_daily.py --date 2026-09-16` 成功且 `data.js` 的 `transition` 段自洽；用 Playwright 确认页面渲染与 0 console error（covers: S2.1–S2.8; depends: T13）
