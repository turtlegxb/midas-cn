import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from midas_cn.pools.ths_cache import (
    build_ths_sector_cache,
    fetch_ths_symbol_profile,
    load_ths_sector_cache,
    save_ths_sector_cache,
    symbol_classification,
)


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        return self.rows


class FakeThsAkShare:
    def stock_board_industry_name_ths(self):
        return FakeFrame([{"name": "消费电子", "code": "881100"}])

    def stock_board_concept_name_ths(self):
        return FakeFrame([{"name": "苹果概念", "code": "301100"}])

    def stock_industry_change_cninfo(self, symbol: str, start_date: str, end_date: str):
        return FakeFrame(
            [
                {
                    "证券代码": symbol,
                    "分类标准": "申银万国行业分类标准",
                    "行业次类": "消费电子",
                    "变更日期": "20210730",
                }
            ]
        )


def fake_fetch_text(url: str, *, encoding: str = "utf-8", referer: str | None = None) -> str:
    if "theme_key_points" in url:
        return json.dumps(
            {
                "status_code": 0,
                "data": [
                    {"title": "要点一：苹果概念", "content": "样本"},
                    {"title": "要点二：消费电子龙头", "content": "样本"},
                ],
            },
            ensure_ascii=False,
        )
    return (
        '<input id="stockName" type="hidden" value="立讯精密">'
        '<input id="marketId" type="hidden" value="33">'
        '<span class="hltip f12 fl">所属行业：</span>'
        '<span class="tip f14 fl info-text" id="companyInfoIndustry" title=""></span>'
    )


class ThsCacheTest(unittest.TestCase):
    @patch("midas_cn.pools.ths_cache._fetch_text", side_effect=fake_fetch_text)
    def test_builds_symbol_industry_and_concept_cache(self, _):
        payload = build_ths_sector_cache(
            FakeThsAkShare(),
            symbols=["002475.SZ"],
            include_board_lists=False,
            request_interval_seconds=0,
        )

        info = symbol_classification(payload, "002475.SZ")

        self.assertEqual(info["industry"], "消费电子")
        self.assertEqual(info["concepts"], ["苹果概念", "消费电子龙头"])

    @patch("midas_cn.pools.ths_cache._fetch_text", side_effect=fake_fetch_text)
    def test_fetches_symbol_profile_from_f10_and_akshare_fallback(self, _):
        info = fetch_ths_symbol_profile(FakeThsAkShare(), "002475.SZ")

        self.assertEqual(info["name"], "立讯精密")
        self.assertEqual(info["industry"], "akshare:消费电子")
        self.assertEqual(info["concept_source"], "同花顺F10题材接口")

    @patch("midas_cn.pools.ths_cache._fetch_text", side_effect=fake_fetch_text)
    def test_saves_and_loads_cache(self, _):
        payload = build_ths_sector_cache(
            FakeThsAkShare(),
            symbols=["002475.SZ"],
            include_board_lists=False,
            request_interval_seconds=0,
        )
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sector_cache.json"
            save_ths_sector_cache(path, payload)

            loaded = load_ths_sector_cache(path)

        self.assertEqual(symbol_classification(loaded, "002475.SZ")["industry"], "消费电子")


if __name__ == "__main__":
    unittest.main()
