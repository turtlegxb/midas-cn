from __future__ import annotations

from abc import ABC, abstractmethod

from midas_cn.models import AnalystView, MarketSnapshot, SecurityContext


def metadata_section(security: SecurityContext, section: str) -> dict:
    value = security.metadata.get(section, {})
    return value if isinstance(value, dict) else {}


def clamp_score(value: float) -> float:
    return max(-1.0, min(1.0, value))


class Analyst(ABC):
    name: str

    @abstractmethod
    def evaluate(self, security: SecurityContext, market: MarketSnapshot) -> AnalystView:
        raise NotImplementedError
