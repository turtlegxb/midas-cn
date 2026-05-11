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

## 环境构建

项目现在同时包含 Python 报告流水线和 Node/Playwright 雪球抓取器。服务器首次部署建议按下面顺序执行。

### 1. Python 环境

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[kline]'
```

说明：

- `.[kline]` 会安装 `akshare`，用于腾讯、东方财富、新浪等行情和新闻源适配。
- 如只跑单元测试或 mock 流程，也可以用 `python -m pip install -e .`。

### 2. Node/Playwright 环境

雪球关注流抓取依赖 Node.js、Playwright 和 stealth 插件：

```bash
npm install
npx playwright install chromium
```

如果服务器是最小化 Linux 镜像，Playwright 可能还需要系统依赖：

```bash
npx playwright install-deps chromium
```

### 3. 环境变量

复制示例文件后按需填写：

```bash
cp .env.example .env
```

至少建议配置：

```bash
export XQ_A_TOKEN="雪球 cookies 里的 xq_a_token"
```

可选配置：

```bash
export XUEQIU_COOKIE="完整雪球 Cookie，兼容旧的单用户 timeline/组合接口"
export OPENAI_API_KEY="..."
```

说明：

- `XQ_A_TOKEN` 是新的雪球关注流主配置，浏览器登录雪球后在 Cookies 中复制 `xq_a_token` 的值即可。
- 如果同时配置 `XUEQIU_COOKIE`，系统会继续兼容老的公开组合和指定大V接口；未配置时会用 `XQ_A_TOKEN` 自动组装基础 Cookie。
- LLM 不影响数据抓取。未配置 LLM 时，报告会使用规则回退；配置后会生成一句话复盘、宏观政策分析、个股新闻解读和雪球 KOL 按 ticker 聚合观点。

### 4. 快速自检

```bash
node scripts/xueqiu_fetcher.js following 5
.venv/bin/python -m unittest discover -s tests
```

在 macOS sandbox 或部分 CI 环境中，Playwright 可能因系统权限无法启动 Chromium；服务器正常 shell 环境一般不需要额外处理。

## 快速运行

```bash
.venv/bin/python -m midas_cn.interfaces.cli report --symbols 600519.SH 300750.SZ
```

生成报告时 CLI 会在终端输出中文进度条和当前步骤说明；如需静默运行，可追加 `--quiet`。

查看本地缓存状态：

```bash
.venv/bin/python -m midas_cn.interfaces.cli cache status
.venv/bin/python -m midas_cn.interfaces.cli cache clear --target K线
.venv/bin/python -m midas_cn.interfaces.cli sources check --symbol 600519.SH
.venv/bin/python -m midas_cn.interfaces.cli report --trade-date 2026-05-08 --symbols 600519.SH
.venv/bin/python -m midas_cn.interfaces.cli report --trade-date 2026-05-08 --refresh xueqiu,news
.venv/bin/python -m midas_cn.interfaces.cli review
.venv/bin/python -m midas_cn.interfaces.cli review --report latest --horizon-days 1
.venv/bin/python scripts/build_ths_sector_cache.py --pool-date 20260508 --skip-board-lists --quiet
```

默认使用 mock 数据源，可在无 API key 的环境里验证流程。后续接入真实行情、财务、公告、新闻和交易接口时，只需要实现 `midas_cn.data.providers.MarketDataProvider`。

复盘命令默认读取 `output/reports` 下最近 30 天的 `chinese_report_*.json`，逐份复盘日报机会池；也可以用 `--report latest` 只复盘最新日报，或传入某个日报 JSON 路径。复盘会计算日报产出后 1/3/5 个交易日收益、观察窗口最大回撤和回撤风险，并归档到 `output/reviews`。

### 单步刷新与重跑

`report --refresh` 可在报告生成前清理指定缓存，相当于重跑某些数据步骤后再组装报告。常用别名：

- `xueqiu`：雪球关注流与公开组合缓存。
- `news`：个股新闻和市场新闻缓存。
- `kline`：K线缓存。
- `index`：指数状态缓存。
- `pools`：选股池缓存。
- `all`：全部缓存。

示例：

```bash
# 只重抓雪球，再重新组装报告
.venv/bin/python -m midas_cn.interfaces.cli report --refresh xueqiu

# 只重抓新闻，包括市场新闻和个股新闻
.venv/bin/python -m midas_cn.interfaces.cli report --refresh news

# 同时刷新雪球和新闻，适合调试 KOL 总结和个股新闻解读
.venv/bin/python -m midas_cn.interfaces.cli report --refresh xueqiu,news

# 刷新全部缓存后完整重跑
.venv/bin/python -m midas_cn.interfaces.cli report --refresh all
```

注意：`--refresh` 不是只执行某一个 step 后退出，而是先清理指定缓存，再完整执行报告流水线。这样可以复用其他未过期缓存，同时保证最终报告仍然是完整一致的。

### K 线数据源

当前已有两种 K 线模式：

- `mock`：内置生成可复现的日 K，用于本地测试，默认会补齐 `EMA8/21/55`、`RSI14`、量比、支撑和压力。
- `akshare`：可选真实 A 股日 K 源，配置 `data.provider = "akshare"` 后优先使用腾讯日 K，失败后回退东方财富和新浪。

安装可选依赖：

```bash
python -m pip install -e '.[kline]'
```

配置项：

```toml
[data]
provider = "akshare"
kline_period = "daily"
kline_adjust = "qfq"
kline_lookback = 90
timeout_seconds = 12

[news]
opportunity_news_sort = "hybrid"

[llm]
enabled = true
opportunity_news_enabled = true

[xueqiu]
enabled = true
token_env = "XQ_A_TOKEN"
cookie_env = "XUEQIU_COOKIE"
following_enabled = true
following_max_posts = 100
following_timeout_seconds = 45
lookback_days = 7

[pools]
build_if_missing = true
technical_limit = 20
industry_enrich_limit = 8
industry_enrich_timeout_seconds = 8

[cache]
data_dir = "output/cache"
ttl_seconds = 86400

[ths_cache]
path = "output/cache/ths_sector/sector_cache.json"
ttl_seconds = 86400
max_industries = 90
max_concepts = 390
request_interval_seconds = 0.2
```

同花顺行业/概念缓存建议由 cron 在报告前刷新，示例见 `config/crontab.example`。当前未接 iFinD 官方 API 时，脚本按 `--symbols` 或 `--pool-date` 中的股票逐只抓同花顺 F10 题材接口，并用已有行业源兜底；正式报告会优先读取该缓存补齐个股行业和概念，缓存缺失时再使用行情快照、巨潮和东方财富的有限兜底。

### 雪球关注流与 KOL 观点

项目内置 `scripts/xueqiu_fetcher.js`，用于抓取登录账号关注流。报告生成时 `XueqiuTracker` 会调用该脚本，默认抓最近 100 条关注动态。

直接测试：

```bash
node scripts/xueqiu_fetcher.js following 100
```

服务器环境建议同时配置 `XQ_A_TOKEN` 和完整 `XUEQIU_COOKIE`，脚本会把完整 Cookie 透传给 Playwright。若服务器访问雪球首页较慢，可在 `[xueqiu]` 中调高 `following_navigation_timeout_ms`；脚本会优先使用轻量 API/个股页初始化，减少首页资源加载导致的超时。老的单用户 timeline 接口容易触发阿里云 WAF，关注流抓取成功时可作为主路径使用。

抓取结果会区分帖子类型：

- `short_post`：短评，雪球原始 `type = "0"`。
- `long_post`：长帖，雪球原始 `type = "2"`。
- `article`：长文，雪球原始 `type = "3"` 或带 `title/rawTitle`。
- `repost`：转发，存在 `retweeted_status`。

报告聚合只保留上一个交易日至今的原发内容，剔除转发；涉及 `.US` 的美股标的会从雪球观点、重叠标的和公开组合调仓中剔除。

对 `long_post` 和 `article`，抓取器会额外请求 `statuses/show.json?id=<post_id>` 补齐原文，并输出：

- `full_text`：完整纯文本。
- `full_raw_text_html`：完整 HTML 原文。
- `detail_fetched`：详情是否成功。
- `detail_error`：详情失败原因。

报告会按 ticker 聚合 KOL 观点，重点展示多位 KOL 重叠提及的标的。KOL 总结只保留上一个交易日 00:00 至报告生成时的内容，并在聚合前丢弃 `repost` 转发内容。LLM 可用时，会基于 `full_text` 生成观点摘要、KOL overlap、看多/看空/中性判断和风险提示；不可用时保留原帖摘要、规则情绪和链接作为回退。最终报告只展示看多/看空/分歧标的，丢弃中性结论。

带归档运行会同时生成：

- `output/reports/chinese_report_<run_id>.md`
- `output/reports/chinese_report_<run_id>.json`
- `output/decisions/<run_id>.json`

## 验证

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall midas_cn scripts tests
node --check scripts/xueqiu_fetcher.js
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
5. 通过 `DecisionReviewEvaluator` 回填实际价格，形成交易决策复盘闭环
6. 通过 `ReportReviewEvaluator` 复盘最近 30 天日报机会池，统计 1/3/5 日收益和最大回撤风险
