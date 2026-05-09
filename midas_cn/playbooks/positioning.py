from __future__ import annotations

from midas_cn.models import Opportunity, OpportunityGrade, PositionPlan, QualityGate, QualityStatus


class PositionPlaybook:
    def __init__(self, warn_satellite_position: float, cash_min_pass: float, cash_min_warn: float):
        self.warn_satellite_position = warn_satellite_position
        self.cash_min_pass = cash_min_pass
        self.cash_min_warn = cash_min_warn

    def build(self, opportunities: list[Opportunity], quality_gate: QualityGate) -> PositionPlan:
        has_a = any(item.grade == OpportunityGrade.A for item in opportunities)
        has_b = any(item.grade == OpportunityGrade.B for item in opportunities)
        notes: list[str] = []

        if quality_gate.status == QualityStatus.FAIL:
            notes.append("质量门禁FAIL，只允许观察，不新增仓位。")
            return PositionPlan((0.0, 0.30), (0.0, 0.0), (0.70, 1.0), 0.0, notes)

        if quality_gate.status == QualityStatus.WARN:
            notes.append("质量门禁WARN，A类机会自动降级，卫星仓单票不超过5%。")
            core = (0.35, 0.55) if has_b else (0.25, 0.45)
            satellite = (0.0, 0.10) if has_b else (0.0, 0.05)
            return PositionPlan(core, satellite, (self.cash_min_warn, 0.55), self.warn_satellite_position, notes)

        if has_a:
            notes.append("质量门禁PASS且存在A类机会，可按计划分批执行。")
            return PositionPlan((0.45, 0.65), (0.05, 0.20), (self.cash_min_pass, 0.40), 0.10, notes)
        if has_b:
            notes.append("无A类机会，B类仅等待回踩确认。")
            return PositionPlan((0.35, 0.55), (0.0, 0.12), (0.33, 0.50), 0.06, notes)
        notes.append("机会质量不足，维持现金和核心仓防守。")
        return PositionPlan((0.25, 0.45), (0.0, 0.05), (0.50, 0.70), 0.03, notes)

