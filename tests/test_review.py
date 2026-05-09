import unittest
from datetime import datetime

from midas_cn.config import load_config
from midas_cn.orchestration.factory import build_trading_desk
from midas_cn.review.evaluator import DecisionReviewEvaluator


class DecisionReviewTest(unittest.TestCase):
    def test_review_evaluator_closes_decision_feedback_loop(self):
        config = load_config("config/system.toml")
        config.raw.setdefault("pools", {})["enabled"] = False
        config.raw.setdefault("llm", {})["enabled"] = False
        config.raw.setdefault("xueqiu", {})["enabled"] = False
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


if __name__ == "__main__":
    unittest.main()
