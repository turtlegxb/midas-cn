from __future__ import annotations

from midas_cn.analysts.base import Analyst, clamp_score, metadata_section
from midas_cn.models import AnalystView, MarketSnapshot, SecurityContext


class ChinaMarketAnalyst(Analyst):
    """A-share specific rules, flows and policy context."""

    name = "china_market"

    def evaluate(self, security: SecurityContext, market: MarketSnapshot) -> AnalystView:
        profile = metadata_section(security, "china_market")
        northbound = float(profile.get("northbound_flow_score", 0.5))
        margin = float(profile.get("margin_flow_score", 0.5))
        policy_theme = float(profile.get("policy_theme_score", 0.0))
        is_st = bool(profile.get("is_st", False))
        limit_status = str(profile.get("limit_status", "normal"))

        score = (northbound - 0.5) * 0.55 + (margin - 0.5) * 0.35 + policy_theme * 0.25
        flags: list[str] = []
        if is_st:
            score -= 0.55
            flags.append("ST risk")
        if limit_status in {"limit_up", "limit_down"}:
            score -= 0.20
            flags.append(limit_status)

        return AnalystView(
            name=self.name,
            score=round(clamp_score(score), 3),
            confidence=0.58,
            summary="A股本土市场分析：纳入涨跌停、ST、北向资金、融资情绪与政策主题约束。",
            evidence={
                "northbound_flow_score": northbound,
                "margin_flow_score": margin,
                "policy_theme_score": policy_theme,
                "limit_status": limit_status,
                "board": profile.get("board", "unknown"),
                "flags": flags,
            },
        )

