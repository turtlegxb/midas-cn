from __future__ import annotations

from midas_cn.analysts.base import Analyst, clamp_score, metadata_section
from midas_cn.models import AnalystView, MarketSnapshot, SecurityContext


class NewsAnalyst(Analyst):
    """Company news, announcements, policy and regulatory events."""

    name = "news"

    def evaluate(self, security: SecurityContext, market: MarketSnapshot) -> AnalystView:
        profile = metadata_section(security, "news")
        policy_score = float(profile.get("policy_score", 0.0))
        earnings_surprise = float(profile.get("earnings_surprise", 0.0))
        regulatory_risk = float(profile.get("regulatory_risk", 0.0))
        event_heat = float(profile.get("event_heat", 0.0))
        headline_count = int(profile.get("headline_count", 0))
        items = list(profile.get("items", []))
        sources = list(profile.get("sources", []))
        source_status = dict(profile.get("source_status", {}))
        source_results = list(profile.get("source_results", []))

        heat_quality = min(event_heat, 0.7) * 0.12
        noise_penalty = max(event_heat - 0.75, 0) * 0.25
        score = policy_score * 0.45 + earnings_surprise * 0.55 + heat_quality - regulatory_risk * 0.65 - noise_penalty

        return AnalystView(
            name=self.name,
            score=round(clamp_score(score), 3),
            confidence=0.52,
            summary="新闻事件分析：覆盖政策受益、业绩超预期、监管风险和事件热度噪音。",
            evidence={
                "policy_score": policy_score,
                "earnings_surprise": earnings_surprise,
                "regulatory_risk": regulatory_risk,
                "event_heat": event_heat,
                "headline_count": headline_count,
                "sources": sources,
                "source_status": source_status,
                "source_results": source_results,
                "items": items[:5],
            },
        )
