from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
import signal
import socket
import threading
import time
from typing import Any

from midas_cn.data.kline import build_technical_profile, normalize_symbol_for_akshare
from midas_cn.data.news import (
    build_news_profile,
    filter_recent_items,
    flatten_source_items,
    source_results_to_dicts,
    source_status_from_results,
    row_value,
)
from midas_cn.models import KLineBar, MarketSnapshot, NewsItem, SecurityContext, SourceResult, SourceStatus
from midas_cn.storage.data_cache import DataCache, kline_bars_from_dicts, source_results_from_dicts


class MarketDataProvider(ABC):
    @abstractmethod
    def get_market_snapshot(self, benchmarks: list[str]) -> MarketSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_security_context(self, symbol: str) -> SecurityContext:
        raise NotImplementedError

    def get_daily_bars(self, symbol: str, lookback: int = 90) -> list[KLineBar]:
        raise NotImplementedError

    def get_daily_bars_result(self, symbol: str, lookback: int = 90) -> SourceResult:
        try:
            bars = self.get_daily_bars(symbol, lookback)
            return SourceResult(
                data="行情/K线",
                source=self.__class__.__name__,
                provider=f"{self.__class__.__name__}.get_daily_bars",
                status=SourceStatus.SUCCESS if bars else SourceStatus.MISSING,
                checked_at=datetime.now().isoformat(),
                context={"symbol": symbol},
            )
        except Exception as exc:
            return SourceResult(
                data="行情/K线",
                source=self.__class__.__name__,
                provider=f"{self.__class__.__name__}.get_daily_bars",
                status=SourceStatus.FAILED,
                error_type=type(exc).__name__,
                error_message=str(exc),
                checked_at=datetime.now().isoformat(),
                context={"symbol": symbol},
            )

    def get_security_news(self, symbol: str, lookback_days: int = 2, limit: int = 20) -> list[NewsItem]:
        return flatten_source_items(self.get_security_news_results(symbol, lookback_days, limit))

    def get_market_news(self, lookback_days: int = 2, limit: int = 50) -> list[NewsItem]:
        return flatten_source_items(self.get_market_news_results(lookback_days, limit))

    def get_security_news_results(
        self,
        symbol: str,
        lookback_days: int = 2,
        limit: int = 20,
    ) -> list[SourceResult]:
        raise NotImplementedError

    def get_market_news_results(self, lookback_days: int = 2, limit: int = 50) -> list[SourceResult]:
        raise NotImplementedError


class MockMarketDataProvider(MarketDataProvider):
    """Deterministic placeholder provider for architecture validation."""

    _names = {
        "600519.SH": ("贵州茅台", "食品饮料"),
        "300750.SZ": ("宁德时代", "电力设备"),
        "510300.SH": ("沪深300ETF", "宽基ETF"),
    }

    _profiles = {
        "600519.SH": {
            "technical": {
                "trend_strength": 0.42,
                "ma_alignment": 0.35,
                "rsi": 54.0,
                "volume_ratio": 0.94,
                "support": 1520.0,
                "resistance": 1680.0,
            },
            "fundamental": {
                "roe": 0.29,
                "revenue_growth": 0.15,
                "profit_growth": 0.18,
                "pe_percentile": 0.62,
                "debt_to_asset": 0.18,
                "dividend_yield": 0.025,
            },
            "news": {
                "policy_score": 0.05,
                "earnings_surprise": 0.10,
                "regulatory_risk": 0.08,
                "event_heat": 0.30,
                "headline_count": 8,
            },
            "sentiment": {
                "sentiment_score": 0.58,
                "discussion_heat": 0.45,
                "kol_divergence": 0.25,
                "retail_chase_risk": 0.20,
            },
            "china_market": {
                "northbound_flow_score": 0.52,
                "margin_flow_score": 0.48,
                "limit_status": "normal",
                "is_st": False,
                "board": "main",
                "policy_theme_score": 0.35,
            },
        },
        "300750.SZ": {
            "technical": {
                "trend_strength": 0.36,
                "ma_alignment": 0.22,
                "rsi": 61.0,
                "volume_ratio": 1.18,
                "support": 186.0,
                "resistance": 228.0,
            },
            "fundamental": {
                "roe": 0.22,
                "revenue_growth": 0.24,
                "profit_growth": 0.20,
                "pe_percentile": 0.70,
                "debt_to_asset": 0.62,
                "dividend_yield": 0.006,
            },
            "news": {
                "policy_score": 0.18,
                "earnings_surprise": 0.04,
                "regulatory_risk": 0.12,
                "event_heat": 0.52,
                "headline_count": 14,
            },
            "sentiment": {
                "sentiment_score": 0.64,
                "discussion_heat": 0.70,
                "kol_divergence": 0.40,
                "retail_chase_risk": 0.45,
            },
            "china_market": {
                "northbound_flow_score": 0.60,
                "margin_flow_score": 0.56,
                "limit_status": "normal",
                "is_st": False,
                "board": "chinext",
                "policy_theme_score": 0.62,
            },
        },
        "510300.SH": {
            "technical": {
                "trend_strength": 0.28,
                "ma_alignment": 0.18,
                "rsi": 51.0,
                "volume_ratio": 1.02,
                "support": 3.9,
                "resistance": 4.25,
            },
            "fundamental": {
                "roe": 0.10,
                "revenue_growth": 0.06,
                "profit_growth": 0.06,
                "pe_percentile": 0.48,
                "debt_to_asset": 0.0,
                "dividend_yield": 0.018,
            },
            "news": {
                "policy_score": 0.08,
                "earnings_surprise": 0.0,
                "regulatory_risk": 0.04,
                "event_heat": 0.22,
                "headline_count": 4,
            },
            "sentiment": {
                "sentiment_score": 0.52,
                "discussion_heat": 0.38,
                "kol_divergence": 0.18,
                "retail_chase_risk": 0.12,
            },
            "china_market": {
                "northbound_flow_score": 0.50,
                "margin_flow_score": 0.50,
                "limit_status": "normal",
                "is_st": False,
                "board": "etf",
                "policy_theme_score": 0.20,
            },
        },
    }

    def get_market_snapshot(self, benchmarks: list[str]) -> MarketSnapshot:
        return MarketSnapshot(
            as_of=datetime.now(),
            benchmark_trend=0.18,
            breadth_score=0.56,
            liquidity_score=0.62,
            volatility_score=0.34,
            notes=[f"mock benchmarks: {', '.join(benchmarks)}"],
        )

    def get_security_context(self, symbol: str) -> SecurityContext:
        name, sector = self._names.get(symbol, (symbol, "未分类"))
        seed = sum(ord(char) for char in symbol)
        bars = self.get_daily_bars(symbol)
        kline_result = self.get_daily_bars_result(symbol)
        technical = build_technical_profile(bars).as_dict()
        news_results = self.get_security_news_results(symbol)
        news_items = flatten_source_items(news_results)
        news_profile = build_news_profile(news_items)
        news_profile["source_status"] = source_status_from_results(news_results)
        news_profile["source_results"] = source_results_to_dicts(news_results)
        return SecurityContext(
            symbol=symbol,
            name=name,
            sector=sector,
            price=technical["close"] or round(10 + seed % 500 + (seed % 17) / 10, 2),
            liquidity_score=round(0.45 + (seed % 40) / 100, 2),
            metadata={
                "provider": "mock",
                **self._fallback_profile(seed),
                **self._profiles.get(symbol, {}),
                "technical": {
                    **self._fallback_profile(seed)["technical"],
                    **self._profiles.get(symbol, {}).get("technical", {}),
                    **technical,
                },
                "kline_source_results": source_results_to_dicts([kline_result]),
                "news": {
                    **self._fallback_profile(seed)["news"],
                    **self._profiles.get(symbol, {}).get("news", {}),
                    **news_profile,
                },
            },
        )

    def get_daily_bars(self, symbol: str, lookback: int = 90) -> list[KLineBar]:
        seed = sum(ord(char) for char in symbol)
        base_price = 10 + seed % 500 + (seed % 17) / 10
        bars: list[KLineBar] = []
        start = datetime.now().date() - timedelta(days=lookback * 2)
        trading_index = 0
        current = base_price * 0.88
        for day_offset in range(lookback * 2):
            current_date = start + timedelta(days=day_offset)
            if current_date.weekday() >= 5:
                continue
            drift = 0.0015 + ((seed + trading_index) % 7 - 3) * 0.001
            current = max(1.0, current * (1 + drift))
            high = current * (1.01 + ((seed + trading_index) % 3) * 0.002)
            low = current * (0.99 - ((seed + trading_index) % 2) * 0.002)
            open_ = (high + low) / 2 * (0.995 + ((seed + trading_index) % 5) * 0.002)
            volume = 1_000_000 + (seed % 1000) * 1000 + trading_index * 1500
            bars.append(
                KLineBar(
                    date=current_date.isoformat(),
                    open=round(open_, 3),
                    high=round(high, 3),
                    low=round(low, 3),
                    close=round(current, 3),
                    volume=float(volume),
                    amount=round(volume * current, 2),
                )
            )
            trading_index += 1
            if len(bars) >= lookback:
                break
        return bars[-lookback:]

    def get_security_news(self, symbol: str, lookback_days: int = 2, limit: int = 20) -> list[NewsItem]:
        return flatten_source_items(self.get_security_news_results(symbol, lookback_days, limit))

    def get_security_news_results(
        self,
        symbol: str,
        lookback_days: int = 2,
        limit: int = 20,
    ) -> list[SourceResult]:
        name, sector = self._names.get(symbol, (symbol, "未分类"))
        items = [
            NewsItem(
                title=f"{name} 所属{sector}板块维持高关注度",
                source="mock_sector_news",
                published_at=datetime.now().date().isoformat(),
                url=f"https://example.com/{symbol}/sector",
                category="sector",
            ),
            NewsItem(
                title=f"{name} 盘后数据进入机会扫描池",
                source="mock_security_news",
                published_at=datetime.now().date().isoformat(),
                url=f"https://example.com/{symbol}/news",
                category="company",
            ),
        ]
        return [
            SourceResult(
                data="个股新闻/公告",
                source="mock_security_news",
                provider="MockMarketDataProvider.get_security_news",
                status=SourceStatus.FALLBACK,
                items=items[:limit],
                checked_at=datetime.now().isoformat(),
                context={"symbol": symbol},
            )
        ]

    def get_market_news(self, lookback_days: int = 2, limit: int = 50) -> list[NewsItem]:
        return flatten_source_items(self.get_market_news_results(lookback_days, limit))

    def get_market_news_results(self, lookback_days: int = 2, limit: int = 50) -> list[SourceResult]:
        items = [
            NewsItem(
                title="A股盘后日报市场新闻占位：指数、成交额、板块轮动等待真实源覆盖",
                source="mock_market_news",
                published_at=datetime.now().date().isoformat(),
                category="market",
            )
        ]
        return [
            SourceResult(
                data="市场新闻/政策",
                source="mock_market_news",
                provider="MockMarketDataProvider.get_market_news",
                status=SourceStatus.FALLBACK,
                items=items[:limit],
                checked_at=datetime.now().isoformat(),
                context={},
            )
        ]

    def _fallback_profile(self, seed: int) -> dict[str, dict[str, float | int | bool | str]]:
        base = (seed % 100) / 100
        return {
            "technical": {
                "trend_strength": round(base - 0.5, 2),
                "ma_alignment": round(base - 0.5, 2),
                "rsi": 35 + seed % 35,
                "volume_ratio": round(0.8 + (seed % 60) / 100, 2),
                "support": round(8 + seed % 200, 2),
                "resistance": round(12 + seed % 240, 2),
            },
            "fundamental": {
                "roe": round(0.06 + (seed % 24) / 100, 3),
                "revenue_growth": round(-0.05 + (seed % 30) / 100, 3),
                "profit_growth": round(-0.08 + (seed % 34) / 100, 3),
                "pe_percentile": round((seed % 90) / 100, 2),
                "debt_to_asset": round(0.2 + (seed % 55) / 100, 2),
                "dividend_yield": round((seed % 5) / 100, 3),
            },
            "news": {
                "policy_score": round(-0.1 + (seed % 30) / 100, 2),
                "earnings_surprise": round(-0.08 + (seed % 20) / 100, 2),
                "regulatory_risk": round((seed % 20) / 100, 2),
                "event_heat": round((seed % 70) / 100, 2),
                "headline_count": seed % 18,
            },
            "sentiment": {
                "sentiment_score": round(0.35 + (seed % 35) / 100, 2),
                "discussion_heat": round((seed % 80) / 100, 2),
                "kol_divergence": round((seed % 50) / 100, 2),
                "retail_chase_risk": round((seed % 45) / 100, 2),
            },
            "china_market": {
                "northbound_flow_score": round(0.35 + (seed % 30) / 100, 2),
                "margin_flow_score": round(0.30 + (seed % 35) / 100, 2),
                "limit_status": "normal",
                "is_st": False,
                "board": "unknown",
                "policy_theme_score": round((seed % 60) / 100, 2),
            },
        }


class AkShareMarketDataProvider(MarketDataProvider):
    def __init__(
        self,
        period: str = "daily",
        adjust: str = "qfq",
        lookback: int = 90,
        news_lookback_days: int = 2,
        max_news_items: int = 20,
        kline_retries: int = 2,
        timeout_seconds: float = 12.0,
        cache: DataCache | None = None,
    ):
        try:
            import akshare as akshare_module
        except ImportError as exc:
            raise RuntimeError("akshare provider requires installing optional dependency: pip install '.[kline]'") from exc
        self.akshare = akshare_module
        self.period = period
        self.adjust = adjust
        self.lookback = lookback
        self.news_lookback_days = news_lookback_days
        self.max_news_items = max_news_items
        self.kline_retries = kline_retries
        self.timeout_seconds = timeout_seconds
        self.cache = cache
        self.fallback = MockMarketDataProvider()

    def get_market_snapshot(self, benchmarks: list[str]) -> MarketSnapshot:
        return self.fallback.get_market_snapshot(benchmarks)

    def get_security_context(self, symbol: str) -> SecurityContext:
        base = self.fallback.get_security_context(symbol)
        bars, kline_result = self._get_daily_bars_with_result(symbol, self.lookback)
        if not bars:
            news_results = self.get_security_news_results(
                symbol,
                getattr(self, "news_lookback_days", 2),
                getattr(self, "max_news_items", 20),
            )
            news_items = flatten_source_items(news_results)
            news_profile = build_news_profile(news_items)
            news_profile["source_status"] = source_status_from_results(news_results)
            news_profile["source_results"] = source_results_to_dicts(news_results)
            return SecurityContext(
                symbol=base.symbol,
                name=base.name,
                sector=base.sector,
                price=base.price,
                liquidity_score=base.liquidity_score,
                metadata={
                    **base.metadata,
                    "provider": "akshare",
                    "kline_source": "mock_fallback",
                    "kline_error": kline_result.error_message,
                    "kline_source_results": source_results_to_dicts([kline_result]),
                    "news": {
                        **base.metadata.get("news", {}),
                        **news_profile,
                    },
                },
            )
        if not bars:
            return base
        technical = build_technical_profile(bars).as_dict()
        news_results = self.get_security_news_results(symbol, self.news_lookback_days, self.max_news_items)
        news_items = flatten_source_items(news_results)
        news_profile = build_news_profile(news_items)
        news_profile["source_status"] = source_status_from_results(news_results)
        news_profile["source_results"] = source_results_to_dicts(news_results)
        return SecurityContext(
            symbol=base.symbol,
            name=base.name,
            sector=base.sector,
            price=technical["close"] or base.price,
            liquidity_score=base.liquidity_score,
            metadata={
                **base.metadata,
                "provider": "akshare",
                "kline_source": kline_result.provider,
                "kline_source_results": source_results_to_dicts([kline_result]),
                "technical": {
                    **base.metadata.get("technical", {}),
                    **technical,
                },
                "news": {
                    **base.metadata.get("news", {}),
                    **news_profile,
                },
            },
        )

    def get_daily_bars(self, symbol: str, lookback: int = 90) -> list[KLineBar]:
        bars, result = self._get_daily_bars_with_result(symbol, lookback)
        if result.status == SourceStatus.FAILED:
            raise ConnectionError(result.error_message)
        return bars

    def get_daily_bars_result(self, symbol: str, lookback: int = 90) -> SourceResult:
        return self._get_daily_bars_with_result(symbol, lookback)[1]

    def _get_daily_bars_with_result(self, symbol: str, lookback: int = 90) -> tuple[list[KLineBar], SourceResult]:
        period = getattr(self, "period", "daily")
        adjust = getattr(self, "adjust", "qfq")
        cache_key = f"tx_primary|{symbol}|{lookback}|{period}|{adjust}"
        cache = getattr(self, "cache", None)
        if cache:
            cached = cache.load("kline", cache_key)
            if cached:
                bars = kline_bars_from_dicts(cached.get("bars", []))
                return bars, source_results_from_dicts([cached["result"]])[0]
        errors: list[str] = []
        for source, provider, fetcher in [
            ("akshare_stock_zh_a_hist_tx", "akshare.stock_zh_a_hist_tx", self._fetch_daily_bars_tencent),
            ("akshare_stock_zh_a_hist", "akshare.stock_zh_a_hist", self._fetch_daily_bars_eastmoney),
            ("akshare_stock_zh_a_daily", "akshare.stock_zh_a_daily", self._fetch_daily_bars_sina),
        ]:
            try:
                bars = fetcher(symbol, lookback)
                result = SourceResult(
                    data="行情/K线",
                    source=source,
                    provider=provider,
                    status=SourceStatus.SUCCESS if bars else SourceStatus.MISSING,
                    checked_at=datetime.now().isoformat(),
                    context={"symbol": symbol, "lookback": str(lookback)},
                )
                if cache:
                    cache.save("kline", cache_key, {"bars": bars, "result": result})
                return bars, result
            except Exception as exc:
                errors.append(f"{provider}:{type(exc).__name__}:{exc}")
        fallback = self.fallback.get_daily_bars_result(symbol, lookback)
        result = SourceResult(
            data="行情/K线",
            source="akshare_kline_chain",
            provider="akshare.stock_zh_a_hist_tx|stock_zh_a_hist|stock_zh_a_daily",
            status=SourceStatus.FAILED,
            error_type="KLineSourceChainError",
            error_message="; ".join(errors),
            fallback_source=fallback.source,
            checked_at=datetime.now().isoformat(),
            context={"symbol": symbol, "lookback": str(lookback)},
        )
        return [], result

    def _fetch_daily_bars_eastmoney(self, symbol: str, lookback: int) -> list[KLineBar]:
        code = normalize_symbol_for_akshare(symbol)
        start_date, end_date = self._kline_date_range(lookback)
        frame = self._call_with_retry(
            lambda: self.akshare.stock_zh_a_hist(
                symbol=code,
                period=self.period,
                start_date=start_date,
                end_date=end_date,
                adjust=self.adjust,
            ),
            retries=self.kline_retries,
        )
        return [self._row_to_bar(row) for row in self._rows_from_dataframe(frame)][-lookback:]

    def _fetch_daily_bars_sina(self, symbol: str, lookback: int) -> list[KLineBar]:
        start_date, end_date = self._kline_date_range(lookback)
        frame = self._call_with_retry(
            lambda: self.akshare.stock_zh_a_daily(
                symbol=self._symbol_with_exchange_prefix(symbol),
                start_date=start_date,
                end_date=end_date,
                adjust=self.adjust if self.adjust in {"qfq", "hfq"} else "",
            ),
            retries=self.kline_retries,
        )
        return [self._row_to_bar(row) for row in self._rows_from_dataframe(frame)][-lookback:]

    def _fetch_daily_bars_tencent(self, symbol: str, lookback: int) -> list[KLineBar]:
        start_date, end_date = self._kline_date_range(lookback)
        frame = self._call_with_retry(
            lambda: self.akshare.stock_zh_a_hist_tx(
                symbol=self._symbol_with_exchange_prefix(symbol),
                start_date=start_date,
                end_date=end_date,
                adjust=self.adjust if self.adjust in {"qfq", "hfq"} else "",
            ),
            retries=self.kline_retries,
        )
        return [self._row_to_bar(row) for row in self._rows_from_dataframe(frame)][-lookback:]

    def _symbol_with_exchange_prefix(self, symbol: str) -> str:
        code, exchange = symbol.split(".", 1)
        return f"{exchange.lower()}{code}"

    def _kline_date_range(self, lookback: int) -> tuple[str, str]:
        end = datetime.now().date()
        start = end - timedelta(days=max(lookback * 3, 120))
        return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    def _call_with_retry(self, func, retries: int = 2, delay_seconds: float = 0.4):
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return self._call_with_timeout(func)
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(delay_seconds * (attempt + 1))
        raise last_exc if last_exc else RuntimeError("unknown retry failure")

    def _call_with_timeout(self, func):
        timeout_seconds = float(getattr(self, "timeout_seconds", 12.0))
        if timeout_seconds <= 0:
            return func()
        previous_socket_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout_seconds)
        if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
            try:
                return func()
            finally:
                socket.setdefaulttimeout(previous_socket_timeout)

        def handle_timeout(signum, frame):
            raise TimeoutError(f"external data source timed out after {timeout_seconds:g}s")

        previous_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, handle_timeout)
        previous_timer = signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        try:
            return func()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            socket.setdefaulttimeout(previous_socket_timeout)
            if previous_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])

    def _rows_from_dataframe(self, frame: Any) -> list[dict[str, Any]]:
        if hasattr(frame, "tail"):
            frame = frame.tail(self.lookback)
        if hasattr(frame, "to_dict"):
            return list(frame.to_dict("records"))
        return list(frame)

    def _row_to_bar(self, row: dict[str, Any]) -> KLineBar:
        return KLineBar(
            date=str(row.get("日期") or row.get("date")),
            open=float(row.get("开盘") or row.get("open")),
            high=float(row.get("最高") or row.get("high")),
            low=float(row.get("最低") or row.get("low")),
            close=float(row.get("收盘") or row.get("close")),
            volume=float(row.get("成交量") or row.get("volume") or 0.0),
            amount=float(row.get("成交额") or row.get("amount") or 0.0),
        )

    def get_security_news(self, symbol: str, lookback_days: int = 2, limit: int = 20) -> list[NewsItem]:
        return flatten_source_items(self.get_security_news_results(symbol, lookback_days, limit))

    def get_security_news_results(
        self,
        symbol: str,
        lookback_days: int = 2,
        limit: int = 20,
    ) -> list[SourceResult]:
        cache_key = f"{symbol}|{lookback_days}|{limit}|security"
        cache = getattr(self, "cache", None)
        if cache:
            cached = cache.load("security_news", cache_key)
            if cached:
                return source_results_from_dicts(cached)
        code = normalize_symbol_for_akshare(symbol)
        results: list[SourceResult] = []

        results.append(
            self._fetch_source(
                data="个股新闻/公告",
                source="eastmoney_stock_news",
                provider="akshare.stock_news_em",
                context={"symbol": symbol},
                fetch=lambda: self._news_rows_to_items(
                    self._rows_from_dataframe(self.akshare.stock_news_em(symbol=code)),
                    "eastmoney_stock_news",
                    "company",
                ),
                lookback_days=lookback_days,
                limit=limit,
            )
        )

        end_date = datetime.now().strftime("%Y%m%d")
        begin_date = (datetime.now() - timedelta(days=max(lookback_days, 2))).strftime("%Y%m%d")
        results.append(
            self._fetch_source(
                data="个股新闻/公告",
                source="eastmoney_stock_notice",
                provider="akshare.stock_individual_notice_report",
                context={"symbol": symbol},
                fetch=lambda: self._notice_rows_to_items(
                    self._rows_from_dataframe(
                        self.akshare.stock_individual_notice_report(
                            security=code,
                            symbol="全部",
                            begin_date=begin_date,
                            end_date=end_date,
                        )
                    ),
                    "eastmoney_stock_notice",
                ),
                lookback_days=lookback_days,
                limit=limit,
            )
        )

        results.append(
            self._fetch_source(
                data="个股新闻/公告",
                source="cninfo_disclosure",
                provider="akshare.stock_zh_a_disclosure_report_cninfo",
                context={"symbol": symbol},
                fetch=lambda: self._notice_rows_to_items(
                    self._rows_from_dataframe(
                        self.akshare.stock_zh_a_disclosure_report_cninfo(
                            symbol=code,
                            market="沪深京",
                            start_date=begin_date,
                            end_date=end_date,
                        )
                    ),
                    "cninfo_disclosure",
                ),
                lookback_days=lookback_days,
                limit=limit,
            )
        )

        if not any(result.items for result in results):
            fallback_results = self.fallback.get_security_news_results(symbol, lookback_days, limit)
            results = results + [
                SourceResult(
                    data="个股新闻/公告",
                    source=result.source,
                    provider=result.provider,
                    status=SourceStatus.FALLBACK,
                    items=result.items,
                    fallback_source="mock_security_news",
                    checked_at=datetime.now().isoformat(),
                    context={"symbol": symbol},
                )
                for result in fallback_results
            ]
            if cache:
                cache.save("security_news", cache_key, results)
            return results
        if cache:
            cache.save("security_news", cache_key, results)
        return results

    def get_market_news(self, lookback_days: int = 2, limit: int = 50) -> list[NewsItem]:
        return flatten_source_items(self.get_market_news_results(lookback_days, limit))

    def get_market_news_results(self, lookback_days: int = 2, limit: int = 50) -> list[SourceResult]:
        cache_key = f"{lookback_days}|{limit}|market"
        cache = getattr(self, "cache", None)
        if cache:
            cached = cache.load("market_news", cache_key)
            if cached:
                return source_results_from_dicts(cached)
        results = [
            self._fetch_source(
                data="市场新闻/政策",
                source="eastmoney_global",
                provider="akshare.stock_info_global_em",
                context={},
                fetch=lambda: self._news_rows_to_items(
                    self._rows_from_dataframe(self.akshare.stock_info_global_em()),
                    "eastmoney_global",
                    "market",
                ),
                lookback_days=lookback_days,
                limit=limit,
            ),
            self._fetch_source(
                data="市场新闻/政策",
                source="cctv",
                provider="akshare.news_cctv",
                context={},
                fetch=lambda: self._news_rows_to_items(
                    self._rows_from_dataframe(self.akshare.news_cctv(date=datetime.now().strftime("%Y%m%d"))),
                    "cctv",
                    "policy",
                ),
                lookback_days=lookback_days,
                limit=limit,
            ),
        ]
        if not any(result.items for result in results):
            fallback_results = self.fallback.get_market_news_results(lookback_days, limit)
            results = results + [
                SourceResult(
                    data="市场新闻/政策",
                    source=result.source,
                    provider=result.provider,
                    status=SourceStatus.FALLBACK,
                    items=result.items,
                    fallback_source="mock_market_news",
                    checked_at=datetime.now().isoformat(),
                    context={},
                )
                for result in fallback_results
            ]
            if cache:
                cache.save("market_news", cache_key, results)
            return results
        if cache:
            cache.save("market_news", cache_key, results)
        return results

    def _fetch_source(
        self,
        data: str,
        source: str,
        provider: str,
        context: dict[str, str],
        fetch,
        lookback_days: int,
        limit: int,
        retries: int = 1,
    ) -> SourceResult:
        checked_at = datetime.now().isoformat()
        last_exc: Exception | None = None
        for _ in range(retries + 1):
            try:
                items = filter_recent_items(self._call_with_timeout(fetch), lookback_days)[:limit]
                status = SourceStatus.SUCCESS if items else SourceStatus.MISSING
                return SourceResult(
                    data=data,
                    source=source,
                    provider=provider,
                    status=status,
                    items=items,
                    checked_at=checked_at,
                    context=context,
                )
            except Exception as exc:
                last_exc = exc
        return SourceResult(
            data=data,
            source=source,
            provider=provider,
            status=SourceStatus.FAILED,
            error_type=type(last_exc).__name__ if last_exc else "UnknownError",
            error_message=str(last_exc) if last_exc else "unknown error",
            checked_at=checked_at,
            context=context,
        )

    def _news_rows_to_items(self, rows: list[dict[str, Any]], source: str, category: str) -> list[NewsItem]:
        items: list[NewsItem] = []
        for row in rows:
            title = row_value(row, "新闻标题", "标题", "title", "内容", "摘要")
            if not title:
                continue
            items.append(
                NewsItem(
                    title=str(title),
                    source=source,
                    published_at=str(row_value(row, "发布时间", "时间", "日期", "date") or ""),
                    url=row_value(row, "新闻链接", "链接", "url"),
                    summary=row_value(row, "新闻内容", "摘要", "内容"),
                    category=category,
                )
            )
        return items

    def _notice_rows_to_items(self, rows: list[dict[str, Any]], source: str) -> list[NewsItem]:
        items: list[NewsItem] = []
        for row in rows:
            title = row_value(row, "公告标题", "标题", "notice_title", "art_title")
            if not title:
                continue
            items.append(
                NewsItem(
                    title=str(title),
                    source=source,
                    published_at=str(row_value(row, "公告日期", "日期", "notice_date") or ""),
                    url=row_value(row, "公告链接", "链接", "url"),
                    summary=row_value(row, "公告类型", "类型", "category"),
                    category="announcement",
                )
            )
        return items


def build_provider(provider_name: str, config: dict[str, Any] | None = None) -> MarketDataProvider:
    config = config or {}
    if provider_name == "mock":
        return MockMarketDataProvider()
    if provider_name == "akshare":
        return AkShareMarketDataProvider(
            period=str(config.get("kline_period", "daily")),
            adjust=str(config.get("kline_adjust", "qfq")),
            lookback=int(config.get("kline_lookback", 90)),
            news_lookback_days=int(config.get("news_lookback_days", config.get("lookback_days", 2))),
            max_news_items=int(config.get("max_news_items", config.get("max_items_per_symbol", 20))),
            kline_retries=int(config.get("kline_retries", 2)),
            timeout_seconds=float(config.get("timeout_seconds", config.get("source_timeout_seconds", 12))),
            cache=config.get("cache"),
        )
    raise ValueError(f"unsupported data provider: {provider_name}")
