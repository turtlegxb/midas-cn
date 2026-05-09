from __future__ import annotations

from midas_cn.analysts.base import Analyst
from midas_cn.models import AnalystView, MarketSnapshot, SecurityContext


class MacroRegimeAnalyst(Analyst):
    name = "macro_regime"

    def evaluate(self, security: SecurityContext, market: MarketSnapshot) -> AnalystView:
        score = (market.benchmark_trend * 0.45) + (market.liquidity_score - 0.5) * 0.75
        return AnalystView(
            name=self.name,
            score=round(score, 3),
            confidence=0.60,
            summary="宏观环境按指数趋势与流动性给出中性偏积极底色。",
            evidence={
                "benchmark_trend": market.benchmark_trend,
                "liquidity_score": market.liquidity_score,
            },
        )

