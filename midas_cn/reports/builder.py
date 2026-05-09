from __future__ import annotations

from collections import Counter
from datetime import datetime

from midas_cn.models import (
    DailyReport,
    MarketSnapshot,
    Opportunity,
    OpportunityGrade,
    PositionPlan,
    QualityGate,
    SourceResult,
    SourceStatus,
    StockPool,
    StockPoolEntry,
    TradeDecision,
    TradingCalendarCheck,
)
from midas_cn.data.news import source_results_to_dicts
from midas_cn.llm.synthesis import ReportSynthesisService
from midas_cn.pools.builder import to_number


def format_pct(value: float | None) -> str:
    if value is None:
        return "待接指数K线"
    return f"{value:+.2%}"


def format_turnover(amount: float | None, volume: float | None) -> str:
    if amount:
        return f"{amount / 100_000_000:.1f}亿元"
    if volume:
        return f"{volume / 100_000_000:.2f}亿股"
    return "待接指数K线"


class DailyReportBuilder:
    def __init__(self, llm_synthesis: ReportSynthesisService | None = None):
        self.llm_synthesis = llm_synthesis or ReportSynthesisService()

    def build(
        self,
        run_id: str,
        as_of: datetime,
        calendar: TradingCalendarCheck,
        quality_gate: QualityGate,
        market: MarketSnapshot,
        opportunities: list[Opportunity],
        position_plan: PositionPlan,
        decisions: list[TradeDecision],
        universe: list[str],
        market_news_results: list[SourceResult] | None = None,
        stock_pools: list[StockPool] | None = None,
        xueqiu_snapshot: object | None = None,
        technical_profiles: dict[str, dict] | None = None,
        index_profiles: dict[str, dict] | None = None,
    ) -> DailyReport:
        ranked_opportunities = sorted(
            self._pool_opportunities(stock_pools or [], quality_gate, technical_profiles or {}) or opportunities,
            key=lambda item: item.score,
            reverse=True,
        )
        hidden_opportunities = [item for item in ranked_opportunities if item.grade not in {OpportunityGrade.A, OpportunityGrade.B}]
        report_opportunities = [item for item in ranked_opportunities if item.grade in {OpportunityGrade.A, OpportunityGrade.B}]
        grade_counts = Counter(item.grade.value for item in report_opportunities)
        hidden_count = len(hidden_opportunities)
        overall_review = self._overall_review(market, report_opportunities, quality_gate)
        index_state = self._index_state(market, index_profiles or {})
        sentiment_breadth = self._market_sentiment_breadth(calendar, market, market_news_results or [])
        pool_analysis = self._stock_pool_analysis(stock_pools or [])
        theme_rotation = self._theme_rotation_analysis(stock_pools or [], report_opportunities)
        xueqiu_tracking = self._xueqiu_tracking_analysis(xueqiu_snapshot, stock_pools or [], report_opportunities)
        market_regime_score = self._market_regime_score(calendar, market, index_state, theme_rotation, market_news_results or [])
        overall_review = {
            **overall_review,
            "market_mode": market_regime_score["mode"],
            "risk_label": market_regime_score["risk_label"],
            "regime_summary": market_regime_score["summary"],
            "market_regime_score": market_regime_score,
        }
        action_summary = [
            {
                "item": "市场模式",
                "conclusion": f"{market_regime_score['mode']}，{market_regime_score['risk_label']}",
                "action": self._market_action(market),
            },
            {"item": "质量门禁", "conclusion": quality_gate.status.value, "action": self._quality_action(quality_gate)},
            {
                "item": "机会分布",
                "conclusion": ", ".join(f"{grade}:{grade_counts.get(grade, 0)}" for grade in ["A", "B"])
                + (f"，B以下已隐藏:{hidden_count}" if hidden_count else ""),
                "action": "只展示A/B机会，B以下不展示。",
            },
            {
                "item": "模型仓位",
                "conclusion": self._position_summary(position_plan),
                "action": "; ".join(position_plan.notes),
            },
        ]
        synthesis = self.llm_synthesis.synthesize(
            trade_date=calendar.trade_date,
            base_review=overall_review,
            market=market,
            index_state=index_state,
            sentiment_breadth=sentiment_breadth,
            theme_rotation=theme_rotation,
            opportunities=report_opportunities,
            market_news_results=market_news_results or [],
        )
        overall_review = {**overall_review, "one_line": synthesis.one_line_review}
        return DailyReport(
            run_id=run_id,
            as_of=as_of,
            calendar=calendar,
            quality_gate=quality_gate,
            market_snapshot=market,
            action_summary=action_summary,
            opportunities=report_opportunities,
            position_plan=position_plan,
            next_day_scenarios=self._scenarios(),
            risk_warnings=self._risk_warnings(quality_gate, report_opportunities),
            source_audit=self._source_audit(
                universe,
                opportunities,
                market_news_results or [],
                stock_pools or [],
                synthesis.source_result,
                getattr(xueqiu_snapshot, "source_result", None),
            ),
            decisions=decisions,
            metadata={
                "universe": universe,
                "source_results": self._source_results(
                    opportunities,
                    market_news_results or [],
                    stock_pools or [],
                    synthesis.source_result,
                    getattr(xueqiu_snapshot, "source_result", None),
                ),
                "overall_review": overall_review,
                "index_state": index_state,
                "market_sentiment_breadth": sentiment_breadth,
                "market_regime_score": market_regime_score,
                "stock_pool_analysis": pool_analysis,
                "theme_rotation": theme_rotation,
                "xueqiu_tracking": xueqiu_tracking,
                "macro_policy_analysis": synthesis.macro_policy_analysis,
                "llm_synthesis": {
                    "status": synthesis.source_result.status.value,
                    "provider": synthesis.source_result.provider,
                    "error_message": synthesis.source_result.error_message,
                },
                "technical_coverage": self._technical_coverage(technical_profiles or {}),
                "hidden_opportunities": {
                    "below_b_count": len(hidden_opportunities),
                    "below_b_symbols": [
                        {"symbol": item.symbol, "name": item.name, "score": item.score}
                        for item in hidden_opportunities
                    ],
                },
            },
        )

    def _market_mode(self, market: MarketSnapshot) -> str:
        if market.benchmark_trend > 0.15 and market.breadth_score > 0.55:
            return "风险偏好分化转强"
        if market.benchmark_trend < -0.15:
            return "风险偏好收缩"
        return "震荡观察"

    def _overall_review(
        self,
        market: MarketSnapshot,
        opportunities: list[Opportunity],
        quality_gate: QualityGate,
    ) -> dict[str, str]:
        grade_counts = Counter(item.grade.value for item in opportunities)
        mode = self._market_mode(market)
        if grade_counts.get("A", 0):
            next_step = "优先执行A类计划，B类等待回踩确认。"
        elif grade_counts.get("B", 0):
            next_step = "不追高，等待B类标的回踩承接和量能二次确认。"
        elif grade_counts.get("C", 0):
            next_step = "维持观察池，等待主题连续性、成交额和新闻催化二次确认。"
        else:
            next_step = "机会质量不足，维持现金和防守仓位。"
        if market.breadth_score >= 0.55:
            breadth = "宽度偏积极"
        elif market.breadth_score <= 0.35:
            breadth = "宽度偏弱"
        else:
            breadth = "宽度中性"
        return {
            "one_line": (
                f"今日市场处于{mode}，{breadth}，质量门禁为{self._quality_status_label(quality_gate)}；"
                f"机会分布为A{grade_counts.get('A', 0)} / B{grade_counts.get('B', 0)}，B以下不展示。"
            ),
            "market_mode": mode,
            "core_logic": self._core_logic(market),
            "next_step": next_step,
        }

    def _core_logic(self, market: MarketSnapshot) -> str:
        if market.benchmark_trend > 0.15 and market.breadth_score > 0.55:
            return "指数趋势和市场宽度同时偏正面，但仍需验证主线持续性与成交额承接。"
        if market.benchmark_trend > 0 and market.breadth_score < 0.45:
            return "指数偏强但内部扩散不足，优先防止少数权重拉动造成的假强势。"
        if market.benchmark_trend < -0.15:
            return "指数趋势转弱，机会扫描以回避和降低仓位为主。"
        return "市场处于震荡观察区，机会等级更多依赖个股技术面、新闻催化和风控约束。"

    def _quality_status_label(self, quality_gate: QualityGate) -> str:
        return {"PASS": "通过", "WARN": "警告", "FAIL": "失败"}.get(quality_gate.status.value, quality_gate.status.value)

    def _index_state(self, market: MarketSnapshot, index_profiles: dict[str, dict] | None = None) -> list[dict[str, str]]:
        benchmarks = [
            ("上证指数", market.benchmark_trend * 0.95),
            ("深证成指", market.benchmark_trend * 1.05),
            ("创业板指", market.benchmark_trend * 1.12),
            ("科创50", market.benchmark_trend * 1.20),
            ("沪深300", market.benchmark_trend),
            ("中证500", market.benchmark_trend * 0.85),
            ("中证1000", market.benchmark_trend * 0.75),
        ]
        rows = []
        for name, trend in benchmarks:
            profile = (index_profiles or {}).get(name, {})
            bars = list(profile.get("bars") or [])
            technical = dict(profile.get("technical") or {})
            if trend > 0.15:
                judgement = "趋势偏强"
            elif trend < -0.15:
                judgement = "趋势偏弱"
            else:
                judgement = "震荡"
            if technical:
                rsi = float(technical.get("rsi") or 50)
                volume_ratio = float(technical.get("volume_ratio") or 1)
                if rsi >= 80:
                    judgement = "强势但严重超买"
                elif rsi >= 70:
                    judgement = "偏强但短线偏热"
                elif trend > 0.15 and volume_ratio >= 1.2:
                    judgement = "放量偏强"
                elif trend < -0.15:
                    judgement = "趋势偏弱"
            close = bars[-1].close if bars else None
            previous_close = bars[-2].close if len(bars) >= 2 else None
            daily_change = (close / previous_close - 1) if close and previous_close else None
            five_day_base = bars[-6].close if len(bars) >= 6 else None
            five_day_change = (close / five_day_base - 1) if close and five_day_base else None
            last_amount = bars[-1].amount if bars else None
            last_volume = bars[-1].volume if bars else None
            rows.append(
                {
                    "index": name,
                    "close": f"{close:.2f}" if close is not None else "待接指数K线",
                    "daily_change": format_pct(daily_change),
                    "turnover": format_turnover(last_amount, last_volume),
                    "rsi14": f"{float(technical.get('rsi')):.1f}" if technical.get("rsi") is not None else "待接指数K线",
                    "ema8": f"{float(technical.get('ema8')):.2f}" if technical.get("ema8") is not None else "待接指数K线",
                    "five_day": format_pct(five_day_change),
                    "trend_score": f"{trend:.2f}",
                    "breadth_confirm": f"{market.breadth_score:.2f}",
                    "volatility": f"{market.volatility_score:.2f}",
                    "judgement": judgement,
                    "status": str(profile.get("status") or "derived"),
                }
            )
        return rows

    def _market_sentiment_breadth(
        self,
        calendar: TradingCalendarCheck,
        market: MarketSnapshot,
        market_news_results: list[SourceResult],
    ) -> list[dict[str, str]]:
        news_success = sum(1 for item in market_news_results if item.status.value == "success")
        news_failed = sum(1 for item in market_news_results if item.status.value == "failed")
        dimensions = [
            {
                "dimension": "交易日状态",
                "value": "可交易" if calendar.is_trading_day else calendar.reason,
                "signal": "可交易" if calendar.is_trading_day else "不可交易/非报告日",
                "source": "AShareCalendar",
            },
            {
                "dimension": "指数趋势",
                "value": f"{market.benchmark_trend:.2f}",
                "signal": "风险偏好开启" if market.benchmark_trend > 0.15 else "中性" if market.benchmark_trend >= -0.15 else "风险偏好收缩",
                "source": "MarketSnapshot",
            },
            {
                "dimension": "市场宽度",
                "value": f"{market.breadth_score:.2f}",
                "signal": "扩散较好" if market.breadth_score > 0.55 else "扩散不足" if market.breadth_score < 0.45 else "中性",
                "source": "MarketSnapshot",
            },
            {
                "dimension": "流动性",
                "value": f"{market.liquidity_score:.2f}",
                "signal": "偏宽松" if market.liquidity_score > 0.55 else "偏紧" if market.liquidity_score < 0.4 else "中性",
                "source": "MarketSnapshot",
            },
            {
                "dimension": "波动率",
                "value": f"{market.volatility_score:.2f}",
                "signal": "风险升高" if market.volatility_score > 0.7 else "可控",
                "source": "MarketSnapshot",
            },
            {
                "dimension": "新闻源",
                "value": f"success={news_success}, failed={news_failed}",
                "signal": "可用" if news_success else "需回退/复核",
                "source": "SourceResult",
            },
        ]
        return dimensions

    def _market_regime_score(
        self,
        calendar: TradingCalendarCheck,
        market: MarketSnapshot,
        index_state: list[dict[str, str]],
        theme_rotation: dict,
        market_news_results: list[SourceResult],
    ) -> dict:
        main_themes = [item.get("theme", "") for item in theme_rotation.get("main_themes", []) if item.get("theme")]
        risk_themes = [item.get("theme", "") for item in theme_rotation.get("risk_themes", []) if item.get("theme")]
        tech_indexes = [
            item for item in index_state
            if item.get("index") in {"创业板指", "科创50"} and "偏强" in str(item.get("judgement", ""))
        ]
        news_success = sum(1 for item in market_news_results if item.status == SourceStatus.SUCCESS)
        dimensions = [
            self._regime_dimension(
                "交易日状态",
                1.0 if calendar.is_trading_day else 0.0,
                "可交易" if calendar.is_trading_day else "不可交易/非报告日",
                "A股交易日历",
            ),
            self._regime_dimension(
                "指数趋势",
                self._score_by_threshold(market.benchmark_trend, [(0.15, 1.0), (0.05, 0.75), (-0.05, 0.5), (-0.15, 0.25)]),
                "Risk On" if market.benchmark_trend > 0.15 else "中性偏多" if market.benchmark_trend > 0.05 else "中性" if market.benchmark_trend >= -0.05 else "Risk Off",
                "市场快照/指数K线",
            ),
            self._regime_dimension(
                "成交与流动性",
                self._score_by_threshold(market.liquidity_score, [(0.65, 1.0), (0.55, 0.75), (0.45, 0.5), (0.35, 0.25)]),
                "Risk On" if market.liquidity_score > 0.65 else "中性偏多" if market.liquidity_score > 0.55 else "中性" if market.liquidity_score >= 0.45 else "偏紧",
                "市场快照",
            ),
            self._regime_dimension(
                "市场宽度",
                self._score_by_threshold(market.breadth_score, [(0.65, 1.0), (0.55, 0.75), (0.45, 0.5), (0.35, 0.25)]),
                "全面扩散" if market.breadth_score > 0.65 else "Risk On但非全扩散" if market.breadth_score > 0.55 else "中性" if market.breadth_score >= 0.45 else "扩散不足",
                "市场快照/涨跌家数",
            ),
            self._regime_dimension(
                "科技成长强度",
                1.0 if tech_indexes and main_themes else 0.75 if main_themes else 0.5 if tech_indexes else 0.25,
                "强Risk On但拥挤" if tech_indexes and main_themes else "主线活跃" if main_themes else "指数线索有限",
                "指数K线/板块轮动",
            ),
            self._regime_dimension(
                "板块扩散结构",
                1.0 if len(main_themes) >= 5 and not risk_themes else 0.75 if main_themes else 0.25,
                "集中进攻" if main_themes and risk_themes else "主线扩散" if main_themes else "无清晰主线",
                "选股池/板块统计",
            ),
            self._regime_dimension(
                "政策与流动性",
                0.75 if news_success and market.liquidity_score > 0.55 else 0.5 if news_success else 0.25,
                "中性偏多" if news_success and market.liquidity_score > 0.55 else "中性" if news_success else "缺少政策新闻确认",
                "市场新闻/政策",
            ),
            self._regime_dimension(
                "北向/融资/跨资产",
                0.0,
                "数据缺失，不计入正向确认",
                "待接北向、融资和跨资产主数据",
            ),
        ]
        score = round(sum(float(item["score"]) for item in dimensions), 2)
        risk_label = self._risk_label(score, main_themes, risk_themes, market)
        mode = self._regime_mode(risk_label, main_themes, risk_themes)
        summary = self._regime_summary(score, risk_label, mode, main_themes, risk_themes)
        return {
            "score": score,
            "max_score": 8,
            "risk_label": risk_label,
            "mode": mode,
            "summary": summary,
            "dimensions": dimensions,
        }

    def _regime_dimension(self, dimension: str, score: float, signal: str, source: str) -> dict[str, str | float]:
        return {
            "dimension": dimension,
            "score": round(max(0.0, min(1.0, score)), 2),
            "signal": signal,
            "source": source,
        }

    def _score_by_threshold(self, value: float, thresholds: list[tuple[float, float]]) -> float:
        for threshold, score in thresholds:
            if value > threshold:
                return score
        return 0.0

    def _risk_label(self, score: float, main_themes: list[str], risk_themes: list[str], market: MarketSnapshot) -> str:
        if score >= 6.5 and market.breadth_score > 0.65 and not risk_themes:
            return "全面Risk On"
        if score >= 5.5:
            return "分化Risk On"
        if score >= 4.0:
            return "震荡偏多"
        if score >= 2.5:
            return "震荡"
        return "Risk Off"

    def _regime_mode(self, risk_label: str, main_themes: list[str], risk_themes: list[str]) -> str:
        if risk_label == "全面Risk On":
            return "全面进攻"
        if risk_label == "分化Risk On" and main_themes:
            return "集中进攻"
        if risk_label == "分化Risk On":
            return "风险偏好修复"
        if risk_label == "震荡偏多":
            return "轮动试探"
        if risk_label == "Risk Off":
            return "防守"
        return "震荡观察"

    def _regime_summary(self, score: float, risk_label: str, mode: str, main_themes: list[str], risk_themes: list[str]) -> str:
        main_text = "、".join(main_themes[:3]) if main_themes else "暂无清晰主线"
        risk_text = "、".join(risk_themes[:3]) if risk_themes else "弱势方向暂未形成显著拖累"
        if risk_label == "全面Risk On":
            return f"综合评分：{score:.1f}/8 → {risk_label}。 风险偏好全面修复，主线扩散至{main_text}，模式定为“{mode}”。"
        if risk_label == "分化Risk On":
            return (
                f"综合评分：{score:.1f}/8 → {risk_label}。 风险偏好明显修复，但资金集中在{main_text}方向，"
                f"{risk_text}仍有分歧；模式定为“{mode}”，不升为全面Risk On。"
            )
        if risk_label == "Risk Off":
            return f"综合评分：{score:.1f}/8 → {risk_label}。 指数、宽度或流动性未给出正向确认，模式定为“{mode}”。"
        return f"综合评分：{score:.1f}/8 → {risk_label}。 主线线索为{main_text}，但确认度不足，模式定为“{mode}”。"

    def _market_action(self, market: MarketSnapshot) -> str:
        if market.volatility_score > 0.7:
            return "降低卫星仓，等待波动回落。"
        if market.breadth_score > 0.55:
            return "核心仓持有，卫星仓等待回踩确认。"
        return "维持现金，等待宽度修复。"

    def _quality_action(self, quality_gate: QualityGate) -> str:
        if quality_gate.status.value == "PASS":
            return "允许生成A类交易计划。"
        if quality_gate.status.value == "WARN":
            return "报告可生成，但A类机会降级，买点需补齐数据确认。"
        return "停止交易建议，只输出风险说明。"

    def _position_summary(self, plan: PositionPlan) -> str:
        return (
            f"核心仓 {plan.core_position_range[0]:.0%}-{plan.core_position_range[1]:.0%}, "
            f"卫星仓 {plan.satellite_position_range[0]:.0%}-{plan.satellite_position_range[1]:.0%}, "
            f"现金 {plan.cash_range[0]:.0%}-{plan.cash_range[1]:.0%}"
        )

    def _scenarios(self) -> list[dict[str, str]]:
        return [
            {"scenario": "主线验证", "trigger": "主线低开或平开后不破关键位并重新放量", "action": "B类可小仓试探，严格单票上限。"},
            {"scenario": "主线钝化", "trigger": "高位震荡且无持续涨停梯队", "action": "不新增，维持现金和核心仓。"},
            {"scenario": "主线证伪", "trigger": "核心指数或主题龙头放量长阴", "action": "清理卫星仓，提高现金。"},
        ]

    def _risk_warnings(self, quality_gate: QualityGate, opportunities: list[Opportunity]) -> list[str]:
        warnings = list(quality_gate.warnings[:5])
        if any(item.grade == OpportunityGrade.B for item in opportunities):
            warnings.append("B类机会只代表等待池，不代表可立即买入。")
        if any(item.grade == OpportunityGrade.D for item in opportunities):
            warnings.append("D类标的弱于系统，不做弱反弹。")
        return warnings

    def _source_audit(
        self,
        universe: list[str],
        opportunities: list[Opportunity],
        market_news_results: list[SourceResult],
        stock_pools: list[StockPool],
        llm_result: SourceResult | None = None,
        xueqiu_result: SourceResult | None = None,
    ) -> list[dict[str, str]]:
        security_news_status = self._merge_source_status(
            item.evidence.get("news_source_status", {}) for item in opportunities
        )
        market_news_status = {result.source: result.status.value for result in market_news_results}
        kline_status = self._kline_status(opportunities)
        rows = [
            {"data": "股票池", "source": ", ".join(universe), "status": "有效"},
            {"data": "交易日", "source": "AShareCalendar", "status": "基础日历，待接交易所完整休市表"},
        ]
        rows.extend(
            {"data": "行情/K线", "source": source, "status": status}
            for source, status in sorted(kline_status.items())
        )
        rows.extend(
            {"data": "个股新闻/公告", "source": source, "status": status}
            for source, status in sorted(security_news_status.items())
        )
        rows.extend(
            {"data": "市场新闻/政策", "source": source, "status": status}
            for source, status in sorted(market_news_status.items())
        )
        rows.extend(
            {"data": "选股池", "source": pool.source, "status": pool.status.value}
            for pool in stock_pools
        )
        if llm_result:
            rows.append({"data": llm_result.data, "source": llm_result.source, "status": llm_result.status.value})
        if xueqiu_result:
            rows.append({"data": xueqiu_result.data, "source": xueqiu_result.source, "status": xueqiu_result.status.value})
        if not security_news_status:
            rows.append({"data": "个股新闻/公告", "source": "未获取", "status": "missing"})
        if not market_news_status:
            rows.append({"data": "市场新闻/政策", "source": "未获取", "status": "missing"})
        if not stock_pools:
            rows.append({"data": "选股池", "source": "未获取", "status": "missing"})
        return rows

    def _kline_status(self, opportunities: list[Opportunity]) -> dict[str, str]:
        statuses: dict[str, str] = {}
        for item in opportunities:
            results = item.evidence.get("kline_source_results", [])
            if results:
                for result in results:
                    source = str(result.get("source") or "unknown")
                    status = str(result.get("status") or "missing")
                    statuses[source] = self._dominant_status(statuses.get(source), status)
            else:
                source = str(item.evidence.get("kline_source") or item.evidence.get("provider") or "unknown")
                status = "fallback" if source == "mock_fallback" or source == "mock" else "success"
                statuses[source] = self._dominant_status(statuses.get(source), status)
        return statuses

    def _merge_source_status(self, statuses: list[dict[str, str]] | object) -> dict[str, str]:
        merged: dict[str, str] = {}
        for item in statuses:
            for source, status in dict(item).items():
                merged[source] = self._dominant_status(merged.get(source), status)
        return merged

    def _dominant_status(self, current: str | None, new: str) -> str:
        order = {"failed": 3, "partial": 2, "fallback": 1, "success": 0, "missing": 0}
        if current is None:
            return new
        if {current, new} == {"success", "failed"}:
            return "partial"
        return current if order.get(current, 0) >= order.get(new, 0) else new

    def _source_results(
        self,
        opportunities: list[Opportunity],
        market_news_results: list[SourceResult],
        stock_pools: list[StockPool],
        llm_result: SourceResult | None = None,
        xueqiu_result: SourceResult | None = None,
    ) -> list[dict]:
        security_results: list[dict] = []
        seen: set[tuple[str, str, str | None]] = set()
        for opportunity in opportunities:
            for result in opportunity.evidence.get("kline_source_results", []):
                key = (
                    result.get("data", ""),
                    result.get("source", ""),
                    str(sorted((result.get("context") or {}).items())),
                    result.get("error_message"),
                )
                if key not in seen:
                    security_results.append(result)
                    seen.add(key)
            for result in opportunity.evidence.get("source_results", []):
                context = result.get("context") or {}
                key = (
                    result.get("data", ""),
                    result.get("source", ""),
                    str(sorted(context.items())),
                    result.get("error_message"),
                )
                if key not in seen:
                    security_results.append(result)
                    seen.add(key)
        results = security_results + source_results_to_dicts(market_news_results) + self._stock_pool_source_results(stock_pools)
        if llm_result:
            results.extend(source_results_to_dicts([llm_result]))
        if xueqiu_result:
            results.extend(source_results_to_dicts([xueqiu_result]))
        return results

    def _stock_pool_source_results(self, stock_pools: list[StockPool]) -> list[dict]:
        return [
            {
                "data": "选股池",
                "source": pool.source,
                "provider": pool.name,
                "status": pool.status.value,
                "items": [],
                "error_type": None,
                "error_message": pool.error_message,
                "fallback_source": None,
                "checked_at": None,
                "context": {"as_of": pool.as_of, "entries": str(len(pool.entries))},
            }
            for pool in stock_pools
        ]

    def _stock_pool_analysis(self, stock_pools: list[StockPool]) -> dict:
        if not stock_pools:
            return {
                "summary": "未加载当日选股池，报告仅基于默认观察股票池和指数环境生成。",
                "action": "先运行 scripts/build_stock_pools.py 或开启正式归档生成，补齐选股池后再做主题交叉验证。",
                "source_health": {"success": 0, "fallback": 0, "failed": 0, "missing": 1},
                "pools": [],
                "overlap": [],
                "themes": [],
            }

        status_counts = Counter(pool.status.value for pool in stock_pools)
        usable_pools = [pool for pool in stock_pools if pool.status in {SourceStatus.SUCCESS, SourceStatus.FALLBACK}]
        failed_pools = [pool for pool in stock_pools if pool.status == SourceStatus.FAILED]
        symbol_pools: dict[str, dict[str, object]] = {}
        theme_counts: Counter[str] = Counter()
        for pool in usable_pools:
            for entry in pool.entries:
                item = symbol_pools.setdefault(entry.symbol, {"symbol": entry.symbol, "name": entry.name, "pools": []})
                item["pools"].append(pool.name)
                industry = entry.metrics.get("所属行业")
                if industry:
                    theme_counts[str(industry)] += 1

        overlap = sorted(
            (
                {
                    "symbol": str(item["symbol"]),
                    "name": str(item["name"]),
                    "pool_count": len(item["pools"]),
                    "pools": list(item["pools"]),
                }
                for item in symbol_pools.values()
                if len(item["pools"]) >= 2
            ),
            key=lambda item: (-int(item["pool_count"]), item["symbol"]),
        )[:10]
        themes = [
            {"theme": theme, "count": count}
            for theme, count in theme_counts.most_common(8)
        ]
        pool_rows = [
            {
                "name": pool.name,
                "description": pool.description,
                "status": pool.status.value,
                "entries": len(pool.entries),
                "source": pool.source,
                "top": [
                    {
                        "rank": entry.rank,
                        "symbol": entry.symbol,
                        "name": entry.name,
                        "metrics": entry.metrics,
                    }
                    for entry in pool.entries[:5]
                ],
                "error_message": pool.error_message,
            }
            for pool in stock_pools
        ]
        if failed_pools and usable_pools:
            source_note = f"{len(usable_pools)}个池可用，{len(failed_pools)}个池失败；失败池不参与机会判断。"
        elif failed_pools:
            source_note = "选股池关键数据源全部失败，不将选股池纳入交易偏好。"
        else:
            source_note = "选股池数据源可用，可用于与指数状态、机会评级交叉验证。"

        hot_pool_names = [pool.description for pool in usable_pools if pool.entries]
        if overlap:
            focus = f"重合标的优先观察：{', '.join(item['symbol'] + ' ' + item['name'] for item in overlap[:5])}。"
        elif hot_pool_names:
            focus = "暂无多池重合标的，按池类型分层观察，不做强主线确认。"
        else:
            focus = "可用选股池为空，暂不提供扩展观察标的。"
        return {
            "summary": f"{source_note}{focus}",
            "action": self._stock_pool_action(usable_pools, failed_pools, overlap),
            "source_health": {
                "success": status_counts.get("success", 0),
                "fallback": status_counts.get("fallback", 0),
                "failed": status_counts.get("failed", 0),
                "missing": status_counts.get("missing", 0),
            },
            "pools": pool_rows,
            "overlap": overlap,
            "themes": themes,
        }

    def _theme_rotation_analysis(
        self,
        stock_pools: list[StockPool],
        opportunities: list[Opportunity],
    ) -> dict:
        usable_pools = [pool for pool in stock_pools if pool.status in {SourceStatus.SUCCESS, SourceStatus.FALLBACK}]
        if not usable_pools:
            return {
                "summary": "选股池缺失，暂无法判断板块轮动。",
                "stage": "等待数据",
                "main_themes": [],
                "watch_themes": [],
                "risk_themes": [],
            }

        theme_stats: dict[str, dict] = {}
        symbol_score = {item.symbol: item.score for item in opportunities}
        for pool in usable_pools:
            for entry in pool.entries:
                theme = str(entry.metrics.get("所属行业") or "").strip()
                if not theme:
                    continue
                stat = theme_stats.setdefault(
                    theme,
                    {
                        "theme": theme,
                        "hits": 0,
                        "limit_up": 0,
                        "broken_limit_up": 0,
                        "turnover": 0,
                        "limit_down": 0,
                        "symbols": [],
                        "score": 0.0,
                    },
                )
                stat["hits"] += 1
                stat["symbols"].append({"symbol": entry.symbol, "name": entry.name, "pool": pool.name})
                if pool.name == "limit_up":
                    stat["limit_up"] += 1
                    stat["score"] += 3.0
                elif pool.name == "broken_limit_up":
                    stat["broken_limit_up"] += 1
                    stat["score"] += 1.0
                elif pool.name == "turnover_top20":
                    stat["turnover"] += 1
                    stat["score"] += 1.2
                elif pool.name == "limit_down":
                    stat["limit_down"] += 1
                    stat["score"] -= 2.0
                else:
                    stat["score"] += 1.5
                stat["score"] += max(symbol_score.get(entry.symbol, 0.0), 0) * 0.35

        ranked = sorted(theme_stats.values(), key=lambda item: (item["score"], item["hits"]), reverse=True)
        rows = [self._theme_row(item) for item in ranked]
        main_themes = [item for item in rows if item["score"] >= 3.2 and item["limit_down"] == 0][:5]
        main_theme_names = {item["theme"] for item in main_themes}
        watch_themes = [item for item in rows if item["score"] > 0 and item["theme"] not in main_theme_names][:5]
        risk_themes = [item for item in rows if item["limit_down"] > 0 or item["broken_limit_up"] >= 2][:5]

        if main_themes:
            leader_text = "、".join(item["theme"] for item in main_themes[:3])
            stage = "主线扩散" if any(item["limit_up"] >= 2 for item in main_themes) else "轮动试探"
            summary = f"板块线索集中在{leader_text}；当前更接近{stage}，次日重点看前排封单、后排换手和炸板修复。"
        elif watch_themes:
            stage = "快速轮动"
            summary = "热点较分散，尚未形成清晰主线；优先观察换手承接，不追单日脉冲。"
        else:
            stage = "退潮观察"
            summary = "可用池未给出有效板块合力，先按退潮处理。"
        return {
            "summary": summary,
            "stage": stage,
            "main_themes": main_themes,
            "watch_themes": watch_themes,
            "risk_themes": risk_themes,
        }

    def _xueqiu_tracking_analysis(
        self,
        snapshot: object | None,
        stock_pools: list[StockPool],
        opportunities: list[Opportunity],
    ) -> dict:
        if snapshot is None:
            return {
                "summary": "未开启雪球大V与持仓跟踪。",
                "status": "missing",
                "post_count": 0,
                "position_change_count": 0,
                "mentioned_symbols": [],
                "confirmed_position_changes": [],
                "overlaps": [],
            }
        posts = list(getattr(snapshot, "posts", []) or [])
        changes = list(getattr(snapshot, "position_changes", []) or [])
        symbol_counter: Counter[str] = Counter()
        symbol_names: dict[str, str] = {}
        for post in posts:
            for symbol in getattr(post, "symbols", []) or []:
                symbol_counter[symbol] += 1
        for change in changes:
            symbol = getattr(change, "stock_symbol", "")
            if symbol:
                symbol_counter[symbol] += 1
                symbol_names[symbol] = getattr(change, "stock_name", "") or symbol

        pool_symbols = {
            entry.symbol: entry.name
            for pool in stock_pools
            if pool.status in {SourceStatus.SUCCESS, SourceStatus.FALLBACK}
            for entry in pool.entries
        }
        opportunity_symbols = {item.symbol: item.name for item in opportunities}
        mentioned = [
            {"symbol": symbol, "name": symbol_names.get(symbol) or pool_symbols.get(symbol) or opportunity_symbols.get(symbol) or symbol, "count": count}
            for symbol, count in symbol_counter.most_common(12)
        ]
        overlaps = [
            {
                "symbol": item["symbol"],
                "name": item["name"],
                "xueqiu_mentions": item["count"],
                "in_opportunity": item["symbol"] in opportunity_symbols,
                "in_stock_pool": item["symbol"] in pool_symbols,
            }
            for item in mentioned
            if item["symbol"] in opportunity_symbols or item["symbol"] in pool_symbols
        ][:10]
        confirmed_changes = [
            {
                "portfolio": getattr(change, "portfolio_name", ""),
                "symbol": getattr(change, "stock_symbol", ""),
                "name": getattr(change, "stock_name", ""),
                "action": getattr(change, "action", ""),
                "before": getattr(change, "weight_before", None),
                "after": getattr(change, "weight_after", None),
                "changed_at": getattr(change, "changed_at", None),
            }
            for change in changes[:12]
        ]
        status = getattr(getattr(snapshot, "status", None), "value", str(getattr(snapshot, "status", "missing")))
        if status in {"success", "partial"} and (posts or changes):
            summary = f"雪球跟踪获取到{len(posts)}条大V帖子、{len(changes)}条公开组合调仓；与选股池/机会重合{len(overlaps)}个。"
        else:
            result = getattr(snapshot, "source_result", None)
            summary = f"雪球跟踪暂不可用：{getattr(result, 'error_message', None) or '未获取到公开帖子或组合调仓'}。"
        return {
            "summary": summary,
            "status": status,
            "post_count": len(posts),
            "position_change_count": len(changes),
            "mentioned_symbols": mentioned,
            "confirmed_position_changes": confirmed_changes,
            "overlaps": overlaps,
        }

    def _theme_row(self, item: dict) -> dict:
        symbols = item["symbols"][:5]
        return {
            "theme": item["theme"],
            "score": round(item["score"], 2),
            "hits": item["hits"],
            "limit_up": item["limit_up"],
            "broken_limit_up": item["broken_limit_up"],
            "turnover": item["turnover"],
            "limit_down": item["limit_down"],
            "symbols": symbols,
            "judgement": self._theme_judgement(item),
        }

    def _theme_judgement(self, item: dict) -> str:
        if item["limit_down"] > 0:
            return "有跌停拖累，先看修复，不做主线确认。"
        if item["limit_up"] >= 2 and item["turnover"] >= 1:
            return "涨停与换手共振，具备主线候选特征。"
        if item["limit_up"] >= 1:
            return "有前排强度，等待后排扩散。"
        if item["broken_limit_up"] >= 2:
            return "炸板偏多，说明分歧较大。"
        return "仅有热度线索，等待确认。"

    def _stock_pool_action(
        self,
        usable_pools: list[StockPool],
        failed_pools: list[StockPool],
        overlap: list[dict],
    ) -> str:
        if not usable_pools:
            return "不基于选股池新增交易动作，等待资金流和行情池恢复。"
        if overlap:
            return "多池重合标的进入次日优先跟踪，但必须等待分时承接、板块延续和风控价格同时确认。"
        if failed_pools:
            return "涨跌停与换手池可用于情绪观察；资金流池失败，不能把高换手直接解释为资金净流入。"
        return "优先跟踪同时具备资金、换手和涨停梯队确认的标的，未重合标的只做主题温度参考。"

    def _pool_opportunities(
        self,
        stock_pools: list[StockPool],
        quality_gate: QualityGate,
        technical_profiles: dict[str, dict],
    ) -> list[Opportunity]:
        usable_pools = [pool for pool in stock_pools if pool.status in {SourceStatus.SUCCESS, SourceStatus.FALLBACK}]
        if not usable_pools:
            return []
        candidates: dict[str, dict] = {}
        for pool in usable_pools:
            for entry in pool.entries:
                item = candidates.setdefault(
                    entry.symbol,
                    {
                        "symbol": entry.symbol,
                        "name": entry.name,
                        "score": 0.0,
                        "risk": [],
                        "pools": [],
                        "metrics": {},
                    },
                )
                contribution = self._pool_score(pool, entry)
                item["score"] += contribution
                item["pools"].append(pool.name)
                item["metrics"].update(entry.metrics)
                if pool.name == "limit_down":
                    item["risk"].append("当日跌停")

        opportunities = [
            self._pool_candidate_to_opportunity(item, quality_gate, technical_profiles.get(item["symbol"], {}))
            for item in candidates.values()
        ]
        return sorted(opportunities, key=lambda item: item.score, reverse=True)

    def _pool_score(self, pool: StockPool, entry: StockPoolEntry) -> float:
        base_weights = {
            "main_net_inflow_top20": 0.38,
            "small_float_net_inflow_top20": 0.32,
            "turnover_top20": 0.18,
            "limit_up": 0.34,
            "broken_limit_up": 0.12,
            "limit_down": -0.42,
        }
        base = base_weights.get(pool.name, 0.08)
        rank_bonus = max(0.0, (21 - min(entry.rank, 20)) / 20) * abs(base) * 0.45
        if base < 0:
            return base - rank_bonus
        score = base + rank_bonus
        if "成交额" in entry.metrics:
            score += min((to_number(entry.metrics.get("成交额")) or 0) / 10_000_000_000, 0.08)
        if "今日主力净流入-净额" in entry.metrics:
            score += min((to_number(entry.metrics.get("今日主力净流入-净额")) or 0) / 1_000_000_000, 0.10)
        return score

    def _pool_candidate_to_opportunity(self, item: dict, quality_gate: QualityGate, technical_result: dict) -> Opportunity:
        technical = dict(technical_result.get("technical") or {})
        technical_score = self._technical_score(technical)
        score = round(max(-1.0, min(1.0, item["score"] + technical_score)), 3)
        if score >= 0.72:
            grade = OpportunityGrade.A
        elif score >= 0.36:
            grade = OpportunityGrade.B
        elif score >= 0.08:
            grade = OpportunityGrade.C
        else:
            grade = OpportunityGrade.D
        if quality_gate.status.value != "PASS" and grade == OpportunityGrade.A:
            grade = OpportunityGrade.B
        pool_names = [self._pool_display_name(name) for name in item["pools"]]
        technical_text = self._technical_trigger_text(technical, technical_result)
        trigger = "命中选股池：" + "、".join(pool_names) + f"；{technical_text}；次日需验证开盘承接、板块延续和成交额。"
        invalidation = self._technical_invalidation_text(technical)
        if "当日跌停" in item["risk"]:
            action = "风险观察，不做反包预设，只有快速修复并放量站回关键位才重新评估。"
        elif grade in {OpportunityGrade.A, OpportunityGrade.B}:
            action = "进入优先跟踪，等待分时确认后再考虑小仓试探。"
        elif grade == OpportunityGrade.C:
            action = "放入观察池，等待第二天强弱确认。"
        else:
            action = "回避，暂不参与。"
        return Opportunity(
            symbol=item["symbol"],
            name=item["name"],
            grade=grade,
            score=score,
            trigger=trigger,
            invalidation=invalidation,
            action=action,
            risk_flags=list(dict.fromkeys(item["risk"])),
            evidence={
                "source": "选股池",
                "pools": pool_names,
                "sector": str(item["metrics"].get("所属行业") or "未分类"),
                "metrics": item["metrics"],
                "pool_score": round(item["score"], 3),
                "technical_score": round(technical_score, 3),
                "technical": technical,
                "technical_status": technical_result.get("status"),
                "technical_error": technical_result.get("error_message"),
            },
        )

    def _technical_score(self, technical: dict) -> float:
        if not technical:
            return 0.0
        trend = float(technical.get("trend_strength") or 0)
        ma_alignment = float(technical.get("ma_alignment") or 0)
        rsi = float(technical.get("rsi") or 50)
        volume_ratio = float(technical.get("volume_ratio") or 1)
        close = to_number(technical.get("close"))
        support = to_number(technical.get("support"))
        resistance = to_number(technical.get("resistance"))
        score = trend * 0.08 + ma_alignment * 0.07
        score += max(-0.08, min((volume_ratio - 1.0) * 0.04, 0.05))
        if rsi >= 82:
            score -= 0.10
        elif rsi >= 72:
            score -= 0.06
        elif 45 <= rsi <= 68:
            score += 0.02
        elif rsi <= 30:
            score -= 0.06
        if close and resistance and resistance > 0:
            distance_to_resistance = resistance / close - 1
            if 0 <= distance_to_resistance <= 0.03:
                score += 0.015
            elif distance_to_resistance < 0:
                score -= 0.06
        if close and support and support > 0:
            distance_to_support = close / support - 1
            if 0 <= distance_to_support <= 0.03:
                score -= 0.06
        return max(-0.18, min(0.12, score))

    def _technical_trigger_text(self, technical: dict, technical_result: dict) -> str:
        if not technical:
            if technical_result.get("status") == "failed":
                return "技术面数据暂缺，不能确认趋势结构"
            return "技术面未纳入确认"
        parts = []
        trend = float(technical.get("trend_strength") or 0)
        ma_alignment = float(technical.get("ma_alignment") or 0)
        volume_ratio = float(technical.get("volume_ratio") or 1)
        rsi = float(technical.get("rsi") or 50)
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
        if volume_ratio >= 1.5:
            parts.append("放量确认")
        elif volume_ratio < 0.8:
            parts.append("量能不足")
        if rsi >= 75:
            parts.append("RSI偏热")
        elif 45 <= rsi <= 68:
            parts.append("RSI处于健康区")
        return "技术面：" + "、".join(parts)

    def _technical_invalidation_text(self, technical: dict) -> str:
        support = to_number(technical.get("support")) if technical else None
        ema21 = to_number(technical.get("ema21")) if technical else None
        if support and ema21:
            return f"跌破技术支撑{support:.2f}或21日均线{ema21:.2f}，板块梯队断裂，或高换手后无法维持红盘。"
        if support:
            return f"跌破技术支撑{support:.2f}、板块梯队断裂，或高换手后无法维持红盘。"
        return "跌破当日关键低点、板块梯队断裂，或高换手后无法维持红盘。"

    def _technical_coverage(self, technical_profiles: dict[str, dict]) -> dict[str, int]:
        success = sum(1 for item in technical_profiles.values() if item.get("status") == "success")
        failed = sum(1 for item in technical_profiles.values() if item.get("status") == "failed")
        return {"success": success, "failed": failed, "total": len(technical_profiles)}

    def _pool_display_name(self, name: str) -> str:
        return {
            "main_net_inflow_top20": "主力净额流入前二十",
            "small_float_net_inflow_top20": "中小流通市值资金流入前二十",
            "turnover_top20": "换手率前二十",
            "limit_up": "当日涨停",
            "limit_down": "当日跌停",
            "broken_limit_up": "当日炸板",
        }.get(name, name)
