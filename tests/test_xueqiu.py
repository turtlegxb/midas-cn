import unittest
import os
from tempfile import TemporaryDirectory
from pathlib import Path

from midas_cn.models import SourceStatus
from midas_cn.social.xueqiu import (
    XueqiuArchive,
    XueqiuClient,
    XueqiuPost,
    XueqiuPositionChange,
    XueqiuSnapshot,
    XueqiuTracker,
    extract_symbols,
)


class XueqiuTest(unittest.TestCase):
    def test_extract_symbols_normalizes_a_share_codes(self):
        self.assertEqual(
            extract_symbols("关注 $贵州茅台(SH600519)$、SZ300750 和 688981"),
            ["600519.SH", "300750.SZ", "688981.SH"],
        )

    def test_extract_symbols_supports_xueqiu_hk_and_us_tags(self):
        self.assertEqual(
            extract_symbols("$腾讯控股(00700)$ $英伟达(NVDA)$ $寒武纪(SH688256)$"),
            ["00700.HK", "NVDA.US", "688256.SH"],
        )

    def test_archive_round_trip(self):
        snapshot = XueqiuSnapshot(
            as_of="20260508",
            status=SourceStatus.SUCCESS,
            position_changes=[
                XueqiuPositionChange(
                    portfolio_name="样本组合",
                    portfolio_symbol="ZH123456",
                    stock_symbol="300750.SZ",
                    stock_name="宁德时代",
                    action="加仓",
                    weight_before=4.0,
                    weight_after=6.5,
                    changed_at="2026-05-08T15:00:00",
                )
            ],
        )
        with TemporaryDirectory() as temp_dir:
            archive = XueqiuArchive(Path(temp_dir))
            archive.save("20260508", snapshot)
            loaded = archive.load("20260508")

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.status, SourceStatus.SUCCESS)
        self.assertEqual(loaded.position_changes[0].stock_symbol, "300750.SZ")

    def test_archive_expires(self):
        snapshot = XueqiuSnapshot(as_of="20260508", status=SourceStatus.SUCCESS)
        with TemporaryDirectory() as temp_dir:
            archive = XueqiuArchive(Path(temp_dir), ttl_seconds=1)
            path = archive.save("20260508", snapshot)
            old_timestamp = path.stat().st_mtime - 2
            os.utime(path, (old_timestamp, old_timestamp))

            self.assertIsNone(archive.load("20260508"))

    def test_tracker_uses_timeline_url_when_configured(self):
        class FakeClient:
            def user_timeline_url(self, url):
                self.url = url
                return {
                    "statuses": [
                        {
                            "id": 1,
                            "user_id": 8537206007,
                            "created_at": 1778317135000,
                            "text": "看好 SH600519 和 SZ300750",
                        }
                    ]
                }

        tracker = XueqiuTracker({"max_posts_per_account": 5})
        posts = tracker._fetch_posts(
            FakeClient(),
            {"name": "样本", "timeline_url": "https://xueqiu.com/v4/statuses/user_timeline.json?page=1&user_id=1&md5__1038=x"},
            cutoff=__import__("datetime").datetime(2020, 1, 1),
        )

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].symbols, ["600519.SH", "300750.SZ"])

    def test_tracker_fetches_detail_for_long_posts(self):
        class FakeClient:
            def user_timeline(self, user_id, *, count):
                return {
                    "statuses": [
                        {
                            "id": 2,
                            "user_id": 8537206007,
                            "created_at": 1778317135000,
                            "type": "2",
                            "text": "摘要 $寒武纪(SH688256)$",
                        }
                    ]
                }

            def status_detail(self, status_id):
                return {
                    "id": int(status_id),
                    "user_id": 8537206007,
                    "type": "2",
                    "text": "完整正文 $寒武纪(SH688256)$ 第一段 第二段",
                }

        tracker = XueqiuTracker({"max_posts_per_account": 5})
        posts = tracker._fetch_posts(FakeClient(), {"name": "样本", "user_id": "8537206007"}, cutoff=__import__("datetime").datetime(2020, 1, 1))

        self.assertEqual(posts[0].post_type, "long_post")
        self.assertIn("完整正文", posts[0].full_text)
        self.assertTrue(posts[0].metrics["detail_fetched"])

    def test_tracker_parses_following_rows(self):
        tracker = XueqiuTracker({"max_posts_per_account": 5})
        post = tracker._post_from_following_row(
            {
                "id": 123,
                "user_id": "8537206007",
                "screen_name": "样本KOL",
                "created_at": 1778317135000,
                "text": "$腾讯控股(00700)$ $中芯国际(SH688981)$ AI投入继续升温",
                "link": "https://xueqiu.com/8537206007/123",
                "reply_count": 3,
                "fav_count": 2,
            },
            cutoff=__import__("datetime").datetime(2020, 1, 1),
        )

        self.assertIsNotNone(post)
        self.assertEqual(post.account_name, "样本KOL")
        self.assertEqual(post.symbols, ["00700.HK", "688981.SH"])
        self.assertEqual(post.full_text, "$腾讯控股(00700)$ $中芯国际(SH688981)$ AI投入继续升温")


if __name__ == "__main__":
    unittest.main()
