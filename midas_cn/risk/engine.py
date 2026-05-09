from __future__ import annotations

from midas_cn.models import MarketSnapshot, RiskPlan, SecurityContext


class RiskEngine:
    def __init__(
        self,
        max_single_position: float,
        min_liquidity_score: float,
        default_stop_loss_pct: float,
    ):
        self.max_single_position = max_single_position
        self.min_liquidity_score = min_liquidity_score
        self.default_stop_loss_pct = default_stop_loss_pct

    def plan(self, security: SecurityContext, market: MarketSnapshot) -> RiskPlan:
        flags: list[str] = []
        max_position = self.max_single_position

        if security.liquidity_score < self.min_liquidity_score:
            flags.append("liquidity_below_minimum")
            max_position = 0.0

        if market.volatility_score > 0.75:
            flags.append("market_volatility_elevated")
            max_position *= 0.5

        if market.liquidity_score < 0.35:
            flags.append("market_liquidity_weak")
            max_position *= 0.5

        return RiskPlan(
            max_position=round(max_position, 4),
            stop_loss_pct=self.default_stop_loss_pct,
            risk_flags=flags,
        )

