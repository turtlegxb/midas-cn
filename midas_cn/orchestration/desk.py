from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from midas_cn.analysts.base import Analyst
from midas_cn.calendar.a_share import AShareCalendar
from midas_cn.config import AppConfig
from midas_cn.data.kline import build_technical_profile
from midas_cn.data.news import row_value
from midas_cn.data.providers import MarketDataProvider, build_provider
from midas_cn.decision.engine import DecisionEngine
from midas_cn.llm import build_report_synthesis_service
from midas_cn.models import DailyReport, DecisionRun, KLineBar, SourceStatus
from midas_cn.playbooks.positioning import PositionPlaybook
from midas_cn.pools.builder import AkShareStockPoolBuilder, latest_report_trade_date
from midas_cn.pools.storage import StockPoolArchive
from midas_cn.quality.gates import DataQualityGate
from midas_cn.reports.builder import DailyReportBuilder
from midas_cn.risk.engine import RiskEngine
from midas_cn.scanners.opportunity import OpportunityScanner
from midas_cn.social import XueqiuArchive, XueqiuTracker
from midas_cn.storage.archive import DecisionArchive
from midas_cn.storage.report_archive import DailyReportArchive
from midas_cn.universe.symbols import normalize_symbols


ProgressCallback = Callable[[int, int, str], None]


class TradingDesk:
    def __init__(
        self,
        config: AppConfig,
        analysts: list[Analyst],
        provider: MarketDataProvider | None = None,
    ):
        self.config = config
        self.analysts = analysts
        data_config = {**config.section("data"), **config.section("news")}
        self.provider = provider or build_provider(data_config.get("provider", "mock"), data_config)
        self.risk_engine = RiskEngine(
            max_single_position=float(config.section("risk").get("max_single_position", 0.10)),
            min_liquidity_score=float(config.section("risk").get("min_liquidity_score", 0.40)),
            default_stop_loss_pct=float(config.section("risk").get("default_stop_loss_pct", 0.06)),
        )
        self.decision_engine = DecisionEngine(
            buy_threshold=float(config.section("decision").get("buy_threshold", 0.65)),
            sell_threshold=float(config.section("decision").get("sell_threshold", -0.45)),
            watch_threshold=float(config.section("decision").get("watch_threshold", 0.35)),
        )
        self.archive = DecisionArchive(config.archive_dir)
        self.report_archive = DailyReportArchive(config.report_archive_dir)
        self.pool_archive = StockPoolArchive(config.pool_archive_dir)
        self.xueqiu_archive = XueqiuArchive(config.social_archive_dir)
        self.calendar = AShareCalendar(report_days=list(config.section("system").get("report_days", [])))
        self.quality_gate = DataQualityGate(
            required_security_sections=list(config.section("quality").get("required_security_sections", [])),
            required_technical_fields_for_a=list(config.section("quality").get("required_technical_fields_for_a", [])),
            allow_warn_report=bool(config.section("quality").get("allow_warn_report", True)),
        )
        self.opportunity_scanner = OpportunityScanner(
            a_threshold=float(config.section("opportunity").get("a_threshold", 0.55)),
            b_threshold=float(config.section("opportunity").get("b_threshold", 0.15)),
            c_threshold=float(config.section("opportunity").get("c_threshold", 0.0)),
            d_threshold=float(config.section("opportunity").get("d_threshold", -0.20)),
            max_warn_grade=str(config.section("opportunity").get("max_warn_grade", "B")),
        )
        self.position_playbook = PositionPlaybook(
            warn_satellite_position=float(config.section("playbook").get("warn_satellite_position", 0.05)),
            cash_min_pass=float(config.section("playbook").get("cash_min_pass", 0.25)),
            cash_min_warn=float(config.section("playbook").get("cash_min_warn", 0.35)),
        )
        self.report_builder = DailyReportBuilder(
            llm_synthesis=build_report_synthesis_service(config.section("llm")),
        )

    def run(
        self,
        symbols: list[str] | None = None,
        persist: bool = True,
        now: datetime | None = None,
        progress: ProgressCallback | None = None,
    ) -> tuple[DecisionRun, str | None]:
        report, paths = self.run_daily_report(symbols=symbols, persist=persist, now=now, progress=progress)
        decision_run = DecisionRun(
            run_id=report.run_id,
            as_of=report.as_of,
            market_snapshot=report.market_snapshot,
            decisions=report.decisions,
            metadata={
                **report.metadata,
                "calendar": report.calendar,
                "quality_gate": report.quality_gate,
                "opportunities": report.opportunities,
                "report_paths": paths,
            },
        )
        archive_path = str(self.archive.save(decision_run)) if persist else None
        return decision_run, archive_path

    def run_daily_report(
        self,
        symbols: list[str] | None = None,
        persist: bool = True,
        now: datetime | None = None,
        progress: ProgressCallback | None = None,
    ) -> tuple[DailyReport, dict[str, str]]:
        total_steps = 13

        def emit(step: int, message: str) -> None:
            if progress:
                progress(step, total_steps, message)

        emit(1, "初始化股票池与交易日历")
        universe = normalize_symbols(symbols or self.config.default_symbols)
        as_of = now or datetime.now()
        calendar = self.calendar.check(as_of)
        emit(2, "拉取市场快照")
        market = self.provider.get_market_snapshot(self.config.benchmark_symbols)
        emit(3, "拉取市场新闻与政策信息")
        market_news_results = self.provider.get_market_news_results(
            lookback_days=int(self.config.section("news").get("lookback_days", 2)),
            limit=50,
        )
        emit(4, "拉取核心标的上下文")
        securities = [self.provider.get_security_context(symbol) for symbol in universe]
        emit(5, "执行数据质量检查")
        quality_gate = self.quality_gate.evaluate(market, securities)
        opportunities = []
        decisions = []

        emit(6, "运行分析师与机会扫描")
        for security in securities:
            views = [analyst.evaluate(security, market) for analyst in self.analysts]
            risk_plan = self.risk_engine.plan(security, market)
            opportunity = self.opportunity_scanner.scan(security, views, risk_plan, quality_gate)
            opportunities.append(opportunity)
            decisions.append(self.decision_engine.decide_from_opportunity(opportunity, views, risk_plan))

        emit(7, "生成仓位与风控方案")
        position_plan = self.position_playbook.build(opportunities, quality_gate)
        pool_trade_date = calendar.trade_date.replace("-", "") if calendar.is_trading_day else latest_report_trade_date(as_of.date())
        emit(8, "构建或读取选股池")
        stock_pools = self._load_or_build_stock_pools(pool_trade_date, persist)
        emit(9, "拉取雪球跟踪数据")
        xueqiu_snapshot = self._load_or_fetch_xueqiu(pool_trade_date, persist)
        emit(10, "计算选股池技术指标")
        pool_technical_profiles = self._build_pool_technical_profiles(stock_pools)
        report_opportunities, _ = self.report_builder.rank_report_opportunities(
            opportunities,
            quality_gate,
            stock_pools,
            pool_technical_profiles,
        )
        emit(11, "补充个股最新新闻")
        opportunity_news_results = self._fetch_opportunity_news(report_opportunities)
        emit(12, "计算指数技术状态")
        index_profiles = self._build_index_profiles()
        emit(13, "组装并保存中文报告")
        report = self.report_builder.build(
            run_id=as_of.strftime("%Y%m%d_%H%M%S"),
            as_of=as_of,
            calendar=calendar,
            quality_gate=quality_gate,
            market=market,
            opportunities=opportunities,
            position_plan=position_plan,
            decisions=decisions,
            universe=universe,
            market_news_results=market_news_results,
            stock_pools=stock_pools,
            xueqiu_snapshot=xueqiu_snapshot,
            opportunity_news_results=opportunity_news_results,
            technical_profiles=pool_technical_profiles,
            index_profiles=index_profiles,
        )
        paths: dict[str, str] = {}
        if persist:
            json_path, markdown_path = self.report_archive.save(report)
            paths = {"report_json": str(json_path), "report_markdown": str(markdown_path)}
        return report, paths

    def _load_or_build_stock_pools(self, trade_date: str, persist: bool):
        pool_config = self.config.section("pools")
        if not bool(pool_config.get("enabled", True)):
            return []
        cached = self.pool_archive.load(trade_date)
        if cached:
            return cached
        should_build = persist or bool(pool_config.get("build_if_missing", False))
        akshare_module = getattr(self.provider, "akshare", None)
        if not should_build or akshare_module is None:
            return []
        pools = AkShareStockPoolBuilder(
            akshare_module,
            top_n=int(pool_config.get("top_n", 20)),
            small_float_cap=float(pool_config.get("small_float_cap", 100_000_000_000)),
        ).build(trade_date)
        self.pool_archive.save(trade_date, pools)
        return pools

    def _fetch_opportunity_news(self, opportunities):
        news_config = self.config.section("news")
        lookback_days = int(news_config.get("lookback_days", 2))
        limit = int(news_config.get("max_items_per_symbol", 20))
        results = {}
        for opportunity in opportunities[:10]:
            try:
                results[opportunity.symbol] = self.provider.get_security_news_results(
                    opportunity.symbol,
                    lookback_days=lookback_days,
                    limit=limit,
                )
            except Exception as exc:
                results[opportunity.symbol] = []
        return results

    def _load_or_fetch_xueqiu(self, trade_date: str, persist: bool):
        xueqiu_config = self.config.section("xueqiu")
        if not bool(xueqiu_config.get("enabled", False)):
            return None
        cached = self.xueqiu_archive.load(trade_date)
        if cached and cached.status != SourceStatus.MISSING:
            return cached
        snapshot = XueqiuTracker(xueqiu_config).fetch(trade_date)
        if persist:
            self.xueqiu_archive.save(trade_date, snapshot)
        return snapshot

    def _build_pool_technical_profiles(self, stock_pools):
        pool_config = self.config.section("pools")
        limit = int(pool_config.get("technical_limit", 40))
        if limit <= 0:
            return {}
        candidates = self._technical_candidate_symbols(stock_pools, limit)
        profiles = {}
        lookback = int(self.config.section("data").get("kline_lookback", 90))
        for symbol in candidates:
            try:
                bars = self.provider.get_daily_bars(symbol, lookback)
                technical = build_technical_profile(bars).as_dict()
                profiles[symbol] = {"status": "success", "technical": technical, "error_message": None}
            except Exception as exc:
                profiles[symbol] = {"status": "failed", "technical": {}, "error_message": str(exc)}
        return profiles

    def _technical_candidate_symbols(self, stock_pools, limit: int) -> list[str]:
        weights = {
            "main_net_inflow_top20": 0.38,
            "small_float_net_inflow_top20": 0.32,
            "turnover_top20": 0.18,
            "limit_up": 0.34,
            "broken_limit_up": 0.12,
            "limit_down": -0.42,
        }
        scores = {}
        for pool in stock_pools:
            if pool.status not in {SourceStatus.SUCCESS, SourceStatus.FALLBACK}:
                continue
            for entry in pool.entries:
                score = weights.get(pool.name, 0.05)
                score += max(0.0, (21 - min(entry.rank, 20)) / 20) * abs(score) * 0.45
                scores[entry.symbol] = scores.get(entry.symbol, 0.0) + score
        return [symbol for symbol, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]]

    def _build_index_profiles(self):
        index_symbols = {
            "上证指数": "000001",
            "深证成指": "399001",
            "创业板指": "399006",
            "科创50": "000688",
            "沪深300": "000300",
            "中证500": "000905",
            "中证1000": "000852",
        }
        lookback = int(self.config.section("data").get("kline_lookback", 90))
        profiles = {}
        for name, code in index_symbols.items():
            try:
                bars = self._get_index_daily_bars(name, code, lookback)
                technical = build_technical_profile(bars).as_dict()
                profiles[name] = {
                    "status": "success",
                    "bars": bars,
                    "technical": technical,
                    "error_message": None,
                }
            except Exception as exc:
                profiles[name] = {"status": "failed", "bars": [], "technical": {}, "error_message": str(exc)}
        return profiles

    def _get_index_daily_bars(self, name: str, code: str, lookback: int) -> list[KLineBar]:
        akshare_module = getattr(self.provider, "akshare", None)
        if akshare_module is None:
            return self.provider.get_daily_bars(code, lookback)
        end = datetime.now().date()
        start = end - timedelta(days=max(lookback * 3, 120))
        try:
            frame = akshare_module.index_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
        except Exception:
            frame = akshare_module.stock_zh_index_daily(symbol=self._sina_index_symbol(code))
        rows = frame.to_dict("records") if hasattr(frame, "to_dict") else list(frame)
        bars = [self._index_row_to_bar(row) for row in rows]
        return bars[-lookback:]

    def _sina_index_symbol(self, code: str) -> str:
        if code.startswith(("399", "159")):
            return f"sz{code}"
        return f"sh{code}"

    def _index_row_to_bar(self, row) -> KLineBar:
        return KLineBar(
            date=str(row_value(row, "日期", "date")),
            open=float(row_value(row, "开盘", "open")),
            high=float(row_value(row, "最高", "high")),
            low=float(row_value(row, "最低", "low")),
            close=float(row_value(row, "收盘", "close")),
            volume=float(row_value(row, "成交量", "volume") or 0),
            amount=float(row_value(row, "成交额", "amount") or 0),
        )
