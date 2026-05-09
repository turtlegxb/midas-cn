from __future__ import annotations

from midas_cn.models import MarketSnapshot, QualityGate, QualityStatus, SecurityContext


class DataQualityGate:
    def __init__(
        self,
        required_security_sections: list[str],
        required_technical_fields_for_a: list[str],
        allow_warn_report: bool = True,
    ):
        self.required_security_sections = required_security_sections
        self.required_technical_fields_for_a = required_technical_fields_for_a
        self.allow_warn_report = allow_warn_report

    def evaluate(self, market: MarketSnapshot, securities: list[SecurityContext]) -> QualityGate:
        missing: list[str] = []
        warnings: list[str] = []

        for field_name in ["benchmark_trend", "breadth_score", "liquidity_score", "volatility_score"]:
            if getattr(market, field_name, None) is None:
                missing.append(f"market.{field_name}")

        for security in securities:
            for section in self.required_security_sections:
                if section not in security.metadata:
                    missing.append(f"{security.symbol}.{section}")
            technical = security.metadata.get("technical", {})
            for field_name in self.required_technical_fields_for_a:
                if field_name not in technical:
                    warnings.append(f"{security.symbol}.technical.{field_name}_missing_for_a_grade")

        if missing:
            status = QualityStatus.FAIL
        elif warnings:
            status = QualityStatus.WARN if self.allow_warn_report else QualityStatus.FAIL
        else:
            status = QualityStatus.PASS

        return QualityGate(status=status, missing_items=missing, warnings=warnings)

