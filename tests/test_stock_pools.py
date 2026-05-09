from __future__ import annotations

import unittest
from datetime import date

from midas_cn.models import SourceStatus
from midas_cn.pools.builder import (
    POOL_BROKEN_LIMIT_UP,
    POOL_LIMIT_DOWN,
    POOL_LIMIT_UP,
    POOL_MAIN_NET_INFLOW,
    POOL_SMALL_FLOAT_NET_INFLOW,
    POOL_TURNOVER,
    AkShareStockPoolBuilder,
    is_st_name,
    latest_report_trade_date,
    to_number,
)


class FakeAkShare:
    def stock_main_fund_flow(self, symbol: str):
        self.fund_symbol = symbol
        return [
            {"代码": "000001", "名称": "平安银行", "今日主力净流入": 30_000_000, "今日涨跌幅": 2.3},
            {"代码": "000002", "名称": "万科A", "今日主力净流入": 20_000_000, "今日涨跌幅": 1.0},
            {"代码": "000003", "名称": "ST测试", "今日主力净流入": 90_000_000, "今日涨跌幅": 5.0},
            {"代码": "600001", "名称": "浦发样本", "今日主力净流入": 10_000_000, "今日涨跌幅": -0.5},
        ]

    def stock_zh_a_spot_em(self):
        return [
            {"代码": "000001", "名称": "平安银行", "换手率": 1.1, "流通市值": 120_000_000_000, "成交额": 2_000_000_000},
            {"代码": "000002", "名称": "万科A", "换手率": 9.2, "流通市值": 80_000_000_000, "成交额": 1_500_000_000},
            {"代码": "600001", "名称": "浦发样本", "换手率": 3.5, "流通市值": 70_000_000_000, "成交额": 700_000_000},
            {"代码": "000003", "名称": "*ST测试", "换手率": 99.0, "流通市值": 1_000_000_000, "成交额": 10_000_000},
        ]

    def stock_zt_pool_em(self, date: str):
        return [
            {"代码": "300001", "名称": "涨停一号", "成交额": "3亿", "换手率": "12.5", "所属行业": "软件"},
            {"代码": "300002", "名称": "ST涨停", "成交额": "9亿", "换手率": "20", "所属行业": "软件"},
        ]

    def stock_zt_pool_dtgc_em(self, date: str):
        return [{"代码": "002001", "名称": "跌停一号", "成交额": "2亿", "换手率": "5"}]

    def stock_zt_pool_zbgc_em(self, date: str):
        return [{"代码": "605001", "名称": "炸板一号", "成交额": "4亿", "换手率": "18", "炸板次数": 2}]


class FailingAkShare(FakeAkShare):
    def stock_main_fund_flow(self, symbol: str):
        raise ConnectionError("fund source down")


class StockPoolBuilderTest(unittest.TestCase):
    def test_builds_all_pools_and_filters_st(self):
        pools = AkShareStockPoolBuilder(FakeAkShare(), top_n=2).build("20260508")
        by_name = {pool.name: pool for pool in pools}

        self.assertEqual(
            set(by_name),
            {
                POOL_MAIN_NET_INFLOW,
                POOL_SMALL_FLOAT_NET_INFLOW,
                POOL_TURNOVER,
                POOL_LIMIT_UP,
                POOL_LIMIT_DOWN,
                POOL_BROKEN_LIMIT_UP,
            },
        )
        self.assertEqual(by_name[POOL_MAIN_NET_INFLOW].status, SourceStatus.SUCCESS)
        self.assertEqual([entry.symbol for entry in by_name[POOL_MAIN_NET_INFLOW].entries], ["000001.SZ", "000002.SZ"])
        self.assertEqual([entry.symbol for entry in by_name[POOL_SMALL_FLOAT_NET_INFLOW].entries], ["000002.SZ", "600001.SH"])
        self.assertEqual([entry.symbol for entry in by_name[POOL_TURNOVER].entries], ["000002.SZ", "600001.SH"])
        self.assertEqual([entry.symbol for entry in by_name[POOL_LIMIT_UP].entries], ["300001.SZ"])
        self.assertTrue(all("ST" not in entry.name for pool in pools for entry in pool.entries))

    def test_marks_failed_source_without_mixing_with_success(self):
        pools = AkShareStockPoolBuilder(FailingAkShare(), top_n=2).build("20260508")
        by_name = {pool.name: pool for pool in pools}

        self.assertEqual(by_name[POOL_MAIN_NET_INFLOW].status, SourceStatus.FAILED)
        self.assertIn("fund source down", by_name[POOL_MAIN_NET_INFLOW].error_message or "")
        self.assertEqual(by_name[POOL_LIMIT_UP].status, SourceStatus.SUCCESS)
        self.assertEqual(len(by_name[POOL_LIMIT_UP].entries), 1)

    def test_helpers_parse_dates_and_values(self):
        self.assertEqual(latest_report_trade_date(date(2026, 5, 9)), "20260508")
        self.assertEqual(to_number("1.5亿"), 150_000_000)
        self.assertEqual(to_number("3,200万"), 32_000_000)
        self.assertEqual(to_number("12.3%"), 12.3)
        self.assertTrue(is_st_name("*ST样本"))


if __name__ == "__main__":
    unittest.main()
