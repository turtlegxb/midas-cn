import unittest

from midas_cn.config import load_config
from midas_cn.orchestration.factory import build_trading_desk


class PipelineTest(unittest.TestCase):
    def test_default_config_builds_stock_pools_if_cache_is_missing(self):
        config = load_config("config/system.toml")

        self.assertTrue(config.section("pools").get("build_if_missing"))
        self.assertEqual(config.section("cache").get("ttl_seconds"), 86_400)

    def test_pipeline_runs_without_archive(self):
        config = load_config("config/system.toml")
        config.raw.setdefault("pools", {})["enabled"] = False
        config.raw.setdefault("llm", {})["enabled"] = False
        config.raw.setdefault("xueqiu", {})["enabled"] = False
        desk = build_trading_desk(config)
        decision_run, archive_path = desk.run(["600519", "300750"], persist=False)

        self.assertIsNone(archive_path)
        self.assertEqual(len(decision_run.decisions), 2)
        self.assertEqual(
            {decision.symbol for decision in decision_run.decisions},
            {"600519.SH", "300750.SZ"},
        )
        self.assertTrue(all(decision.views for decision in decision_run.decisions))
        self.assertEqual(len(decision_run.decisions[0].views), 8)

    def test_pipeline_reports_progress_events(self):
        config = load_config("config/system.toml")
        config.raw.setdefault("data", {})["provider"] = "mock"
        config.raw.setdefault("pools", {})["enabled"] = False
        config.raw.setdefault("llm", {})["enabled"] = False
        config.raw.setdefault("xueqiu", {})["enabled"] = False
        desk = build_trading_desk(config)
        events = []

        desk.run(
            ["600519"],
            persist=False,
            progress=lambda step, total, message: events.append((step, total, message)),
        )

        first_by_step = {}
        for event in events:
            first_by_step.setdefault(event[0], event)

        self.assertEqual(events[0], (1, 13, "初始化股票池与交易日历"))
        self.assertEqual(events[-1], (13, 13, "组装并保存中文报告"))
        self.assertEqual(sorted(first_by_step), list(range(1, 14)))
        self.assertTrue(first_by_step[4][2].startswith("拉取核心标的上下文：600519.SH"))
        self.assertTrue(all(total == 13 for _, total, _ in events))


if __name__ == "__main__":
    unittest.main()
