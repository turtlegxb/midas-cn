import unittest

from midas_cn.analysts.china_market import ChinaMarketAnalyst
from midas_cn.config import load_config
from midas_cn.data.providers import MockMarketDataProvider
from midas_cn.orchestration.factory import build_trading_desk


class AnalystsTest(unittest.TestCase):
    def test_default_desk_uses_tradingagents_cn_inspired_analyst_set(self):
        desk = build_trading_desk(load_config("config/system.toml"))

        self.assertEqual(
            [analyst.name for analyst in desk.analysts],
            [
                "macro_regime",
                "market_breadth",
                "china_market",
                "technical",
                "fundamental",
                "news",
                "sentiment",
                "sector_rotation",
            ],
        )

    def test_china_market_analyst_reads_a_share_specific_profile(self):
        provider = MockMarketDataProvider()
        market = provider.get_market_snapshot(["000300.SH"])
        security = provider.get_security_context("300750.SZ")

        view = ChinaMarketAnalyst().evaluate(security, market)

        self.assertEqual(view.name, "china_market")
        self.assertEqual(view.evidence["board"], "chinext")
        self.assertGreater(view.score, 0)


if __name__ == "__main__":
    unittest.main()

