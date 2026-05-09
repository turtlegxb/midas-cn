import unittest

from midas_cn.config import load_config
from midas_cn.orchestration.factory import build_trading_desk


class PipelineTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
