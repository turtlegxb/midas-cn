from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from midas_cn.data.news import source_results_to_dicts
from midas_cn.llm.client import LLMClient, build_llm_client, compact_llm_error
from midas_cn.models import MarketSnapshot, NewsItem, Opportunity, SourceResult, SourceStatus


@dataclass(frozen=True)
class ReportSynthesis:
    one_line_review: str
    macro_policy_analysis: dict[str, str]
    source_result: SourceResult


class ReportSynthesisService:
    def __init__(
        self,
        client: LLMClient | None = None,
        enabled: bool = False,
        temperature: float = 0.0,
        opportunity_news_enabled: bool = True,
    ):
        self.client = client
        self.enabled = enabled
        self.temperature = temperature
        self.opportunity_news_enabled = opportunity_news_enabled

    def synthesize(
        self,
        *,
        trade_date: str,
        base_review: dict[str, str],
        market: MarketSnapshot,
        index_state: list[dict[str, str]],
        sentiment_breadth: list[dict[str, str]],
        theme_rotation: dict[str, Any],
        opportunities: list[Opportunity],
        market_news_results: list[SourceResult],
    ) -> ReportSynthesis:
        fallback = self._fallback_synthesis(
            base_review=base_review,
            market=market,
            theme_rotation=theme_rotation,
            market_news_results=market_news_results,
        )
        if not self.enabled:
            return ReportSynthesis(
                one_line_review=fallback["one_line_review"],
                macro_policy_analysis=fallback["macro_policy_analysis"],
                source_result=self._source_result(SourceStatus.MISSING, "规则复盘", "未开启大模型复盘"),
            )
        if self.client is None:
            return ReportSynthesis(
                one_line_review=fallback["one_line_review"],
                macro_policy_analysis=fallback["macro_policy_analysis"],
                source_result=self._source_result(SourceStatus.MISSING, "规则复盘", "未配置可用的大模型密钥"),
            )

        try:
            response = self.client.complete(
                self._messages(
                    trade_date=trade_date,
                    base_review=base_review,
                    market=market,
                    index_state=index_state,
                    sentiment_breadth=sentiment_breadth,
                    theme_rotation=theme_rotation,
                    opportunities=opportunities,
                    market_news_results=market_news_results,
                ),
                temperature=self.temperature,
            )
            parsed = _parse_json_object(response.content)
            macro = _normalize_macro_analysis(parsed.get("macro_policy_analysis", {}))
            return ReportSynthesis(
                one_line_review=_clean_one_line(parsed.get("one_line_review")) or fallback["one_line_review"],
                macro_policy_analysis=macro or fallback["macro_policy_analysis"],
                source_result=self._source_result(SourceStatus.SUCCESS, f"{response.provider}:{response.model}", None),
            )
        except Exception as exc:
            return ReportSynthesis(
                one_line_review=fallback["one_line_review"],
                macro_policy_analysis=fallback["macro_policy_analysis"],
                source_result=self._source_result(SourceStatus.FALLBACK, "规则复盘", compact_llm_error(exc)),
            )

    def _messages(
        self,
        *,
        trade_date: str,
        base_review: dict[str, str],
        market: MarketSnapshot,
        index_state: list[dict[str, str]],
        sentiment_breadth: list[dict[str, str]],
        theme_rotation: dict[str, Any],
        opportunities: list[Opportunity],
        market_news_results: list[SourceResult],
    ) -> list[dict[str, str]]:
        context = {
            "trade_date": trade_date,
            "base_review": base_review,
            "market_snapshot": {
                "benchmark_trend": market.benchmark_trend,
                "breadth_score": market.breadth_score,
                "liquidity_score": market.liquidity_score,
                "volatility_score": market.volatility_score,
                "notes": market.notes[:5],
            },
            "index_state": index_state[:7],
            "sentiment_breadth": sentiment_breadth,
            "market_regime_score": base_review.get("market_regime_score") or {
                "risk_label": base_review.get("risk_label"),
                "mode": base_review.get("market_mode"),
                "summary": base_review.get("regime_summary"),
            },
            "theme_rotation": theme_rotation,
            "top_opportunities": [
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "grade": item.grade.value,
                    "score": item.score,
                    "sector": item.evidence.get("sector"),
                    "pools": item.evidence.get("pools", []),
                    "technical": item.evidence.get("technical", {}),
                }
                for item in opportunities[:8]
            ],
            "market_news": _news_digest(market_news_results),
        }
        return [
            {
                "role": "system",
                "content": (
                    "你是A股盘后复盘和宏观政策分析师。只能基于用户提供的结构化数据分析，"
                    "不要编造没有给出的政策、新闻或数据。输出必须是JSON对象。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请生成中文投研报告补充内容。要求：\n"
                    "1. one_line_review 为一句话，80字以内，包含市场状态、宽度/情绪、主线和次日执行重点。\n"
                    "2. macro_policy_analysis 包含 summary、policy_stance、liquidity、fiscal_industry、external、market_impact、risks、next_watch 八个字段。\n"
                    "3. 宏观及经济政策分析要连接到A股风格、指数和板块轮动，不给确定性预测。\n"
                    "4. 每个字段只写一个自然段，不要输出标题、列表、表格、Markdown或免责声明。\n\n"
                    f"结构化数据：{json.dumps(context, ensure_ascii=False, default=str)}"
                ),
            },
        ]

    def _fallback_synthesis(
        self,
        *,
        base_review: dict[str, str],
        market: MarketSnapshot,
        theme_rotation: dict[str, Any],
        market_news_results: list[SourceResult],
    ) -> dict[str, Any]:
        main_themes = "、".join(item.get("theme", "") for item in theme_rotation.get("main_themes", [])[:3] if item.get("theme"))
        if not main_themes:
            main_themes = "暂无清晰主线"
        policy_titles = _news_titles(market_news_results, categories={"policy", "macro"})[:3]
        policy_hint = "；".join(policy_titles) if policy_titles else "公开新闻源未给出强政策催化"
        liquidity = "偏宽松" if market.liquidity_score > 0.55 else "偏紧" if market.liquidity_score < 0.4 else "中性"
        breadth = "扩散较好" if market.breadth_score > 0.55 else "扩散不足" if market.breadth_score < 0.45 else "中性"
        one_line = (
            f"{base_review.get('market_mode', '震荡观察')}下市场宽度{breadth}，主线集中在{main_themes}；"
            f"次日以指数承接、板块延续和A/B机会分时确认作为执行条件。"
        )
        return {
            "one_line_review": _clean_one_line(one_line),
            "macro_policy_analysis": {
                "summary": f"宏观和政策线索以流动性{liquidity}、政策新闻可验证性为核心约束，当前不宜脱离指数承接单独放大仓位。",
                "policy_stance": f"政策观察：{policy_hint}。",
                "liquidity": f"流动性评分为{market.liquidity_score:.2f}，对高换手和题材扩散有支撑，但仍需成交额连续确认。",
                "fiscal_industry": f"板块映射上优先观察{main_themes}是否从单日强度扩散到产业链后排。",
                "external": "外部因素未在当前数据中形成明确方向，按扰动项处理。",
                "market_impact": "若指数维持红盘且宽度不走弱，主题机会可继续筛选；若宽度回落，降低题材追高权重。",
                "risks": "主要风险是政策预期落空、强势板块炸板扩散、以及资金流数据缺口导致强度误判。",
                "next_watch": "次日重点跟踪指数开盘承接、涨停梯队、成交额变化和政策新闻是否被新增数据确认。",
            },
        }

    def _source_result(self, status: SourceStatus, provider: str, error_message: str | None) -> SourceResult:
        return SourceResult(
            data="一句话复盘与宏观政策",
            source="大模型复盘服务",
            provider=provider,
            status=status,
            error_message=error_message,
            context={},
        )

    def synthesize_opportunity_news(self, opportunities: list[Opportunity]) -> tuple[list[Opportunity], SourceResult]:
        fallback_source = self._opportunity_news_source_result(SourceStatus.MISSING, "规则新闻解读", "未开启大模型个股新闻解读")
        eligible = [item for item in opportunities if item.evidence.get("news_items")]
        if not eligible:
            return opportunities, self._opportunity_news_source_result(SourceStatus.MISSING, "规则新闻解读", "没有可解读的个股新闻")
        if not self.enabled or not self.opportunity_news_enabled:
            return opportunities, fallback_source
        if self.client is None:
            return opportunities, self._opportunity_news_source_result(SourceStatus.MISSING, "规则新闻解读", "未配置可用的大模型密钥")

        try:
            response = self.client.complete(self._opportunity_news_messages(eligible), temperature=self.temperature)
            parsed = _parse_json_object(response.content)
            insights = _normalize_opportunity_news_insights(parsed.get("items"))
            if not insights:
                raise ValueError("模型未返回有效个股新闻解读")
            return (
                _attach_opportunity_news_insights(opportunities, insights, response.provider, response.model),
                self._opportunity_news_source_result(SourceStatus.SUCCESS, f"{response.provider}:{response.model}", None),
            )
        except Exception as exc:
            return (
                opportunities,
                self._opportunity_news_source_result(SourceStatus.FALLBACK, "规则新闻解读", compact_llm_error(exc)),
            )

    def _opportunity_news_messages(self, opportunities: list[Opportunity]) -> list[dict[str, str]]:
        context = {
            "opportunities": [
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "grade": item.grade.value,
                    "score": item.score,
                    "sector": item.evidence.get("sector"),
                    "pools": item.evidence.get("pools", []),
                    "technical": item.evidence.get("technical", {}),
                    "news": [
                        {
                            "title": news.get("title"),
                            "summary": news.get("summary"),
                            "source": news.get("source"),
                            "published_at": news.get("published_at"),
                            "category": news.get("category"),
                        }
                        for news in list(item.evidence.get("news_items") or [])[:3]
                    ],
                }
                for item in opportunities[:10]
            ]
        }
        return [
            {
                "role": "system",
                "content": (
                    "你是A股个股新闻解读助手。只能基于用户提供的新闻标题、摘要和结构化数据判断，"
                    "不要编造新闻、财务数据、研报观点或确定性预测。输出必须是JSON对象。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请为每只股票生成新闻解读。要求：\n"
                    "1. 返回 items 数组，每项包含 symbol、news_summary、news_risk_label、news_signal 四个字段。\n"
                    "2. news_summary 一句话，60字以内，说明催化、风险或信息不足。\n"
                    "3. news_risk_label 只能是：无明显风险、关注风险、明显风险、信息不足。\n"
                    "4. news_signal 只能是：positive、neutral、negative。\n"
                    "5. 不要输出Markdown、列表、免责声明或多余字段。\n\n"
                    f"结构化数据：{json.dumps(context, ensure_ascii=False, default=str)}"
                ),
            },
        ]

    def _opportunity_news_source_result(self, status: SourceStatus, provider: str, error_message: str | None) -> SourceResult:
        return SourceResult(
            data="个股新闻解读",
            source="大模型新闻解读服务",
            provider=provider,
            status=status,
            error_message=error_message,
            context={},
        )

    def synthesize_xueqiu_ticker_views(self, ticker_views: list[dict[str, Any]]) -> tuple[dict[str, dict[str, str]], SourceResult]:
        eligible = [item for item in ticker_views if item.get("symbol") and item.get("posts")]
        if not eligible:
            return {}, self._xueqiu_source_result(SourceStatus.MISSING, "规则雪球观点聚合", "没有可聚合的雪球个股观点")
        if not self.enabled:
            return {}, self._xueqiu_source_result(SourceStatus.MISSING, "规则雪球观点聚合", "未开启大模型复盘")
        if self.client is None:
            return {}, self._xueqiu_source_result(SourceStatus.MISSING, "规则雪球观点聚合", "未配置可用的大模型密钥")

        try:
            response = self.client.complete(self._xueqiu_ticker_messages(eligible), temperature=self.temperature)
            parsed = _parse_json_object(response.content)
            insights = _normalize_xueqiu_ticker_insights(parsed.get("items"))
            if not insights:
                raise ValueError("模型未返回有效雪球观点聚合")
            return insights, self._xueqiu_source_result(SourceStatus.SUCCESS, f"{response.provider}:{response.model}", None)
        except Exception as exc:
            return {}, self._xueqiu_source_result(SourceStatus.FALLBACK, "规则雪球观点聚合", compact_llm_error(exc))

    def _xueqiu_ticker_messages(self, ticker_views: list[dict[str, Any]]) -> list[dict[str, str]]:
        context = {
            "ticker_views": [
                {
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "kol_count": item.get("kol_count"),
                    "post_count": item.get("post_count"),
                    "overlap_level": item.get("overlap_level"),
                    "posts": [
                        {
                            "kol": post.get("kol"),
                            "created_at": post.get("created_at"),
                            "post_type": post.get("post_type"),
                            "title": post.get("title"),
                            "text": post.get("text"),
                            "url": post.get("url"),
                        }
                        for post in list(item.get("posts") or [])[:6]
                    ],
                }
                for item in eligible_ticker_views(ticker_views)[:15]
            ]
        }
        return [
            {
                "role": "system",
                "content": (
                    "你是A股和跨市场股票舆情分析师。只能基于用户提供的雪球帖子聚合观点。"
                    "忽略与大盘、指数、个股或行业投资无关的生活、体育、闲聊内容。输出必须是JSON对象。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请按ticker总结KOL观点。要求：\n"
                    "1. 返回 items 数组，每项包含 symbol、view_summary、kol_overlap_summary、sentiment、risk_note 四个字段。\n"
                    "2. view_summary 一句话，80字以内，概括该标的被提及的核心投资观点。\n"
                    "3. 注意 post_type：short_post=短评，long_post=长帖，article=长文，repost=转发；转发观点必须降权，优先总结原发长文/长帖。\n"
                    "4. kol_overlap_summary 说明是否有多个KOL重叠提及，以及重叠意味着主题热度、共识或分歧。\n"
                    "5. sentiment 只能是 positive、neutral、negative、mixed。\n"
                    "6. risk_note 一句话说明主要风险或信息不足。\n"
                    "7. 不要编造帖子之外的信息，不要输出Markdown或多余字段。\n\n"
                    f"结构化数据：{json.dumps(context, ensure_ascii=False, default=str)}"
                ),
            },
        ]

    def _xueqiu_source_result(self, status: SourceStatus, provider: str, error_message: str | None) -> SourceResult:
        return SourceResult(
            data="雪球KOL观点聚合",
            source="大模型雪球解读服务",
            provider=provider,
            status=status,
            error_message=error_message,
            context={},
        )


def build_report_synthesis_service(config: dict) -> ReportSynthesisService:
    enabled = bool(config.get("enabled", False))
    client = build_llm_client(config)
    return ReportSynthesisService(
        client=client,
        enabled=enabled,
        temperature=float(config.get("temperature", 0.0)),
        opportunity_news_enabled=bool(config.get("opportunity_news_enabled", True)),
    )


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    return parsed if isinstance(parsed, dict) else {}


def _normalize_macro_analysis(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    fields = ["summary", "policy_stance", "liquidity", "fiscal_industry", "external", "market_impact", "risks", "next_watch"]
    return {field: _clean_paragraph(value.get(field)) for field in fields if _clean_paragraph(value.get(field))}


def _clean_one_line(value: object) -> str:
    text = " ".join(str(value or "").split())
    return text[:120]


def _clean_paragraph(value: object) -> str:
    text = str(value or "").replace("|", "/")
    parts = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        if line.startswith(("-", "*")):
            line = line[1:].strip()
        parts.append(line)
    return " ".join(parts).strip()


def _normalize_opportunity_news_insights(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, list):
        return {}
    rows: dict[str, dict[str, str]] = {}
    valid_risks = {"无明显风险", "关注风险", "明显风险", "信息不足"}
    valid_signals = {"positive", "neutral", "negative"}
    for item in value:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            continue
        risk_label = _clean_paragraph(item.get("news_risk_label")) or "信息不足"
        signal = str(item.get("news_signal") or "neutral").strip().lower()
        rows[symbol] = {
            "news_summary": _clean_one_line(item.get("news_summary"))[:80],
            "news_risk_label": risk_label if risk_label in valid_risks else "信息不足",
            "news_signal": signal if signal in valid_signals else "neutral",
        }
    return rows


def eligible_ticker_views(ticker_views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in ticker_views
        if item.get("symbol") and item.get("posts") and _is_investment_related_view(item)
    ]


def _is_investment_related_view(item: dict[str, Any]) -> bool:
    text = " ".join(
        f"{post.get('title', '')} {post.get('text', '')}"
        for post in list(item.get("posts") or [])[:8]
        if isinstance(post, dict)
    )
    if item.get("symbol"):
        return True
    keywords = ["大盘", "指数", "个股", "行业", "板块", "估值", "业绩", "利润", "营收", "订单", "产能", "资本开支"]
    return any(keyword in text for keyword in keywords)


def _normalize_xueqiu_ticker_insights(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, list):
        return {}
    rows: dict[str, dict[str, str]] = {}
    valid_sentiments = {"positive", "neutral", "negative", "mixed"}
    for item in value:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            continue
        sentiment = str(item.get("sentiment") or "neutral").strip().lower()
        rows[symbol] = {
            "view_summary": _clean_one_line(item.get("view_summary"))[:100],
            "kol_overlap_summary": _clean_one_line(item.get("kol_overlap_summary"))[:100],
            "sentiment": sentiment if sentiment in valid_sentiments else "neutral",
            "risk_note": _clean_one_line(item.get("risk_note"))[:100],
        }
    return rows


def _attach_opportunity_news_insights(
    opportunities: list[Opportunity],
    insights: dict[str, dict[str, str]],
    provider: str,
    model: str,
) -> list[Opportunity]:
    updated = []
    for item in opportunities:
        insight = insights.get(item.symbol)
        if not insight:
            updated.append(item)
            continue
        source_result = SourceResult(
            data="个股新闻解读",
            source="大模型新闻解读服务",
            provider=f"{provider}:{model}",
            status=SourceStatus.SUCCESS,
            error_message=None,
            context={"symbol": item.symbol},
        )
        evidence = {
            **item.evidence,
            **insight,
            "llm_news_source_status": {"大模型新闻解读服务": SourceStatus.SUCCESS.value},
            "source_results": list(item.evidence.get("source_results") or []) + source_results_to_dicts([source_result]),
        }
        updated.append(
            Opportunity(
                symbol=item.symbol,
                name=item.name,
                grade=item.grade,
                score=item.score,
                trigger=item.trigger,
                invalidation=item.invalidation,
                action=item.action,
                risk_flags=item.risk_flags,
                evidence=evidence,
            )
        )
    return updated


def _news_digest(results: list[SourceResult]) -> list[dict[str, str]]:
    rows = []
    for result in results:
        for item in result.items[:8]:
            rows.append(
                {
                    "title": item.title,
                    "source": item.source,
                    "published_at": item.published_at or "",
                    "category": item.category or "",
                    "summary": item.summary or "",
                }
            )
    return rows[:20]


def _news_titles(results: list[SourceResult], categories: set[str]) -> list[str]:
    titles = []
    for item in (news for result in results for news in result.items):
        if _matches_category(item, categories):
            titles.append(item.title)
    return titles


def _matches_category(item: NewsItem, categories: set[str]) -> bool:
    text = f"{item.category or ''} {item.title} {item.summary or ''}".lower()
    return any(category in text for category in categories) or any(keyword in text for keyword in ["政策", "央行", "财政", "经济", "会议"])
