from __future__ import annotations

from midas_cn.analysts.base import Analyst
from midas_cn.models import AnalystView, MarketSnapshot, SecurityContext


class SecurityQualityAnalyst(Analyst):
    name = "security_quality"

    def evaluate(self, security: SecurityContext, market: MarketSnapshot) -> AnalystView:
        score = (security.liquidity_score - 0.5) * 0.8
        return AnalystView(
            name=self.name,
            score=round(score, 3),
            confidence=0.50,
            summary="个股质量占位模型当前以流动性和可交易性作为最低门槛。",
            evidence={
                "price": security.price,
                "liquidity_score": security.liquidity_score,
            },
        )

