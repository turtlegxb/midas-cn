from __future__ import annotations

from datetime import date, datetime

from midas_cn.models import TradingCalendarCheck


WEEKDAY_NAMES = {
    0: "monday",
    1: "tuesday",
    2: "wednesday",
    3: "thursday",
    4: "friday",
    5: "saturday",
    6: "sunday",
}


class AShareCalendar:
    """Minimal exchange calendar facade until a full holiday provider is wired."""

    def __init__(self, report_days: list[str] | None = None, holidays: set[date] | None = None):
        self.report_days = set(report_days or ["monday", "tuesday", "wednesday", "thursday"])
        self.holidays = holidays or {
            date(2026, 1, 1),
            date(2026, 5, 1),
            date(2026, 5, 4),
            date(2026, 5, 5),
        }

    def check(self, now: datetime) -> TradingCalendarCheck:
        weekday = WEEKDAY_NAMES[now.weekday()]
        is_weekday = now.weekday() < 5
        is_holiday = now.date() in self.holidays
        is_trading_day = is_weekday and not is_holiday
        is_report_day = is_trading_day and weekday in self.report_days

        if not is_weekday:
            reason = "weekend"
        elif is_holiday:
            reason = "exchange_holiday"
        elif not is_report_day:
            reason = "trading_day_not_in_report_schedule"
        else:
            reason = "scheduled_post_close_report_day"

        return TradingCalendarCheck(
            trade_date=now.date().isoformat(),
            is_trading_day=is_trading_day,
            is_report_day=is_report_day,
            reason=reason,
        )

