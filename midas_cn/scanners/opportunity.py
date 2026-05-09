from __future__ import annotations

from midas_cn.models import (
    AnalystView,
    Opportunity,
    OpportunityGrade,
    QualityGate,
    QualityStatus,
    RiskPlan,
    SecurityContext,
)


GRADE_ORDER = {
    OpportunityGrade.A: 4,
    OpportunityGrade.B: 3,
    OpportunityGrade.C: 2,
    OpportunityGrade.D: 1,
}


class OpportunityScanner:
    def __init__(
        self,
        a_threshold: float,
        b_threshold: float,
        c_threshold: float,
        d_threshold: float,
        max_warn_grade: str = "B",
    ):
        self.a_threshold = a_threshold
        self.b_threshold = b_threshold
        self.c_threshold = c_threshold
        self.d_threshold = d_threshold
        self.max_warn_grade = OpportunityGrade(max_warn_grade)

    def scan(
        self,
        security: SecurityContext,
        views: list[AnalystView],
        risk_plan: RiskPlan,
        quality_gate: QualityGate,
    ) -> Opportunity:
        score = self._weighted_score(views)
        grade = self._grade(score, risk_plan, quality_gate)
        trigger = self._trigger(grade, security)
        invalidation = self._invalidation(grade, security)
        action = self._action(grade, quality_gate)

        return Opportunity(
            symbol=security.symbol,
            name=security.name,
            grade=grade,
            score=round(score, 3),
            trigger=trigger,
            invalidation=invalidation,
            action=action,
            risk_flags=risk_plan.risk_flags,
            evidence={
                "sector": security.sector,
                "price": security.price,
                "provider": security.metadata.get("provider"),
                "kline_source": security.metadata.get("kline_source"),
                "kline_source_results": security.metadata.get("kline_source_results", []),
                "view_scores": {view.name: view.score for view in views},
                "news_sources": self._news_sources(views),
                "news_source_status": self._news_source_status(views),
                "news_items": self._news_items(views),
                "source_results": self._source_results(views),
            },
        )

    def _weighted_score(self, views: list[AnalystView]) -> float:
        total_weight = sum(max(view.confidence, 0.01) for view in views)
        return sum(view.score * max(view.confidence, 0.01) for view in views) / max(total_weight, 0.01)

    def _grade(self, score: float, risk_plan: RiskPlan, quality_gate: QualityGate) -> OpportunityGrade:
        if risk_plan.max_position <= 0 or score <= self.d_threshold:
            return OpportunityGrade.D
        if score >= self.a_threshold:
            grade = OpportunityGrade.A
        elif score >= self.b_threshold:
            grade = OpportunityGrade.B
        elif score >= self.c_threshold:
            grade = OpportunityGrade.C
        else:
            grade = OpportunityGrade.D

        if quality_gate.status != QualityStatus.PASS and GRADE_ORDER[grade] > GRADE_ORDER[self.max_warn_grade]:
            return self.max_warn_grade
        return grade

    def _trigger(self, grade: OpportunityGrade, security: SecurityContext) -> str:
        if grade == OpportunityGrade.A:
            return "趋势、基本面、资金和质量门禁同时确认，可按交易计划执行。"
        if grade == OpportunityGrade.B:
            return "回踩不破关键承接位且再次放量时升级观察。"
        if grade == OpportunityGrade.C:
            return "主题或事件继续发酵，并出现板块梯队和成交额二次确认。"
        return "重新强于指数且风险标记解除。"

    def _invalidation(self, grade: OpportunityGrade, security: SecurityContext) -> str:
        technical = security.metadata.get("technical", {})
        support = technical.get("support", "关键低点")
        if grade in {OpportunityGrade.A, OpportunityGrade.B}:
            return f"跌破 {support} 或板块相对强度转负。"
        if grade == OpportunityGrade.C:
            return "冲高回落、成交萎缩或新闻催化证伪。"
        return "继续弱于指数或出现新增监管/流动性风险。"

    def _action(self, grade: OpportunityGrade, quality_gate: QualityGate) -> str:
        if grade == OpportunityGrade.A:
            return "允许纳入交易计划，按风控仓位执行。"
        if grade == OpportunityGrade.B:
            suffix = "；WARN质量下不直接追买。" if quality_gate.status != QualityStatus.PASS else "。"
            return "等待回踩承接，单票卫星仓上限控制" + suffix
        if grade == OpportunityGrade.C:
            return "只观察或极小仓试错，等待连续性确认。"
        return "回避或减仓，不做弱反弹。"

    def _news_sources(self, views: list[AnalystView]) -> list[str]:
        for view in views:
            if view.name == "news":
                return list(view.evidence.get("sources", []))
        return []

    def _news_items(self, views: list[AnalystView]) -> list[dict]:
        for view in views:
            if view.name == "news":
                return list(view.evidence.get("items", []))
        return []

    def _news_source_status(self, views: list[AnalystView]) -> dict[str, str]:
        for view in views:
            if view.name == "news":
                return dict(view.evidence.get("source_status", {}))
        return {}

    def _source_results(self, views: list[AnalystView]) -> list[dict]:
        for view in views:
            if view.name == "news":
                return list(view.evidence.get("source_results", []))
        return []
