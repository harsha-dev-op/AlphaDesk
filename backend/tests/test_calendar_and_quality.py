from datetime import date, time
from decimal import Decimal

from app.models import DailyPrice, Security, TradingCalendar
from app.repositories.calendar import TradingCalendarRepository
from app.services.calendar import TradingCalendarService
from app.services.quality import DataQualityService


def test_calendar_weekend_holiday_and_navigation(db):
    entries = [
        TradingCalendar(exchange="NSE", trading_date=date(2025, 2, 21), is_trading_day=True, session_open=time(9, 15), session_close=time(15, 30), session_type="REGULAR"),
        TradingCalendar(exchange="NSE", trading_date=date(2025, 2, 22), is_trading_day=False, session_type="CLOSED", notes="Weekend"),
        TradingCalendar(exchange="NSE", trading_date=date(2025, 2, 23), is_trading_day=False, session_type="CLOSED", notes="Weekend"),
        TradingCalendar(exchange="NSE", trading_date=date(2025, 2, 24), is_trading_day=False, session_type="CLOSED", notes="Holiday fixture"),
        TradingCalendar(exchange="NSE", trading_date=date(2025, 2, 25), is_trading_day=True, session_open=time(9, 15), session_close=time(15, 30), session_type="REGULAR"),
    ]
    db.add_all(entries)
    db.commit()
    service = TradingCalendarService(TradingCalendarRepository(db))
    assert not service.is_trading_day("NSE", date(2025, 2, 22))
    assert not service.is_trading_day("NSE", date(2025, 2, 24))
    assert service.previous_trading_day("NSE", date(2025, 2, 25)) == date(2025, 2, 21)
    assert service.next_trading_day("NSE", date(2025, 2, 21)) == date(2025, 2, 25)


def test_data_quality_status_reports_missing_session(db):
    company = Security(exchange="NSE", symbol="QUALITY", trading_symbol="QUALITY-EQ", company_name="Quality Demo", security_type="EQUITY", currency="INR", is_active=True)
    db.add(company)
    db.flush()
    for day in [date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 5)]:
        db.add(TradingCalendar(exchange="NSE", trading_date=day, is_trading_day=True, session_type="REGULAR"))
    for day in [date(2025, 3, 3), date(2025, 3, 5)]:
        db.add(DailyPrice(security_id=company.id, trading_date=day, open=Decimal("100"), high=Decimal("102"), low=Decimal("99"), close=Decimal("101"), volume=100, source="TEST"))
    db.commit()
    overall, checks, _, _ = DataQualityService(db).run(as_of=date(2025, 3, 5))
    coverage = next(check for check in checks if check.name == "Session coverage")
    assert overall.value == "WARNING"
    assert coverage.issue_count == 1


def test_data_quality_endpoint(client):
    response = client.get("/api/v1/data-quality/status")
    assert response.status_code == 200
    assert response.json()["status"] in {"HEALTHY", "WARNING", "FAILED"}
