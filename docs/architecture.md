# Midas CN Architecture

## Miranda 映射

| Miranda 概念 | Midas CN 对应 | 说明 |
| --- | --- | --- |
| `orchestrator.py` | `midas_cn/orchestration/desk.py` | 中央调度决策流水线 |
| `config.yaml` | `config/system.toml` | 全局配置、风险阈值、股票池 |
| benchmark / individual 分组 | macro / breadth / china_market / technical / fundamental / news / sentiment / sector | A 股交易决策域分层 |
| memory / archive | `storage/archive.py` | 先保留审计归档，记忆层后续可接数据库 |
| Discord / scripts | `interfaces/` / `scripts/` | 外部入口与运维脚本分离 |

## 不复用范围

本项目不使用 Miranda 的 `agents`、`prompts`、`tools` 目录，也不复制其中实现。当前骨架使用显式 Python 类表达分析组件，后续如需 LLM 或外部数据，建议通过 `data/providers.py` 和新的 service 层接入。

## 后续接入优先级

1. A 股行情 provider：日线、分钟线、成交额、涨跌停状态、北向资金。
2. 股票池服务：指数成分、行业分类、流动性过滤、黑名单。
3. 事件服务：公告、财报、龙虎榜、监管处罚、产业政策。
4. 风控扩展：涨跌停不可交易、停牌、单票/行业/主题暴露、组合回撤。
5. 回测与评估：将 `DecisionRun` 接到可复现的信号评估流程。

## TradingAgents-CN Analyst 映射

参考 `hsliuping/TradingAgents-CN` 的 analyst 边界，本项目将职责拆成以下纯 Python 组件：

| TradingAgents-CN 模块 | Midas CN 模块 | 当前职责 |
| --- | --- | --- |
| `china_market_analyst.py` | `analysts/china_market.py` | A股制度、北向资金、融资情绪、政策主题、ST/涨跌停约束 |
| `market_analyst.py` | `analysts/technical.py` | 趋势、均线、RSI、量能、支撑压力 |
| `fundamentals_analyst.py` | `analysts/fundamental.py` | ROE、增长、估值分位、杠杆、分红 |
| `news_analyst.py` | `analysts/news.py` | 政策、公告/业绩、监管风险、事件热度 |
| `social_media_analyst.py` | `analysts/sentiment.py` | 财经社区情绪、讨论热度、KOL分歧、追高风险 |

这里没有引入 LangChain 节点、prompt 或工具绑定。分析师消费 `SecurityContext.metadata` 的结构化字段，真实数据接入时由 provider 负责填充。

## 第一阶段产物

盘后日报由 `TradingDesk.run_daily_report()` 生成：

| 要求 | 实现 |
| --- | --- |
| 交易日判断 | `calendar/a_share.py` |
| 数据质量门禁 | `quality/gates.py` |
| 指数和市场宽度快照 | `data/providers.py` + `MarketSnapshot` |
| K线与技术指标 | `data/kline.py` + `MarketDataProvider.get_daily_bars()` |
| 板块/主题/个股分析 | `analysts/` |
| A/B/C/D 机会分类 | `scanners/opportunity.py` |
| Markdown + JSON 归档 | `reports/markdown.py` + `storage/report_archive.py` |

## 第二阶段产物

日报驱动决策由 `TradingDesk.run()` 生成：

| 要求 | 实现 |
| --- | --- |
| 从日报提取候选 | `DailyReport.opportunities` |
| A/B/C/D 仓位规则 | `playbooks/positioning.py` |
| 结构化交易决策 | `decision/engine.py` |
| 决策归档 | `storage/archive.py` |
| 复盘/回测闭环 | `review/evaluator.py` |
