from __future__ import annotations

from midas_cn.analysts.base import Analyst
from midas_cn.models import AnalystView, MarketSnapshot, SecurityContext


class MarketBreadthAnalyst(Analyst):
    name = "market_breadth"

    def evaluate(self, security: SecurityContext, market: MarketSnapshot) -> AnalystView:
        score = (market.breadth_score - 0.5) * 1.2 - max(market.volatility_score - 0.5, 0) * 0.5
        return AnalystView(
            name=self.name,
            score=round(score, 3),
            confidence=0.55,
            summary="市场宽度用于过滤指数上涨但内部扩散不足的环境。",
            evidence={
                "breadth_score": market.breadth_score,
                "volatility_score": market.volatility_score,
            },
        )

