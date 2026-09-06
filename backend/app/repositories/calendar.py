from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TradingCalendar


class TradingCalendarRepository:
    def __init__(self, session: Session):
        self.session = session

    def entry(self, exchange: str, day: date) -> TradingCalendar | None:
        return self.session.scalar(select(TradingCalendar).where(TradingCalendar.exchange == exchange, TradingCalendar.trading_date == day))

    def trading_days(self, exchange: str, start: date, end: date) -> list[date]:
        return list(
            self.session.scalars(
                select(TradingCalendar.trading_date)
                .where(
                    TradingCalendar.exchange == exchange,
                    TradingCalendar.is_trading_day.is_(True),
                    TradingCalendar.trading_date.between(start, end),
                )
                .order_by(TradingCalendar.trading_date)
            )
        )

    def entries(self, exchange: str, start: date, end: date) -> list[TradingCalendar]:
        return list(
            self.session.scalars(
                select(TradingCalendar)
                .where(
                    TradingCalendar.exchange == exchange,
                    TradingCalendar.trading_date.between(start, end),
                )
                .order_by(TradingCalendar.trading_date)
            )
        )

    def entries_for_dates(self, exchange_dates: set[tuple[str, date]]) -> dict[tuple[str, date], TradingCalendar]:
        if not exchange_dates:
            return {}
        exchanges = {exchange for exchange, _ in exchange_dates}
        earliest = min(day for _, day in exchange_dates)
        latest = max(day for _, day in exchange_dates)
        rows = self.session.scalars(
            select(TradingCalendar).where(
                TradingCalendar.exchange.in_(exchanges),
                TradingCalendar.trading_date.between(earliest, latest),
            )
        )
        return {
            (row.exchange, row.trading_date): row
            for row in rows
            if (row.exchange, row.trading_date) in exchange_dates
        }

    def trading_days_for_exchanges(self, exchanges: set[str], start: date, end: date) -> dict[str, list[date]]:
        grouped = {exchange: [] for exchange in exchanges}
        if not exchanges:
            return grouped
        rows = self.session.execute(
            select(TradingCalendar.exchange, TradingCalendar.trading_date)
            .where(
                TradingCalendar.exchange.in_(exchanges),
                TradingCalendar.is_trading_day.is_(True),
                TradingCalendar.trading_date.between(start, end),
            )
            .order_by(TradingCalendar.exchange, TradingCalendar.trading_date)
        )
        for exchange, trading_date in rows:
            grouped[exchange].append(trading_date)
        return grouped

    def previous(self, exchange: str, day: date) -> date | None:
        return self.session.scalar(
            select(TradingCalendar.trading_date)
            .where(TradingCalendar.exchange == exchange, TradingCalendar.is_trading_day.is_(True), TradingCalendar.trading_date < day)
            .order_by(TradingCalendar.trading_date.desc())
            .limit(1)
        )

    def next(self, exchange: str, day: date) -> date | None:
        return self.session.scalar(
            select(TradingCalendar.trading_date)
            .where(TradingCalendar.exchange == exchange, TradingCalendar.is_trading_day.is_(True), TradingCalendar.trading_date > day)
            .order_by(TradingCalendar.trading_date)
            .limit(1)
        )
