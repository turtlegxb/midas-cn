import unittest
from datetime import datetime
from tempfile import TemporaryDirectory
from pathlib import Path

from midas_cn.config import load_config
from midas_cn.models import OpportunityGrade, QualityStatus, SourceStatus, StockPool, StockPoolEntry
from midas_cn.orchestration.factory import build_trading_desk
from midas_cn.pools.storage import StockPoolArchive
from midas_cn.reports.markdown import MarkdownReportRenderer


class DailyReportTest(unittest.TestCase):
    def test_daily_report_builds_phase_one_sections(self):
        config = load_config("config/system.toml")
        config.raw.setdefault("data", {})["provider"] = "mock"
        config.raw.setdefault("llm", {})["enabled"] = False
        config.raw.setdefault("xueqiu", {})["enabled"] = False
        desk = build_trading_desk(config)
        with TemporaryDirectory() as temp_dir:
            desk.pool_archive = StockPoolArchive(Path(temp_dir))
            desk.pool_archive.save(
                "20260506",
                [
                    StockPool(
                        name="turnover_top20",
                        description="换手率top20，不含ST",
                        entries=[
                            StockPoolEntry("300001.SZ", "样本科技", "换手率Top20", 1, {"换手率": 18.2, "所属行业": "软件"})
                        ],
                        source="sina.Market_Center.getHQNodeData",
                        status=SourceStatus.FALLBACK,
                        as_of="20260506",
                        error_message="primary failed",
                    ),
                    StockPool(
                        name="limit_up",
                        description="当日涨停，不含ST",
                        entries=[
                            StockPoolEntry("300001.SZ", "样本科技", "当日涨停", 1, {"成交额": 300000000, "所属行业": "软件"})
                        ],
                        source="akshare.stock_zt_pool_em",
                        status=SourceStatus.SUCCESS,
                        as_of="20260506",
                    ),
                ],
            )
            report, paths = desk.run_daily_report(
                ["600519", "300750"],
                persist=False,
                now=datetime(2026, 5, 6, 15, 55),
            )

        self.assertEqual(paths, {})
        self.assertTrue(report.calendar.is_trading_day)
        self.assertTrue(report.calendar.is_report_day)
        self.assertEqual(report.quality_gate.status, QualityStatus.PASS)
        self.assertEqual(len(report.action_summary), 4)
        self.assertEqual(len(report.opportunities), 1)
        self.assertLessEqual(len(report.opportunities), 10)
        self.assertEqual(report.opportunities[0].symbol, "300001.SZ")
        self.assertTrue(report.next_day_scenarios)
        self.assertTrue(report.source_audit)
        self.assertTrue(any(item["data"] == "个股新闻/公告" for item in report.source_audit))
        self.assertTrue(any(item["data"] == "市场新闻/政策" for item in report.source_audit))
        self.assertTrue(all(item["status"] != "有效或回退" for item in report.source_audit))
        self.assertIn("source_results", report.metadata)
        self.assertTrue(report.metadata["source_results"])
        self.assertTrue(all("error_message" in item for item in report.metadata["source_results"]))
        self.assertTrue(any(item["data"] == "行情/K线" for item in report.metadata["source_results"]))
        self.assertIn("overall_review", report.metadata)
        self.assertIn("index_state", report.metadata)
        self.assertIn("market_sentiment_breadth", report.metadata)
        self.assertIn("market_regime_score", report.metadata)
        self.assertIn("stock_pool_analysis", report.metadata)
        self.assertIn("theme_rotation", report.metadata)
        self.assertIn("xueqiu_tracking", report.metadata)
        self.assertIn("macro_policy_analysis", report.metadata)
        self.assertIn("llm_synthesis", report.metadata)
        self.assertIn("technical_coverage", report.metadata)
        self.assertTrue(report.metadata["overall_review"]["one_line"])
        self.assertTrue(report.metadata["macro_policy_analysis"]["summary"])
        self.assertTrue(report.metadata["index_state"])
        index_names = {item["index"] for item in report.metadata["index_state"]}
        self.assertTrue({"上证指数", "深证成指", "创业板指"}.issubset(index_names))
        self.assertTrue(report.metadata["market_sentiment_breadth"])
        self.assertGreater(report.metadata["market_regime_score"]["score"], 0)
        self.assertIn(report.metadata["market_regime_score"]["risk_label"], {"分化Risk On", "震荡偏多"})
        self.assertTrue(report.metadata["market_regime_score"]["mode"])
        self.assertEqual(len(report.metadata["market_regime_score"]["dimensions"]), 8)
        self.assertEqual(report.metadata["stock_pool_analysis"]["source_health"]["success"], 1)
        self.assertEqual(report.metadata["stock_pool_analysis"]["source_health"]["fallback"], 1)
        self.assertEqual(report.metadata["stock_pool_analysis"]["overlap"][0]["symbol"], "300001.SZ")
        self.assertEqual(report.metadata["theme_rotation"]["main_themes"][0]["theme"], "软件")
        self.assertEqual(report.metadata["technical_coverage"]["success"], 1)
        self.assertIn("技术面", report.opportunities[0].trigger)
        self.assertIn("technical_score", report.opportunities[0].evidence)
        self.assertEqual(report.opportunities[0].evidence["sector"], "软件")
        self.assertTrue(report.opportunities[0].evidence["news_items"])
        self.assertTrue(report.opportunities[0].evidence["technical"])
        self.assertTrue(any(item["data"] == "选股池" for item in report.source_audit))
        rendered = MarkdownReportRenderer().render(report)
        self.assertIn("板块轮动与主题深挖", rendered)
        self.assertIn("雪球大V与持仓跟踪", rendered)
        self.assertIn("宏观及经济政策分析", rendered)
        self.assertIn("综合评分：", rendered)
        self.assertIn("| 北向/融资/跨资产 | 0.00/1 | 数据缺失，不计入正向确认", rendered)
        self.assertIn("大模型复盘服务", rendered)
        self.assertIn("软件", rendered)
        self.assertIn("- 板块：软件", rendered)
        self.assertIn("- 最新新闻：", rendered)
        self.assertNotIn("- 最新新闻：暂无", rendered)
        self.assertIn("  - [300001.SZ", rendered)
        self.assertIn("](https://example.com/300001.SZ/", rendered)
        self.assertIn("使用回退源", rendered)
        self.assertIn("东方财富涨停池", rendered)
        self.assertNotIn("akshare.", rendered)
        self.assertNotIn("sina.Market", rendered)
        self.assertNotIn("fallback", rendered)

    def test_phase_two_decisions_are_derived_from_opportunity_grades(self):
        config = load_config("config/system.toml")
        config.raw.setdefault("data", {})["provider"] = "mock"
        config.raw.setdefault("llm", {})["enabled"] = False
        config.raw.setdefault("xueqiu", {})["enabled"] = False
        config.raw.setdefault("pools", {})["enabled"] = False
        desk = build_trading_desk(config)
        report, _ = desk.run_daily_report(
            ["600519", "300750"],
            persist=False,
            now=datetime(2026, 5, 6, 15, 55),
        )

        grades = {item.symbol: item.grade for item in report.opportunities}
        decisions = {item.symbol: item for item in report.decisions}

        for symbol, grade in grades.items():
            if grade == OpportunityGrade.A:
                self.assertEqual(decisions[symbol].signal.value, "BUY")
                self.assertIn("grade=A", decisions[symbol].rationale)
            elif grade == OpportunityGrade.B:
                self.assertEqual(decisions[symbol].signal.value, "WATCH")
                self.assertIn("grade=B", decisions[symbol].rationale)
            elif grade == OpportunityGrade.C:
                self.assertEqual(decisions[symbol].signal.value, "WATCH")
                self.assertIn("grade=C", decisions[symbol].rationale)
            else:
                self.assertIn(decisions[symbol].signal.value, {"HOLD", "SELL"})

    def test_daily_report_limits_displayed_opportunities_to_ten(self):
        config = load_config("config/system.toml")
        config.raw.setdefault("data", {})["provider"] = "mock"
        config.raw.setdefault("llm", {})["enabled"] = False
        config.raw.setdefault("xueqiu", {})["enabled"] = False
        desk = build_trading_desk(config)
        entries = [
            StockPoolEntry(f"300{i:03d}.SZ", f"样本{i}", "当日涨停", i, {"成交额": 300000000, "所属行业": "软件"})
            for i in range(1, 13)
        ]
        with TemporaryDirectory() as temp_dir:
            desk.pool_archive = StockPoolArchive(Path(temp_dir))
            desk.pool_archive.save(
                "20260506",
                [
                    StockPool(
                        name="limit_up",
                        description="当日涨停，不含ST",
                        entries=entries,
                        source="akshare.stock_zt_pool_em",
                        status=SourceStatus.SUCCESS,
                        as_of="20260506",
                    )
                ],
            )
            report, _ = desk.run_daily_report(
                ["600519", "300750"],
                persist=False,
                now=datetime(2026, 5, 6, 15, 55),
            )

        self.assertLessEqual(len(report.opportunities), 10)
        self.assertEqual(report.metadata["hidden_opportunities"]["display_limit"], 10)
        self.assertGreaterEqual(report.metadata["hidden_opportunities"]["below_b_count"], 2)

    def test_failed_critical_stock_pool_cache_is_rebuilt(self):
        config = load_config("config/system.toml")
        config.raw.setdefault("data", {})["provider"] = "mock"
        desk = build_trading_desk(config)

        self.assertTrue(
            desk._stock_pool_cache_needs_rebuild(
                [
                    StockPool(
                        name="main_net_inflow_top20",
                        description="主力净额流入top20，不含ST",
                        entries=[],
                        source="akshare.stock_main_fund_flow",
                        status=SourceStatus.FAILED,
                        as_of="20260508",
                    )
                ]
            )
        )
