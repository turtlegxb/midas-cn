from __future__ import annotations

from midas_cn.models import DailyReport


class MarkdownReportRenderer:
    def render(self, report: DailyReport) -> str:
        lines = [
            f"# A股盘后日报与机会扫描 | {report.calendar.trade_date} | {report.run_id}",
            "",
            f"生成时间：{report.as_of.strftime('%Y-%m-%d %H:%M:%S')} / 质量门禁：{status_label(report.quality_gate.status.value)}",
            f"交易日状态：{reason_label(report.calendar.reason)}",
            "",
            "## 会员行动摘要",
            "",
            "| 项目 | 结论 | 动作 |",
            "| --- | --- | --- |",
        ]
        for item in report.action_summary:
            lines.append(f"| {item['item']} | {status_text(item['conclusion'])} | {item['action']} |")

        review = report.metadata.get("overall_review", {})
        lines.extend([
            "",
            "## 一句话复盘",
            "",
            f"- 今日市场：{review.get('one_line', '暂无')}",
            f"- 市场模式：{review.get('market_mode', '暂无')}",
            f"- 核心逻辑：{review.get('core_logic', '暂无')}",
            f"- 下一步：{review.get('next_step', '暂无')}",
        ])

        macro = report.metadata.get("macro_policy_analysis", {})
        lines.extend([
            "",
            "## 宏观及经济政策分析",
            "",
            macro_bullet("综合判断", macro.get("summary")),
            macro_bullet("政策基调", macro.get("policy_stance")),
            macro_bullet("流动性", macro.get("liquidity")),
            macro_bullet("财政与产业", macro.get("fiscal_industry")),
            macro_bullet("外部环境", macro.get("external")),
            macro_bullet("市场影响", macro.get("market_impact")),
            macro_bullet("风险点", macro.get("risks")),
            macro_bullet("次日跟踪", macro.get("next_watch")),
        ])

        lines.extend([
            "",
            "## 指数收盘与技术状态",
            "",
            "| 指数 | 收盘价 | 日涨跌 | 成交额/成交量 | RSI14 | EMA8 | 5日表现 | 判断 |",
            "| --- | ---: | ---: | --- | ---: | ---: | ---: | --- |",
        ])
        for item in report.metadata.get("index_state", []):
            lines.append(
                f"| {item['index']} | {item.get('close', '待接指数K线')} | {item.get('daily_change', '待接指数K线')} | "
                f"{item.get('turnover', '待接指数K线')} | {item.get('rsi14', '待接指数K线')} | "
                f"{item.get('ema8', '待接指数K线')} | {item.get('five_day', '待接指数K线')} | {item['judgement']} |"
            )
        lines.extend([
            "",
            "技术指标说明：RSI14 与 EMA8 基于可获取的指数日K复算；RSI > 70 视为偏热，RSI > 80 视为严重超买。若指数K线不可用，则仅保留市场快照派生判断。",
        ])

        lines.extend([
            "",
            "## 市场情绪与宽度",
            "",
            "| 维度 | 数值/状态 | 信号 | 来源 |",
            "| --- | --- | --- | --- |",
        ])
        for item in report.metadata.get("market_sentiment_breadth", []):
            lines.append(
                f"| {item['dimension']} | {status_text(item['value'])} | {item['signal']} | {source_label(item['source'])} |"
            )
        market_regime = report.metadata.get("market_regime_score", {})
        if market_regime:
            lines.extend([
                "",
                market_regime.get("summary", "综合评分：暂无"),
                "",
                "| 评分维度 | 得分 | 信号 | 来源 |",
                "| --- | ---: | --- | --- |",
            ])
            for item in market_regime.get("dimensions", []):
                lines.append(
                    f"| {item.get('dimension')} | {float(item.get('score', 0)):.2f}/1 | "
                    f"{item.get('signal')} | {source_label(item.get('source'))} |"
                )

        theme_rotation = report.metadata.get("theme_rotation", {})
        lines.extend([
            "",
            "## 板块轮动与主题深挖",
            "",
            f"- 轮动阶段：{theme_rotation.get('stage', '暂无')}",
            f"- 综合判断：{theme_rotation.get('summary', '暂无')}",
        ])
        if theme_rotation.get("main_themes"):
            lines.extend([
                "",
                "| 主线候选 | 强度分 | 命中 | 涨停 | 炸板 | 换手 | 代表标的 | 判断 |",
                "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
            ])
            for item in theme_rotation.get("main_themes", []):
                lines.append(theme_row(item))
        if theme_rotation.get("watch_themes"):
            lines.extend([
                "",
                "| 轮动观察 | 强度分 | 命中 | 涨停 | 炸板 | 换手 | 代表标的 | 判断 |",
                "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
            ])
            for item in theme_rotation.get("watch_themes", [])[:5]:
                lines.append(theme_row(item))
        if theme_rotation.get("risk_themes"):
            lines.extend([
                "",
                "| 分歧/风险主题 | 强度分 | 跌停 | 炸板 | 代表标的 | 判断 |",
                "| --- | ---: | ---: | ---: | --- | --- |",
            ])
            for item in theme_rotation.get("risk_themes", [])[:5]:
                symbols = symbol_list(item.get("symbols", []))
                lines.append(
                    f"| {item.get('theme')} | {item.get('score')} | {item.get('limit_down')} | "
                    f"{item.get('broken_limit_up')} | {symbols} | {item.get('judgement')} |"
                )

        xueqiu = report.metadata.get("xueqiu_tracking", {})
        lines.extend([
            "",
            "## 雪球大V与持仓跟踪",
            "",
            f"- 状态：{status_label(xueqiu.get('status', 'missing'))}",
            f"- 综合判断：{clean_markdown_field(xueqiu.get('summary'))}",
        ])
        if xueqiu.get("overlaps"):
            lines.extend([
                "",
                "| 重合标的 | 名称 | 雪球热度 | 命中机会 | 命中选股池 |",
                "| --- | --- | ---: | --- | --- |",
            ])
            for item in xueqiu.get("overlaps", [])[:10]:
                lines.append(
                    f"| {item.get('symbol')} | {item.get('name')} | {item.get('xueqiu_mentions')} | "
                    f"{yes_no(item.get('in_opportunity'))} | {yes_no(item.get('in_stock_pool'))} |"
                )
        if xueqiu.get("confirmed_position_changes"):
            lines.extend([
                "",
                "| 公开组合 | 标的 | 动作 | 调整前 | 调整后 | 时间 |",
                "| --- | --- | --- | ---: | ---: | --- |",
            ])
            for item in xueqiu.get("confirmed_position_changes", [])[:10]:
                lines.append(
                    f"| {item.get('portfolio')} | {item.get('symbol')} {item.get('name')} | {item.get('action')} | "
                    f"{format_weight(item.get('before'))} | {format_weight(item.get('after'))} | {item.get('changed_at') or '未知'} |"
                )

        lines.extend([
            "## 机会评级",
        ])
        for grade in ("A", "B"):
            grade_items = [item for item in report.opportunities if item.grade.value == grade]
            if not grade_items:
                continue
            lines.extend(["", f"### {grade}类机会"])
            for item in grade_items:
                lines.extend(opportunity_card(item))
        hidden = report.metadata.get("hidden_opportunities", {})
        if hidden.get("below_b_count"):
            lines.append(f"\n注：已隐藏 {hidden.get('below_b_count')} 个B级以下标的。")

        lines.extend([
            "",
            "## 仓位与调仓建议",
            "",
            f"- 核心仓：{report.position_plan.core_position_range[0]:.0%}-{report.position_plan.core_position_range[1]:.0%}",
            f"- 卫星仓：{report.position_plan.satellite_position_range[0]:.0%}-{report.position_plan.satellite_position_range[1]:.0%}",
            f"- 现金：{report.position_plan.cash_range[0]:.0%}-{report.position_plan.cash_range[1]:.0%}",
            f"- 单票卫星上限：{report.position_plan.max_single_satellite:.0%}",
        ])
        for note in report.position_plan.notes:
            lines.append(f"- {note}")

        lines.extend([
            "",
            "## 明日三情景预案",
            "",
            "| 情景 | 触发 | 动作 |",
            "| --- | --- | --- |",
        ])
        for item in report.next_day_scenarios:
            lines.append(f"| {item['scenario']} | {item['trigger']} | {item['action']} |")

        lines.extend([
            "",
            "## 风险提示",
            "",
        ])
        lines.extend(f"- {item}" for item in report.risk_warnings)
        lines.extend([
            "",
            "## 数据来源声明",
            "",
            "| 数据类型 | 来源 | 状态 | 错误摘要 |",
            "| --- | --- | --- | --- |",
        ])
        details = {
            (item.get("data"), item.get("source")): item
            for item in report.metadata.get("source_results", [])
        }
        for item in report.source_audit:
            detail = details.get((item["data"], item["source"]), {})
            error = (detail.get("error_message") or "")[:120].replace("|", "/")
            if error:
                error = error_label(error)
            if error and item["status"] in {"failed", "partial"}:
                error += "；详见结构化明细"
            lines.append(
                f"| {item['data']} | {source_label(item['source'])} | {status_label(item['status'])} | {error} |"
            )

        lines.extend([
            "",
            "## 质量与缺失提示",
            "",
            f"- 状态：{status_label(report.quality_gate.status.value)}",
            f"- 缺失：{', '.join(report.quality_gate.missing_items) if report.quality_gate.missing_items else '无'}",
            f"- 警告：{', '.join(report.quality_gate.warnings[:8]) if report.quality_gate.warnings else '无'}",
        ])

        return "\n".join(lines) + "\n"


def status_label(value: object) -> str:
    return {
        "success": "成功",
        "failed": "失败",
        "fallback": "使用回退源",
        "partial": "部分成功",
        "missing": "缺失",
        "PASS": "通过",
        "WARN": "警告",
        "FAIL": "失败",
        "有效": "有效",
    }.get(str(value), str(value))


def reason_label(value: object) -> str:
    return {
        "weekend": "周末",
        "exchange_holiday": "交易所休市",
        "trading_day_not_in_report_schedule": "交易日，非计划报告日",
        "scheduled_post_close_report_day": "计划盘后报告日",
    }.get(str(value), str(value))


def source_label(value: object) -> str:
    text = str(value)
    mapping = {
        "AShareCalendar": "A股交易日历",
        "MarketSnapshot": "市场快照",
        "SourceResult": "新闻源检查",
        "akshare.stock_main_fund_flow": "东方财富主力资金流",
        "akshare.stock_individual_fund_flow_rank(今日)": "东方财富个股资金排行",
        "akshare.stock_zh_a_spot_em": "东方财富A股实时行情",
        "akshare.stock_main_fund_flow + akshare.stock_zh_a_spot_em": "东方财富资金流与行情",
        "akshare.stock_main_fund_flow + sina.Market_Center.getHQNodeData": "东方财富资金流与新浪行情",
        "sina.Market_Center.getHQNodeData": "新浪A股实时行情",
        "akshare.stock_zt_pool_em": "东方财富涨停池",
        "akshare.stock_zt_pool_dtgc_em": "东方财富跌停池",
        "akshare.stock_zt_pool_zbgc_em": "东方财富炸板池",
        "AkShareMarketDataProvider": "行情数据服务",
        "MockMarketDataProvider": "模拟数据服务",
        "mock_fallback": "模拟回退数据",
        "eastmoney_global": "东方财富全球资讯",
        "cctv": "央视新闻",
        "eastmoney_stock_news": "东方财富个股新闻",
        "eastmoney_stock_notice": "东方财富公告",
        "cninfo_disclosure": "巨潮资讯公告",
        "akshare_stock_zh_a_hist": "东方财富日K行情",
        "akshare_stock_zh_a_daily": "新浪日K行情",
        "akshare_stock_zh_a_hist_tx": "腾讯日K行情",
        "llm_report_synthesis": "大模型复盘服务",
        "xueqiu": "雪球公开数据",
        "未获取": "未获取",
    }
    if text in mapping:
        return mapping[text]
    if text.startswith("akshare."):
        return "第三方行情接口"
    return text


def pool_label(value: object) -> str:
    return {
        "main_net_inflow_top20": "主力净额流入前二十",
        "small_float_net_inflow_top20": "中小流通市值资金流入前二十",
        "turnover_top20": "换手率前二十",
        "limit_up": "当日涨停",
        "limit_down": "当日跌停",
        "broken_limit_up": "当日炸板",
    }.get(str(value), str(value))


def status_text(value: object) -> str:
    text = str(value)
    return (
        text.replace("success", "成功")
        .replace("failed", "失败")
        .replace("fallback", "回退")
        .replace("missing", "缺失")
        .replace("PASS", "通过")
        .replace("WARN", "警告")
        .replace("FAIL", "失败")
        .replace("weekend", "周末")
        .replace("exchange_holiday", "交易所休市")
        .replace("trading_day_not_in_report_schedule", "交易日，非计划报告日")
        .replace("scheduled_post_close_report_day", "计划盘后报告日")
    )


def error_label(value: object) -> str:
    text = str(value)
    if "RemoteDisconnected" in text or "Remote end closed connection" in text:
        return "远端连接主动断开"
    if "NameResolutionError" in text or "Failed to resolve" in text:
        return "网络域名解析失败"
    if "timed out" in text or "timeout" in text.lower():
        return "请求超时"
    if "ConnectionError" in text:
        return "网络连接失败"
    return text


def macro_bullet(label: str, value: object) -> str:
    return f"- {label}：{clean_markdown_field(value)}"


def clean_markdown_field(value: object) -> str:
    text = str(value or "暂无")
    text = text.replace("|", "/")
    cleaned = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        if line.startswith(("-", "*")):
            line = line[1:].strip()
        cleaned.append(line)
    return " ".join(cleaned) if cleaned else "暂无"


def yes_no(value: object) -> str:
    return "是" if bool(value) else "否"


def format_weight(value: object) -> str:
    if value in (None, ""):
        return "未知"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return str(value)


def symbol_list(items: list[dict]) -> str:
    return "；".join(f"{item.get('symbol')} {item.get('name')}" for item in items[:4]) or "无"


def theme_row(item: dict) -> str:
    return (
        f"| {item.get('theme')} | {item.get('score')} | {item.get('hits')} | "
        f"{item.get('limit_up')} | {item.get('broken_limit_up')} | {item.get('turnover')} | "
        f"{symbol_list(item.get('symbols', []))} | {item.get('judgement')} |"
    )


def opportunity_card(item) -> list[str]:
    pools = "、".join(item.evidence.get("pools", [])) or "选股池"
    technical = technical_brief(item.evidence.get("technical", {}), item.evidence.get("technical_score"))
    return [
        "",
        f"**{item.symbol} {item.name}｜{item.grade.value}｜{item.score:.3f}**",
        f"- 板块：{item.evidence.get('sector') or '未分类'}",
        f"- 入选：{pools}",
        f"- 技术：{technical}",
        f"- 触发：{strip_pool_prefix(item.trigger)}",
        f"- 失效：{item.invalidation}",
        f"- 动作：{item.action}",
    ]


def strip_pool_prefix(text: str) -> str:
    marker = "；"
    if text.startswith("命中选股池：") and marker in text:
        return text.split(marker, 1)[1]
    return text


def technical_brief(technical: dict, technical_score: object) -> str:
    if not technical:
        return "技术面未确认"
    parts = []
    trend = float(technical.get("trend_strength") or 0)
    ma_alignment = float(technical.get("ma_alignment") or 0)
    rsi = technical.get("rsi")
    volume_ratio = technical.get("volume_ratio")
    support = technical.get("support")
    ema21 = technical.get("ema21")
    if trend > 0.18:
        parts.append("趋势偏强")
    elif trend < -0.12:
        parts.append("趋势偏弱")
    else:
        parts.append("趋势震荡")
    if ma_alignment > 0.3:
        parts.append("均线多头")
    elif ma_alignment < -0.3:
        parts.append("均线空头")
    if volume_ratio is not None:
        parts.append(f"量比{float(volume_ratio):.2f}")
    if rsi is not None:
        parts.append(f"RSI {float(rsi):.1f}")
    if support is not None:
        parts.append(f"支撑{float(support):.2f}")
    if ema21 is not None:
        parts.append(f"21日线{float(ema21):.2f}")
    if technical_score is not None:
        parts.append(f"技术分{float(technical_score):+.3f}")
    return "，".join(parts)
