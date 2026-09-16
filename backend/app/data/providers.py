from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo


DEMO_MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")


def _demo_legacy_availability(announcement_date: date) -> datetime:
    """Conservative deterministic fallback for fictional data, not source truth."""
    return datetime.combine(announcement_date, time(23, 59, 59), tzinfo=DEMO_MARKET_TIMEZONE)


class MarketDataProvider(ABC):
    """Vendor-neutral contract. Provider payloads are normalized before persistence."""

    @abstractmethod
    def get_security_master(self) -> list[dict]: ...

    @abstractmethod
    def get_daily_prices(self, start: date, end: date) -> list[dict]: ...

    @abstractmethod
    def get_corporate_actions(self) -> list[dict]: ...

    @abstractmethod
    def get_index_membership(self) -> list[dict]: ...

    @abstractmethod
    def get_index_daily_prices(self) -> list[dict]: ...

    @abstractmethod
    def get_fundamentals(self) -> list[dict]: ...


class DemoMarketDataProvider(MarketDataProvider):
    """Deterministic, fictional data for local development. It is not investment data."""

    code = "DEMO_LOCAL"

    def get_security_master(self) -> list[dict]:
        return [
            {"exchange": "NSE", "symbol": "ALPHAIND", "trading_symbol": "ALPHAIND-EQ", "series": "EQ", "company_name": "Alpha Industries Demo Ltd", "isin": "DEMO00000001", "security_type": "EQUITY", "sector": "Industrials", "industry": "Capital Goods", "currency": "INR", "listing_date": date(2020, 1, 1), "is_active": True, "data_origin": "DEMO"},
            {"exchange": "NSE", "symbol": "BETATECH", "trading_symbol": "BETATECH-EQ", "series": "EQ", "company_name": "Beta Technology Demo Ltd", "isin": "DEMO00000002", "security_type": "EQUITY", "sector": "Information Technology", "industry": "Software", "currency": "INR", "listing_date": date(2021, 3, 15), "is_active": True, "data_origin": "DEMO"},
            {"exchange": "NSE", "symbol": "GAMMAFIN", "trading_symbol": "GAMMAFIN-EQ", "series": "EQ", "company_name": "Gamma Finance Demo Ltd", "isin": "DEMO00000003", "security_type": "EQUITY", "sector": "Financial Services", "industry": "Consumer Finance", "currency": "INR", "listing_date": date(2019, 7, 4), "is_active": True, "data_origin": "DEMO"},
            {"exchange": "NSE", "symbol": "OLDCO", "trading_symbol": "OLDCO-EQ", "series": "EQ", "company_name": "Old Company Demo Ltd", "isin": "DEMO00000004", "security_type": "EQUITY", "sector": "Materials", "industry": "Specialty Chemicals", "currency": "INR", "listing_date": date(2018, 5, 10), "delisting_date": date(2025, 2, 14), "is_active": False, "data_origin": "DEMO"},
        ]

    def get_daily_prices(self, start: date, end: date) -> list[dict]:
        rows: list[dict] = []
        day = start
        symbols = ["ALPHAIND", "BETATECH", "GAMMAFIN", "OLDCO"]
        while day <= end:
            if day.weekday() < 5 and day != date(2025, 2, 26):
                offset = (day - start).days
                for index, symbol in enumerate(symbols):
                    if symbol == "OLDCO" and day > date(2025, 2, 14):
                        continue
                    base = Decimal(100 + index * 80) + Decimal(offset) * Decimal("0.35")
                    if symbol == "ALPHAIND" and day >= date(2025, 2, 3):
                        base /= Decimal("2")
                    if symbol == "BETATECH" and day >= date(2025, 2, 10):
                        base /= Decimal("2")
                    open_ = base.quantize(Decimal("0.01"))
                    close = (base + Decimal((offset + index) % 5 - 2) * Decimal("0.22")).quantize(Decimal("0.01"))
                    high = max(open_, close) + Decimal("1.10")
                    low = min(open_, close) - Decimal("0.90")
                    volume = 100_000 + offset * 700 + index * 11_000
                    rows.append({"symbol": symbol, "trading_date": day, "open": open_, "high": high, "low": low, "close": close, "volume": volume, "traded_value": (close * volume).quantize(Decimal("0.01")), "source": self.code, "data_origin": "DEMO"})
            day += timedelta(days=1)
        return rows

    def get_corporate_actions(self) -> list[dict]:
        return [
            {"symbol": "ALPHAIND", "action_type": "STOCK_SPLIT", "announcement_date": date(2025, 1, 10), "ex_date": date(2025, 2, 3), "record_date": date(2025, 2, 3), "ratio_numerator": Decimal("2"), "ratio_denominator": Decimal("1"), "notes": "Demo 2-for-1 split", "source": self.code, "available_at": _demo_legacy_availability(date(2025, 1, 10)), "data_origin": "DEMO"},
            {"symbol": "BETATECH", "action_type": "BONUS", "announcement_date": date(2025, 1, 15), "ex_date": date(2025, 2, 10), "record_date": date(2025, 2, 10), "ratio_numerator": Decimal("1"), "ratio_denominator": Decimal("1"), "notes": "Demo 1-for-1 bonus", "source": self.code, "available_at": _demo_legacy_availability(date(2025, 1, 15)), "data_origin": "DEMO"},
        ]

    def get_index_membership(self) -> list[dict]:
        return [
            {"index_symbol": "NIFTYDEMO100", "symbol": "ALPHAIND", "valid_from": date(2025, 1, 1), "valid_to": None, "source": self.code, "data_origin": "DEMO"},
            {"index_symbol": "NIFTYDEMO100", "symbol": "BETATECH", "valid_from": date(2025, 1, 1), "valid_to": date(2025, 2, 14), "source": self.code, "data_origin": "DEMO"},
            {"index_symbol": "NIFTYDEMO100", "symbol": "GAMMAFIN", "valid_from": date(2025, 2, 15), "valid_to": None, "source": self.code, "data_origin": "DEMO"},
            {"index_symbol": "NIFTYDEMO100", "symbol": "OLDCO", "valid_from": date(2025, 1, 1), "valid_to": date(2025, 2, 14), "source": self.code, "data_origin": "DEMO"},
        ]

    def get_index_daily_prices(self) -> list[dict]:
        """Generate fictional benchmark levels with derived four-regime coverage."""
        rows: list[dict] = []
        current = date(2021, 1, 4)
        previous_close = Decimal("1000")
        session_index = 0
        while session_index < 1300:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue
            if session_index < 320:
                factor = Decimal("1.0015")
            elif session_index < 500:
                factor = Decimal("1.035") if session_index % 2 == 0 else Decimal("0.967")
            elif session_index < 800:
                factor = Decimal("0.9970")
            elif session_index < 1050:
                cycle = (Decimal("1.002"), Decimal("0.998"), Decimal("1.001"), Decimal("0.999"))
                factor = cycle[session_index % len(cycle)]
            else:
                factor = Decimal("1.0012")
            close = (previous_close * factor).quantize(Decimal("0.000001"))
            open_ = previous_close
            range_factor = Decimal("0.010") if 320 <= session_index < 500 else Decimal("0.003")
            high = (max(open_, close) * (Decimal(1) + range_factor)).quantize(Decimal("0.000001"))
            low = (min(open_, close) * (Decimal(1) - range_factor)).quantize(Decimal("0.000001"))
            rows.append(
                {
                    "index_symbol": "NIFTYDEMO100",
                    "trading_date": current,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "source_mode": "DEMO",
                    "source": self.code,
                    "available_at": datetime.combine(
                        current, time(15, 30), tzinfo=DEMO_MARKET_TIMEZONE
                    ),
                }
            )
            previous_close = close
            session_index += 1
            current += timedelta(days=1)
        return rows

    def get_fundamentals(self) -> list[dict]:
        periods = [
            (date(2024, 4, 1), date(2024, 6, 30), 2025, 1, date(2024, 8, 1)),
            (date(2024, 7, 1), date(2024, 9, 30), 2025, 2, date(2024, 11, 5)),
        ]
        rows: list[dict] = []
        for security_index, symbol in enumerate(["ALPHAIND", "BETATECH", "GAMMAFIN", "OLDCO"]):
            for period_index, (start, end, year, quarter, reported) in enumerate(periods):
                scale = Decimal(1000 + security_index * 300 + period_index * 120)
                rows.append({"symbol": symbol, "fiscal_period_start": start, "fiscal_period_end": end, "fiscal_year": year, "fiscal_quarter": quarter, "reported_date": reported, "effective_date": reported, "revenue": scale, "ebitda": scale * Decimal("0.24"), "operating_profit": scale * Decimal("0.20"), "net_profit": scale * Decimal("0.13"), "eps": Decimal("4.2") + Decimal(security_index) + Decimal(period_index) / 2, "total_assets": scale * 4, "total_debt": scale * Decimal("0.8"), "cash": scale * Decimal("0.4"), "equity": scale * Decimal("2.2"), "source": self.code})
        return rows
