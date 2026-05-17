from __future__ import annotations

from dataclasses import replace
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
from midas_cn.models import DailyReport, DecisionRun, KLineBar, SecurityContext, SourceStatus, StockPool, StockPoolEntry
from midas_cn.playbooks.positioning import PositionPlaybook
from midas_cn.pools.builder import AkShareStockPoolBuilder, latest_report_trade_date
from midas_cn.pools.storage import StockPoolArchive
from midas_cn.pools.ths_cache import load_ths_sector_cache, symbol_classification
from midas_cn.quality.gates import DataQualityGate
from midas_cn.reports.builder import DailyReportBuilder, GENERIC_CONCEPT_TAGS, TOP_THEME_LIMIT
from midas_cn.risk.engine import RiskEngine
from midas_cn.scanners.opportunity import OpportunityScanner
from midas_cn.social import XueqiuArchive, XueqiuTracker
from midas_cn.storage.archive import DecisionArchive
from midas_cn.storage.data_cache import DataCache, kline_bars_from_dicts
from midas_cn.storage.report_archive import DailyReportArchive
from midas_cn.storage.stock_sector_mapping import (
    fetch_stock_sector_capacities,
    fetch_stock_sector_mappings,
    fetch_stock_sector_mappings_by_themes,
)
from midas_cn.universe.symbols import normalize_symbol, normalize_symbols


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
        cache_config = config.section("cache")
        cache_ttl_seconds = int(cache_config.get("ttl_seconds", 86_400))
        self.data_cache = DataCache(
            config.data_cache_dir,
            ttl_seconds=cache_ttl_seconds,
        )
        data_config["cache"] = self.data_cache
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
        self.pool_archive = StockPoolArchive(config.pool_archive_dir, ttl_seconds=cache_ttl_seconds)
        self.xueqiu_archive = XueqiuArchive(config.social_archive_dir, ttl_seconds=cache_ttl_seconds)
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
            opportunity_news_sort=str(config.section("news").get("opportunity_news_sort", "hybrid")),
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
        total_steps = 14

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
        universe_sector_mappings, _ = self._load_stock_sector_mappings_for_symbols(universe)
        securities = []
        for index, symbol in enumerate(universe, start=1):
            emit(4, f"拉取核心标的上下文：{symbol} ({index}/{len(universe)})")
            security = self.provider.get_security_context(symbol)
            securities.append(self._apply_stock_sector_mapping_to_security(security, universe_sector_mappings))
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
        stock_pools = self._load_or_build_stock_pools(pool_trade_date, persist, emit)
        emit(9, "拉取雪球跟踪数据")
        xueqiu_snapshot = self._load_or_fetch_xueqiu(pool_trade_date, persist)
        emit(10, "识别热主题并扩展回调候选")
        stock_sector_mappings, stock_sector_mapping_result = self._load_stock_sector_mappings([], stock_pools)
        stock_sector_capacities, stock_sector_capacity_result = self._load_stock_sector_capacities()
        theme_pullback_pool, theme_mappings, theme_mapping_result = self._build_theme_pullback_pool(
            stock_pools,
            stock_sector_mappings,
            pool_trade_date,
        )
        stock_sector_mappings = {**stock_sector_mappings, **theme_mappings}
        if theme_mappings and theme_mapping_result:
            stock_sector_mapping_result = theme_mapping_result
        analysis_stock_pools = stock_pools + ([theme_pullback_pool] if theme_pullback_pool else [])
        emit(11, "计算热主题回调候选技术指标")
        pool_technical_profiles = self._build_pool_technical_profiles(analysis_stock_pools, emit)
        report_opportunities, _ = self.report_builder.rank_report_opportunities(
            opportunities,
            quality_gate,
            analysis_stock_pools,
            pool_technical_profiles,
        )
        emit(12, "补充个股最新新闻")
        opportunity_news_results = self._fetch_opportunity_news(report_opportunities)
        emit(13, "计算指数技术状态")
        index_profiles = self._build_index_profiles()
        emit(14, "组装并保存中文报告")
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
            stock_pools=analysis_stock_pools,
            xueqiu_snapshot=xueqiu_snapshot,
            opportunity_news_results=opportunity_news_results,
            technical_profiles=pool_technical_profiles,
            index_profiles=index_profiles,
            stock_sector_mappings=stock_sector_mappings,
            stock_sector_mapping_result=stock_sector_mapping_result,
            stock_sector_capacities=stock_sector_capacities,
            stock_sector_capacity_result=stock_sector_capacity_result,
        )
        paths: dict[str, str] = {}
        if persist:
            json_path, markdown_path = self.report_archive.save(report)
            paths = {"report_json": str(json_path), "report_markdown": str(markdown_path)}
        return report, paths

    def _load_or_build_stock_pools(self, trade_date: str, persist: bool, progress_emit=None):
        pool_config = self.config.section("pools")
        if not bool(pool_config.get("enabled", True)):
            return []
        should_build = persist or bool(pool_config.get("build_if_missing", False))
        akshare_module = getattr(self.provider, "akshare", None)
        sector_cache = self._load_ths_sector_cache()
        if progress_emit:
            progress_emit(8, "检查选股池缓存")
        cached = self.pool_archive.load(trade_date)
        if cached and not self._stock_pool_cache_needs_rebuild(cached):
            if progress_emit:
                progress_emit(8, "读取选股池缓存并合并同花顺行业/概念缓存")
            return self._apply_sector_cache_to_pools(cached, sector_cache)
        if cached and (not should_build or akshare_module is None):
            if progress_emit:
                progress_emit(8, "使用已有选股池缓存")
            return self._apply_sector_cache_to_pools(cached, sector_cache)
        if not should_build or akshare_module is None:
            return []
        if progress_emit:
            progress_emit(8, "开始构建选股池")
        pools = AkShareStockPoolBuilder(
            akshare_module,
            top_n=int(pool_config.get("top_n", 20)),
            small_float_cap=float(pool_config.get("small_float_cap", 100_000_000_000)),
            industry_enrich_limit=int(pool_config.get("industry_enrich_limit", 8)),
            industry_enrich_timeout_seconds=float(pool_config.get("industry_enrich_timeout_seconds", 8)),
            sector_cache=sector_cache,
            progress=(lambda message: progress_emit(8, f"构建选股池：{message}")) if progress_emit else None,
        ).build(trade_date)
        self.pool_archive.save(trade_date, pools)
        return pools

    def _load_ths_sector_cache(self) -> dict:
        cache_config = self.config.section("ths_cache")
        return load_ths_sector_cache(
            self.config.ths_sector_cache_path,
            ttl_seconds=int(cache_config.get("ttl_seconds", self.config.section("cache").get("ttl_seconds", 86_400))),
        )

    def _apply_sector_cache_to_pools(self, stock_pools: list[StockPool], sector_cache: dict) -> list[StockPool]:
        if not sector_cache:
            return stock_pools
        pools = []
        for pool in stock_pools:
            entries = []
            for entry in pool.entries:
                cached = symbol_classification(sector_cache, entry.symbol)
                if not cached:
                    entries.append(entry)
                    continue
                metrics = dict(entry.metrics)
                if cached.get("industry"):
                    metrics["所属行业"] = cached["industry"]
                    metrics["行业来源"] = cached.get("industry_source", "同花顺行业缓存")
                if cached.get("concepts"):
                    metrics["概念"] = list(cached.get("concepts") or [])[:8]
                    metrics["概念来源"] = cached.get("concept_source", "同花顺概念缓存")
                entries.append(
                    StockPoolEntry(
                        symbol=entry.symbol,
                        name=entry.name,
                        reason=entry.reason,
                        rank=entry.rank,
                        metrics=metrics,
                    )
                )
            pools.append(
                StockPool(
                    name=pool.name,
                    description=pool.description,
                    entries=entries,
                    source=pool.source,
                    status=pool.status,
                    as_of=pool.as_of,
                    error_message=pool.error_message,
                )
            )
        return pools

    def _stock_pool_cache_needs_rebuild(self, stock_pools) -> bool:
        critical_names = {"main_net_inflow_top20", "small_float_net_inflow_top20"}
        for pool in stock_pools:
            if pool.status == SourceStatus.FALLBACK:
                return True
            if pool.name in critical_names and pool.status == SourceStatus.FAILED:
                return True
        return False

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

    def _load_stock_sector_mappings(self, opportunities, stock_pools=None):
        symbols = [opportunity.symbol for opportunity in opportunities[:10]]
        for pool in stock_pools or []:
            symbols.extend(entry.symbol for entry in pool.entries)
        return self._load_stock_sector_mappings_for_symbols(symbols)

    def _load_stock_sector_mappings_for_symbols(self, symbols: list[str]):
        return fetch_stock_sector_mappings(symbols, self.config.section("mongodb"))

    def _load_stock_sector_capacities(self):
        return fetch_stock_sector_capacities(self.config.section("mongodb"))

    def _build_theme_pullback_pool(
        self,
        stock_pools: list[StockPool],
        stock_sector_mappings: dict[str, dict],
        trade_date: str,
    ) -> tuple[StockPool | None, dict[str, dict], object | None]:
        pool_config = self.config.section("pools")
        if not bool(pool_config.get("theme_pullback_enabled", True)):
            return None, {}, None
        theme_limit = int(pool_config.get("theme_pullback_theme_limit", TOP_THEME_LIMIT))
        candidate_limit = int(pool_config.get("theme_pullback_candidate_limit", 80))
        hot_themes = self._hot_themes_from_stock_pools(stock_pools, stock_sector_mappings, theme_limit)
        if not hot_themes:
            return None, {}, None
        theme_names = [item["theme"] for item in hot_themes]
        theme_mappings, result = fetch_stock_sector_mappings_by_themes(
            theme_names,
            self.config.section("mongodb"),
            limit=candidate_limit,
        )
        entries = self._theme_pullback_entries(hot_themes, theme_mappings, stock_pools, candidate_limit)
        if not entries:
            return None, theme_mappings, result
        return (
            StockPool(
                name="theme_pullback",
                description="热门行业/概念回调候选",
                entries=entries,
                source="mongodb.stock_sector_mapping",
                status=SourceStatus.SUCCESS,
                as_of=trade_date,
                error_message=None,
            ),
            theme_mappings,
            result,
        )

    def _hot_themes_from_stock_pools(
        self,
        stock_pools: list[StockPool],
        stock_sector_mappings: dict[str, dict],
        limit: int,
    ) -> list[dict[str, object]]:
        theme_scores: dict[str, dict[str, object]] = {}
        for pool in stock_pools:
            if pool.status not in {SourceStatus.SUCCESS, SourceStatus.FALLBACK}:
                continue
            for entry in pool.entries:
                for theme in self._themes_for_stock_pool_entry(entry, stock_sector_mappings):
                    item = theme_scores.setdefault(theme, {"theme": theme, "score": 0.0, "hits": 0})
                    item["hits"] = int(item["hits"]) + 1
                    item["score"] = float(item["score"]) + self._theme_heat_score(pool, entry)
        ranked = sorted(theme_scores.values(), key=lambda item: (float(item["score"]), int(item["hits"])), reverse=True)
        return ranked[:limit]

    def _themes_for_stock_pool_entry(self, entry: StockPoolEntry, stock_sector_mappings: dict[str, dict]) -> list[str]:
        code = entry.symbol.split(".", 1)[0].zfill(6)
        mapping = stock_sector_mappings.get(code) or stock_sector_mappings.get(entry.symbol) or {}
        themes = []
        themes.extend(str(item).strip() for item in mapping.get("industry_sectors") or [])
        themes.extend(
            str(item).strip()
            for item in mapping.get("concept_sectors") or []
            if str(item).strip() not in GENERIC_CONCEPT_TAGS
        )
        fallback_industry = str(entry.metrics.get("所属行业") or "").strip()
        if fallback_industry:
            themes.append(fallback_industry)
        return self._unique_themes(themes)[:TOP_THEME_LIMIT]

    def _theme_heat_score(self, pool: StockPool, entry: StockPoolEntry) -> float:
        weights = {
            "limit_up": 3.0,
            "broken_limit_up": 1.0,
            "main_net_inflow_top20": 1.6,
            "small_float_net_inflow_top20": 1.4,
            "turnover_top20": 1.1,
            "limit_down": -2.0,
        }
        base = weights.get(pool.name, 0.5)
        rank_bonus = max(0.0, (21 - min(entry.rank, 20)) / 20) * abs(base) * 0.35
        return base - rank_bonus if base < 0 else base + rank_bonus

    def _theme_pullback_entries(
        self,
        hot_themes: list[dict[str, object]],
        theme_mappings: dict[str, dict],
        stock_pools: list[StockPool],
        limit: int,
    ) -> list[StockPoolEntry]:
        if not theme_mappings:
            return []
        theme_rank = {str(item["theme"]): index for index, item in enumerate(hot_themes)}
        theme_score = {str(item["theme"]): float(item.get("score") or 0) for item in hot_themes}
        blocked_symbols = {
            entry.symbol
            for pool in stock_pools
            if pool.name in {"limit_up", "broken_limit_up"}
            for entry in pool.entries
        }
        rows = []
        for code, mapping in theme_mappings.items():
            try:
                symbol = normalize_symbol(code)
            except ValueError:
                continue
            if symbol in blocked_symbols:
                continue
            matched_themes = self._matched_hot_themes(mapping, theme_rank)
            if not matched_themes:
                continue
            best_rank = min(theme_rank[theme] for theme in matched_themes)
            rows.append(
                {
                    "symbol": symbol,
                    "name": str(mapping.get("stock_name") or symbol),
                    "rank_score": (best_rank, -max(theme_score[theme] for theme in matched_themes), symbol),
                    "themes": matched_themes,
                    "mapping": mapping,
                }
            )
        rows = sorted(rows, key=lambda item: item["rank_score"])[:limit]
        entries = []
        for rank, row in enumerate(rows, start=1):
            mapping = row["mapping"]
            concepts = [
                concept
                for concept in list(mapping.get("concept_sectors") or [])
                if str(concept).strip() not in GENERIC_CONCEPT_TAGS
            ][:8]
            entries.append(
                StockPoolEntry(
                    symbol=str(row["symbol"]),
                    name=str(row["name"]),
                    reason="热门行业/概念内回调候选",
                    rank=rank,
                    metrics={
                        "热主题": list(row["themes"])[:TOP_THEME_LIMIT],
                        "所属行业": ";".join(mapping.get("industry_sectors") or []),
                        "概念": concepts,
                        "映射来源": "mongodb.stock_sector_mapping",
                    },
                )
            )
        return entries

    def _matched_hot_themes(self, mapping: dict, theme_rank: dict[str, int]) -> list[str]:
        themes = []
        themes.extend(str(item).strip() for item in mapping.get("industry_sectors") or [])
        themes.extend(
            str(item).strip()
            for item in mapping.get("concept_sectors") or []
            if str(item).strip() not in GENERIC_CONCEPT_TAGS
        )
        return [theme for theme in self._unique_themes(themes) if theme in theme_rank]

    def _unique_themes(self, themes) -> list[str]:
        items = []
        seen = set()
        for theme in themes:
            value = str(theme or "").strip()
            if not value or value in seen:
                continue
            items.append(value)
            seen.add(value)
        return items

    def _apply_stock_sector_mapping_to_security(
        self,
        security: SecurityContext,
        mappings: dict[str, dict],
    ) -> SecurityContext:
        if not mappings:
            return security
        code = security.symbol.split(".", 1)[0].zfill(6)
        mapping = mappings.get(code) or mappings.get(security.symbol)
        if not mapping:
            return security
        industries = list(mapping.get("industry_sectors") or [])
        concepts = list(mapping.get("concept_sectors") or [])
        sector = industries[0] if industries else security.sector
        metadata = dict(security.metadata)
        metadata["stock_sector_mapping"] = {
            "source": "mongodb.stock_sector_mapping",
            "stock_code": mapping.get("stock_code") or code,
            "stock_name": mapping.get("stock_name") or security.name,
            "industry_sectors": industries,
            "concept_sectors": concepts,
            "updated_at": mapping.get("updated_at") or "",
        }
        if industries:
            metadata["industry_sectors"] = industries
        if concepts:
            metadata["concepts"] = concepts[:12]
            metadata["concept_sectors"] = concepts
        return replace(
            security,
            name=str(mapping.get("stock_name") or security.name),
            sector=sector,
            metadata=metadata,
        )

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

    def _build_pool_technical_profiles(self, stock_pools, progress_emit=None):
        pool_config = self.config.section("pools")
        limit = int(pool_config.get("technical_limit", 40))
        if limit <= 0:
            return {}
        candidates = self._technical_candidate_symbols(stock_pools, limit)
        profiles = {}
        lookback = int(self.config.section("data").get("kline_lookback", 90))
        total = len(candidates)
        for index, symbol in enumerate(candidates, start=1):
            if progress_emit:
                progress_emit(10, f"计算选股池技术指标：{symbol} ({index}/{total})")
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
            "theme_pullback": 0.34,
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
        cached = self.data_cache.load("index_profiles", "default")
        if cached:
            return {
                name: {
                    **profile,
                    "bars": kline_bars_from_dicts(profile.get("bars", [])),
                }
                for name, profile in cached.items()
            }
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
        self.data_cache.save("index_profiles", "default", profiles)
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
