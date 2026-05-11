from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Signal(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    WATCH = "WATCH"


class QualityStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class OpportunityGrade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class SourceStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    FALLBACK = "fallback"
    PARTIAL = "partial"
    MISSING = "missing"


@dataclass(frozen=True)
class MarketSnapshot:
    as_of: datetime
    benchmark_trend: float
    breadth_score: float
    liquidity_score: float
    volatility_score: float
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnalystView:
    name: str
    score: float
    confidence: float
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SecurityContext:
    symbol: str
    name: str
    sector: str
    price: float
    liquidity_score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KLineBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None


@dataclass(frozen=True)
class NewsItem:
    title: str
    source: str
    published_at: str | None = None
    url: str | None = None
    summary: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class SourceResult:
    data: str
    source: str
    provider: str
    status: SourceStatus
    items: list[NewsItem] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    fallback_source: str | None = None
    checked_at: str | None = None
    context: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskPlan:
    max_position: float
    stop_loss_pct: float
    risk_flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TradingCalendarCheck:
    trade_date: str
    is_trading_day: bool
    is_report_day: bool
    reason: str


@dataclass(frozen=True)
class QualityGate:
    status: QualityStatus
    missing_items: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Opportunity:
    symbol: str
    name: str
    grade: OpportunityGrade
    score: float
    trigger: str
    invalidation: str
    action: str
    risk_flags: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StockPoolEntry:
    symbol: str
    name: str
    reason: str
    rank: int
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StockPool:
    name: str
    description: str
    entries: list[StockPoolEntry]
    source: str
    status: SourceStatus
    as_of: str
    error_message: str | None = None


@dataclass(frozen=True)
class PositionPlan:
    core_position_range: tuple[float, float]
    satellite_position_range: tuple[float, float]
    cash_range: tuple[float, float]
    max_single_satellite: float
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TradeDecision:
    symbol: str
    signal: Signal
    score: float
    confidence: float
    risk_plan: RiskPlan
    rationale: str
    views: list[AnalystView] = field(default_factory=list)


@dataclass(frozen=True)
class DecisionRun:
    run_id: str
    as_of: datetime
    market_snapshot: MarketSnapshot
    decisions: list[TradeDecision]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DailyReport:
    run_id: str
    as_of: datetime
    calendar: TradingCalendarCheck
    quality_gate: QualityGate
    market_snapshot: MarketSnapshot
    action_summary: list[dict[str, str]]
    opportunities: list[Opportunity]
    position_plan: PositionPlan
    next_day_scenarios: list[dict[str, str]]
    risk_warnings: list[str]
    source_audit: list[dict[str, str]]
    decisions: list[TradeDecision] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionReviewItem:
    symbol: str
    signal: Signal
    entry_price: float
    exit_price: float
    return_pct: float
    followed_plan: bool
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DecisionReview:
    run_id: str
    reviewed_at: datetime
    horizon: str
    items: list[DecisionReviewItem]
    hit_rate: float
    average_return: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReportReviewItem:
    symbol: str
    name: str
    grade: str
    entry_price: float
    exit_price: float
    return_pct: float
    hit: bool
    horizon_returns: dict[str, float] = field(default_factory=dict)
    max_drawdown: float = 0.0
    drawdown_risk: str = "低"
    trigger: str = ""
    invalidation: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReportReview:
    report_run_id: str
    reviewed_at: datetime
    horizon: str
    items: list[ReportReviewItem]
    hit_rate: float
    average_return: float
    best_symbol: str | None = None
    worst_symbol: str | None = None
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
