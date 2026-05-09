"""Domain analysts for A-share decision workflows."""

from midas_cn.analysts.breadth import MarketBreadthAnalyst
from midas_cn.analysts.china_market import ChinaMarketAnalyst
from midas_cn.analysts.fundamental import FundamentalAnalyst
from midas_cn.analysts.macro import MacroRegimeAnalyst
from midas_cn.analysts.news import NewsAnalyst
from midas_cn.analysts.sector import SectorRotationAnalyst
from midas_cn.analysts.sentiment import SentimentAnalyst
from midas_cn.analysts.technical import TechnicalAnalyst

__all__ = [
    "ChinaMarketAnalyst",
    "FundamentalAnalyst",
    "MacroRegimeAnalyst",
    "MarketBreadthAnalyst",
    "NewsAnalyst",
    "SectorRotationAnalyst",
    "SentimentAnalyst",
    "TechnicalAnalyst",
]
