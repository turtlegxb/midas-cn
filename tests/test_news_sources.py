import unittest

from midas_cn.data.news import build_news_profile
from midas_cn.data.providers import AkShareMarketDataProvider, MockMarketDataProvider
from midas_cn.models import NewsItem, SourceStatus


class NewsSourceTest(unittest.TestCase):
    def test_news_profile_scores_and_preserves_sources(self):
        profile = build_news_profile(
            [
                NewsItem(title="公司业绩预增并获得政策支持", source="eastmoney_stock_news"),
                NewsItem(title="交易所发布监管问询", source="eastmoney_stock_notice"),
            ]
        )

        self.assertEqual(profile["headline_count"], 2)
        self.assertIn("eastmoney_stock_news", profile["sources"])
        self.assertEqual(profile["source_status"]["eastmoney_stock_news"], "success")
        self.assertGreater(profile["policy_score"], 0)
        self.assertGreater(profile["regulatory_risk"], 0)

    def test_news_profile_distinguishes_fallback_and_failed_sources(self):
        profile = build_news_profile(
            [
                NewsItem(title="占位新闻", source="mock_security_news"),
                NewsItem(title="AkShare新闻源回退", source="akshare_source_warning", category="source_warning"),
            ]
        )

        self.assertEqual(profile["source_status"]["mock_security_news"], "fallback")
        self.assertEqual(profile["source_status"]["akshare_source_warning"], "failed")

    def test_akshare_news_row_mapping_accepts_common_columns(self):
        provider = object.__new__(AkShareMarketDataProvider)
        rows = [
            {
                "新闻标题": "A股收评：科技主线活跃",
                "发布时间": "2026-05-06 15:30:00",
                "新闻链接": "https://example.com/news",
                "新闻内容": "市场成交额放大",
            }
        ]

        items = provider._news_rows_to_items(rows, "eastmoney_stock_news", "company")

        self.assertEqual(items[0].title, "A股收评：科技主线活跃")
        self.assertEqual(items[0].source, "eastmoney_stock_news")
        self.assertEqual(items[0].url, "https://example.com/news")

    def test_akshare_notice_row_mapping_accepts_common_columns(self):
        provider = object.__new__(AkShareMarketDataProvider)
        rows = [
            {
                "公告标题": "关于签署重大合同的公告",
                "公告日期": "2026-05-06",
                "公告链接": "https://example.com/notice",
                "公告类型": "重大事项",
            }
        ]

        items = provider._notice_rows_to_items(rows, "eastmoney_stock_notice")

        self.assertEqual(items[0].category, "announcement")
        self.assertEqual(items[0].summary, "重大事项")

    def test_mock_provider_exposes_security_and_market_news(self):
        provider = MockMarketDataProvider()

        self.assertTrue(provider.get_security_news("600519.SH"))
        self.assertTrue(provider.get_market_news())
        self.assertEqual(provider.get_security_news_results("600519.SH")[0].status, SourceStatus.FALLBACK)

    def test_akshare_fetch_source_preserves_failure_reason(self):
        provider = object.__new__(AkShareMarketDataProvider)
        provider.timeout_seconds = 12

        def fail():
            raise RuntimeError("dns failed")

        result = provider._fetch_source(
            data="个股新闻/公告",
            source="eastmoney_stock_news",
            provider="akshare.stock_news_em",
            context={"symbol": "600519.SH"},
            fetch=fail,
            lookback_days=2,
            limit=10,
            retries=0,
        )

        self.assertEqual(result.status, SourceStatus.FAILED)
        self.assertEqual(result.error_type, "RuntimeError")
        self.assertIn("dns failed", result.error_message)

    def test_akshare_fetch_source_times_out(self):
        provider = object.__new__(AkShareMarketDataProvider)
        provider.timeout_seconds = 0.01

        def hang():
            import time

            time.sleep(1)
            return []

        result = provider._fetch_source(
            data="个股新闻/公告",
            source="eastmoney_stock_news",
            provider="akshare.stock_news_em",
            context={"symbol": "600519.SH"},
            fetch=hang,
            lookback_days=2,
            limit=10,
            retries=0,
        )

        self.assertEqual(result.status, SourceStatus.FAILED)
        self.assertEqual(result.error_type, "TimeoutError")
        self.assertIn("timed out", result.error_message)

    def test_akshare_security_news_results_do_not_use_mock_fallback(self):
        provider = object.__new__(AkShareMarketDataProvider)
        provider.fallback = MockMarketDataProvider()
        provider.timeout_seconds = 12

        def fail_source(*args, **kwargs):
            raise RuntimeError("remote failed")

        class FakeAkshare:
            stock_news_em = fail_source
            stock_individual_notice_report = fail_source
            stock_zh_a_disclosure_report_cninfo = fail_source

        provider.akshare = FakeAkshare()

        results = provider.get_security_news_results("600519.SH", lookback_days=0, limit=10)
        statuses = {result.source: result.status for result in results}

        self.assertEqual(statuses["eastmoney_stock_news"], SourceStatus.FAILED)
        self.assertEqual(statuses["eastmoney_stock_notice"], SourceStatus.FAILED)
        self.assertEqual(statuses["cninfo_disclosure"], SourceStatus.FAILED)
        self.assertNotIn("mock_security_news", statuses)

    def test_security_news_source_chain_prioritizes_cninfo(self):
        provider = object.__new__(AkShareMarketDataProvider)
        provider.timeout_seconds = 12

        class FakeAkshare:
            def stock_zh_a_disclosure_report_cninfo(self, **kwargs):
                return [{"公告标题": "巨潮公告", "公告日期": "2026-05-14"}]

            def stock_individual_notice_report(self, **kwargs):
                return [{"公告标题": "东方财富公告", "公告日期": "2026-05-14"}]

            def stock_news_em(self, **kwargs):
                return [{"新闻标题": "东方财富新闻", "发布时间": "2026-05-14 10:00:00"}]

        provider.akshare = FakeAkshare()

        results = provider.get_security_news_results("600519.SH", lookback_days=0, limit=10)

        self.assertEqual([result.source for result in results], ["cninfo_disclosure", "eastmoney_stock_notice", "eastmoney_stock_news"])
        self.assertEqual(results[0].items[0].title, "巨潮公告")

    def test_market_news_source_chain_prioritizes_cctv(self):
        provider = object.__new__(AkShareMarketDataProvider)
        provider.timeout_seconds = 12

        class FakeAkshare:
            def news_cctv(self, **kwargs):
                return [{"标题": "央视政策", "日期": "2026-05-14"}]

            def stock_info_global_em(self):
                return [{"标题": "东方财富市场新闻", "日期": "2026-05-14"}]

        provider.akshare = FakeAkshare()

        results = provider.get_market_news_results(lookback_days=0, limit=10)

        self.assertEqual([result.source for result in results], ["cctv", "eastmoney_global"])
        self.assertEqual(results[0].items[0].title, "央视政策")


if __name__ == "__main__":
    unittest.main()
