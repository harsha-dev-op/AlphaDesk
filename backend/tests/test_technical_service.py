from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.models import CorporateAction, DailyPrice, DataIngestionRun, Security, TradingCalendar
from app.technical.service import TechnicalFeatureService


def seed_security(db, *, symbol: str = "TECH", rows: int = 220, missing_index: int | None = None) -> Security:
    security = Security(exchange="NSE", symbol=symbol, trading_symbol=f"{symbol}-EQ", company_name=f"{symbol} Test", security_type="EQUITY", currency="INR", is_active=True)
    db.add(security)
    db.flush()
    start = date(2024, 1, 1)
    for index in range(rows):
        day = start + timedelta(days=index)
        db.add(TradingCalendar(exchange="NSE", trading_date=day, is_trading_day=True, session_open=time(9, 15), session_close=time(15, 30)))
        if index == missing_index:
            continue
        value = Decimal(100 + index)
        db.add(DailyPrice(security_id=security.id, trading_date=day, open=value, high=value + 1, low=value - 1, close=value, volume=1000 + index, traded_value=value * (1000 + index), source="TESTSET"))
    db.add(DataIngestionRun(dataset_code="technical_fixture", dataset_version="v1.2.3", provider="TESTSET", status="SUCCESS", completed_at=datetime(2024, 12, 31, tzinfo=UTC), records_written=rows))
    db.commit()
    return security


def test_requested_range_uses_pre_start_history_for_sma_200(db):
    security = seed_security(db)
    start = date(2024, 1, 1) + timedelta(days=209)
    response = TechnicalFeatureService(db).compute(security, start_date=start, end_date=start + timedelta(days=2), adjustment_policy="RAW", as_of=datetime(2025, 1, 1, tzinfo=UTC))
    assert len(response.items) == 3
    assert response.items[0].values["SMA_200"] is not None
    assert response.items[0].observation_date == start
    assert response.dataset.dataset_version == "v1.2.3"


def test_missing_expected_session_warns_without_inventing_a_row(db):
    security = seed_security(db, rows=10, missing_index=5)
    response = TechnicalFeatureService(db).compute(security, start_date=None, end_date=date(2024, 1, 10), adjustment_policy="RAW", as_of=datetime(2025, 1, 1, tzinfo=UTC))
    assert len(response.items) == 9
    assert float(response.items[5].values["RET_1D"]) == pytest.approx(106 / 104 - 1)
    assert response.quality_warnings == ["Missing expected trading sessions: 2024-01-06"]


def test_session_close_availability_and_timezone_filter(db):
    security = seed_security(db, rows=1)
    service = TechnicalFeatureService(db)
    before = service.compute(security, start_date=None, end_date=date(2024, 1, 1), adjustment_policy="RAW", as_of=datetime.fromisoformat("2024-01-01T15:29:59+05:30"))
    after = service.compute(security, start_date=None, end_date=date(2024, 1, 1), adjustment_policy="RAW", as_of=datetime.fromisoformat("2024-01-01T15:30:00+05:30"))
    assert before.items == []
    assert len(after.items) == 1
    assert after.items[0].available_at.isoformat() == "2024-01-01T15:30:00+05:30"


def test_calendar_fallback_is_conservative(db):
    security = Security(exchange="NSE", symbol="NOCAL", trading_symbol="NOCAL-EQ", company_name="No Calendar", security_type="EQUITY", currency="INR", is_active=True)
    db.add(security)
    db.flush()
    db.add(DailyPrice(security_id=security.id, trading_date=date(2024, 2, 1), open=100, high=101, low=99, close=100, volume=1000, source="TEST"))
    db.commit()
    service = TechnicalFeatureService(db)
    before = service.compute(security, start_date=None, end_date=date(2024, 2, 1), adjustment_policy="RAW", as_of=datetime.fromisoformat("2024-02-01T23:59:58+05:30"))
    after = service.compute(security, start_date=None, end_date=date(2024, 2, 1), adjustment_policy="RAW", as_of=datetime.fromisoformat("2024-02-01T23:59:59+05:30"))
    assert before.items == []
    assert len(after.items) == 1


def test_split_and_bonus_adjusted_features_are_continuous_while_raw_tape_is_unchanged(db):
    security = Security(exchange="NSE", symbol="ACTIONS", trading_symbol="ACTIONS-EQ", company_name="Action Test", security_type="EQUITY", currency="INR", is_active=True)
    db.add(security)
    db.flush()
    start = date(2024, 1, 1)
    for index in range(45):
        day = start + timedelta(days=index)
        close = Decimal(200) if index < 15 else (Decimal(100) if index < 35 else Decimal(50))
        db.add(DailyPrice(security_id=security.id, trading_date=day, open=close, high=close + 1, low=close - 1, close=close, volume=1000, traded_value=close * 1000, source="ACTIONS"))
        db.add(TradingCalendar(exchange="NSE", trading_date=day, is_trading_day=True, session_close=time(15, 30)))
    db.add_all([
        CorporateAction(security_id=security.id, action_type="STOCK_SPLIT", ex_date=start + timedelta(days=15), ratio_numerator=2, ratio_denominator=1, source="ACTIONS", available_at=datetime(2023, 12, 1, tzinfo=UTC)),
        CorporateAction(security_id=security.id, action_type="BONUS", ex_date=start + timedelta(days=35), ratio_numerator=1, ratio_denominator=1, source="ACTIONS", available_at=datetime(2023, 12, 2, tzinfo=UTC)),
    ])
    db.commit()
    service = TechnicalFeatureService(db)
    end = start + timedelta(days=44)
    adjusted = service.compute(security, start_date=end, end_date=end, adjustment_policy="ADJUSTED", as_of=datetime(2025, 1, 1, tzinfo=UTC)).items[0]
    raw = service.compute(security, start_date=end, end_date=end, adjustment_policy="RAW", as_of=datetime(2025, 1, 1, tzinfo=UTC)).items[0]
    split_day = start + timedelta(days=15)
    bonus_day = start + timedelta(days=35)
    event_rows = service.compute(security, start_date=split_day, end_date=bonus_day, adjustment_policy="ADJUSTED", as_of=datetime(2025, 1, 1, tzinfo=UTC)).items
    by_date = {row.observation_date: row for row in event_rows}
    assert by_date[split_day].values["RET_1D"] == 0
    assert by_date[bonus_day].values["RET_1D"] == 0
    assert adjusted.values["SMA_20"] == 50
    assert float(adjusted.values["ATR_14"]) == pytest.approx(1.469272019038955)
    assert adjusted.values["TRUE_RANGE"] == 2
    assert raw.values["SMA_20"] == Decimal("75")
    assert raw.values["ATR_14"] > 2
    persisted = db.query(DailyPrice).filter(DailyPrice.security_id == security.id).order_by(DailyPrice.trading_date).first()
    assert persisted.close == 200


def test_exchange_holiday_is_not_reported_as_missing(db):
    security = Security(exchange="NSE", symbol="HOLIDAY", trading_symbol="HOLIDAY-EQ", company_name="Holiday Test", security_type="EQUITY", currency="INR", is_active=True)
    db.add(security)
    db.flush()
    start = date(2024, 3, 1)
    for index in (0, 2):
        day = start + timedelta(days=index)
        db.add(DailyPrice(security_id=security.id, trading_date=day, open=100, high=101, low=99, close=100, volume=1000, source="TEST"))
        db.add(TradingCalendar(exchange="NSE", trading_date=day, is_trading_day=True, session_close=time(15, 30)))
    db.add(TradingCalendar(exchange="NSE", trading_date=start + timedelta(days=1), is_trading_day=False, session_type="CLOSED", notes="Exchange holiday"))
    db.commit()
    response = TechnicalFeatureService(db).compute(security, start_date=None, end_date=start + timedelta(days=2), adjustment_policy="RAW", as_of=datetime(2025, 1, 1, tzinfo=UTC))
    assert response.quality_warnings == []
    assert len(response.items) == 2


def test_appending_future_rows_and_action_does_not_change_past_feature(db):
    security = seed_security(db, rows=25, symbol="PIT")
    service = TechnicalFeatureService(db)
    horizon = date(2024, 1, 25)
    as_of = datetime(2025, 1, 1, tzinfo=UTC)
    before = service.compute(security, start_date=horizon, end_date=horizon, adjustment_policy="ADJUSTED", as_of=as_of).items[0].values
    future = date(2024, 1, 26)
    db.add(DailyPrice(security_id=security.id, trading_date=future, open=62, high=63, low=61, close=62, volume=2000, source="TESTSET"))
    db.add(TradingCalendar(exchange="NSE", trading_date=future, is_trading_day=True, session_close=time(15, 30)))
    db.add(CorporateAction(security_id=security.id, action_type="STOCK_SPLIT", ex_date=future, ratio_numerator=2, ratio_denominator=1, source="TESTSET", available_at=datetime(2024, 1, 1, tzinfo=UTC)))
    db.commit()
    after = service.compute(security, start_date=horizon, end_date=horizon, adjustment_policy="ADJUSTED", as_of=as_of).items[0].values
    assert after == before
