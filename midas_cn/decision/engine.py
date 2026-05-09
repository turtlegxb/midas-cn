from __future__ import annotations

from midas_cn.models import AnalystView, Opportunity, OpportunityGrade, RiskPlan, Signal, TradeDecision


class DecisionEngine:
    def __init__(self, buy_threshold: float, sell_threshold: float, watch_threshold: float):
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.watch_threshold = watch_threshold

    def decide(self, symbol: str, views: list[AnalystView], risk_plan: RiskPlan) -> TradeDecision:
        weighted_score = self._weighted_score(views)
        confidence = self._confidence(views, risk_plan)
        signal = self._signal(weighted_score, confidence, risk_plan)
        rationale = self._rationale(signal, weighted_score, views, risk_plan)
        return TradeDecision(
            symbol=symbol,
            signal=signal,
            score=round(weighted_score, 3),
            confidence=round(confidence, 3),
            risk_plan=risk_plan,
            rationale=rationale,
            views=views,
        )

    def decide_from_opportunity(
        self,
        opportunity: Opportunity,
        views: list[AnalystView],
        risk_plan: RiskPlan,
    ) -> TradeDecision:
        confidence = self._confidence(views, risk_plan)
        if risk_plan.max_position <= 0:
            signal = Signal.HOLD
        elif opportunity.grade == OpportunityGrade.A:
            signal = Signal.BUY
        elif opportunity.grade == OpportunityGrade.B:
            signal = Signal.WATCH
        elif opportunity.grade == OpportunityGrade.C:
            signal = Signal.WATCH if opportunity.score > 0 else Signal.HOLD
        elif opportunity.score <= self.sell_threshold:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD

        return TradeDecision(
            symbol=opportunity.symbol,
            signal=signal,
            score=opportunity.score,
            confidence=round(confidence, 3),
            risk_plan=risk_plan,
            rationale=(
                f"{signal.value} grade={opportunity.grade.value}; "
                f"trigger={opportunity.trigger}; invalidation={opportunity.invalidation}"
            ),
            views=views,
        )

    def _weighted_score(self, views: list[AnalystView]) -> float:
        total_weight = sum(max(view.confidence, 0.01) for view in views)
        return sum(view.score * max(view.confidence, 0.01) for view in views) / total_weight

    def _confidence(self, views: list[AnalystView], risk_plan: RiskPlan) -> float:
        base = sum(view.confidence for view in views) / max(len(views), 1)
        penalty = 0.08 * len(risk_plan.risk_flags)
        return max(0.0, min(1.0, base - penalty))

    def _signal(self, score: float, confidence: float, risk_plan: RiskPlan) -> Signal:
        if risk_plan.max_position <= 0:
            return Signal.HOLD
        if score >= self.buy_threshold and confidence >= 0.45:
            return Signal.BUY
        if score <= self.sell_threshold and confidence >= 0.45:
            return Signal.SELL
        if abs(score) >= self.watch_threshold:
            return Signal.WATCH
        return Signal.HOLD

    def _rationale(
        self,
        signal: Signal,
        score: float,
        views: list[AnalystView],
        risk_plan: RiskPlan,
    ) -> str:
        strongest = max(views, key=lambda view: abs(view.score), default=None)
        driver = strongest.name if strongest else "none"
        flags = "; ".join(risk_plan.risk_flags) if risk_plan.risk_flags else "无硬性风控拦截"
        return f"{signal.value} score={score:.2f}; main_driver={driver}; risk={flags}"
