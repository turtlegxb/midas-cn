from __future__ import annotations

import datetime as dt
from typing import Iterable

import requests
from dotenv import load_dotenv

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1470778032624107536/JVQ6AC6wD_PH3yP8jfLXv-90klj3WCKY4zp1vl3qeEoLvi-g-WTU_mTpr8AmKq5vzACY"
DISCORD_MESSAGE_LIMIT = 1900


def build_final_trade_decision_markdown(
    ticker: str,
    trade_date: str,
    final_trade_decision_zh: str,
) -> str:
    """Render only the Chinese final trade decision into Discord-friendly Markdown."""
    sections = [
        f"# TradingAgents Report: {ticker}",
        f"- Analysis date: `{trade_date}`",
        "## 最终交易结论",
        final_trade_decision_zh or "_未生成中文翻译。_",
    ]

    return "\n\n".join(sections)


def split_for_discord(markdown: str, limit: int = DISCORD_MESSAGE_LIMIT) -> Iterable[str]:
    """Split Markdown into webhook-sized chunks, preferring paragraph boundaries."""
    current = ""
    for block in markdown.split("\n\n"):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            yield current
            current = ""

        while len(block) > limit:
            split_at = block.rfind("\n", 0, limit)
            if split_at <= 0:
                split_at = limit
            yield block[:split_at]
            block = block[split_at:].lstrip()
        current = block

    if current:
        yield current


def post_markdown_to_discord(webhook_url: str, markdown: str) -> None:
    """Send the Markdown report to Discord via webhook."""
    for chunk in split_for_discord(markdown):
        response = requests.post(
            webhook_url,
            json={"content": chunk},
            timeout=30,
        )
        response.raise_for_status()


def translate_final_trade_decision(llm, final_trade_decision: str) -> str:
    """Translate the final trade decision into concise, natural Chinese."""
    if not final_trade_decision.strip():
        return ""

    prompt = (
        "Translate the following trading decision report into Simplified Chinese. "
        "Preserve the meaning, recommendation strength, structure, headings, bullet points, "
        "and any ticker symbols. Do not add commentary.\n\n"
        f"{final_trade_decision}"
    )
    response = llm.invoke(prompt)
    return str(response.content).strip()


def main() -> None:
    load_dotenv()

    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = 1
    config["data_vendors"] = {
        "core_stock_apis": "yfinance",
        "technical_indicators": "yfinance",
        "fundamental_data": "yfinance",
        "news_data": "yfinance",
    }

    ticker = "AMD"
    trade_date = dt.date.today().isoformat()

    ta = TradingAgentsGraph(debug=False, config=config)
    final_state, _decision = ta.propagate(ticker, trade_date)
    final_trade_decision = (final_state.get("final_trade_decision") or "").strip()
    final_trade_decision_zh = translate_final_trade_decision(
        ta.quick_thinking_llm,
        final_trade_decision,
    )

    markdown = build_final_trade_decision_markdown(
        ticker,
        trade_date,
        final_trade_decision_zh,
    )
    print(markdown)
    post_markdown_to_discord(DISCORD_WEBHOOK_URL, markdown)


if __name__ == "__main__":
    main()
