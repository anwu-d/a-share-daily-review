---
topic: A股「业务转型 + 业绩尚未被市场充分定价」模块 —— 数据源可达性与分析口径
date: 2026-09-18
depth: standard
---

# Research Brief

## 研究问题（refined question）

在 `E:\Project\stock`（本地 A 股短线每日复盘引擎 + 静态结论页）里新增一个模块，
用于识别并跟踪**正在做业务转型、且转型业绩尚未被市场充分定价**的股票。

用户给定的五个分析维度是模块的内容骨架：

1. **股权激励 / 员工持股的考核目标**（巨潮搜「公司名 + 激励计划」）
   → 净利润目标绝对值、同比增速、考核年限，与当前业绩的落差
2. **最新财报实际数据**（巨潮「定期报告」）
   → 用中报/季报倒算下半年需完成多少利润才能达标，缺口是否合理
3. **公司在互动易 / 业绩说明会的口径**（深交所互动易、全景网）
   → 对「量产时间」「订单进展」「收入占比」的表述，是否用「研发中」「不确定性」等模糊词汇回避
4. **转型业务的真实进展节点**（异动公告、调研纪要、重大合同公告）
   → 区分「送样 / 小批量 / 量产」；送样 ≠ 订单，订单 ≠ 收入
5. **机构研报与公司口径的偏差**

**本文档要回答的不是「怎么设计页面」，而是「哪些能自动化、哪些必须人工」** ——
即每个维度背后数据源的真实可达性、接口契约、字段结构、反爬与许可限制，
以及维度 ①/② 所需数字能否被机器可靠抽取。

## 已知事实（orchestrator 已实测，2026-09-18）

| 源 | 方法 | 结果 |
|---|---|---|
| `www.cninfo.com.cn/new/hisAnnouncement/query` | POST | **200，真实数据**。搜「股权激励」`totalAnnouncement=3244`，返回 `secCode/secName/announcementTitle/announcementTime` 等 |
| 同上，`category=category_bndbg_szsh`（定期报告） | POST | **200**，`totalAnnouncement=11081`，含「2026年半年度报告」 |
| `irm.cninfo.com.cn/newircs/index/search` | POST | **200 但 `totalRecord=0`**，说明端点存在、请求参数不对 |
| `irm.cninfo.com.cn/newircs/company/getCompanyList` | GET | 404 |
| `reportapi.eastmoney.com/report/list` | GET | **400 `Required String parameter 'beginTime' is not present`** → 端点存在、需正确参数 |
| 外网 baseline（baidu.com） | GET | 200，本环境可直连外网 |

环境限制：本会话**没有 WebSearch 工具**，只有 WebFetch + Bash。子代理可用
`curl` / Python `requests` 直连接口做实测（这是最强证据），或用
`https://html.duckduckgo.com/html/?q=...` 与 `https://www.bing.com/search?q=...&setlang=zh` 做搜索。

项目已有先例：`engine/_extract_pdfs.py`、`engine/_parse_mhtml.py` 及
`data/cache/pdftext/*.txt` —— 说明「解析外部 PDF/网页取文本」在本项目已跑通过。

## Scope boundaries

**In（要研究）**
- 巨潮公告检索 API 的完整请求/响应契约：全部可用 `category` / `column` / `searchkey` 组合、
  分页上限、`adjunctUrl` 形态、是否需要 cookie/referer、被限流的迹象
- 「投资者关系活动记录表」（调研纪要）在巨潮的分类或检索方式
- 互动易问答接口的正确契约（搜索 + 按公司取问答列表 + 回答正文）
- 定期报告与财务数据：是否必须解析 PDF，还是存在结构化来源（巨潮 XBRL、
  东财财报 API、新浪/同花顺财务接口）
- 股权激励方案里的「净利润考核目标 / 增速 / 考核年限」在公告中的位置与可抽取性
- 机构研报接口契约（东财 reportapi 正确参数）、是否有一致预期（consensus）数据
- 「未被充分定价 / 预期差」在 A 股语境下的**可计算口径**（估值分位、盈利预测上调、
  超预期幅度 SUE、公告后漂移 PEAD 等）
- A 股「转型题材」的已知陷阱与分析纪律（送样/小批量/量产的口径差别，
  蹭概念 vs 真实收入确认）

**Out（不研究）**
- 具体某只股票的基本面结论
- 页面视觉设计与交互
- 存量引擎（K线形态/宏观择时）的改造
- 任何需要付费数据商（Wind/iFinD）才能拿到的字段 —— 只记录「只能人工或付费」

**假设**
- 目标是「尽量自动、拿不到的老实标『需人工』」，而不是假装全自动
- 模块服务的是短线复盘页，因此时效性与「最近 30~90 天有新公告/新问答」比长历史更重要
- 合规：只做公开信息检索与展示，不构成投资建议（与项目既有口径一致）

## Angles

- **F1** 巨潮资讯网公告检索 API 的完整契约（含各 `category`、调研纪要、分页与限流）
- **F2** A 股公告 PDF / 财报数字的结构化抽取路径（激励考核目标、营收净利的取数方案）
- **F3** 深交所互动易与全景网的问答数据契约
- **F4** 机构研报与一致预期数据源契约（东财 reportapi 等）
- **F5** 「业务转型 + 业绩未充分定价」的量化口径与 A 股实践陷阱

## 预算

standard：round 1 五个子代理并行，每个最多 6 次检索；最多 1 轮补研；目标 15+ 源。
