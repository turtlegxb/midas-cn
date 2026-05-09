from __future__ import annotations

from midas_cn.analysts.breadth import MarketBreadthAnalyst
from midas_cn.analysts.china_market import ChinaMarketAnalyst
from midas_cn.analysts.fundamental import FundamentalAnalyst
from midas_cn.analysts.macro import MacroRegimeAnalyst
from midas_cn.analysts.news import NewsAnalyst
from midas_cn.analysts.sector import SectorRotationAnalyst
from midas_cn.analysts.sentiment import SentimentAnalyst
from midas_cn.analysts.technical import TechnicalAnalyst
from midas_cn.config import AppConfig, load_config
from midas_cn.orchestration.desk import TradingDesk


def build_trading_desk(config: AppConfig | None = None) -> TradingDesk:
    app_config = config or load_config()
    analysts = [
        MacroRegimeAnalyst(),
        MarketBreadthAnalyst(),
        ChinaMarketAnalyst(),
        TechnicalAnalyst(),
        FundamentalAnalyst(),
        NewsAnalyst(),
        SentimentAnalyst(),
        SectorRotationAnalyst(),
    ]
    return TradingDesk(app_config, analysts)
