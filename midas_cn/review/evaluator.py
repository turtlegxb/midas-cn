from __future__ import annotations

from datetime import datetime

from midas_cn.models import DecisionReview, DecisionReviewItem, DecisionRun, Signal


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

