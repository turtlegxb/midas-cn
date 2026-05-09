from __future__ import annotations

from midas_cn.analysts.base import Analyst
from midas_cn.models import AnalystView, MarketSnapshot, SecurityContext


class EventCatalystAnalyst(Analyst):
    name = "event_catalyst"

    def evaluate(self, security: SecurityContext, market: MarketSnapshot) -> AnalystView:
        return AnalystView(
            name=self.name,
            score=0.0,
            confidence=0.30,
            summary="事件催化模块已预留，后续接入公告、财报日历、监管和新闻源。",
            evidence={"status": "placeholder"},
        )

