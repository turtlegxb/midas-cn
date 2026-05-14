import unittest
from datetime import datetime
from tempfile import TemporaryDirectory
from pathlib import Path

from midas_cn.config import load_config
from midas_cn.models import (
    DailyReport,
    MarketSnapshot,
    NewsItem,
    Opportunity,
    OpportunityGrade,
    PositionPlan,
    QualityGate,
    QualityStatus,
    SecurityContext,
    SourceResult,
    SourceStatus,
    StockPool,
    StockPoolEntry,
    TradingCalendarCheck,
)
from midas_cn.orchestration.factory import build_trading_desk
from midas_cn.pools.storage import StockPoolArchive
from midas_cn.reports.builder import DailyReportBuilder
from midas_cn.reports.markdown import MarkdownReportRenderer


class DailyReportTest(unittest.TestCase):
    def test_xueqiu_table_deduplicates_symbol_name_and_separates_next_section(self):
        report = DailyReport(
            run_id="20260510_150000",
            as_of=datetime(2026, 5, 10, 15, 0),
            calendar=TradingCalendarCheck("2026-05-10", False, False, "weekend"),
            quality_gate=QualityGate(QualityStatus.PASS),
            market_snapshot=MarketSnapshot(datetime(2026, 5, 10, 15, 0), 0.1, 0.5, 0.5, 0.3),
            action_summary=[],
            opportunities=[],
            position_plan=PositionPlan((0.2, 0.4), (0.0, 0.1), (0.5, 0.7), 0.05),
            next_day_scenarios=[],
            risk_warnings=[],
            source_audit=[],
            metadata={
                "xueqiu_tracking": {
                    "status": "success",
                    "summary": "样本",
                    "ticker_views": [
                        {
                            "symbol": "002475.SZ",
                            "name": "002475.SZ",
                            "sentiment": "positive",
                            "overlap_level": "单KOL提及",
                            "kol_count": 1,
                            "post_count": 1,
                            "posts": [{"post_type": "short_post", "text": "看好", "kol": "样本"}],
                        }
                    ],
                    "confirmed_position_changes": [
                        {
                            "portfolio": "样本组合",
                            "symbol": "002475.SZ",
                            "name": "002475.SZ",
                            "action": "加仓",
                            "before": 1.0,
                            "after": 2.0,
                            "changed_at": "2026-05-10T15:00:00",
                        }
                    ],
                }
            },
        )

        rendered = MarkdownReportRenderer().render(report)

        self.assertIn("| 002475.SZ | 看多 |", rendered)
        self.assertNotIn("002475.SZ 002475.SZ", rendered)
        self.assertIn("| 样本组合 | 002475.SZ | 加仓 |", rendered)
        self.assertIn("2026-05-10T15:00:00 |\n\n## 机会评级", rendered)

    def test_pool_score_uses_saturation_instead_of_clamping_to_one(self):
        builder = DailyReportBuilder()
        pools = [
            StockPool(
                name="main_net_inflow_top20",
                description="主力净额流入top20，不含ST",
                entries=[StockPoolEntry("300001.SZ", "样本科技", "主力净流入", 1, {"今日主力净流入-净额": 2_000_000_000})],
                source="test",
                status=SourceStatus.SUCCESS,
                as_of="20260508",
            ),
            StockPool(
                name="small_float_net_inflow_top20",
                description="小市值净流入top20，不含ST",
                entries=[StockPoolEntry("300001.SZ", "样本科技", "小市值净流入", 1, {"今日主力净流入-净额": 2_000_000_000})],
                source="test",
                status=SourceStatus.SUCCESS,
                as_of="20260508",
            ),
            StockPool(
                name="limit_up",
                description="当日涨停，不含ST",
                entries=[StockPoolEntry("300001.SZ", "样本科技", "当日涨停", 1, {"成交额": 20_000_000_000})],
                source="test",
                status=SourceStatus.SUCCESS,
                as_of="20260508",
            ),
        ]

        opportunities, _ = builder.rank_report_opportunities(
            [],
            QualityGate(status=QualityStatus.PASS),
            pools,
            {"300001.SZ": {"status": "success", "technical": {"trend_strength": 1, "ma_alignment": 1, "rsi": 60, "volume_ratio": 2}}},
        )

        self.assertEqual(len(opportunities), 1)
        self.assertLess(opportunities[0].score, 0.95)
        self.assertLess(opportunities[0].evidence["pool_score"], 0.92)

    def test_hybrid_news_sort_prioritizes_important_disclosures(self):
        builder = DailyReportBuilder(opportunity_news_sort="hybrid")
        items = [
            {
                "title": "普通盘后新闻",
                "source": "eastmoney_stock_news",
                "published_at": "2026-05-10 16:00:00",
                "category": "company",
            },
            {
                "title": "关于重大合同中标的公告",
                "source": "eastmoney_stock_notice",
                "published_at": "2026-05-09 18:00:00",
                "category": "announcement",
            },
        ]

        sorted_items = builder._sort_news_items(items)

        self.assertEqual(sorted_items[0]["title"], "关于重大合同中标的公告")

    def test_company_name_in_title_boosts_opportunity_news_sorting(self):
        builder = DailyReportBuilder(opportunity_news_sort="hybrid")
        items = [
            {
                "title": "行业订单持续改善",
                "source": "eastmoney_stock_news",
                "published_at": "2026-05-10 18:00:00",
                "category": "company",
            },
            {
                "title": "立讯精密获得大客户关注",
                "source": "eastmoney_stock_news",
                "published_at": "2026-05-10 17:00:00",
                "category": "company",
            },
        ]

        sorted_items = builder._sort_news_items(items, "立讯精密", "002475.SZ")

        self.assertEqual(sorted_items[0]["title"], "立讯精密获得大客户关注")

    def test_opportunity_news_score_and_downgrade_are_applied(self):
        builder = DailyReportBuilder(opportunity_news_sort="hybrid")
        opportunity = Opportunity(
            symbol="300001.SZ",
            name="样本科技",
            grade=OpportunityGrade.A,
            score=0.74,
            trigger="命中选股池：当日涨停；技术面偏强。",
            invalidation="跌破支撑位。",
            action="进入优先跟踪。",
            risk_flags=[],
            evidence={
                "pools": ["当日涨停"],
                "sector": "软件",
                "pool_score": 0.70,
                "technical_score": 0.04,
                "technical": {"rsi": 61},
                "score_breakdown": {
                    "pool_score": 0.70,
                    "technical_score": 0.04,
                    "final_score": 0.74,
                },
            },
        )
        news_result = SourceResult(
            data="个股新闻/公告",
            source="eastmoney_stock_notice",
            provider="test",
            status=SourceStatus.SUCCESS,
            items=[
                NewsItem(
                    title="关于重大合同中标的公告",
                    source="eastmoney_stock_notice",
                    published_at="2026-05-09 18:00:00",
                    url="https://example.com/notice",
                    category="announcement",
                )
            ],
        )

        updated = builder._attach_opportunity_news([opportunity], {"300001.SZ": [news_result]})[0]

        self.assertEqual(updated.grade, OpportunityGrade.B)
        self.assertGreater(updated.evidence["score_breakdown"]["news_score"], 0)
        self.assertGreater(updated.score, opportunity.score)
        self.assertIn("A类需要至少两个选股池共振", updated.evidence["downgrade_reasons"])

    def test_opportunity_downgrade_runs_without_news_results(self):
        builder = DailyReportBuilder()
        opportunity = Opportunity(
            symbol="300002.SZ",
            name="样本高位",
            grade=OpportunityGrade.A,
            score=0.78,
            trigger="命中选股池：主力净额流入、当日涨停；技术面偏强。",
            invalidation="跌破支撑位。",
            action="进入优先跟踪。",
            risk_flags=[],
            evidence={
                "pools": ["主力净额流入top20", "当日涨停"],
                "pool_score": 0.73,
                "technical_score": 0.05,
                "technical": {"rsi": 88},
                "score_breakdown": {
                    "pool_score": 0.73,
                    "technical_score": 0.05,
                    "final_score": 0.78,
                },
            },
        )

        updated = builder._attach_opportunity_news([opportunity], {})[0]

        self.assertEqual(updated.grade, OpportunityGrade.B)
        self.assertEqual(updated.evidence["score_breakdown"]["news_score"], 0)
        self.assertIn("RSI过热，不给A类", updated.evidence["downgrade_reasons"])

    def test_selected_opportunities_are_grouped_by_database_sector_mapping(self):
        builder = DailyReportBuilder()
        opportunities = [
            Opportunity(
                symbol="300001.SZ",
                name="样本科技",
                grade=OpportunityGrade.A,
                score=0.82,
                trigger="放量突破。",
                invalidation="跌破支撑。",
                action="跟踪。",
                evidence={"sector": "旧行业", "concepts": ["旧概念"]},
            ),
            Opportunity(
                symbol="300002.SZ",
                name="样本制造",
                grade=OpportunityGrade.B,
                score=0.51,
                trigger="资金流入。",
                invalidation="跌破均线。",
                action="观察。",
                evidence={},
            ),
        ]

        enriched = builder._attach_stock_sector_mappings(
            opportunities,
            {
                "300001": {
                    "stock_code": "300001",
                    "stock_name": "样本科技",
                    "industry_sectors": ["软件开发"],
                    "concept_sectors": ["人工智能", "信创"],
                    "updated_at": "2026-05-14 21:39:54",
                },
                "300002": {
                    "stock_code": "300002",
                    "stock_name": "样本制造",
                    "industry_sectors": ["软件开发"],
                    "concept_sectors": ["人工智能"],
                    "updated_at": "2026-05-14 21:39:54",
                },
            },
        )
        summary = builder._selected_sector_mapping_summary(enriched, None)

        self.assertEqual(enriched[0].evidence["sector"], "软件开发")
        self.assertEqual(enriched[0].evidence["concepts"], ["人工智能", "信创"])
        self.assertEqual(summary["industries"][0]["theme"], "软件开发")
        self.assertEqual(summary["industries"][0]["count"], 2)
        self.assertEqual(summary["concepts"][0]["theme"], "人工智能")
        self.assertEqual(summary["concepts"][0]["count"], 2)

    def test_mongodb_sector_mapping_enriches_core_security_context(self):
        config = load_config("config/system.toml")
        config.raw.setdefault("mongodb", {})["enabled"] = False
        desk = build_trading_desk(config)
        security = SecurityContext(
            symbol="300750.SZ",
            name="300750.SZ",
            sector="未分类",
            price=100.0,
            liquidity_score=0.5,
            metadata={"provider": "akshare"},
        )

        enriched = desk._apply_stock_sector_mapping_to_security(
            security,
            {
                "300750": {
                    "stock_code": "300750",
                    "stock_name": "宁德时代",
                    "industry_sectors": ["电池"],
                    "concept_sectors": ["动力电池", "储能"],
                    "updated_at": "2026-05-14 21:39:54",
                }
            },
        )

        self.assertEqual(enriched.name, "宁德时代")
        self.assertEqual(enriched.sector, "电池")
        self.assertEqual(enriched.metadata["concepts"], ["动力电池", "储能"])
        self.assertEqual(enriched.metadata["stock_sector_mapping"]["source"], "mongodb.stock_sector_mapping")

    def test_daily_report_builds_phase_one_sections(self):
        config = load_config("config/system.toml")
        config.raw.setdefault("data", {})["provider"] = "mock"
        config.raw.setdefault("llm", {})["enabled"] = False
        config.raw.setdefault("xueqiu", {})["enabled"] = False
        config.raw.setdefault("mongodb", {})["enabled"] = False
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
        self.assertIn("selected_sector_mapping", report.metadata)
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
        self.assertIn("score_breakdown", report.opportunities[0].evidence)
        self.assertIn("source_health", report.metadata)
        self.assertEqual(report.opportunities[0].evidence["sector"], "软件")
        self.assertTrue(report.opportunities[0].evidence["news_items"])
        self.assertTrue(report.opportunities[0].evidence["technical"])
        self.assertTrue(any(item["data"] == "选股池" for item in report.source_audit))
        rendered = MarkdownReportRenderer().render(report)
        self.assertIn("板块轮动与主题深挖", rendered)
        self.assertIn("入选个股行业与概念映射", rendered)
        self.assertIn("雪球大V与持仓跟踪", rendered)
        self.assertIn("宏观及经济政策分析", rendered)
        self.assertIn("综合评分：", rendered)
        self.assertIn("| 北向/融资/跨资产 | 0.00/1 | 数据缺失，不计入正向确认", rendered)
        self.assertIn("大模型复盘服务", rendered)
        self.assertIn("软件", rendered)
        self.assertIn("- 板块：软件", rendered)
        self.assertIn("- 评分：总分", rendered)
        self.assertIn("- 最新新闻：", rendered)
        self.assertNotIn("- 最新新闻：暂无", rendered)
        self.assertIn("  - [300001.SZ", rendered)
        self.assertIn("](https://example.com/300001.SZ/", rendered)
        self.assertIn("使用回退源", rendered)
        self.assertIn("数据源健康面板", rendered)
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
        config.raw.setdefault("mongodb", {})["enabled"] = False
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
        config.raw.setdefault("mongodb", {})["enabled"] = False
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

    def test_ths_sector_cache_overrides_cached_stock_pool(self):
        config = load_config("config/system.toml")
        config.raw.setdefault("data", {})["provider"] = "mock"
        desk = build_trading_desk(config)
        pools = [
            StockPool(
                name="main_net_inflow_top20",
                description="主力净额流入top20，不含ST",
                entries=[
                    StockPoolEntry("002475.SZ", "立讯精密", "主力净流入", 1, {"所属行业": "旧行业"})
                ],
                source="test",
                status=SourceStatus.SUCCESS,
                as_of="20260508",
            )
        ]

        enriched = desk._apply_sector_cache_to_pools(
            pools,
            {
                "symbols": {
                    "002475.SZ": {
                        "industry": "消费电子",
                        "industry_source": "同花顺行业缓存",
                        "concepts": ["苹果概念"],
                        "concept_source": "同花顺概念缓存",
                    }
                }
            },
        )

        metrics = enriched[0].entries[0].metrics
        self.assertEqual(metrics["所属行业"], "消费电子")
        self.assertEqual(metrics["概念"], ["苹果概念"])
        self.assertFalse(
            desk._stock_pool_cache_needs_rebuild(
                [
                    StockPool(
                        name="main_net_inflow_top20",
                        description="主力净额流入top20，不含ST",
                        entries=[
                            StockPoolEntry("002475.SZ", "立讯精密", "主力净流入", 1, {"净额": 1_000_000_000})
                        ],
                        source="akshare.stock_main_fund_flow",
                        status=SourceStatus.SUCCESS,
                        as_of="20260508",
                    )
                ]
            )
        )
