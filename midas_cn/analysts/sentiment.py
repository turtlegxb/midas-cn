from __future__ import annotations

from midas_cn.analysts.base import Analyst, clamp_score, metadata_section
from midas_cn.models import AnalystView, MarketSnapshot, SecurityContext


class SentimentAnalyst(Analyst):
    """Investor sentiment from financial media and retail communities."""

    name = "sentiment"

    def evaluate(self, security: SecurityContext, market: MarketSnapshot) -> AnalystView:
        profile = metadata_section(security, "sentiment")
        sentiment = float(profile.get("sentiment_score", 0.5))
        heat = float(profile.get("discussion_heat", 0.0))
        divergence = float(profile.get("kol_divergence", 0.0))
        chase_risk = float(profile.get("retail_chase_risk", 0.0))

        score = (sentiment - 0.5) * 0.65 + min(heat, 0.8) * 0.12 - divergence * 0.18 - chase_risk * 0.22
        confidence = 0.45 + min(heat, 0.8) * 0.15

        return AnalystView(
            name=self.name,
            score=round(clamp_score(score), 3),
            confidence=round(confidence, 3),
            summary="社交情绪分析：量化财经社区情绪、讨论热度、KOL分歧和散户追高风险。",
            evidence={
                "sentiment_score": sentiment,
                "discussion_heat": heat,
                "kol_divergence": divergence,
                "retail_chase_risk": chase_risk,
            },
        )

