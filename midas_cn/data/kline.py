from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from midas_cn.models import KLineBar


@dataclass(frozen=True)
class TechnicalProfile:
    trend_strength: float
    ma_alignment: float
    rsi: float
    volume_ratio: float
    support: float
    resistance: float
    ema8: float | None
    ema21: float | None
    ema55: float | None
    close: float

    def as_dict(self) -> dict[str, float | None]:
        return {
            "trend_strength": self.trend_strength,
            "ma_alignment": self.ma_alignment,
            "rsi": self.rsi,
            "volume_ratio": self.volume_ratio,
            "support": self.support,
            "resistance": self.resistance,
            "ema8": self.ema8,
            "ema21": self.ema21,
            "ema55": self.ema55,
            "close": self.close,
        }


def normalize_symbol_for_akshare(symbol: str) -> str:
    return symbol.split(".", 1)[0]


def build_technical_profile(bars: Iterable[KLineBar]) -> TechnicalProfile:
    ordered = list(bars)
    if len(ordered) < 2:
        raise ValueError("at least 2 K-line bars are required")

    closes = [bar.close for bar in ordered]
    highs = [bar.high for bar in ordered]
    lows = [bar.low for bar in ordered]
    volumes = [bar.volume for bar in ordered]
    close = closes[-1]
    ema8 = ema(closes, 8)
    ema21 = ema(closes, 21)
    ema55 = ema(closes, 55)
    rsi14 = rsi(closes, 14)
    volume_base = average(volumes[-21:-1]) if len(volumes) >= 22 else average(volumes[:-1])
    volume_ratio = volumes[-1] / volume_base if volume_base else 1.0
    support = min(lows[-20:]) if len(lows) >= 20 else min(lows)
    resistance = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    trend_strength = _trend_strength(close, ema21, ema55)
    ma_alignment = _ma_alignment(close, ema8, ema21, ema55)

    return TechnicalProfile(
        trend_strength=round(trend_strength, 3),
        ma_alignment=round(ma_alignment, 3),
        rsi=round(rsi14, 2),
        volume_ratio=round(volume_ratio, 3),
        support=round(support, 3),
        resistance=round(resistance, 3),
        ema8=round(ema8, 3) if ema8 is not None else None,
        ema21=round(ema21, 3) if ema21 is not None else None,
        ema55=round(ema55, 3) if ema55 is not None else None,
        close=round(close, 3),
    )


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    current = average(values[:period])
    for value in values[period:]:
        current = value * multiplier + current * (1 - multiplier)
    return current


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values[-period - 1 : -1], values[-period:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    avg_gain = average(gains)
    avg_loss = average(losses)
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _trend_strength(close: float, ema21: float | None, ema55: float | None) -> float:
    anchor = ema55 or ema21
    if not anchor:
        return 0.0
    return max(-1.0, min(1.0, (close / anchor - 1) * 5))


def _ma_alignment(close: float, ema8: float | None, ema21: float | None, ema55: float | None) -> float:
    if ema8 and ema21 and ema55:
        if close > ema8 > ema21 > ema55:
            return 0.65
        if close < ema8 < ema21 < ema55:
            return -0.65
    if ema8 and ema21:
        if close > ema8 > ema21:
            return 0.35
        if close < ema8 < ema21:
            return -0.35
    return 0.0

