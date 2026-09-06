from datetime import date

from app.repositories.calendar import TradingCalendarRepository


class TradingCalendarService:
    def __init__(self, repository: TradingCalendarRepository):
        self.repository = repository

    def is_trading_day(self, exchange: str, day: date) -> bool:
        entry = self.repository.entry(exchange, day)
        return bool(entry and entry.is_trading_day)

    def previous_trading_day(self, exchange: str, day: date) -> date | None:
        return self.repository.previous(exchange, day)

    def next_trading_day(self, exchange: str, day: date) -> date | None:
        return self.repository.next(exchange, day)
