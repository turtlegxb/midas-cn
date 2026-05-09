from __future__ import annotations

import unittest
from datetime import date
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from midas_cn.models import SourceStatus
from midas_cn.pools.builder import (
    POOL_BROKEN_LIMIT_UP,
    POOL_LIMIT_DOWN,
    POOL_LIMIT_UP,
    POOL_MAIN_NET_INFLOW,
    POOL_SMALL_FLOAT_NET_INFLOW,
    POOL_TURNOVER,
    AkShareStockPoolBuilder,
    combine_sources,
    is_st_name,
    latest_report_trade_date,
    normalize_fund_rows,
    to_number,
)
from midas_cn.pools.storage import StockPoolArchive


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
            {"代码": "000001", "名称": "平安银行", "换手率": 1.1, "流通市值": 120_000_000_000, "成交额": 2_000_000_000, "行业": "银行"},
            {"代码": "000002", "名称": "万科A", "换手率": 9.2, "流通市值": 80_000_000_000, "成交额": 1_500_000_000, "行业": "房地产"},
            {"代码": "600001", "名称": "浦发样本", "换手率": 3.5, "流通市值": 70_000_000_000, "成交额": 700_000_000, "行业": "银行"},
            {"代码": "000003", "名称": "*ST测试", "换手率": 99.0, "流通市值": 1_000_000_000, "成交额": 10_000_000, "行业": "风险警示"},
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


class FundFlowFallbackAkShare(FakeAkShare):
    def stock_main_fund_flow(self, symbol: str):
        raise ConnectionError("eastmoney fund source down")

    def stock_individual_fund_flow_rank(self, indicator: str):
        raise ConnectionError("eastmoney rank source down")

    def stock_fund_flow_individual(self, symbol: str):
        return [
            {"股票代码": "000002", "股票简称": "万科A", "净额": "3.2亿", "涨跌幅": "1.5%", "成交额": "8亿"},
            {"股票代码": "600001", "股票简称": "浦发样本", "净额": "1.1亿", "涨跌幅": "-0.5%", "成交额": "5亿"},
            {"股票代码": "000003", "股票简称": "ST测试", "净额": "9亿", "涨跌幅": "5.0%", "成交额": "10亿"},
        ]


class IndividualIndustryAkShare(FakeAkShare):
    def stock_zh_a_spot_em(self):
        return [
            {"代码": "000001", "名称": "平安银行", "换手率": 1.1, "流通市值": 120_000_000_000, "成交额": 2_000_000_000},
            {"代码": "000002", "名称": "万科A", "换手率": 9.2, "流通市值": 80_000_000_000, "成交额": 1_500_000_000},
        ]

    def stock_individual_info_em(self, symbol: str):
        return [
            {"item": "股票代码", "value": symbol},
            {"item": "行业", "value": "个股资料行业"},
        ]


class CninfoIndustryAkShare(IndividualIndustryAkShare):
    def stock_individual_info_em(self, symbol: str):
        raise ConnectionError("eastmoney individual info down")

    def stock_industry_change_cninfo(self, symbol: str, start_date: str, end_date: str):
        return [
            {
                "证券代码": symbol,
                "分类标准": "申银万国行业分类标准",
                "行业次类": "消费电子",
                "行业大类": "消费电子零部件及组装",
                "变更日期": "20210730",
            }
        ]


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
        self.assertEqual(by_name[POOL_MAIN_NET_INFLOW].entries[0].metrics["所属行业"], "银行")
        self.assertEqual(by_name[POOL_SMALL_FLOAT_NET_INFLOW].entries[0].metrics["所属行业"], "房地产")
        self.assertEqual(by_name[POOL_TURNOVER].entries[0].metrics["所属行业"], "房地产")
        self.assertEqual([entry.symbol for entry in by_name[POOL_LIMIT_UP].entries], ["300001.SZ"])
        self.assertTrue(all("ST" not in entry.name for pool in pools for entry in pool.entries))

    def test_missing_pool_industry_falls_back_to_individual_info(self):
        pools = AkShareStockPoolBuilder(IndividualIndustryAkShare(), top_n=1).build("20260508")
        by_name = {pool.name: pool for pool in pools}

        self.assertEqual(by_name[POOL_MAIN_NET_INFLOW].entries[0].metrics["所属行业"], "个股资料行业")

    def test_missing_pool_industry_falls_back_to_cninfo(self):
        pools = AkShareStockPoolBuilder(CninfoIndustryAkShare(), top_n=1).build("20260508")
        by_name = {pool.name: pool for pool in pools}

        self.assertEqual(by_name[POOL_MAIN_NET_INFLOW].entries[0].metrics["所属行业"], "消费电子")

    def test_marks_failed_source_without_mixing_with_success(self):
        pools = AkShareStockPoolBuilder(FailingAkShare(), top_n=2).build("20260508")
        by_name = {pool.name: pool for pool in pools}

        self.assertEqual(by_name[POOL_MAIN_NET_INFLOW].status, SourceStatus.FAILED)
        self.assertIn("fund source down", by_name[POOL_MAIN_NET_INFLOW].error_message or "")
        self.assertEqual(by_name[POOL_LIMIT_UP].status, SourceStatus.SUCCESS)
        self.assertEqual(len(by_name[POOL_LIMIT_UP].entries), 1)

    def test_fund_flow_uses_individual_fallback_and_normalizes_columns(self):
        pools = AkShareStockPoolBuilder(FundFlowFallbackAkShare(), top_n=2).build("20260508")
        by_name = {pool.name: pool for pool in pools}

        self.assertEqual(by_name[POOL_MAIN_NET_INFLOW].status, SourceStatus.FALLBACK)
        self.assertEqual(by_name[POOL_MAIN_NET_INFLOW].source, "akshare.stock_fund_flow_individual(即时)")
        self.assertIn("eastmoney fund source down", by_name[POOL_MAIN_NET_INFLOW].error_message or "")
        self.assertEqual([entry.symbol for entry in by_name[POOL_MAIN_NET_INFLOW].entries], ["000002.SZ", "600001.SH"])
        self.assertEqual(by_name[POOL_MAIN_NET_INFLOW].entries[0].metrics["净额"], 320_000_000)
        self.assertEqual(by_name[POOL_SMALL_FLOAT_NET_INFLOW].status, SourceStatus.FALLBACK)
        self.assertEqual(
            by_name[POOL_SMALL_FLOAT_NET_INFLOW].source,
            "akshare.stock_fund_flow_individual(即时) + akshare.stock_zh_a_spot_em",
        )
        self.assertEqual([entry.symbol for entry in by_name[POOL_SMALL_FLOAT_NET_INFLOW].entries], ["000002.SZ", "600001.SH"])

    def test_helpers_parse_dates_and_values(self):
        self.assertEqual(latest_report_trade_date(date(2026, 5, 9)), "20260508")
        self.assertEqual(to_number("1.5亿"), 150_000_000)
        self.assertEqual(to_number("3,200万"), 32_000_000)
        self.assertEqual(to_number("12.3%"), 12.3)
        self.assertTrue(is_st_name("*ST样本"))
        self.assertEqual(normalize_fund_rows([{"股票代码": 1, "股票简称": "样本", "净额": "1亿"}])[0]["代码"], "000001")
        self.assertEqual(
            combine_sources(("成功源", SourceStatus.FALLBACK), ("失败源", SourceStatus.FAILED)),
            "成功源",
        )

    def test_stock_pool_cache_expires(self):
        with TemporaryDirectory() as temp_dir:
            archive = StockPoolArchive(Path(temp_dir), ttl_seconds=1)
            pools = AkShareStockPoolBuilder(FakeAkShare(), top_n=1).build("20260508")
            path = archive.save("20260508", pools)
            old_timestamp = path.stat().st_mtime - 2
            os.utime(path, (old_timestamp, old_timestamp))

            self.assertEqual(archive.load("20260508"), [])


if __name__ == "__main__":
    unittest.main()
