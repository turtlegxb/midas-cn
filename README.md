# Midas CN

Midas CN 是一个面向 A 股的 trading 决策系统骨架。架构参考 Miranda 的“配置驱动 + 中央调度 + 分组研究 + 决策归档”思路，但不复用 Miranda 的 `agents`、`prompts`、`tools` 目录或实现。

## 架构

```text
midas_cn/
├── analysts/        # A 股领域研究模块：宏观、宽度、本土市场、技术、基本面、新闻、情绪、行业轮动
├── calendar/        # A 股交易日与日报窗口判断
├── data/            # 数据源接口与 provider 适配
├── decision/        # 评分、共识聚合、交易计划生成
├── interfaces/      # CLI / 后续 API / bot 入口
├── orchestration/   # 中央调度器和每日决策流水线
├── playbooks/       # 仓位与交易纪律规则
├── quality/         # 数据质量门禁 PASS/WARN/FAIL
├── reports/         # 盘后日报构建与 Markdown 渲染
├── review/          # 决策复盘与回测评估接口
├── risk/            # 仓位、止损、风控约束
├── scanners/        # A/B/C/D 机会扫描与分级
├── storage/         # 决策记录归档
└── universe/        # 股票池与 A 股代码规范化
```

## 快速运行

```bash
python3 -m midas_cn.interfaces.cli --symbols 600519.SH 300750.SZ
```

默认使用 mock 数据源，可在无 API key 的环境里验证流程。后续接入真实行情、财务、公告、新闻和交易接口时，只需要实现 `midas_cn.data.providers.MarketDataProvider`。

### K 线数据源

当前已有两种 K 线模式：

- `mock`：内置生成可复现的日 K，用于本地测试，默认会补齐 `EMA8/21/55`、`RSI14`、量比、支撑和压力。
- `akshare`：可选真实 A 股日 K 源，配置 `data.provider = "akshare"` 后使用 `akshare.stock_zh_a_hist`。

安装可选依赖：

```bash
pip install '.[kline]'
```

配置项：

```toml
[data]
provider = "akshare"
kline_period = "daily"
kline_adjust = "qfq"
kline_lookback = 90
```

带归档运行会同时生成：

- `output/reports/chinese_report_<run_id>.md`
- `output/reports/chinese_report_<run_id>.json`
- `output/decisions/<run_id>.json`

## 验证

```bash
python3 -m unittest discover -s tests
python3 -m compileall midas_cn scripts tests
```

## 当前流水线

### 第一阶段：盘后日报生成器

1. 读取 `config/system.toml`
2. 判断 A 股交易日和周一至周四盘后报告窗口
3. 规范化股票池
4. 生成市场状态快照
5. 汇总宏观、市场宽度、A股本土制度/资金面、技术面、基本面、新闻事件、社交情绪和行业轮动分析
6. 执行数据质量门禁 `PASS | WARN | FAIL`
7. 生成 A/B/C/D 机会分级
8. 生成仓位区间、明日三情景预案、风险提示和来源审计
9. 归档 Markdown 与 JSON 日报

### 第二阶段：日报驱动决策

1. 从日报机会池派生交易候选
2. 按 A/B/C/D 与质量门禁应用仓位纪律
3. 输出结构化 `TradeDecision`
4. 归档决策 JSON
5. 通过 `DecisionReviewEvaluator` 回填实际价格，形成复盘/回测闭环
