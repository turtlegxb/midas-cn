from __future__ import annotations

import asyncio
import datetime as dt
import os

import discord
from discord import app_commands
from dotenv import load_dotenv

from main import build_final_trade_decision_markdown, split_for_discord, translate_final_trade_decision
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


def run_analysis_and_format(ticker: str, trade_date: str) -> str:
    """Run TradingAgents and return a Discord-ready Markdown report."""
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = 1
    config["data_vendors"] = {
        "core_stock_apis": "yfinance",
        "technical_indicators": "yfinance",
        "fundamental_data": "yfinance",
        "news_data": "yfinance",
    }

    ta = TradingAgentsGraph(debug=False, config=config)
    final_state, _decision = ta.propagate(ticker, trade_date)
    final_trade_decision = (final_state.get("final_trade_decision") or "").strip()
    final_trade_decision_zh = translate_final_trade_decision(
        ta.quick_thinking_llm,
        final_trade_decision,
    )

    return build_final_trade_decision_markdown(
        ticker=ticker,
        trade_date=trade_date,
        final_trade_decision_zh=final_trade_decision_zh,
    )


class TradingAgentsDiscordBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.analysis_lock = asyncio.Lock()

    async def setup_hook(self) -> None:
        guild_id = os.getenv("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


bot = TradingAgentsDiscordBot()


@bot.event
async def on_ready() -> None:
    print(f"Discord bot logged in as {bot.user} (id={bot.user.id})")


@bot.tree.command(name="analyze", description="Run TradingAgents analysis for a ticker")
@app_commands.describe(
    ticker="Ticker symbol, e.g. NVDA or MSFT",
    trade_date="Analysis date in YYYY-MM-DD format. Defaults to today if omitted.",
)
async def analyze(
    interaction: discord.Interaction,
    ticker: str,
    trade_date: str | None = None,
) -> None:
    ticker = ticker.strip().upper()
    trade_date = trade_date or dt.date.today().isoformat()

    await interaction.response.defer(thinking=True)
    response_message = await interaction.original_response()

    async with bot.analysis_lock:
        try:
            markdown = await asyncio.to_thread(run_analysis_and_format, ticker, trade_date)
        except Exception as exc:
            await response_message.edit(
                content=f"Analysis failed for `{ticker}` on `{trade_date}`.\n```text\n{exc}\n```"
            )
            return

    chunks = list(split_for_discord(markdown))
    if not chunks:
        await response_message.edit(content="No result generated.")
        return

    await response_message.edit(
        content=f"Analysis complete for `{ticker}` on `{trade_date}`. Result posted below."
    )

    channel = interaction.channel
    if channel is None:
        return

    await channel.send(chunks[0])
    for chunk in chunks[1:]:
        await channel.send(chunk)


def main() -> None:
    load_dotenv()

    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set.")

    bot.run(token)


if __name__ == "__main__":
    main()
