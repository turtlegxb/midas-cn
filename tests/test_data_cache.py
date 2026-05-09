import unittest
from datetime import datetime, timedelta
import json
from tempfile import TemporaryDirectory
from pathlib import Path

from midas_cn.data.providers import AkShareMarketDataProvider
from midas_cn.models import KLineBar, SourceStatus
from midas_cn.storage.data_cache import DataCache


class DataCacheTest(unittest.TestCase):
    def test_cache_respects_one_day_ttl(self):
        with TemporaryDirectory() as temp_dir:
            cache = DataCache(Path(temp_dir), ttl_seconds=86_400)
            cache.save("sample", "key", {"value": 1})

            self.assertEqual(cache.load("sample", "key"), {"value": 1})
            self.assertTrue((Path(temp_dir) / "sample").exists())

    def test_cache_returns_none_after_expiry(self):
        with TemporaryDirectory() as temp_dir:
            cache = DataCache(Path(temp_dir), ttl_seconds=86_400)
            path = cache.save("sample", "key", {"value": 1})
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["expires_at"] = (datetime.now() - timedelta(seconds=1)).isoformat()
            path.write_text(json.dumps(payload), encoding="utf-8")

            self.assertIsNone(cache.load("sample", "key"))

    def test_akshare_kline_uses_cache(self):
        with TemporaryDirectory() as temp_dir:
            provider = object.__new__(AkShareMarketDataProvider)
            provider.period = "daily"
            provider.adjust = "qfq"
            provider.cache = DataCache(Path(temp_dir), ttl_seconds=86_400)
            provider.fallback = None
            provider.kline_retries = 0
            calls = {"count": 0}

            def fetch(symbol, lookback):
                calls["count"] += 1
                return [KLineBar("2026-05-08", 1.0, 1.1, 0.9, 1.0, 100.0)]

            provider._fetch_daily_bars_eastmoney = fetch
            provider._fetch_daily_bars_sina = fetch
            provider._fetch_daily_bars_tencent = fetch

            first_bars, first_result = provider._get_daily_bars_with_result("600519.SH", 1)
            second_bars, second_result = provider._get_daily_bars_with_result("600519.SH", 1)

            self.assertEqual(calls["count"], 1)
            self.assertEqual(first_bars, second_bars)
            self.assertEqual(first_result.status, SourceStatus.SUCCESS)
            self.assertEqual(second_result.status, SourceStatus.SUCCESS)


if __name__ == "__main__":
    unittest.main()
