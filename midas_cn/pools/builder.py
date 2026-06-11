from __future__ import annotations

from datetime import date, datetime, timedelta
import time
from typing import Any

from midas_cn.data.news import row_value
from midas_cn.models import SourceStatus, StockPool, StockPoolEntry
from midas_cn.pools.ths_cache import symbol_classification
from midas_cn.universe.symbols import normalize_symbol


POOL_MAIN_NET_INFLOW = "main_net_inflow_top20"
POOL_SMALL_FLOAT_NET_INFLOW = "small_float_net_inflow_top20"
POOL_TURNOVER = "turnover_top20"
POOL_LIMIT_UP = "limit_up"
POOL_LIMIT_DOWN = "limit_down"
POOL_BROKEN_LIMIT_UP = "broken_limit_up"
POOL_NATIONAL_TEAM_ETF_WATCH = "national_team_etf_watch"


NATIONAL_TEAM_ETF_TARGETS = (
    {"code": "510300", "name": "沪深300ETF", "category": "沪深300"},
    {"code": "510310", "name": "沪深300ETF易方达", "category": "沪深300"},
    {"code": "510330", "name": "沪深300ETF华夏", "category": "沪深300"},
    {"code": "159919", "name": "沪深300ETF", "category": "沪深300"},
    {"code": "510050", "name": "上证50ETF", "category": "上证50"},
    {"code": "510500", "name": "中证500ETF", "category": "中证500"},
    {"code": "512500", "name": "中证500ETF", "category": "中证500"},
    {"code": "512100", "name": "中证1000ETF", "category": "中证1000"},
    {"code": "159845", "name": "中证1000ETF", "category": "中证1000"},
    {"code": "588000", "name": "科创50ETF", "category": "科创50"},
    {"code": "588080", "name": "科创板50ETF", "category": "科创50"},
    {"code": "159915", "name": "创业板ETF", "category": "创业板"},
)


class AkShareStockPoolBuilder:
    def __init__(
        self,
        akshare_module,
        top_n: int = 20,
        small_float_cap: float = 100_000_000_000,
        industry_enrich_limit: int = 8,
        industry_enrich_timeout_seconds: float = 8.0,
        sector_cache: dict[str, Any] | None = None,
        progress: Any | None = None,
    ):
        self.akshare = akshare_module
        self.top_n = top_n
        self.small_float_cap = small_float_cap
        self.industry_enrich_limit = industry_enrich_limit
        self.industry_enrich_timeout_seconds = industry_enrich_timeout_seconds
        self.sector_cache = sector_cache or {}
        self.progress = progress
        self._industry_cache: dict[str, str] = {}

    def build(self, trade_date: str | None = None) -> list[StockPool]:
        as_of = trade_date or latest_report_trade_date()
        self._emit("拉取资金流选股池")
        fund_rows, fund_status, fund_source, fund_error = self._fetch_fund_rows()
        self._emit("拉取全市场行情快照")
        spot_rows, spot_status, spot_source, spot_error = self._fetch_spot_rows()
        spot_rows = [normalize_spot_row(row) for row in spot_rows]
        spot_by_code = {str(row_value(row, "代码", "code")).zfill(6): row for row in spot_rows if row_value(row, "代码", "code")}

        self._emit("生成资金流、换手率选股池")
        pools = [
            self._main_net_inflow_pool(fund_rows, spot_by_code, as_of, fund_status, fund_source, fund_error),
            self._small_float_net_inflow_pool(
                fund_rows,
                spot_by_code,
                as_of,
                combine_status(fund_status, spot_status),
                combine_sources((fund_source, fund_status), (spot_source, spot_status)),
                combine_errors(fund_error, spot_error),
            ),
            self._turnover_pool(spot_rows, as_of, spot_status, spot_source, spot_error),
        ]
        self._emit("拉取涨停池")
        pools.append(
            self._limit_pool(
                POOL_LIMIT_UP,
                "当日涨停",
                "akshare.stock_zt_pool_em",
                lambda: self.akshare.stock_zt_pool_em(date=as_of),
                as_of,
                fallback_rows=self._derived_limit_rows(spot_rows, "up"),
                fallback_source=f"derived.spot.limit_up({spot_source})",
            )
        )
        self._emit("拉取跌停池")
        pools.append(
            self._limit_pool(
                POOL_LIMIT_DOWN,
                "当日跌停",
                "akshare.stock_zt_pool_dtgc_em",
                lambda: self.akshare.stock_zt_pool_dtgc_em(date=as_of),
                as_of,
                fallback_rows=self._derived_limit_rows(spot_rows, "down"),
                fallback_source=f"derived.spot.limit_down({spot_source})",
            )
        )
        self._emit("拉取炸板池")
        pools.append(
            self._limit_pool(
                POOL_BROKEN_LIMIT_UP,
                "当日炸板",
                "akshare.stock_zt_pool_zbgc_em",
                lambda: self.akshare.stock_zt_pool_zbgc_em(date=as_of),
                as_of,
            )
        )
        self._emit("拉取国家队ETF监控")
        pools.append(self._national_team_etf_pool(as_of))
        self._emit("补齐选股池行业与概念")
        return self._fill_missing_industries(pools)

    def _emit(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def _main_net_inflow_pool(
        self,
        rows: list[dict[str, Any]],
        spot_by_code: dict[str, dict[str, Any]],
        as_of: str,
        status: SourceStatus,
        source: str,
        error: str | None,
    ) -> StockPool:
        merged = merge_spot_rows(rows, spot_by_code)
        entries = self._ranked_entries(
            merged,
            reason="主力净额流入Top20",
            sort_field_candidates=("今日主力净流入-净额", "今日主力净流入", "主力净流入", "净额", "净流入"),
            extra_metrics=("今日主力净流入-净额", "今日主力净流入", "主力净流入", "净额", "今日涨跌幅", "涨跌幅", "最新价", "成交额", "流通市值", "所属行业"),
        )
        return StockPool(
            name=POOL_MAIN_NET_INFLOW,
            description="主力净额流入top20，不含ST",
            entries=entries,
            source=source,
            status=status,
            as_of=as_of,
            error_message=error,
        )

    def _small_float_net_inflow_pool(
        self,
        fund_rows: list[dict[str, Any]],
        spot_by_code: dict[str, dict[str, Any]],
        as_of: str,
        status: SourceStatus,
        source: str,
        error: str | None,
    ) -> StockPool:
        merged = []
        for row in fund_rows:
            code = str(row_value(row, "代码", "code") or "").zfill(6)
            spot = spot_by_code.get(code, {})
            float_cap = to_number(row_value(row, "流通市值") or row_value(spot, "流通市值"))
            if float_cap is None or float_cap >= self.small_float_cap:
                continue
            merged.append({**spot, **row, "流通市值": float_cap})
        entries = self._ranked_entries(
            merged,
            reason="流通市值<1000亿成交净额流入Top20",
            sort_field_candidates=("今日主力净流入-净额", "今日主力净流入", "主力净流入", "净额", "净流入"),
            extra_metrics=("今日主力净流入-净额", "今日主力净流入", "主力净流入", "净额", "流通市值", "今日涨跌幅", "涨跌幅", "成交额", "所属行业"),
        )
        return StockPool(
            name=POOL_SMALL_FLOAT_NET_INFLOW,
            description="流通市值<1000亿中成交净额流入top20，不含ST",
            entries=entries,
            source=source,
            status=status,
            as_of=as_of,
            error_message=error,
        )

    def _turnover_pool(
        self,
        rows: list[dict[str, Any]],
        as_of: str,
        status: SourceStatus,
        source: str,
        error: str | None,
    ) -> StockPool:
        entries = self._ranked_entries(
            rows,
            reason="换手率Top20",
            sort_field_candidates=("换手率",),
            extra_metrics=("换手率", "成交额", "涨跌幅", "最新价", "流通市值", "所属行业"),
        )
        return StockPool(
            name=POOL_TURNOVER,
            description="换手率top20，不含ST",
            entries=entries,
            source=source,
            status=status,
            as_of=as_of,
            error_message=error,
        )

    def _limit_pool(
        self,
        name: str,
        description: str,
        source: str,
        fetch,
        as_of: str,
        fallback_rows: list[dict[str, Any]] | None = None,
        fallback_source: str | None = None,
    ) -> StockPool:
        rows, error = self._fetch_rows(fetch)
        status = SourceStatus.FAILED if error else SourceStatus.SUCCESS
        source_name = source
        error_message = error
        if error and fallback_rows:
            rows = fallback_rows
            status = SourceStatus.FALLBACK
            source_name = fallback_source or source
            error_message = f"primary {source} failed: {error}"
        entries = self._ranked_entries(
            rows,
            reason=description,
            sort_field_candidates=("成交额",),
            extra_metrics=("涨跌幅", "最新价", "成交额", "换手率", "流通市值", "炸板次数", "连板数", "所属行业"),
        )
        return StockPool(
            name=name,
            description=f"{description}，不含ST",
            entries=entries,
            source=source_name,
            status=status,
            as_of=as_of,
            error_message=error_message,
        )

    def _national_team_etf_pool(self, as_of: str) -> StockPool:
        rows, error = self._fetch_rows(self._fund_etf_spot_rows)
        source = "akshare.fund_etf_spot_em"
        if error:
            return StockPool(
                name=POOL_NATIONAL_TEAM_ETF_WATCH,
                description="国家队ETF行为监控",
                entries=[],
                source=source,
                status=SourceStatus.FAILED,
                as_of=as_of,
                error_message=error,
            )

        target_by_code = {item["code"]: item for item in NATIONAL_TEAM_ETF_TARGETS}
        normalized_rows = normalize_etf_spot_rows(rows)
        entries: list[StockPoolEntry] = []
        history_errors = []
        has_history_fetch = getattr(self.akshare, "fund_etf_hist_em", None) is not None
        if has_history_fetch:
            source = "akshare.fund_etf_spot_em + akshare.fund_etf_hist_em"
        monitored_rows = []
        for row in normalized_rows:
            code = str(row_value(row, "代码", "基金代码", "code", "symbol") or "").zfill(6)
            if code not in target_by_code:
                continue
            target = target_by_code[code]
            history_metrics, history_error = self._etf_history_metrics(code, as_of)
            if history_error:
                history_errors.append(f"{code}: {history_error}")
            item = {
                **row,
                **history_metrics,
                "代码": code,
                "名称": str(row_value(row, "名称", "基金简称") or target["name"]),
                "ETF类别": target["category"],
                "所属行业": "宽基ETF",
            }
            item.update(national_team_etf_metrics(item))
            monitored_rows.append(item)

        ranked = sorted(
            monitored_rows,
            key=lambda item: (
                to_number(item.get("承接评分")) or 0,
                to_number(item.get("成交额")) or 0,
            ),
            reverse=True,
        )
        for rank, row in enumerate(ranked[: self.top_n], start=1):
            code = str(row.get("代码") or "").zfill(6)
            metrics = {
                field: row[field]
                for field in (
                    "ETF类别",
                    "所属行业",
                    "最新价",
                    "涨跌幅",
                    "成交额",
                    "成交量",
                    "换手率",
                    "折溢价率",
                    "净额",
                    "5日均额",
                    "成交额/5日均额",
                    "承接评分",
                    "监控信号",
                    "行为推断",
                )
                if field in row and row[field] is not None
            }
            entries.append(
                StockPoolEntry(
                    symbol=normalize_symbol(code),
                    name=str(row.get("名称") or target_by_code[code]["name"]),
                    reason="国家队ETF行为监控",
                    rank=rank,
                    metrics=metrics,
                )
            )

        if not entries:
            return StockPool(
                name=POOL_NATIONAL_TEAM_ETF_WATCH,
                description="国家队ETF行为监控",
                entries=[],
                source=source,
                status=SourceStatus.MISSING,
                as_of=as_of,
                error_message="fund_etf_spot_em did not return monitored broad ETF rows",
            )
        return StockPool(
            name=POOL_NATIONAL_TEAM_ETF_WATCH,
            description="国家队ETF行为监控",
            entries=entries,
            source=source,
            status=SourceStatus.PARTIAL if history_errors else SourceStatus.SUCCESS,
            as_of=as_of,
            error_message="; ".join(history_errors[:5]) if history_errors else None,
        )

    def _fund_etf_spot_rows(self):
        fetch = getattr(self.akshare, "fund_etf_spot_em", None)
        if not fetch:
            raise AttributeError("akshare missing fund_etf_spot_em")
        return fetch()

    def _etf_history_metrics(self, code: str, as_of: str) -> tuple[dict[str, Any], str | None]:
        fetch = getattr(self.akshare, "fund_etf_hist_em", None)
        if not fetch:
            return {"历史均额状态": "missing"}, None
        end_date = _compact_date(as_of) or datetime.now().strftime("%Y%m%d")
        try:
            start = datetime.strptime(end_date, "%Y%m%d").date() - timedelta(days=45)
        except ValueError:
            start = datetime.now().date() - timedelta(days=45)
        rows, error = self._fetch_rows(
            lambda: fetch(
                symbol=code,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end_date,
                adjust="",
            )
        )
        if error:
            return {"历史均额状态": "failed"}, error
        rows = sorted(rows, key=lambda row: _compact_date(row_value(row, "日期", "date", "交易日期")))
        previous_rows = [
            row
            for row in rows
            if _compact_date(row_value(row, "日期", "date", "交易日期"))
            and _compact_date(row_value(row, "日期", "date", "交易日期")) < end_date
        ]
        if not previous_rows and len(rows) > 1:
            previous_rows = rows[:-1]
        amounts = [
            amount
            for amount in (to_number(row_value(row, "成交额", "amount")) for row in previous_rows[-5:])
            if amount is not None and amount > 0
        ]
        if not amounts:
            return {"历史均额状态": "missing"}, None
        return {"5日均额": sum(amounts) / len(amounts), "历史均额状态": "success"}, None

    def _derived_limit_rows(self, spot_rows: list[dict[str, Any]], direction: str) -> list[dict[str, Any]]:
        rows = []
        for row in spot_rows:
            pct_change = to_number(row_value(row, "涨跌幅", "今日涨跌幅"))
            if pct_change is None:
                continue
            if direction == "up" and pct_change < 9.8:
                continue
            if direction == "down" and pct_change > -9.8:
                continue
            item = dict(row)
            item["涨跌幅"] = pct_change
            rows.append(item)
        return rows

    def _ranked_entries(
        self,
        rows: list[dict[str, Any]],
        reason: str,
        sort_field_candidates: tuple[str, ...],
        extra_metrics: tuple[str, ...],
    ) -> list[StockPoolEntry]:
        filtered = [row for row in rows if not is_st_name(str(row_value(row, "名称", "name") or ""))]
        ranked = sorted(filtered, key=lambda row: sort_value(row, sort_field_candidates), reverse=True)
        entries: list[StockPoolEntry] = []
        for rank, row in enumerate(ranked[: self.top_n], start=1):
            raw_code = row_value(row, "代码", "code")
            if raw_code is None or str(raw_code).strip() == "":
                continue
            code = str(raw_code).strip().zfill(6)
            name = str(row_value(row, "名称", "name") or "")
            metrics = {}
            for field in extra_metrics:
                value = row_value(row, field)
                if value is not None:
                    metrics[field] = value
            entries.append(
                StockPoolEntry(
                    symbol=normalize_symbol(code),
                    name=name,
                    reason=reason,
                    rank=rank,
                    metrics=metrics,
                )
            )
        return entries

    def _fill_missing_industries(self, pools: list[StockPool]) -> list[StockPool]:
        started_at = time.monotonic()
        lookup_count = 0
        enriched_pools = []
        for pool in pools:
            enriched_entries = []
            for entry in pool.entries:
                cached = symbol_classification(self.sector_cache, entry.symbol) if self.sector_cache else {}
                if cached:
                    metrics = dict(entry.metrics)
                    if cached.get("industry"):
                        metrics["所属行业"] = cached["industry"]
                        metrics["行业来源"] = cached.get("industry_source", "同花顺行业缓存")
                    if cached.get("concepts"):
                        metrics["概念"] = list(cached.get("concepts") or [])[:8]
                        metrics["概念来源"] = cached.get("concept_source", "同花顺概念缓存")
                    enriched_entries.append(
                        StockPoolEntry(
                            symbol=entry.symbol,
                            name=entry.name,
                            reason=entry.reason,
                            rank=entry.rank,
                            metrics=metrics,
                        )
                    )
                    continue
                if entry.metrics.get("所属行业"):
                    enriched_entries.append(entry)
                    continue
                if lookup_count >= self.industry_enrich_limit:
                    enriched_entries.append(entry)
                    continue
                if time.monotonic() - started_at >= self.industry_enrich_timeout_seconds:
                    enriched_entries.append(entry)
                    continue
                lookup_count += 1
                industry = self._industry_for_symbol(entry.symbol)
                if not industry:
                    enriched_entries.append(entry)
                    continue
                enriched_entries.append(
                    StockPoolEntry(
                        symbol=entry.symbol,
                        name=entry.name,
                        reason=entry.reason,
                        rank=entry.rank,
                        metrics={**entry.metrics, "所属行业": industry},
                    )
                )
            enriched_pools.append(
                StockPool(
                    name=pool.name,
                    description=pool.description,
                    entries=enriched_entries,
                    source=pool.source,
                    status=pool.status,
                    as_of=pool.as_of,
                    error_message=pool.error_message,
                )
            )
        return enriched_pools

    def _industry_for_symbol(self, symbol: str) -> str:
        code = normalize_symbol(symbol).split(".", 1)[0]
        if code in self._industry_cache:
            return self._industry_cache[code]
        industry = self._industry_from_cninfo(code)
        if industry:
            self._industry_cache[code] = industry
            return industry
        fetch = getattr(self.akshare, "stock_individual_info_em", None)
        if not fetch:
            self._industry_cache[code] = ""
            return ""
        try:
            frame = fetch(symbol=code)
            rows = list(frame.to_dict("records")) if hasattr(frame, "to_dict") else list(frame)
            for row in rows:
                item = str(row_value(row, "item") or "").strip()
                value = str(row_value(row, "value") or "").strip()
                if item == "行业" and value:
                    self._industry_cache[code] = value
                    return value
        except Exception:
            pass
        self._industry_cache[code] = ""
        return ""

    def _industry_from_cninfo(self, code: str) -> str:
        fetch = getattr(self.akshare, "stock_industry_change_cninfo", None)
        if not fetch:
            return ""
        try:
            frame = fetch(symbol=code, start_date="19900101", end_date=datetime.now().strftime("%Y%m%d"))
            rows = list(frame.to_dict("records")) if hasattr(frame, "to_dict") else list(frame)
        except Exception:
            return ""
        rows = sorted(rows, key=lambda row: str(row_value(row, "变更日期") or ""), reverse=True)
        preferred = [row for row in rows if "申银万国" in str(row_value(row, "分类标准") or "")]
        for row in preferred + rows:
            for field in ("行业次类", "行业大类", "行业中类", "行业门类"):
                value = row_value(row, field)
                if value is not None and str(value).strip() and str(value).lower() != "nan":
                    return str(value).strip()
        return ""

    def _fetch_rows(self, fetch, retries: int = 0, delay_seconds: float = 0.5) -> tuple[list[dict[str, Any]], str | None]:
        last_error = None
        for attempt in range(retries + 1):
            try:
                frame = fetch()
                if hasattr(frame, "to_dict"):
                    return list(frame.to_dict("records")), None
                return list(frame), None
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < retries:
                    time.sleep(delay_seconds * (attempt + 1))
        return [], last_error

    def _fetch_fund_rows(self) -> tuple[list[dict[str, Any]], SourceStatus, str, str | None]:
        rows, error = self._fetch_rows(lambda: self.akshare.stock_main_fund_flow("全部股票"))
        if not error:
            return normalize_fund_rows(rows), SourceStatus.SUCCESS, "akshare.stock_main_fund_flow", None

        fallback = getattr(self.akshare, "stock_individual_fund_flow_rank", None)
        if fallback:
            fallback_rows, fallback_error = self._fetch_rows(lambda: fallback(indicator="今日"))
            if not fallback_error:
                return (
                    normalize_fund_rows(fallback_rows),
                    SourceStatus.SUCCESS,
                    "akshare.stock_individual_fund_flow_rank(今日)",
                    f"primary akshare.stock_main_fund_flow failed: {error}",
                )
            error = f"primary akshare.stock_main_fund_flow failed: {error}; fallback akshare.stock_individual_fund_flow_rank(今日) failed: {fallback_error}"
        fallback = getattr(self.akshare, "stock_fund_flow_individual", None)
        if fallback:
            fallback_rows, fallback_error = self._fetch_rows(lambda: fallback(symbol="即时"), retries=2, delay_seconds=1.0)
            if not fallback_error:
                return (
                    normalize_fund_rows(fallback_rows),
                    SourceStatus.SUCCESS,
                    "akshare.stock_fund_flow_individual(即时)",
                    error,
                )
            error = f"{error}; fallback akshare.stock_fund_flow_individual(即时) failed: {fallback_error}"
        return [], SourceStatus.FAILED, "akshare.stock_main_fund_flow", error

    def _fetch_spot_rows(self) -> tuple[list[dict[str, Any]], SourceStatus, str, str | None]:
        rows, error = self._fetch_rows(self.akshare.stock_zh_a_spot_em)
        if not error:
            return rows, SourceStatus.SUCCESS, "akshare.stock_zh_a_spot_em", None

        fallback_rows, fallback_error = self._fetch_rows(self._sina_spot_rows)
        if not fallback_error:
            return (
                fallback_rows,
                SourceStatus.SUCCESS,
                "sina.Market_Center.getHQNodeData",
                f"primary akshare.stock_zh_a_spot_em failed: {error}",
            )
        return (
            [],
            SourceStatus.FAILED,
            "akshare.stock_zh_a_spot_em",
            f"primary akshare.stock_zh_a_spot_em failed: {error}; fallback sina.Market_Center.getHQNodeData failed: {fallback_error}",
        )

    def _sina_spot_rows(self) -> list[dict[str, Any]]:
        import math
        import re

        import requests

        from akshare.stock.cons import zh_sina_a_stock_count_url, zh_sina_a_stock_payload, zh_sina_a_stock_url
        from akshare.utils import demjson

        count_response = requests.get(zh_sina_a_stock_count_url, timeout=15)
        count_response.raise_for_status()
        total_match = re.search(r"\d+", count_response.text)
        if not total_match:
            raise ValueError("sina stock count response missing total")
        total = int(total_match.group(0))
        pages = math.ceil(total / int(zh_sina_a_stock_payload.get("num", 80)))
        payload = zh_sina_a_stock_payload.copy()
        rows: list[dict[str, Any]] = []
        for page in range(1, pages + 1):
            payload.update({"page": str(page)})
            response = requests.get(zh_sina_a_stock_url, params=payload, timeout=15)
            response.raise_for_status()
            for item in demjson.decode(response.text):
                rows.append(
                    {
                        "代码": item.get("code"),
                        "名称": item.get("name"),
                        "最新价": to_number(item.get("trade")),
                        "涨跌额": to_number(item.get("pricechange")),
                        "涨跌幅": to_number(item.get("changepercent")),
                        "成交量": to_number(item.get("volume")),
                        "成交额": to_number(item.get("amount")),
                        "总市值": multiply_optional(to_number(item.get("mktcap")), 10_000),
                        "流通市值": multiply_optional(to_number(item.get("nmc")), 10_000),
                        "换手率": to_number(item.get("turnoverratio")),
                    }
                )
        return rows


def latest_report_trade_date(today: date | None = None) -> str:
    current = today or datetime.now().date()
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current.strftime("%Y%m%d")


def is_st_name(name: str) -> bool:
    normalized = name.upper().replace("＊", "*")
    return "ST" in normalized


def sort_value(row: dict[str, Any], candidates: tuple[str, ...]) -> float:
    for field in candidates:
        value = to_number(row_value(row, field))
        if value is not None:
            return value
    return float("-inf")


def normalize_fund_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        code = row_value(row, "代码", "股票代码", "code", "symbol")
        name = row_value(row, "名称", "股票简称", "name")
        net_amount = row_value(row, "今日主力净流入-净额", "今日主力净流入", "主力净流入", "净额", "资金流入净额")
        pct_change = row_value(row, "今日涨跌幅", "涨跌幅", "阶段涨跌幅")
        item = dict(row)
        if code is not None:
            item["代码"] = str(code).zfill(6)
        if name is not None:
            item["名称"] = name
        if net_amount is not None:
            item["净额"] = to_number(net_amount)
            item.setdefault("今日主力净流入-净额", item["净额"])
        if pct_change is not None:
            item["涨跌幅"] = to_number(pct_change)
            item.setdefault("今日涨跌幅", item["涨跌幅"])
        return_value = row_value(row, "换手率", "连续换手率")
        if return_value is not None:
            item["换手率"] = to_number(return_value)
        amount = row_value(row, "成交额")
        if amount is not None:
            item["成交额"] = to_number(amount)
        normalized.append(item)
    return normalized


def normalize_spot_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    code = row_value(row, "代码", "股票代码", "code", "symbol")
    industry = row_value(row, "所属行业", "行业", "板块")
    if code is not None:
        item["代码"] = str(code).zfill(6)
    if industry is not None and str(industry).strip():
        item["所属行业"] = str(industry).strip()
    return item


def normalize_etf_spot_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        code = row_value(row, "代码", "基金代码", "code", "symbol")
        name = row_value(row, "名称", "基金简称", "name")
        amount = row_value(row, "成交额", "amount")
        volume = row_value(row, "成交量", "volume")
        pct_change = row_value(row, "涨跌幅", "日增长率", "增长率", "changepercent")
        latest = row_value(row, "最新价", "市价", "最新净值", "单位净值", "trade")
        turnover = row_value(row, "换手率", "turnoverratio")
        discount = row_value(row, "折溢价率", "折价率", "溢价率")
        net_amount = row_value(row, "净额", "主力净流入", "主力净流入-净额", "资金流入净额")
        item = dict(row)
        if code is not None:
            item["代码"] = str(code).zfill(6)
        if name is not None:
            item["名称"] = str(name)
        if amount is not None:
            item["成交额"] = to_number(amount)
        if volume is not None:
            item["成交量"] = to_number(volume)
        if pct_change is not None:
            item["涨跌幅"] = to_number(pct_change)
        if latest is not None:
            item["最新价"] = to_number(latest)
        if turnover is not None:
            item["换手率"] = to_number(turnover)
        if discount is not None:
            item["折溢价率"] = to_number(discount)
        if net_amount is not None:
            item["净额"] = to_number(net_amount)
        normalized.append(item)
    return normalized


def merge_spot_rows(rows: list[dict[str, Any]], spot_by_code: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    merged = []
    for row in rows:
        code = str(row_value(row, "代码", "股票代码", "code", "symbol") or "").zfill(6)
        spot = spot_by_code.get(code, {})
        merged.append({**spot, **row})
    return merged


def to_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "None", "nan"}:
        return None
    multiplier = 1.0
    if text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("%"):
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def multiply_optional(value: float | None, multiplier: float) -> float | None:
    if value is None:
        return None
    return value * multiplier


def national_team_etf_metrics(row: dict[str, Any]) -> dict[str, Any]:
    amount = to_number(row_value(row, "成交额"))
    avg_amount = to_number(row_value(row, "5日均额"))
    pct_change = to_number(row_value(row, "涨跌幅"))
    net_amount = to_number(row_value(row, "净额"))
    amount_ratio = amount / avg_amount if amount and avg_amount and avg_amount > 0 else None
    score = 0.0
    if amount is not None:
        if amount >= 10_000_000_000:
            score += 0.34
        elif amount >= 5_000_000_000:
            score += 0.24
        elif amount >= 2_000_000_000:
            score += 0.14
        elif amount >= 800_000_000:
            score += 0.06
    if amount_ratio is not None:
        if amount_ratio >= 2.5:
            score += 0.34
        elif amount_ratio >= 1.8:
            score += 0.25
        elif amount_ratio >= 1.3:
            score += 0.13
    if net_amount is not None and net_amount > 0:
        score += min(net_amount / 5_000_000_000, 0.12)
    if pct_change is not None and amount_ratio is not None:
        if pct_change <= -1.0 and amount_ratio >= 1.5:
            score += 0.10
        elif -0.8 <= pct_change <= 0.8 and amount_ratio >= 1.3:
            score += 0.06
        elif pct_change >= 2.5 and amount_ratio < 1.3:
            score -= 0.04
    score = round(max(0.0, min(1.0, score)), 3)
    return {
        "成交额/5日均额": round(amount_ratio, 2) if amount_ratio is not None else None,
        "承接评分": score,
        "监控信号": national_team_etf_signal(score),
        "行为推断": national_team_etf_inference(score, pct_change, amount_ratio),
    }


def national_team_etf_signal(score: float) -> str:
    if score >= 0.72:
        return "疑似托底强"
    if score >= 0.45:
        return "放量承接"
    if score >= 0.25:
        return "温和增量"
    return "无明显异动"


def national_team_etf_inference(score: float, pct_change: float | None, amount_ratio: float | None) -> str:
    if score >= 0.72 and pct_change is not None and pct_change <= 0:
        return "宽基ETF逆势放量，疑似稳定资金承接；不能确认交易主体。"
    if score >= 0.72:
        return "宽基ETF显著放量，疑似中长期资金或配置资金集中流入；不能确认交易主体。"
    if score >= 0.45:
        return "ETF成交明显高于近期均值，说明指数层面有承接资金。"
    if score >= 0.25:
        return "ETF成交温和放大，仅作为风险偏好修复线索。"
    if amount_ratio is None:
        return "缺少历史均额，暂仅按当日成交额观察。"
    return "未见异常放量，不作为国家队行为确认。"


def _compact_date(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def combine_status(*statuses: SourceStatus) -> SourceStatus:
    if any(status == SourceStatus.FAILED for status in statuses):
        return SourceStatus.FAILED
    if any(status == SourceStatus.FALLBACK for status in statuses):
        return SourceStatus.FALLBACK
    return SourceStatus.SUCCESS


def combine_sources(*sources: tuple[str, SourceStatus]) -> str:
    usable = [source for source, status in sources if status in {SourceStatus.SUCCESS, SourceStatus.FALLBACK}]
    if usable:
        return " + ".join(usable)
    return " + ".join(source for source, _ in sources)


def combine_errors(*errors: str | None) -> str | None:
    messages = [error for error in errors if error]
    if not messages:
        return None
    return "; ".join(messages)
