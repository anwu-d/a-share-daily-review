# A股「业务转型 + 业绩尚未被市场充分定价」模块：数据源可达性与分析口径

> Generated 2026-09-18 · depth: standard · 39 sources · workspace: `research/business-transition-pricing/`
> 方法：6 个并行子代理**直接调用活接口**取证（约 400 次真实请求）+ 5 份激励草案与 60 份调研纪要 PDF 实测解析。凡标 `primary` 的结论均为对端点的直接实测，不依赖二手文章。

## Executive summary

- **这个模块可以大部分自动化，但不是全部。** 五个维度里，②③⑤ 可全自动，④ 可自动化但须两段式且成本高，① 只能靠 PDF 解析且精度有上限。
- **维度 ② 完全不必碰 PDF**：东财 `RPT_LICO_FN_CPD` 免登录直出中报/季报累计营收、归母净利润及同比 `YSTZ`/`SJLTZ` [19]；新浪 [23]、同花顺 [24] 可交叉校验。
- **维度 ⑤ 有一个被低估的发现：存在完全免密钥的分析师一致预期 EPS 序列。** `RPT_WEB_RESPREDICT` 仅 6 次请求即取全 2916 只有覆盖个股，含 4 年 EPS（A/E 标记）、评级分布、目标价上下限、概念标签 [16]；`ProfitForecast/PageAjax` 还给出 **`EPS_LASTMONTHS` 月度修正**与**逐家机构明细** [17]——这正是「是否已被定价」最直接的观测量。
- **维度 ① 的目标数字只能从草案 PDF 抽，且已被实证排除结构化替代**：东财 `RPT_EQUITY_INCENTIVE` 的 19 个字段**没有一个**考核目标字段 [21]。实测草案给出 **7 种互不相同的指标措辞** [8] 与**整类漏检的透视表布局** [7]，纯正则覆盖 6/7，补「表头驱动列序对齐」后 7/7 [7]。
- **① 的 back-solve 精度有结构性上限**：同一「净利润」在草案脚注里至少三套口径（归母+股份支付摊销 / 扣非归母+剔除股份支付 / 归母+剔除多期影响）[5]，而股份支付摊销额在东财 203 个字段里**没有单列科目** [5]。
- **维度 ③ 可自动化，但有一个致命陷阱**：互动易 `mainContent` 是**投资者提问**、`attachedContent` 才是**公司回复**。819 条样本里「送样」在提问侧出现 12 次、**回复侧 0 次** [11]。且沪深覆盖不全：互动易**零沪市覆盖** [12]，上证 e 互动接口未能定位 [12]。
- **维度 ④ 可行但要付出下载成本**：`fulltextSearch/full?isfulltext=true` 是真正的正文级索引（「量产」205,906 条 vs 仅标题 63 条）[3]，可作候选召回；调研纪要 PDF **全部是原生电子版、60/60 无扫描件**，提取干净（CJK 占比 50~79%，0 个 cid）[10]。闭环验证 4/4：检索摘要里的里程碑词在 PDF 文本中全部复现 [9]。
- **④ 不能靠关键词预筛**：60 篇调研纪要中 52% 至少含 1 个里程碑词，但早期信号极稀——`送样` 仅 **2%**、`小批量` 3%、`正式订单` 2%，而 `验证` 25%、`量产` 18% [1]。以「送样/小批量」为主筛条件必须全量下载后再计数。
- **「未被充分定价」的口径要挑有 A 股实证的用**：一致预期**上调**修正与基本面口径 SUE 有实证支撑；机构调研频次是**同步/滞后指标、不是 alpha**；估值分位与收入结构迁移**无一手实证**，只能当风险温度计 [29][30][31]。
- **陷阱可编码**：激励目标设低 [32]、只考核营收不考核净利 [33]、目标低于卖方一致预期 [34]、框架协议「不具有法律效力」[35]、把中试当产业化 [39]——每条都有案例与可自动检测的信号。
- **实施前必须先处理的工程约束**：巨潮两个端点有**不同的分页硬墙**——`hisAnnouncement` `pageSize≤30` 且 `pageNum≥101` 静默回卷第 1 页（3000 条/次上限）[1]，`fulltextSearch` `pageSize≤100` 且 offset≥20000 后恒空 [3]；且**非法参数静默失效**（非法 category 返回全市场、非法 tabName 返回 0），入库前必须用 `totalAnnouncement` 自校验 [1]。

## Background & scope

问题：在本地 A 股短线复盘引擎里加一个模块，识别「正在转型、且转型业绩尚未被价格充分反映」的股票，骨架是用户给定的五个维度。本报告的产出不是页面设计，而是**每个维度背后数据源的真实可达性、接口契约与精度上限**——即「哪些能自动化、哪些必须人工」。范围限定在免登录公开源；付费终端（iFinD / Choice）只记录为「不可得」。假设：目标是「尽量自动、拿不到就老实标需人工」；服务短线复盘，因此时效性与「最近 30~90 天有新公告/新问答」比长历史更重要。全部接口事实来自 2026-09-18 的直接请求实测——调研过程中 DuckDuckGo 在本机不可达、百度返回反爬页、Sogou 二次查询即拦截、Bing 中文结果退化，唯一稳定检索入口是 360 搜索，因此研究策略改为「直接打接口而非搜文章」。

## 一、维度可达性总表

| 维度 | 可用源 | 自动化结论 | 关键约束 |
|---|---|---|---|
| ① 激励考核目标 | cninfo 草案 PDF [1][4] | **半自动**（可抽取，精度有上限） | 东财激励库无此字段 [21]；7 种措辞 [8] + 2 类布局 [7]；脚注口径 3 套 [5]；股份支付摊销额无结构化来源 [5] |
| ② 财报实际数 | 东财 [19][20]、新浪 [23]、同花顺 [24] | **全自动** | 无需 PDF；`REPORTDATE` 为年内累计值 |
| ③ 互动易/全景网口径 | 互动易 [11][12][13]、全景网 [15] | **全自动（深市）** | 只扫 `attachedContent`；零沪市覆盖；无日期过滤 |
| ④ 里程碑进展 | `fulltextSearch` [3] → PDF [4] → pypdfium2 [10] | **可自动，成本高** | 早期词稀少须全量下载；复合词须 PDF 精确计数；两所入口不同 [1] |
| ⑤ 研报/一致预期 | 东财 [16][17][18]、同花顺 [24] | **全自动** | 目标价必须用一致预期而非研报明细 [18] |

## 二、维度 ①：唯一必须解析 PDF 的环节，且精度有硬上限

**为什么绕不开。** 东财确有结构化股权激励库 `RPT_EQUITY_INCENTIVE`（15,864 条），但其 `columns=ALL` 返回的 19 个字段是授予标的/数量/行权价/授权日/行业，**不含考核年度、指标名、目标值、增速、档位** [21]。cninfo 侧同样没有：`webapi.cninfo.com.cn` 一律要求 token（业务码 401），定期报告的 `adjunctType` 恒为 `PDF`，无 XBRL 伴随资源 [21]。**这是本角度最重要的否定性结论**——它把维度 ① 的实现方式从「查库」锁死为「解析」。

**抽取的真实难度比预期高。** 实测草案给出 **7 种互不相同的指标措辞** [8]：绝对值（`2026 年净利润不低于 2.50 亿元`）、基数式（`以2025年净利润为基数…增长率不低于25%`）、期间对比式（`相较2025年度营业收入，增长率不低于15%`）、累计区间、分部业务+或条件（`矿产资源业务收入不低于10,000万元或…毛利润不低于4,000万元`）、两档制（目标值 AM / 触发值 AN）[8]。列头第三列有三种叫法（`公司层面业绩考核目标` / `业绩考核目标` / **`解除限售条件`**），行标签有五种；最可靠的锚点不是列头，而是表前的 **`……业绩考核目标如下表所示：` 引导句** [6]。

**最危险的是一类会被整类漏检的布局**：雄帝科技（300546）的表是「表头写指标名、数据行只有裸百分比」，全表不含「不低于」，任何以「不低于/增长率不低于」为锚的正则命中 **0 条**；该行 4 个百分比（14.18% / 26.87% / 20.69% / 34.10%）**必须靠表头列序**才能映射为营收/净利的触发值与目标值 [7]。补上「表头驱动 + 列序对齐」路径后 7 份覆盖率才到 **7/7** [7]。直接含义：**抽取器必须有两套路径，且必须落一个覆盖率指标，不能只看命中数。**

**back-solve 的精度天花板。** 同一「净利润」在草案脚注里至少三套定义：① 归母净利润 + 股份支付费用摊销；② 扣非归母净利润 + 剔除股份支付费用；③ 归母净利润 + 剔除多期股份支付影响 [5]。拿财报「归母净利润」直接比对会**系统性算偏**；而股份支付摊销额在 F10 的 203 个字段里没有单列科目，可能只能从附注文本取 [5]。**因此「还需完成多少利润才能达标」不应以精确值呈现，而应做成区间判断并显著标注口径不确定性。**

**取数配方。** 发现侧：cninfo `category=category_gqjl_szsh`（2026-08-01~09-18 共 4172 条），员工持股计划需另用标题检索 `searchkey=员工持股计划`（786 条）[1]；或 `fulltextSearch` 的 `searchkey=激励计划（草案）`（此接口 `pageSize=100` 被尊重，前 100 条含 29 份纯草案正文）[1]。精筛侧：东财 `np-anotice-stock` 的 `columns[].column_name` 直接是「股权激励计划」/「股权激励计划摘要」，可一键滤掉法律意见书与自查表 [22]。PDF 直链 = `http://static.cninfo.com.cn/finalpage/<YYYY-MM-DD>/<announcementId>.PDF`，无 cookie 可下载 [4]。

**解析技术要点（已实测）。** 用 `pypdfium2` 的 `page.get_textpage().get_text_range()` 阅读顺序全文，**不要**按 y 坐标聚行——数字与汉字基线相差约 1pt，朴素取整会把数字整段踢出该行（实测出现 `y=497.0 | 20262.5020262025`），容差需 ≥4pt 才复原 [5]。且阅读顺序文本**不保证单元格分隔**，会整段粘住（`第一个解除限售期2027年度2026-2027年累计收入合计不低于4.3亿元`），正则不能依赖空白或行边界 [5]。

## 三、维度 ②：完全结构化，PDF 解析在此侧纯属浪费

三个免登录源实测可用：东财 `RPT_LICO_FN_CPD` 直出 `TOTAL_OPERATE_INCOME:28416674541.77`、`PARENT_NETPROFIT:8752942991.31`、`YSTZ:20.87`、`SJLTZ:89.3`、`QDATE:"2026Q2"`，全市场一次可拉 [19]；东财 F10 `lrbAjaxNew` 给出 203 项全利润表（`code` 须带市场前缀如 `SZ000858`）[20]；新浪 `vDOWN_ProfitStatement` 是 GB18030 制表符文本、112 个报告期回溯至 1995 [23]；同花顺 `basic.10jqka.com.cn/api/stock/finance/{code}_main.json` 111 期，且**同比/环比是独立数组块**（`report_yoy`/`simple_yoy`/`simple_mom`/`year_yoy`）不必自算 [24]。同花顺的坑：`simple` 块首值是单季（`6.90亿`）、`report` 块首值是累计（`87.53亿`），**两块语义不同不可混用**，且数值带 `亿`/`万` 后缀并存在 `false` 占位 [24]。

## 四、维度 ③：可达，但「提问 vs 回复」是必须写进契约的陷阱

**正确入口**：单公司全量用 `POST /newircs/company/question`，参数放在 **query string** 而非 body（`_t/stockcode/orgId/pageSize/pageNum/keyWord/startDay/endDay`），`pageSize=1000` 一次取回全部（实测 168 条）[11]。`orgId` 须先经 `POST /newircs/index/queryKeyboardInfo` 用代码换 `secid` [13]；在全局搜索里按公司过滤必须写成 `stockCode=orgId_stockCode`（`300857` → 0 条，`9900039793_300857` → 168 条）[13]。关键词检索是 `POST /newircs/index/search`，**参数必须是 form-urlencoded**——传 JSON body 会被静默丢弃并返回全量 72,928 条 [14]，这正是本次调研最初探针得到 `totalRecord:0` 的邻类陷阱 [14]。只读接口全部免登录、免 token、免 Referer [14]。

**致命陷阱**：`mainContent` 是投资者的**提问**，`attachedContent` 才是**公司回复**。819 条样本的逐词对照 [11]：

| 词 | 出现在提问 | 出现在回复 |
|---|---|---|
| 送样 | 12 | **0** |
| 量产 | 24 | 19 |
| 验证 | 18 | 9 |
| 小批量 | 7 | 5 |

典型噪声是投资者把「处于小试/中试/送样哪个阶段」当**问题**抛出（豪鹏科技 001283 一条提问同时含 4 个里程碑词、零披露价值）[11]。**引擎必须只对公司回复侧计数，且不能把「提问里高频提到」当作热度信号。** 另一坑：`<em>` 高亮是**逐字**插入的（`<em>量</em><em>产</em>`），裸 `str.count("量产")` 恒为 0 [12]。

**覆盖缺口**：互动易**零沪市覆盖**（跨公司搜索命中 198 个代码，首字符只有 `0`(93) 与 `3`(105)）[12]；上证 e 互动 `sns.sseinfo.com/api/qa/search` 返回 404，沪市互动问答接口未能定位 [12]。全景网可作补充：`POST https://ir.p5w.net/interaction/getNewR.shtml`，但按公司过滤**必须**用隐藏域 `companyBaseinfoId`(pid)，只传 `companyCode` 会被静默忽略并返回无关公司数据 [15]。另需注意互动易两类端点均**不支持任何日期过滤参数**（`beginTime`/`startDate`/`pubDateStart` 均无效），只能靠「结果按最新活动倒序」翻页到目标日期边界后停止 [11]。

## 五、维度 ④：两段式架构成立，但关键词分层决定了成本

**候选召回段**：`GET http://www.cninfo.com.cn/new/fulltextSearch/full` 是真正的正文级索引——`isfulltext=true` 时「量产」205,906 条，仅标题时 63 条 [3]；返回体带 `announcementContent` 正文摘录，命中的 `<em>` 出自 PDF 正文 [3]。但 `type` 参数**必须留空**（任何值静默返回 0 条），板块要靠结果的 `pageColumn` 客户端过滤（`SHZB`/`SZZB`/`SZCY`/`SHKCB`），且港股代码会混入需按 `secCode` 长度为 6 过滤 [3]。

**关键词必须分层，这是最重要的操作性结论** [3]：

- **分词友好词（可直接全文检索）**：`送样`、`小批量`、`中试`、`量产`。Top 命中高度相关，例：`柯力传感：…六维力传感器已经送样多少客户？`、`金麒麟：…CRH380B型动车组闸片通过小批量试用的公告` [3]。
- **复合词（不可用全文检索，必须 PDF 精确子串计数）**：`收入确认`、`意向订单`、`框架协议`。中文分词把复合词拆成单词后按 **OR** 匹配——`收入确认` 命中的是「补充**确认**关联交易」+「营业**收入**」这类噪声 [3]。

**精确提取段**：调研纪要就是真 PDF（`Content-Type: application/pdf`，magic `%PDF`）[4]。`pypdfium2` 5.13.0 对 **60/60** 篇提取成功、**0 篇扫描件**、CJK 占比 50~79%、0 个 `(cid:)` 占位 [10]。闭环验证 4/4：由全文检索正文命中挑出的调研纪要，其摘要中的里程碑词在 PDF 提取文本中**全部复现、0 处不一致** [9]。**因此「全文检索做候选召回 + PDF 精确提取做里程碑定级」的两段式是可落地的。**

**但成本由词分布决定**：60 篇跨月样本中 52% 至少含 1 个里程碑词，然而 `验证` 25%、`量产` 18%、`产业化` 10%、`中试` 7%，而 `送样` 仅 **2%**、`小批量` 3%、`正式订单` 2% [1]。**若以「送样/小批量」为主筛条件，关键词预筛召回率过低，必须全量下载提取后再计数**——这直接决定模块的抓取成本量级。

**两所入口不同，必须双路**：`tabName=relation`（调研纪要）是**深交所专属**——180 篇抽样中 0 个 `6xxxxx`，`plate=sh`/`plate=sse` 返回 0，显式指定 `stock=600519,gssh0600519` 也是 0 [1]。沪市调研纪要**只能**走 `fulltextSearch`（`searchkey=投资者关系活动记录表` 正文检索 5798 条，其中 48% 为 6 开头）[1]。

## 六、维度 ⑤：一致预期是免密钥可得的，被低估的一环

`reportapi.eastmoney.com/report/list` 的 4 个必填参数（`beginTime`/`endTime`/`pageNo`/`pageSize`，缺任一即 HTTP 400）、`qType` 映射（0=个股/1=行业/2=策略，≥3 恒 0 条）、`ratingChange` 语义（0=上调/1=下调/2=首次覆盖/3=维持）均已实测确定；单条研报携带三年预测 EPS/PE、评级三件套、`indvAimPriceT/L` [18]。

**但目标价字段近乎不可用**：1500 条近月个股研报中仅 **71 条（4.7%）**带目标价，且集中在西南/国信/东吴等少数券商 [18]。**任何依赖研报明细目标价的逻辑会产生 95% 以上空值**——目标价必须改走一致预期接口的 `DEC_AIMPRICEMAX/MIN`（实测填充率约 **73%**）[16]。

**一致预期接口的完整能力**：`RPT_WEB_RESPREDICT`（免密钥、免 cookie）全量仅 6 次请求（`pageSize=500`）覆盖 **2916 只**个股，字段含 `YEAR1-4` + `YEAR_MARK1-4`（A=实际/E=预测）+ `EPS1-4`、`RATING_*_NUM` 评级分布、目标价上下限、`INDUSTRY_BOARD`、`CONCEPTINDEX_BOARD`（**概念标签可直接做转型题材池**）[16]。单股用 `SECURITY_CODE in (...)` 批量过滤，单次约 400 只上限（500 只触发 400，600 只触发 414）[16]。

**修正信号可直接观测**：`emweb.securities.eastmoney.com/PC_HSF10/ProfitForecast/PageAjax?code=SZ002708` 返回 `EPS_LASTMONTHS`（月度修正），实测 002708 的 2026E EPS 在一个月内由 **0.52 被下修至 0.3533（−32%）**；其 `ycmx` 块是逐家机构的 EPS/评级/发布日期明细，是算「卖方分歧」（维度 ⑤ 的核心）的最佳数据 [17]。

**口径冲突警告**：同花顺 `basic.10jqka.com.cn/{code}/worth.html`（服务端直出 GBK HTML）同样给一致预期均值/最小/最大值与逐机构明细，但**两源数字不一致**（002708 同花顺 2026 均值 0.40 vs 东财 0.3533）[24][17]。**两源不可混用求均值**，必须先定基准源。

**时间上限**：研报历史最早只到 **2017-01-02**，完全早于 2017 的区间返回 0 条 [18]；一致预期目前只见到 **1 个滞后月**快照（`EPS_LASTMONTHS`），更长的「修正趋势」需本地逐日持久化自建 [17]。

## 七、「未被充分定价」怎么算：只用有 A 股实证的口径

| 口径 | A 股实证 | 可用性 |
|---|---|---|
| 一致预期**上调**修正（PFRD） | 90 天窗口头部 50 只年化超额 16.88%（一致预期口径仅 10.07%）；**2018 年后负向修正已失去区分度**，只能做多不能做空；约 60 个交易日后衰减明显 [30] | **强，首选** |
| SUE（基本面/时序口径） | TSSUE 市值行业中性后全市场年化 ICIR **3.76**；ALSUE 中性化后 IC 均值 >3% [29] | 强；但 2019-08 后有效性下滑 [31] |
| PEAD（事件后漂移） | 窗口约 **50 个交易日**；惊喜值做**半衰期 20 交易日**衰减加权效果最好 [29] | 可用作时间权重 |
| **EAR3（公告日股价反应口径）** | **完全无效**——T+2~T+60 超额≈0 [29] | **禁用** |
| 机构调研频次 | **同步/滞后指标**：调研前 60 日累计超额 >6%，调研后 60 日不足 2%；>100 家机构参与时未来超额为负 [31] | 只作筛选池/确认信号，**不是 alpha** |
| 估值分位（PE/PB percentile） | **未找到一手中文实证** | 仅作风险温度计 [31] |
| 新业务收入占比/结构迁移 | **未找到 A 股专项实证** | 自建指标，无文献支撑 [31] |

**一处需要澄清的张力**：调研频次本身不是 alpha [31]，但「以机构调研池为股票池、再叠加 SUE + 理想反转 + 大单残差」的合成因子有实证增益（多头年化 21.73%、对冲年化 12.62%；三因子等权合成多头年化 25.66%）[31]。二者可调和——**调研是池子不是因子**；同时须注意该增益主要来自小市值域（沪深 300 内多头仅 13.48%）[31]。

**口径落地的三条硬要求**：① 用一致预期修正时只取**上调**侧；② 用 PEAD 时必须把「事件日距今天数」作衰减权重（`2^(-x/20)`，x 为交易日），让「刚兑现」的排在「喊了很久」的前面；③ 调研数据必须按**公告日**归月并剔掉公告滞后 >30 个日历日的样本（该字段最长可滞后 500 天以上）[31]。

## 八、陷阱清单：每条都有案例，且大多可自动检测

| 陷阱 | 案例与证据 | 可编码信号 |
|---|---|---|
| 激励目标被设到几乎必然达成 | 开润股份 300577：首个考核期 2023 营收只需较 2022 **+0.93%**，而前两年增速为 17%/19%、当年一季度 21%，深交所发关注函追问「是否输送利益」[32] | 把目标反解为**隐含增速**，与公司过去 2 年 + 最近一期实际增速、同期卖方一致预期增速比对 |
| 只考核营收、不考核净利润 | 好上好 001298：2023H1 营收 27.84 亿（-23.51%）、净利 1779.96 万（**-79.30%**）却仍可「达标」[33] | 记录**考核指标集合**；只含营收的加惩罚项 |
| 目标低于卖方一致预期 | 东阿阿胶 000423：群益/中泰给 2024E 净利 13.6/13.4 亿，考核目标仅约 11.2 亿 [34] | 用维度 ⑤ 的一致预期数做 gap；**同一草案内逐项算「松紧度向量」**（该案营业利润率目标形同虚设、ROE 目标反而偏难）[34] |
| 框架协议被当成订单 | 大丰实业 603081×智元机器人：公告原文即写明「**不具有法律效力，没有强制约束力**」，且只含「不低于 1,500 万元**意向**采购订单」，而合资公司注册资本仅 1,000 万元 [35] | 三类词分开计数：`框架协议/战略合作` ⊂ `意向采购/意向性` ⊂ `正式订单/合同` |
| 拿框架协议确认收入 | 飞乐音响 600651：项目确认收入不符合条件，虚增营收 1.8 亿、虚增利润总额 3,784 万，被处罚 [36] | 审计信号：收入确认是否落在正式合同+招投标+完工进度上 |
| 跨界大单 → 退单 | 2026 跨界算力退单潮：海南华铁 603300 的 36.9 亿算力协议终止、被罚 520 万；莲花控股 600186 终止合同占销售合同总额 **82.67%**、预付 1.357 亿（3,188.78 万未追回）与 1.2 亿 GPU「尚未交付」[37] | 交易所已固化成四个字段：**合同是否有法律约束力 / 客户是否实名 / 预付款比例与回收风险 / 设备到位进度是否匹配** [37] |
| 互动易误导性陈述 | 苏大维格 300331：把直写光刻设备表述为「光刻机」与「芯片光刻机」并用，股价当日由跌转涨收 +20%，罚公司 150 万 + 董秘 100 万 [38] | 行业热门词与公司主营口径不匹配时**标红**，不得当利好 |
| 阶段幻觉（中试当产业化） | 金龙羽 002882：以「固态电解质、半固态电芯已进入中试试验」「中试已出样品」换来 16 个交易日 **+96.68%**，随即收关注函 [39] | 见下方阶段枚举 |

**阶段枚举必须硬编码且不可越级**：`送样(sample) < 小试/中试(pilot) < 小批量(small-batch) < 量产(mass production) < 收入确认(revenue)` [39]。**「转型已兑现」的门槛只能设在「量产 + 收入确认」，送样/中试/小批量一律只记进度分不记兑现分** [39]。附带一个可计数的告警特征：金龙羽案中「公司在互动易**密集回应**」本身就是高发模式 [39]。

## 九、实施前必须处理的工程约束（全部为实测）

**分页硬墙（两个端点行为不同，不能套用同一套翻页逻辑）**：

| 端点 | pageSize 上限 | 翻页行为 | 规避 |
|---|---|---|---|
| `hisAnnouncement/query` | **30**（传 50/100/1000 均只回 30）[1] | `pageNum≥101` **静默返回第 1 页**（不是空、不是报错）→ 3000 条/次上限；越过真实末页时 `totalAnnouncement` 归零 [1] | 按**月**切片（按季会超墙：2026Q2 调研纪要 4,824 条 > 3000）[1] |
| `fulltextSearch/full` | **100** [3] | `pageNum` **不回退**，offset ≥20000 后恒返回 0 条 [3] | 按**季**切片（已验证季度求和与整段完全相等、无损）[3] |

**静默失效（最危险的类别，不报错）**：非法的 `category`/`plate` 值 → **返回全市场数据**（`category_gqjl_bf` 与不筛同值 153,372）[1]；非法 `tabName` → 0 条；`seDate` 格式错 → 0 条（只有用 `-` 作分隔符才 500）[1]；`column` 留空 → `tabName` 过滤整体失效并返回全市场 11,100 条 [1]；`fulltextSearch` 的 `type` 非空 → 0 条 [3]；`report/list` 的 `code` 不带 `qType` → 静默空结果 [18]。**规则：每次查询都必须核对 `totalAnnouncement` 是否落在合理量级，不能只看是否报错。**

**`totalAnnouncement` 本身不可靠**：`机器人` 与 `人形机器人` 返回 **0**（而 `人形` 单独搜有 47,234 条），完全不存在的关键词也返回 0，两者无法区分 [3]。**引擎不得把 `totalAnnouncement==0` 当作「无此话题」的证据。**

**其他必须遵守的细则**：巨潮全部端点（含 JSON 数据文件与 PDF 静态站）**强制浏览器 UA，否则 403**（Referer/Cookie 不需要）[1]；`category` 多选分隔符是**分号**（逗号会让过滤器整体失效，实测 16,955 vs 153,372）[1]，合法取值共 26 个 [25]；`searchkey` 是**标题级**且多词结果随词序变化（`股权激励 员工持股计划`=20 条 vs 反序=23 条），生产环境只用单关键词 [1]；`isHLtitle=true` 会插入 `<em>`，入库前必须剥离 [1]；按个股取数必须 `stock="代码,orgId"`（只给代码返回 0 条），`orgId` 有四种命名风格（`gssz0000001`/`GD165627`/`gshk0001211`/`nssc1000767`）须从 `http://www.cninfo.com.cn/new/data/szse_stock.json`（6,255 条映射）取 [2]；`announcementTime` 是**北京时间零点的毫秒戳**（非精确披露时刻）[1]。

**限流现状（单次爆发测试，不代表长期安全）**：巨潮 40 次连发全 200（4.7s）[1]；`report/list` 80 次连发约 7.4 req/s 全 200 [18]；`datacenter-web` 60 次连发约 4.5 req/s 全 200 [18]。建议仍限速 ≤5 req/s、并发 1~4，失败指数退避。

**第三方参照实现**：akshare 已把互动易契约固化（`stock_irm_cninfo`）[26]，也覆盖东财研报与盈利预测（`stock_research_report_em` / `stock_profit_forecast_em`）[27]；另有独立文档完整记录了 cninfo 的 `pageSize` 硬限与 `stock` 格式要求 [28]。**可直接复用以免字段漂移**，但需注意其 `column_map` 列出的 `regulator`/`third`/`pre_disclosure` 三个值在本端点一律返回 0 条 [1]。

## Open questions

1. **股份支付费用摊销额能否结构化获得？** 东财 203 个字段里未见该科目 [5]。拿不到则维度 ① 的 back-solve 只能退化为区间判断——这直接决定该维度的呈现方式。
2. **互动易 `searchTypes=3/4`（公告/调研）的 `totalRecord` 恒为 1000**，疑为 ES 结果窗口截断而非真实总量 [13]。若确为截断，单公司公告/调研证据会不全。
3. **里程碑措辞是否构成可排序的状态机？** 即同一公司能否在 PDF 文本里追踪到「送样 → 小批量 → 量产 → 收入确认」的时序演进，而非孤立提及 [10]。未验证，需 200~300 篇更大样本实验。
4. **一致预期只有 1 个滞后月快照**（`EPS_LASTMONTHS`），更长的修正趋势需本地逐日持久化自建 [17]；是否存在按月份序列的报表名未验证。
5. **沪市互动问答接口未定位**：上证 e 互动 `sns.sseinfo.com/api/qa/search` 返回 404 [12]；沪市维度 ③ 目前无覆盖。
6. **`plate=szse`(9543) 与 `plate=sz`(8798) 相差 745 条**原因未查明，下单前需回归核对 [1]。
7. **东财与同花顺一致预期口径差异**（0.3533 vs 0.40）未对齐机构样本集，做「卖方偏离度」打分前须先定基准源 [17][24]。
8. **本报告中维度口径的量化实证均转自券商研究报告的公开转载**（兴业 2020、国盛 2021、开源 2021），非一手 PDF，且部分结论时效性存疑（SUE 在 2019-08 后有效性下滑）[29][30][31]。

## Sources

[1] cninfo 公告检索端点 `hisAnnouncement/query`（实测 2026-09-18，约 200 次请求；分页墙/静默失效/UA/category 分号/relation 两所覆盖/3000 条墙/里程碑词分布）— http://www.cninfo.com.cn/new/hisAnnouncement/query (accessed 2026-09-18)
[2] cninfo 股票代码→orgId 映射（6,255 条）— http://www.cninfo.com.cn/new/data/szse_stock.json (accessed 2026-09-18)
[3] cninfo 全文检索端点 `fulltextSearch/full`（正文级/100 页上限/offset 20000 墙/季度切片无损/type 须留空/分词分层/totalAnnouncement 不可靠）— http://www.cninfo.com.cn/new/fulltextSearch/full?searchkey=%E9%87%8F%E4%BA%A7&isfulltext=true&pageNum=1&pageSize=30 (accessed 2026-09-18)
[4] cninfo PDF 静态站（调研纪要实测下载，`Content-Type: application/pdf`）— http://static.cninfo.com.cn/finalpage/2026-09-17/1225571006.PDF (accessed 2026-09-18)
[5] 科翔股份 300903 股权激励《草案》PDF（脚注三套口径之一、y 坐标聚行坑、正则去重）— http://static.cninfo.com.cn/finalpage/2026-09-17/1225568030.PDF (published 2026-09-16, accessed 2026-09-18)
[6] 三人行 605168 股权激励《草案》PDF（列头三种叫法、`如下表所示` 引导句、期间对比式措辞）— http://static.cninfo.com.cn/finalpage/2026-09-12/1225561236.PDF (published 2026-09-11, accessed 2026-09-18)
[7] 雄帝科技 300546 股权激励《草案》PDF（透视表布局整类漏检，表头驱动列序对齐后 7/7）— http://static.cninfo.com.cn/finalpage/2026-09-16/1225566692.PDF (published 2026-09-15, accessed 2026-09-18)
[8] 7 份跨板块草案实测样本之一（7 种互不相同的指标措辞）— http://static.cninfo.com.cn/finalpage/2026-09-09/1225554666.PDF (published 2026-09-08, accessed 2026-09-18)
[9] 海辰药业 300584 调研纪要 PDF（全文检索摘要 ↔ PDF 提取文本闭环验证 4/4 一致）— http://static.cninfo.com.cn/finalpage/2026-04-28/1225228031.PDF (published 2026-04-28, accessed 2026-09-18)
[10] pypdfium2 5.13.0 API 文档（`get_text_range`；60/60 篇提取成功、0 扫描件、CJK 50~79%、0 cid）— https://pypdfium2.readthedocs.io/en/stable/python_api.html (accessed 2026-09-18)
[11] 互动易单公司问答端点 `newircs/company/question`（query string 参数、pageSize=1000 取全、提问/回复逐词对照、无日期过滤）— https://irm.cninfo.com.cn/newircs/company/question?stockcode=001283&orgId=9900048773&pageSize=100&pageNum=1 (accessed 2026-09-18)
[12] 互动易市场级全文检索端点 `newircs/index/search`（零沪市覆盖、pageSize 无上限、逐字 `<em>` 高亮）— https://irm.cninfo.com.cn/newircs/index/search?keyWord=%E9%80%81%E6%A0%B7&pageNo=1&pageSize=10 (accessed 2026-09-18)
[13] 互动易 `newircs/index/queryKeyboardInfo`（代码→secid/orgId 换取）— https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo (accessed 2026-09-18)
[14] 互动易前端产物 `app.67c497b19f5603f34c74.js`（baseURL `/newircs`、`sendType:"formdata"` → form-urlencoded 契约、needLogin 标记）— https://irm.cninfo.com.cn/app.67c497b19f5603f34c74.js (accessed 2026-09-18)
[15] 全景网问答端点 `interaction/getNewR.shtml`（`companyBaseinfoId`(pid) 必需、`companyCode` 被静默忽略）— https://ir.p5w.net/interaction/getNewR.shtml (accessed 2026-09-18)
[16] 东财一致预期报表 `RPT_WEB_RESPREDICT`（keyless；2916 只覆盖 / 6 次请求；EPS1-4 + YEAR_MARK；评级分布；目标价 73% 填充；CONCEPTINDEX_BOARD）— https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_WEB_RESPREDICT&columns=ALL&pageNumber=1&pageSize=500 (accessed 2026-09-18)
[17] 东财 F10 盈利预测 `PC_HSF10/ProfitForecast/PageAjax`（`EPS_LASTMONTHS` 月度修正 −32% 实例；`ycmx` 逐机构明细）— https://emweb.securities.eastmoney.com/PC_HSF10/ProfitForecast/PageAjax?code=SZ002708 (accessed 2026-09-18)
[18] 东财研报接口 `report/list`（4 个必填参数；qType/ratingChange 映射；目标价仅 4.7% 填充；pageSize 截断 100；历史最早 2017-01-02；限流实测）— https://reportapi.eastmoney.com/report/list?beginTime=2026-01-01&endTime=2026-09-18&pageNo=1&pageSize=3&qType=0&code=002708 (accessed 2026-09-18)
[19] 东财业绩报表 `RPT_LICO_FN_CPD`（中报/季报累计营收、归母净利润、YSTZ/SJLTZ 同比）— https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_LICO_FN_CPD&columns=ALL&filter=(SECURITY_CODE%3D%22000858%22)&pageNumber=1&pageSize=5 (accessed 2026-09-18)
[20] 东财 F10 利润表 `lrbAjaxNew`（203 项全字段利润表，`code` 须带市场前缀）— https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/lrbAjaxNew?companyType=4&reportDateType=0&reportType=1&dates=2026-06-30&code=SZ000858 (accessed 2026-09-18)
[21] 东财结构化股权激励库 `RPT_EQUITY_INCENTIVE`（19 字段全枚举，**无任何考核目标字段**；cninfo `webapi` 需 token 401）— https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_EQUITY_INCENTIVE&columns=ALL&pageNumber=1&pageSize=1 (accessed 2026-09-18)
[22] 东财个股公告接口 `np-anotice-stock/api/security/ann`（`column_name` 直接是「股权激励计划」/「股权激励计划摘要」）— https://np-anotice-stock.eastmoney.com/api/security/ann?page_size=50&page_index=1&ann_type=A&client_source=web&stock_list=300903 (accessed 2026-09-18)
[23] 新浪财务数据接口 `vDOWN_ProfitStatement`（GB18030 制表符，112 期回溯 1995）— https://money.finance.sina.com.cn/corp/go.php/vDOWN_ProfitStatement/displaytype/4/stockid/000858/ctrl/all.phtml (accessed 2026-09-18)
[24] 同花顺 F10 盈利预测与财务接口（一致预期均值/极值 + 逐机构明细；`report_yoy`/`simple_mom` 独立块；与东财口径不一致）— https://basic.10jqka.com.cn/002708/worth.html (accessed 2026-09-18)
[25] 巨潮官方前端 `history-notice.js`（26 个合法 `category` 枚举、`plate` 含 `szcy`、分号分隔符）— http://static.cninfo.com.cn/new/js/app/disclosure/notice/history-notice.js?v=20231124083101 (published 2023-11-24, accessed 2026-09-18)
[26] akshare `stock_irm_cninfo.py`（互动易契约参照实现）— https://cdn.jsdelivr.net/gh/akfamily/akshare@main/akshare/stock_feature/stock_irm_cninfo.py (published 2024-05-20, accessed 2026-09-18)
[27] akshare `stock_research_report_em.py`（研报/盈利预测契约参照实现，PDF 直链规则）— https://raw.githubusercontent.com/akfamily/akshare/main/akshare/stock_feature/stock_research_report_em.py (published 2025-02-28, accessed 2026-09-18)
[28] 第三方 cninfo 客户端文档 `use_cninfo`（pageSize 硬限 30、`stock` 格式、重复公告与扫描件）— https://raw.githubusercontent.com/rollysys/use_cninfo/main/docs/api_reference.md (published 2026-05-04, accessed 2026-09-18)
[29] 兴业证券经济与金融研究院《A股市场盈利惊喜策略全解析》（TSSUE/ALSUE ICIR、EAR3 无效、PEAD 50 日窗口与 20 日半衰期）— https://www.sohu.com/a/427106856_619348 (published 2020-10-24, accessed 2026-09-18)
[30] 国盛证券研究所《多因子系列之十五：分析师盈利修正后的股价漂移》（PFRD 90 日、正向修正有效/负向 2018 后失效）— https://www.sohu.com/a/453806808_682555 (published 2021-03-03, accessed 2026-09-18)
[31] 开源证券研究所《机构调研选股因子研究》（调研频次为同步/滞后指标、合成因子增益、>100 家机构阈值为负）— https://www.sohu.com/a/490382659_114984 (published 2021-09-17, accessed 2026-09-18)
[32] 金融界/中国基金报：开润股份 300577 股权激励考核目标收关注函（2023 营收目标仅 +0.93%）— https://www.163.com/dy/article/IAK5SA810519QIKK.html (published 2023-07-27, accessed 2026-09-18)
[33] 钛媒体：好上好 001298 股权激励仅设营收考核（净利 -79.3% 仍可「达标」）— https://www.tmtpost.com/6707064.html (published 2023-09-14, accessed 2026-09-18)
[34] 21世纪经济报道：东阿阿胶 000423 激励考核目标低于市场预期（券商 13.6/13.4 亿 vs 目标 11.2 亿）— https://www.21jingji.com/article/20240103/herald/0ceecd1b35410b8499014817ca8fa67a.html (published 2024-01-03, accessed 2026-09-18)
[35] 大丰实业 603081 关于签订战略合作框架协议的公告原文（法定披露版面；「不具有法律效力，没有强制约束力」）— https://paper.cnstock.com/html/2025-03/25/content_2039997.htm (published 2025-03-25, accessed 2026-09-18)
[36] 中国基金报：飞乐音响 600651 收入确认违规被处罚（虚增营收 1.8 亿）— https://m.sohu.com/a/349350515_465270 (published 2019-10-24, accessed 2026-09-18)
[37] 经济观察报：2026 跨界算力大单退单潮与交易所四问（海南华铁、莲花控股、亿田智能）— https://i.ifeng.com/c/8viaaNjAUmZ (published 2026-08-19, accessed 2026-09-18)
[38] 界面新闻（新浪财经转载）：苏大维格 300331 互动易误导性陈述被处罚 — https://finance.sina.com.cn/jjxw/2023-12-29/doc-imzzsywt5570278.shtml (published 2023-12-29, accessed 2026-09-18)
[39] 界面新闻（新浪财经转载）：金龙羽 002882「中试出样品」致 16 日涨 96.68% 后收关注函（阶段枚举依据）— https://finance.sina.cn/2023-02-14/detail-imyfssmn9159698.d.html (published 2023-02-14, accessed 2026-09-18)
