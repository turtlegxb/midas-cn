import unittest
from datetime import datetime

from midas_cn.llm.client import LLMResponse
from midas_cn.llm.synthesis import ReportSynthesisService
from midas_cn.models import MarketSnapshot, SourceResult, SourceStatus
from midas_cn.reports.markdown import clean_markdown_field


class FakeClient:
    provider = "openai"
    model = "test-model"

    def complete(self, messages, *, temperature=0.2):
        return LLMResponse(
            provider=self.provider,
            model=self.model,
            content=(
                '{"one_line_review":"指数震荡中宽度改善，主线围绕软件扩散；次日看成交承接和A类标的分时确认。",'
                '"macro_policy_analysis":{'
                '"summary":"政策线索对风险偏好有支撑，但需要成交额确认。",'
                '"policy_stance":"稳增长预期仍是观察主轴。",'
                '"liquidity":"流动性中性偏积极。",'
                '"fiscal_industry":"产业政策映射到软件主题。",'
                '"external":"外部扰动暂按中性处理。",'
                '"market_impact":"有利于主题扩散但不支持无条件追高。",'
                '"risks":"政策预期落空和炸板扩散。",'
                '"next_watch":"跟踪指数承接、涨停梯队和新增政策新闻。"}}'
            ),
        )


class LLMSynthesisTest(unittest.TestCase):
    def test_successful_synthesis_uses_client_output(self):
        service = ReportSynthesisService(client=FakeClient(), enabled=True)
        result = service.synthesize(
            trade_date="2026-05-06",
            base_review={"market_mode": "震荡观察", "one_line": "基础复盘"},
            market=MarketSnapshot(
                as_of=datetime(2026, 5, 6, 15, 0),
                benchmark_trend=0.1,
                breadth_score=0.56,
                liquidity_score=0.62,
                volatility_score=0.3,
            ),
            index_state=[],
            sentiment_breadth=[],
            theme_rotation={"main_themes": [{"theme": "软件"}]},
            opportunities=[],
            market_news_results=[
                SourceResult(
                    data="市场新闻/政策",
                    source="test",
                    provider="test",
                    status=SourceStatus.SUCCESS,
                )
            ],
        )

        self.assertEqual(result.source_result.status, SourceStatus.SUCCESS)
        self.assertIn("软件", result.one_line_review)
        self.assertEqual(result.macro_policy_analysis["next_watch"], "跟踪指数承接、涨停梯队和新增政策新闻。")

    def test_disabled_synthesis_returns_rule_fallback(self):
        service = ReportSynthesisService(enabled=False)
        result = service.synthesize(
            trade_date="2026-05-06",
            base_review={"market_mode": "震荡观察", "one_line": "基础复盘"},
            market=MarketSnapshot(
                as_of=datetime(2026, 5, 6, 15, 0),
                benchmark_trend=0.1,
                breadth_score=0.42,
                liquidity_score=0.5,
                volatility_score=0.3,
            ),
            index_state=[],
            sentiment_breadth=[],
            theme_rotation={"main_themes": []},
            opportunities=[],
            market_news_results=[],
        )

        self.assertEqual(result.source_result.status, SourceStatus.MISSING)
        self.assertIn("宽度扩散不足", result.one_line_review)
        self.assertTrue(result.macro_policy_analysis["summary"])

    def test_macro_markdown_field_is_cleaned(self):
        self.assertEqual(
            clean_markdown_field("## 标题\n- 第一条 | 第二条\n* 第三条"),
            "标题 第一条 / 第二条 第三条",
        )


if __name__ == "__main__":
    unittest.main()
