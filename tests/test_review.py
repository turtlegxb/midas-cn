import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from midas_cn.config import load_config
from midas_cn.models import (
    DailyReport,
    KLineBar,
    MarketSnapshot,
    Opportunity,
    OpportunityGrade,
    PositionPlan,
    QualityGate,
    QualityStatus,
    TradingCalendarCheck,
)
from midas_cn.orchestration.factory import build_trading_desk
from midas_cn.review.evaluator import (
    DecisionReviewEvaluator,
    ReportReviewArchive,
    ReportReviewEvaluator,
    ReportReviewMarkdownRenderer,
    recent_report_paths,
)


class DecisionReviewTest(unittest.TestCase):
    def test_review_evaluator_closes_decision_feedback_loop(self):
        config = load_config("config/system.toml")
        config.raw.setdefault("pools", {})["enabled"] = False
        config.raw.setdefault("llm", {})["enabled"] = False
        config.raw.setdefault("xueqiu", {})["enabled"] = False
        config.raw.setdefault("mongodb", {})["enabled"] = False
        desk = build_trading_desk(config)
        decision_run, _ = desk.run(["600519", "300750"], persist=False, now=datetime(2026, 5, 6, 15, 55))

        review = DecisionReviewEvaluator().review(
            decision_run,
            entry_prices={"600519.SH": 100.0, "300750.SZ": 200.0},
            exit_prices={"600519.SH": 101.0, "300750.SZ": 198.0},
            reviewed_at=datetime(2026, 5, 7, 15, 30),
        )

        self.assertEqual(review.run_id, decision_run.run_id)
        self.assertEqual(review.metadata["reviewed_count"], 2)
        self.assertEqual(len(review.items), 2)

    def test_report_review_evaluator_reviews_daily_report_opportunities(self):
        report = DailyReport(
            run_id="20260510_150000",
            as_of=datetime(2026, 5, 10, 15, 0),
            calendar=TradingCalendarCheck("2026-05-10", False, False, "weekend"),
            quality_gate=QualityGate(QualityStatus.PASS),
            market_snapshot=MarketSnapshot(datetime(2026, 5, 10, 15, 0), 0.1, 0.5, 0.5, 0.3),
            action_summary=[],
            opportunities=[
                Opportunity(
                    symbol="600519.SH",
                    name="贵州茅台",
                    grade=OpportunityGrade.A,
                    score=0.8,
                    trigger="放量突破。",
                    invalidation="跌破支撑。",
                    action="跟踪",
                    risk_flags=[],
                ),
                Opportunity(
                    symbol="300750.SZ",
                    name="宁德时代",
                    grade=OpportunityGrade.B,
                    score=0.7,
                    trigger="资金流入。",
                    invalidation="跌破均线。",
                    action="观察",
                    risk_flags=[],
                ),
            ],
            position_plan=PositionPlan((0.2, 0.4), (0.0, 0.1), (0.5, 0.7), 0.05),
            next_day_scenarios=[],
            risk_warnings=[],
            source_audit=[],
        )

        review = ReportReviewEvaluator().review(
            report,
            entry_prices={"600519.SH": 100.0, "300750.SZ": 200.0},
            exit_prices={"600519.SH": 103.0, "300750.SZ": 198.0},
            reviewed_at=datetime(2026, 5, 11, 15, 30),
        )

        self.assertEqual(review.report_run_id, "20260510_150000")
        self.assertEqual(review.hit_rate, 0.5)
        self.assertEqual(review.average_return, 0.01)
        self.assertEqual(review.best_symbol, "600519.SH")
        self.assertEqual(review.worst_symbol, "300750.SZ")
        self.assertEqual(review.items[0].trigger, "放量突破。")

    def test_report_review_archive_writes_json_and_markdown(self):
        review = ReportReviewEvaluator().review(
            {
                "run_id": "20260510_150000",
                "calendar": {"trade_date": "2026-05-10"},
                "opportunities": [
                    {
                        "symbol": "600519.SH",
                        "name": "贵州茅台",
                        "grade": "A",
                        "trigger": "放量突破。",
                        "invalidation": "跌破支撑。",
                    }
                ],
            },
            entry_prices={"600519.SH": 100.0},
            exit_prices={"600519.SH": 101.0},
            reviewed_at=datetime(2026, 5, 11, 15, 30),
        )

        rendered = ReportReviewMarkdownRenderer().render(review)
        self.assertIn("# 决策复盘 20260510_150000", rendered)
        self.assertIn("| 600519.SH 贵州茅台 | A |", rendered)

        with TemporaryDirectory() as temp_dir:
            json_path, markdown_path = ReportReviewArchive(Path(temp_dir)).save(review)
            self.assertTrue(json_path.exists())
            self.assertTrue(markdown_path.exists())
            self.assertTrue(json_path.name.startswith("review_20260510_150000_"))

    def test_report_review_calculates_one_three_five_day_returns_and_drawdown(self):
        class FakeProvider:
            def get_daily_bars(self, symbol, lookback=90):
                return [
                    KLineBar("2026-05-10", 10, 10.5, 9.8, 10.0, 1000),
                    KLineBar("2026-05-11", 10, 10.4, 9.7, 10.5, 1000),
                    KLineBar("2026-05-12", 10, 10.8, 10.1, 10.2, 1000),
                    KLineBar("2026-05-13", 10, 10.6, 9.0, 10.8, 1000),
                    KLineBar("2026-05-14", 10, 11.0, 10.2, 10.6, 1000),
                    KLineBar("2026-05-15", 10, 11.2, 10.5, 11.0, 1000),
                ]

        review = ReportReviewEvaluator().review(
            {
                "run_id": "20260510_150000",
                "calendar": {"trade_date": "2026-05-10"},
                "opportunities": [{"symbol": "600519.SH", "name": "贵州茅台", "grade": "A"}],
            },
            provider=FakeProvider(),
            horizon_days=1,
            reviewed_at=datetime(2026, 5, 16, 15, 30),
        )

        item = review.items[0]
        self.assertEqual(item.return_pct, 0.05)
        self.assertEqual(item.horizon_returns, {"1d": 0.05, "3d": 0.08, "5d": 0.1})
        self.assertAlmostEqual(item.max_drawdown, -0.1667, places=4)
        self.assertEqual(item.drawdown_risk, "高")
        self.assertEqual(review.metadata["horizon_average_returns"], {"1d": 0.05, "3d": 0.08, "5d": 0.1})
        self.assertIn("1d均值5.00%", review.summary)
        rendered = ReportReviewMarkdownRenderer().render(review)
        self.assertIn("| 标的 | 等级 | 入场 | 退出 | 1日 | 3日 | 5日 | 最大回撤 |", rendered)
        self.assertIn("- 分周期均值：1d 5.00%，3d 8.00%，5d 10.00%", rendered)
        self.assertIn("高回撤风险", rendered)

    def test_recent_report_paths_uses_filename_date(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_report = root / "chinese_report_20260401_150000.json"
            recent_report = root / "chinese_report_20260510_150000.json"
            old_report.write_text("{}", encoding="utf-8")
            recent_report.write_text("{}", encoding="utf-8")

            paths = recent_report_paths(root, days=30, today=datetime(2026, 5, 11).date())

        self.assertEqual(paths, [recent_report])


if __name__ == "__main__":
    unittest.main()
