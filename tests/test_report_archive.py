import unittest
from datetime import datetime
from tempfile import TemporaryDirectory
from pathlib import Path

from midas_cn.models import (
    DailyReport,
    MarketSnapshot,
    PositionPlan,
    QualityGate,
    QualityStatus,
    TradingCalendarCheck,
)
from midas_cn.storage.report_archive import DailyReportArchive


class DailyReportArchiveTest(unittest.TestCase):
    def test_report_files_use_chinese_report_prefix(self):
        report = DailyReport(
            run_id="20260509_153000",
            as_of=datetime(2026, 5, 9, 15, 30),
            calendar=TradingCalendarCheck("2026-05-09", False, False, "weekend"),
            quality_gate=QualityGate(QualityStatus.PASS),
            market_snapshot=MarketSnapshot(datetime(2026, 5, 9, 15, 30), 0.1, 0.5, 0.5, 0.3),
            action_summary=[],
            opportunities=[],
            position_plan=PositionPlan((0.2, 0.4), (0.0, 0.1), (0.5, 0.7), 0.05),
            next_day_scenarios=[],
            risk_warnings=[],
            source_audit=[],
            metadata={},
        )

        with TemporaryDirectory() as temp_dir:
            json_path, markdown_path = DailyReportArchive(Path(temp_dir)).save(report)

        self.assertEqual(json_path.name, "chinese_report_20260509_153000.json")
        self.assertEqual(markdown_path.name, "chinese_report_20260509_153000.md")


if __name__ == "__main__":
    unittest.main()
