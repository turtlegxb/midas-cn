import unittest

from midas_cn.data.kline import build_technical_profile, normalize_symbol_for_akshare
from midas_cn.data.providers import AkShareMarketDataProvider, MockMarketDataProvider
from midas_cn.models import KLineBar, SourceStatus


class KLineTest(unittest.TestCase):
    def test_build_technical_profile_produces_required_fields(self):
        bars = [
            KLineBar(
                date=f"2026-01-{index + 1:02d}",
                open=10 + index * 0.1,
                high=10.2 + index * 0.1,
                low=9.8 + index * 0.1,
                close=10.1 + index * 0.1,
                volume=1_000_000 + index * 10_000,
            )
            for index in range(60)
        ]

        profile = build_technical_profile(bars).as_dict()

        self.assertIn("ema21", profile)
        self.assertIn("ema55", profile)
        self.assertIsNotNone(profile["ema21"])
        self.assertIsNotNone(profile["ema55"])
        self.assertGreater(profile["volume_ratio"], 0)

    def test_mock_provider_populates_kline_technical_fields(self):
        security = MockMarketDataProvider().get_security_context("600519.SH")

        self.assertEqual(security.metadata["provider"], "mock")
        self.assertIsNotNone(security.metadata["technical"]["ema21"])
        self.assertIsNotNone(security.metadata["technical"]["ema55"])

    def test_akshare_symbol_normalization(self):
        self.assertEqual(normalize_symbol_for_akshare("600519.SH"), "600519")
        self.assertEqual(normalize_symbol_for_akshare("300750.SZ"), "300750")

    def test_akshare_row_mapping_accepts_chinese_columns(self):
        provider = object.__new__(AkShareMarketDataProvider)
        row = {
            "日期": "2026-05-06",
            "开盘": 10,
            "最高": 11,
            "最低": 9,
            "收盘": 10.5,
            "成交量": 123456,
            "成交额": 1296288,
        }

        bar = provider._row_to_bar(row)

        self.assertEqual(bar.date, "2026-05-06")
        self.assertEqual(bar.close, 10.5)
        self.assertEqual(bar.volume, 123456)

    def test_akshare_provider_marks_kline_failed_without_mock_fallback(self):
        provider = object.__new__(AkShareMarketDataProvider)
        provider.fallback = MockMarketDataProvider()
        provider.lookback = 90
        provider.news_lookback_days = 2
        provider.max_news_items = 20
        provider.kline_retries = 0

        def fail(*args, **kwargs):
            raise RuntimeError("remote failed")

        provider._fetch_daily_bars_eastmoney = fail
        provider._fetch_daily_bars_sina = fail
        provider._fetch_daily_bars_tencent = fail

        security = provider.get_security_context("600519.SH")

        self.assertEqual(security.metadata["provider"], "akshare")
        self.assertEqual(security.metadata["kline_source"], "akshare.stock_zh_a_hist_tx|stock_zh_a_hist|stock_zh_a_daily")
        self.assertEqual(security.price, 0.0)
        self.assertEqual(security.metadata["technical"], {})
        self.assertEqual(security.metadata["fundamental"]["source_status"], "missing")
        self.assertEqual(security.metadata["kline_source_results"][0]["status"], SourceStatus.FAILED.value)
        self.assertIn("remote failed", security.metadata["kline_source_results"][0]["error_message"])

    def test_akshare_kline_date_range_limits_request_window(self):
        provider = object.__new__(AkShareMarketDataProvider)
        start, end = provider._kline_date_range(90)

        self.assertEqual(len(start), 8)
        self.assertEqual(len(end), 8)
        self.assertLess(start, end)

    def test_akshare_kline_chain_uses_tencent_as_primary(self):
        provider = object.__new__(AkShareMarketDataProvider)
        provider.fallback = MockMarketDataProvider()
        provider.kline_retries = 0
        provider.period = "daily"
        provider.adjust = "qfq"

        def fail(*args, **kwargs):
            raise RuntimeError("primary failed")

        provider._fetch_daily_bars_eastmoney = fail
        provider._fetch_daily_bars_sina = fail
        provider._fetch_daily_bars_tencent = lambda symbol, lookback: [
            KLineBar("2026-05-08", 1.0, 1.1, 0.9, 1.0, 100.0)
        ]

        bars, result = provider._get_daily_bars_with_result("600519.SH", 1)

        self.assertEqual(len(bars), 1)
        self.assertEqual(result.source, "akshare_stock_zh_a_hist_tx")
        self.assertEqual(result.status, SourceStatus.SUCCESS)

    def test_akshare_kline_chain_falls_back_after_tencent_failure(self):
        provider = object.__new__(AkShareMarketDataProvider)
        provider.fallback = MockMarketDataProvider()
        provider.kline_retries = 0
        provider.period = "daily"
        provider.adjust = "qfq"

        def fail(*args, **kwargs):
            raise RuntimeError("primary failed")

        provider._fetch_daily_bars_tencent = fail
        provider._fetch_daily_bars_eastmoney = lambda symbol, lookback: [
            KLineBar("2026-05-08", 1.0, 1.1, 0.9, 1.0, 100.0)
        ]
        provider._fetch_daily_bars_sina = fail

        bars, result = provider._get_daily_bars_with_result("600519.SH", 1)

        self.assertEqual(len(bars), 1)
        self.assertEqual(result.source, "akshare_stock_zh_a_hist")
        self.assertEqual(result.status, SourceStatus.SUCCESS)

    def test_akshare_market_snapshot_uses_benchmark_kline_instead_of_mock(self):
        provider = object.__new__(AkShareMarketDataProvider)
        provider.fallback = MockMarketDataProvider()
        provider.lookback = 90

        def falling_bars(symbol, lookback):
            bars = []
            close = 100.0
            for index in range(60):
                close *= 1.003
                if index == 59:
                    close *= 0.98
                bars.append(
                    KLineBar(
                        date=f"2026-03-{index + 1:02d}",
                        open=close * 0.99,
                        high=close * 1.01,
                        low=close * 0.98,
                        close=close,
                        volume=1_000_000 + index * 10_000,
                    )
                )
            return bars, type("Result", (), {"source": "test_kline", "error_message": None})()

        provider._get_daily_bars_with_result = falling_bars

        snapshot = provider.get_market_snapshot(["000300.SH", "000905.SH", "000852.SH"])

        self.assertLess(snapshot.benchmark_trend, 0.05)
        self.assertLess(snapshot.breadth_score, 0.4)
        self.assertNotIn("mock benchmarks", " ".join(snapshot.notes))
        self.assertIn("avg_daily_change", " ".join(snapshot.notes))


if __name__ == "__main__":
    unittest.main()
