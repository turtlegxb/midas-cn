from __future__ import annotations

from midas_cn.analysts.base import Analyst
from midas_cn.models import AnalystView, MarketSnapshot, SecurityContext


class SectorRotationAnalyst(Analyst):
    name = "sector_rotation"

    _preferred = {"电力设备", "食品饮料", "宽基ETF"}

    def evaluate(self, security: SecurityContext, market: MarketSnapshot) -> AnalystView:
        concepts = list(security.metadata.get("concepts") or security.metadata.get("concept_sectors") or [])
        mapping = dict(security.metadata.get("stock_sector_mapping") or {})
        if not security.sector or security.sector == "未分类":
            return AnalystView(
                name=self.name,
                score=0.0,
                confidence=0.10,
                summary="行业分类缺失，未用占位板块偏好替代。",
                evidence={"source_status": "missing", "sector": security.sector, "concepts": concepts},
            )
        base = 0.16 if security.sector in self._preferred else -0.03
        score = base + (market.breadth_score - 0.5) * 0.4
        source = mapping.get("source") or "security_context"
        return AnalystView(
            name=self.name,
            score=round(score, 3),
            confidence=0.50,
            summary=f"{security.sector} 板块处于可观察状态，概念映射来自{source}。",
            evidence={"sector": security.sector, "concepts": concepts[:12], "source": source},
        )
