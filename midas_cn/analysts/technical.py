from __future__ import annotations

from midas_cn.analysts.base import Analyst, clamp_score, metadata_section
from midas_cn.models import AnalystView, MarketSnapshot, SecurityContext


class TechnicalAnalyst(Analyst):
    """Price, volume and indicator structure."""

    name = "technical"

    def evaluate(self, security: SecurityContext, market: MarketSnapshot) -> AnalystView:
        profile = metadata_section(security, "technical")
        if not profile:
            return AnalystView(
                name=self.name,
                score=0.0,
                confidence=0.10,
                summary="技术面数据缺失，未用模拟指标替代。",
                evidence={"source_status": "missing"},
            )
        trend = float(profile.get("trend_strength", 0.0))
        ma_alignment = float(profile.get("ma_alignment", 0.0))
        rsi = float(profile.get("rsi", 50.0))
        volume_ratio = float(profile.get("volume_ratio", 1.0))

        rsi_penalty = 0.0
        if rsi >= 75:
            rsi_penalty = -0.18
        elif rsi <= 25:
            rsi_penalty = 0.12

        volume_confirm = min(max(volume_ratio - 1.0, -0.4), 0.6) * 0.18
        score = trend * 0.45 + ma_alignment * 0.35 + volume_confirm + rsi_penalty

        return AnalystView(
            name=self.name,
            score=round(clamp_score(score), 3),
            confidence=0.62,
            summary="技术面分析：聚合趋势强度、均线排列、RSI状态和量能确认。",
            evidence={
                "trend_strength": trend,
                "ma_alignment": ma_alignment,
                "rsi": rsi,
                "volume_ratio": volume_ratio,
                "support": profile.get("support"),
                "resistance": profile.get("resistance"),
            },
        )
