from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from midas_cn.models import NewsItem, SourceResult, SourceStatus


POSITIVE_KEYWORDS = ("利好", "增长", "突破", "中标", "签署", "回购", "增持", "业绩预增", "政策支持")
NEGATIVE_KEYWORDS = ("风险", "处罚", "立案", "减持", "亏损", "下滑", "监管", "终止", "问询")
POLICY_KEYWORDS = ("政策", "国务院", "央行", "发改委", "工信部", "证监会", "交易所")
EARNINGS_KEYWORDS = ("业绩", "年报", "季报", "利润", "营收", "分红", "预告")
REGULATORY_KEYWORDS = ("监管", "处罚", "立案", "问询", "警示", "违规")


def build_news_profile(items: list[NewsItem]) -> dict[str, Any]:
    titles = " ".join(item.title for item in items)
    positive_hits = _count_keywords(titles, POSITIVE_KEYWORDS)
    negative_hits = _count_keywords(titles, NEGATIVE_KEYWORDS)
    policy_hits = _count_keywords(titles, POLICY_KEYWORDS)
    earnings_hits = _count_keywords(titles, EARNINGS_KEYWORDS)
    regulatory_hits = _count_keywords(titles, REGULATORY_KEYWORDS)
    headline_count = len(items)

    policy_score = min(policy_hits / 5, 1.0) * 0.25
    earnings_surprise = min(earnings_hits / 5, 1.0) * 0.18 + min(positive_hits / 10, 1.0) * 0.12
    regulatory_risk = min((regulatory_hits + negative_hits) / 8, 1.0) * 0.35
    event_heat = min(headline_count / 20, 1.0)

    return {
        "policy_score": round(policy_score, 3),
        "earnings_surprise": round(earnings_surprise, 3),
        "regulatory_risk": round(regulatory_risk, 3),
        "event_heat": round(event_heat, 3),
        "headline_count": headline_count,
        "items": [item.__dict__ for item in items[:10]],
        "sources": sorted({item.source for item in items}),
        "source_status": source_status(items),
    }


def source_status(items: list[NewsItem]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for item in items:
        status = "success"
        if item.category == "source_warning" or item.source.endswith("_source_warning"):
            status = "failed"
        elif item.source.startswith("mock_"):
            status = "fallback"
        if statuses.get(item.source) == "failed":
            continue
        if statuses.get(item.source) == "success" and status == "fallback":
            continue
        statuses[item.source] = status
    return statuses


def flatten_source_items(results: list[SourceResult]) -> list[NewsItem]:
    items: list[NewsItem] = []
    for result in results:
        items.extend(result.items)
    return items


def source_status_from_results(results: list[SourceResult]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for result in results:
        statuses[result.source] = _merge_status(statuses.get(result.source), result.status.value)
    return statuses


def source_results_to_dicts(results: list[SourceResult]) -> list[dict[str, Any]]:
    return [
        {
            "data": result.data,
            "source": result.source,
            "provider": result.provider,
            "status": result.status.value,
            "item_count": len(result.items),
            "error_type": result.error_type,
            "error_message": result.error_message,
            "fallback_source": result.fallback_source,
            "checked_at": result.checked_at,
            "context": result.context,
        }
        for result in results
    ]


def filter_recent_items(items: list[NewsItem], lookback_days: int, now: date | None = None) -> list[NewsItem]:
    if lookback_days <= 0:
        return items
    today = now or datetime.now().date()
    cutoff = today - timedelta(days=lookback_days)
    filtered = []
    for item in items:
        published = _parse_date(item.published_at)
        if published is None or published >= cutoff:
            filtered.append(item)
    return filtered


def row_value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def _count_keywords(text: str, keywords: tuple[str, ...]) -> int:
    return sum(text.count(keyword) for keyword in keywords)


def _merge_status(current: str | None, new: str) -> str:
    if current is None:
        return new
    if {current, new} == {SourceStatus.SUCCESS.value, SourceStatus.FAILED.value}:
        return SourceStatus.PARTIAL.value
    order = {
        SourceStatus.FAILED.value: 4,
        SourceStatus.PARTIAL.value: 3,
        SourceStatus.FALLBACK.value: 2,
        SourceStatus.SUCCESS.value: 1,
        SourceStatus.MISSING.value: 0,
    }
    return current if order.get(current, 0) >= order.get(new, 0) else new


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    raw = str(value)[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None
