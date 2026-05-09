import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from midas_cn.pools.ths_cache import build_ths_sector_cache, load_ths_sector_cache, save_ths_sector_cache, symbol_classification


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

    def stock_board_industry_cons_ths(self, symbol: str):
        return FakeFrame([{"代码": "002475", "名称": "立讯精密"}])

    def stock_board_concept_cons_ths(self, symbol: str):
        return FakeFrame([{"代码": "002475", "名称": "立讯精密"}])


class ThsCacheTest(unittest.TestCase):
    def test_builds_symbol_industry_and_concept_cache(self):
        payload = build_ths_sector_cache(FakeThsAkShare(), request_interval_seconds=0)

        info = symbol_classification(payload, "002475.SZ")

        self.assertEqual(info["industry"], "消费电子")
        self.assertEqual(info["concepts"], ["苹果概念"])

    def test_saves_and_loads_cache(self):
        payload = build_ths_sector_cache(FakeThsAkShare(), request_interval_seconds=0)
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sector_cache.json"
            save_ths_sector_cache(path, payload)

            loaded = load_ths_sector_cache(path)

        self.assertEqual(symbol_classification(loaded, "002475.SZ")["industry"], "消费电子")


if __name__ == "__main__":
    unittest.main()
