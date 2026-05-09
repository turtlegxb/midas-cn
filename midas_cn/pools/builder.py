from __future__ import annotations

from datetime import date, datetime, timedelta
import time
from typing import Any

from midas_cn.data.news import row_value
from midas_cn.models import SourceStatus, StockPool, StockPoolEntry
from midas_cn.universe.symbols import normalize_symbol


POOL_MAIN_NET_INFLOW = "main_net_inflow_top20"
POOL_SMALL_FLOAT_NET_INFLOW = "small_float_net_inflow_top20"
POOL_TURNOVER = "turnover_top20"
POOL_LIMIT_UP = "limit_up"
POOL_LIMIT_DOWN = "limit_down"
POOL_BROKEN_LIMIT_UP = "broken_limit_up"


class AkShareStockPoolBuilder:
    def __init__(
        self,
        akshare_module,
        top_n: int = 20,
        small_float_cap: float = 100_000_000_000,
        industry_enrich_limit: int = 8,
        industry_enrich_timeout_seconds: float = 8.0,
    ):
        self.akshare = akshare_module
        self.top_n = top_n
        self.small_float_cap = small_float_cap
        self.industry_enrich_limit = industry_enrich_limit
        self.industry_enrich_timeout_seconds = industry_enrich_timeout_seconds
        self._industry_cache: dict[str, str] = {}

    def build(self, trade_date: str | None = None) -> list[StockPool]:
        as_of = trade_date or latest_report_trade_date()
        fund_rows, fund_status, fund_source, fund_error = self._fetch_fund_rows()
        spot_rows, spot_status, spot_source, spot_error = self._fetch_spot_rows()
        spot_rows = [normalize_spot_row(row) for row in spot_rows]
        spot_by_code = {str(row_value(row, "代码", "code")).zfill(6): row for row in spot_rows if row_value(row, "代码", "code")}

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
            self._limit_pool(
                POOL_LIMIT_UP,
                "当日涨停",
                "akshare.stock_zt_pool_em",
                lambda: self.akshare.stock_zt_pool_em(date=as_of),
                as_of,
            ),
            self._limit_pool(
                POOL_LIMIT_DOWN,
                "当日跌停",
                "akshare.stock_zt_pool_dtgc_em",
                lambda: self.akshare.stock_zt_pool_dtgc_em(date=as_of),
                as_of,
            ),
            self._limit_pool(
                POOL_BROKEN_LIMIT_UP,
                "当日炸板",
                "akshare.stock_zt_pool_zbgc_em",
                lambda: self.akshare.stock_zt_pool_zbgc_em(date=as_of),
                as_of,
            ),
        ]
        return self._fill_missing_industries(pools)

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

    def _limit_pool(self, name: str, description: str, source: str, fetch, as_of: str) -> StockPool:
        rows, error = self._fetch_rows(fetch)
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
            source=source,
            status=SourceStatus.FAILED if error else SourceStatus.SUCCESS,
            as_of=as_of,
            error_message=error,
        )

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
                    SourceStatus.FALLBACK,
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
                    SourceStatus.FALLBACK,
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
                SourceStatus.FALLBACK,
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
