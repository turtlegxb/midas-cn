from __future__ import annotations

from midas_cn.analysts.base import Analyst, clamp_score, metadata_section
from midas_cn.models import AnalystView, MarketSnapshot, SecurityContext


class FundamentalAnalyst(Analyst):
    """Financial quality, growth and valuation discipline."""

    name = "fundamental"

    def evaluate(self, security: SecurityContext, market: MarketSnapshot) -> AnalystView:
        profile = metadata_section(security, "fundamental")
        roe = float(profile.get("roe", 0.10))
        revenue_growth = float(profile.get("revenue_growth", 0.0))
        profit_growth = float(profile.get("profit_growth", 0.0))
        pe_percentile = float(profile.get("pe_percentile", 0.5))
        debt_to_asset = float(profile.get("debt_to_asset", 0.45))
        dividend_yield = float(profile.get("dividend_yield", 0.0))

        quality = (roe - 0.10) * 1.2 + dividend_yield * 1.5
        growth = revenue_growth * 0.35 + profit_growth * 0.45
        valuation = (0.5 - pe_percentile) * 0.35
        leverage_penalty = max(debt_to_asset - 0.65, 0) * 0.45
        score = quality + growth + valuation - leverage_penalty

        return AnalystView(
            name=self.name,
            score=round(clamp_score(score), 3),
            confidence=0.60,
            summary="基本面分析：聚合ROE、收入利润增速、估值分位、资产负债率和分红。",
            evidence={
                "roe": roe,
                "revenue_growth": revenue_growth,
                "profit_growth": profit_growth,
                "pe_percentile": pe_percentile,
                "debt_to_asset": debt_to_asset,
                "dividend_yield": dividend_yield,
            },
        )

