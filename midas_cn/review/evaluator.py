from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from typing import Any

from midas_cn.models import (
    DailyReport,
    DecisionReview,
    DecisionReviewItem,
    DecisionRun,
    KLineBar,
    ReportReview,
    ReportReviewItem,
    Signal,
)


class DecisionReviewEvaluator:
    """Evaluate generated decisions once realized prices are available."""

    def review(
        self,
        decision_run: DecisionRun,
        entry_prices: dict[str, float],
        exit_prices: dict[str, float],
        horizon: str = "next_session",
        reviewed_at: datetime | None = None,
    ) -> DecisionReview:
        items: list[DecisionReviewItem] = []
        for decision in decision_run.decisions:
            if decision.symbol not in entry_prices or decision.symbol not in exit_prices:
                continue
            entry = entry_prices[decision.symbol]
            exit_ = exit_prices[decision.symbol]
            raw_return = (exit_ - entry) / entry if entry else 0.0
            directional_return = -raw_return if decision.signal == Signal.SELL else raw_return
            followed_plan = self._followed_plan(decision.signal, directional_return)
            notes = []
            if decision.signal in {Signal.HOLD, Signal.WATCH}:
                notes.append("non_actionable_signal_reviewed_for_discipline")
            if not followed_plan:
                notes.append("decision_direction_not_confirmed")
            items.append(
                DecisionReviewItem(
                    symbol=decision.symbol,
                    signal=decision.signal,
                    entry_price=entry,
                    exit_price=exit_,
                    return_pct=round(directional_return, 4),
                    followed_plan=followed_plan,
                    notes=notes,
                )
            )

        actionable = [item for item in items if item.signal in {Signal.BUY, Signal.SELL}]
        if actionable:
            hit_rate = sum(1 for item in actionable if item.followed_plan) / len(actionable)
            average_return = sum(item.return_pct for item in actionable) / len(actionable)
        else:
            hit_rate = 0.0
            average_return = 0.0

        return DecisionReview(
            run_id=decision_run.run_id,
            reviewed_at=reviewed_at or datetime.now(),
            horizon=horizon,
            items=items,
            hit_rate=round(hit_rate, 4),
            average_return=round(average_return, 4),
            metadata={"reviewed_count": len(items), "actionable_count": len(actionable)},
        )

    def _followed_plan(self, signal: Signal, directional_return: float) -> bool:
        if signal == Signal.BUY:
            return directional_return > 0
        if signal == Signal.SELL:
            return directional_return > 0
        return True


class ReportReviewEvaluator:
    """Review daily report opportunities against realized prices."""

    def review(
        self,
        report: DailyReport | dict[str, Any],
        entry_prices: dict[str, float] | None = None,
        exit_prices: dict[str, float] | None = None,
        provider: Any | None = None,
        horizon_days: int = 1,
        horizons: list[int] | None = None,
        reviewed_at: datetime | None = None,
    ) -> ReportReview:
        entry_prices = entry_prices or {}
        exit_prices = exit_prices or {}
        horizons = sorted(set(horizons or [1, 3, 5]))
        primary_horizon = max(1, int(horizon_days))
        if primary_horizon not in horizons:
            horizons.append(primary_horizon)
            horizons.sort()
        opportunities = _report_opportunities(report)
        report_run_id = _report_value(report, "run_id") or "unknown"
        trade_date = _report_trade_date(report)
        items: list[ReportReviewItem] = []
        missing_symbols: list[str] = []

        for opportunity in opportunities:
            symbol = str(_item_value(opportunity, "symbol") or "")
            if not symbol:
                continue
            entry = entry_prices.get(symbol)
            exit_ = exit_prices.get(symbol)
            horizon_returns: dict[str, float] = {}
            max_drawdown = 0.0
            if (entry is None or exit_ is None) and provider is not None:
                entry, exit_, horizon_returns, max_drawdown = self._metrics_from_provider(
                    provider,
                    symbol,
                    trade_date,
                    horizons,
                    primary_horizon,
                )
            if entry is None or exit_ is None:
                missing_symbols.append(symbol)
                continue
            realized_return = (float(exit_) - float(entry)) / float(entry) if float(entry) else 0.0
            if not horizon_returns:
                horizon_returns = {f"{primary_horizon}d": round(realized_return, 4)}
            grade = str(_item_value(opportunity, "grade") or "")
            hit = realized_return > 0
            drawdown_risk = self._drawdown_risk(max_drawdown)
            notes = self._item_notes(grade, realized_return, max_drawdown)
            items.append(
                ReportReviewItem(
                    symbol=symbol,
                    name=str(_item_value(opportunity, "name") or symbol),
                    grade=grade,
                    entry_price=round(float(entry), 4),
                    exit_price=round(float(exit_), 4),
                    return_pct=round(realized_return, 4),
                    hit=hit,
                    horizon_returns=horizon_returns,
                    max_drawdown=round(max_drawdown, 4),
                    drawdown_risk=drawdown_risk,
                    trigger=str(_item_value(opportunity, "trigger") or ""),
                    invalidation=str(_item_value(opportunity, "invalidation") or ""),
                    notes=notes,
                )
            )

        hit_rate = sum(1 for item in items if item.hit) / len(items) if items else 0.0
        average_return = sum(item.return_pct for item in items) / len(items) if items else 0.0
        best = max(items, key=lambda item: item.return_pct, default=None)
        worst = min(items, key=lambda item: item.return_pct, default=None)
        horizon_averages = self._horizon_averages(items, horizons)
        summary = self._summary(items, hit_rate, average_return, missing_symbols, horizon_averages)
        return ReportReview(
            report_run_id=report_run_id,
            reviewed_at=reviewed_at or datetime.now(),
            horizon=f"{horizon_days}d",
            items=items,
            hit_rate=round(hit_rate, 4),
            average_return=round(average_return, 4),
            best_symbol=best.symbol if best else None,
            worst_symbol=worst.symbol if worst else None,
            summary=summary,
            metadata={
                "trade_date": trade_date.isoformat() if trade_date else None,
                "horizons": [f"{horizon}d" for horizon in horizons],
                "horizon_average_returns": horizon_averages,
                "reviewed_count": len(items),
                "missing_count": len(missing_symbols),
                "missing_symbols": missing_symbols,
            },
        )

    def _metrics_from_provider(
        self,
        provider: Any,
        symbol: str,
        trade_date: date | None,
        horizons: list[int],
        primary_horizon: int,
    ) -> tuple[float | None, float | None, dict[str, float], float]:
        max_horizon = max(horizons or [1])
        bars = sorted(provider.get_daily_bars(symbol, lookback=max(45, max_horizon + 20)), key=lambda item: item.date)
        if not bars:
            return None, None, {}, 0.0
        if trade_date is None:
            entry_index = max(0, len(bars) - max_horizon - 1)
        else:
            entry_index = next((index for index, bar in enumerate(bars) if _bar_date(bar) >= trade_date), None)
            if entry_index is None:
                return None, None, {}, 0.0
        if entry_index + max_horizon >= len(bars):
            return None, None, {}, 0.0
        entry = float(bars[entry_index].close)
        horizon_returns = {
            f"{horizon}d": round((float(bars[entry_index + horizon].close) - entry) / entry, 4)
            for horizon in horizons
            if entry_index + horizon < len(bars) and entry
        }
        exit_ = float(bars[entry_index + primary_horizon].close)
        window = bars[entry_index : entry_index + max_horizon + 1]
        max_drawdown = self._max_drawdown(window)
        return entry, exit_, horizon_returns, max_drawdown

    def _max_drawdown(self, bars: list[KLineBar]) -> float:
        peak = 0.0
        max_drawdown = 0.0
        for bar in bars:
            high = float(bar.high or bar.close)
            low = float(bar.low or bar.close)
            peak = max(peak, high)
            if peak:
                max_drawdown = min(max_drawdown, (low - peak) / peak)
        return max_drawdown

    def _drawdown_risk(self, max_drawdown: float) -> str:
        if max_drawdown <= -0.08:
            return "高"
        if max_drawdown <= -0.04:
            return "中"
        return "低"

    def _item_notes(self, grade: str, realized_return: float, max_drawdown: float = 0.0) -> list[str]:
        notes: list[str] = []
        if realized_return > 0:
            notes.append("方向验证")
        else:
            notes.append("方向未验证")
        if grade == "A" and realized_return <= 0:
            notes.append("A类机会未兑现")
        if realized_return <= -0.03:
            notes.append("触发回撤复盘")
        if max_drawdown <= -0.08:
            notes.append("高回撤风险")
        return notes

    def _horizon_averages(self, items: list[ReportReviewItem], horizons: list[int]) -> dict[str, float]:
        averages: dict[str, float] = {}
        for horizon in horizons:
            key = f"{horizon}d"
            values = [item.horizon_returns[key] for item in items if key in item.horizon_returns]
            if values:
                averages[key] = round(sum(values) / len(values), 4)
        return averages

    def _summary(
        self,
        items: list[ReportReviewItem],
        hit_rate: float,
        average_return: float,
        missing_symbols: list[str],
        horizon_averages: dict[str, float],
    ) -> str:
        if not items:
            return "暂无可复盘标的，缺少有效入场/退出价格。"
        best = max(items, key=lambda item: item.return_pct)
        worst = min(items, key=lambda item: item.return_pct)
        missing = f"，{len(missing_symbols)}个标的缺少价格未纳入" if missing_symbols else ""
        horizon_text = "，".join(
            f"{key}均值{value:.2%}"
            for key, value in horizon_averages.items()
            if key in {"1d", "3d", "5d"}
        )
        horizon_part = f"；{horizon_text}" if horizon_text else ""
        return (
            f"复盘{len(items)}个机会，命中率{hit_rate:.0%}，平均收益{average_return:.2%}；"
            f"最佳{best.symbol} {best.return_pct:.2%}，最弱{worst.symbol} {worst.return_pct:.2%}"
            f"{horizon_part}{missing}。"
        )


class ReportReviewArchive:
    def __init__(self, archive_dir: Path):
        self.archive_dir = archive_dir

    def save(self, review: ReportReview, renderer: "ReportReviewMarkdownRenderer | None" = None) -> tuple[Path, Path]:
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        filename = f"review_{review.report_run_id}_{review.reviewed_at.strftime('%Y%m%d_%H%M%S')}"
        json_path = self.archive_dir / f"{filename}.json"
        markdown_path = self.archive_dir / f"{filename}.md"
        json_path.write_text(json.dumps(asdict(review), ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
        markdown_path.write_text((renderer or ReportReviewMarkdownRenderer()).render(review), encoding="utf-8")
        return json_path, markdown_path


class ReportReviewMarkdownRenderer:
    def render(self, review: ReportReview) -> str:
        lines = [
            f"# 决策复盘 {review.report_run_id}",
            "",
            f"- 复盘时间：{review.reviewed_at.isoformat(timespec='seconds')}",
            f"- 周期：{review.horizon}",
            f"- 结论：{review.summary}",
            f"- 命中率：{review.hit_rate:.1%}",
            f"- 平均收益：{review.average_return:.2%}",
            f"- 分周期均值：{format_horizon_averages(review.metadata.get('horizon_average_returns', {}))}",
            "",
            "| 标的 | 等级 | 入场 | 退出 | 1日 | 3日 | 5日 | 最大回撤 | 回撤风险 | 是否命中 | 触发条件 | 失败条件 | 备注 |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |",
        ]
        for item in review.items:
            lines.append(
                f"| {item.symbol} {item.name} | {item.grade} | {item.entry_price:.2f} | {item.exit_price:.2f} | "
                f"{_format_pct(item.horizon_returns.get('1d'))} | {_format_pct(item.horizon_returns.get('3d'))} | "
                f"{_format_pct(item.horizon_returns.get('5d'))} | {item.max_drawdown:.2%} | {item.drawdown_risk} | "
                f"{'是' if item.hit else '否'} | {_clean_cell(item.trigger)} | "
                f"{_clean_cell(item.invalidation)} | {'、'.join(item.notes) or '-'} |"
            )
        if not review.items:
            lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | 缺少可复盘价格 |")
        return "\n".join(lines) + "\n"


def load_report_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_report_path(report_dir: Path) -> Path:
    candidates = sorted(report_dir.glob("chinese_report_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"没有找到日报JSON：{report_dir}")
    return candidates[0]


def recent_report_paths(report_dir: Path, days: int = 30, today: date | None = None) -> list[Path]:
    cutoff = (today or date.today()) - timedelta(days=max(1, days))
    rows = [
        path
        for path in report_dir.glob("chinese_report_*.json")
        if (_report_path_date(path) or date.fromtimestamp(path.stat().st_mtime)) >= cutoff
    ]
    return sorted(rows, key=lambda path: (_report_path_date(path) or date.fromtimestamp(path.stat().st_mtime), path.name))


def _report_path_date(path: Path) -> date | None:
    parts = path.stem.split("_")
    for part in parts:
        if len(part) == 8 and part.isdigit():
            return _parse_date(part)
    return None


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    return str(value)


def _report_value(report: DailyReport | dict[str, Any], key: str) -> Any:
    return report.get(key) if isinstance(report, dict) else getattr(report, key)


def _report_opportunities(report: DailyReport | dict[str, Any]) -> list[Any]:
    return list(_report_value(report, "opportunities") or [])


def _report_trade_date(report: DailyReport | dict[str, Any]) -> date | None:
    calendar = _report_value(report, "calendar") or {}
    value = calendar.get("trade_date") if isinstance(calendar, dict) else getattr(calendar, "trade_date", None)
    if value:
        return _parse_date(value)
    as_of = _report_value(report, "as_of")
    return _parse_date(as_of)


def _item_value(item: Any, key: str) -> Any:
    value = item.get(key) if isinstance(item, dict) else getattr(item, key, None)
    if key == "grade" and hasattr(value, "value"):
        return value.value
    return value


def _bar_date(bar: KLineBar) -> date:
    return _parse_date(bar.date) or date.min


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    candidates = [
        (text[:10], "%Y-%m-%d"),
        (text[:8], "%Y%m%d"),
        (text[:19], "%Y-%m-%dT%H:%M:%S"),
    ]
    for candidate, fmt in candidates:
        if not candidate:
            continue
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            pass
    return None


def _clean_cell(value: str, limit: int = 80) -> str:
    return " ".join(str(value or "-").replace("|", "/").split())[:limit]


def _format_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2%}"


def format_horizon_averages(values: dict[str, float]) -> str:
    if not values:
        return "暂无"
    return "，".join(
        f"{key} {float(values[key]):.2%}"
        for key in ("1d", "3d", "5d")
        if key in values
    ) or "暂无"
