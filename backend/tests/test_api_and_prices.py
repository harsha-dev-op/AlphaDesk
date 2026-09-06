import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import CorporateAction, DailyPrice, Security
from app.quality.checks import validate_ohlcv
from app.repositories.securities import SecurityRepository
from app.schemas.api import SecurityCreate
from app.services.adjustments import PriceAdjustmentService


def make_security(symbol: str = "TESTCO") -> Security:
    return Security(exchange="NSE", symbol=symbol, trading_symbol=f"{symbol}-EQ", company_name=f"{symbol} Demo Ltd", security_type="EQUITY", currency="INR", is_active=True)


def make_price(security_id, day: date, close: str = "100") -> DailyPrice:
    value = Decimal(close)
    return DailyPrice(security_id=security_id, trading_date=day, open=value, high=value + 2, low=value - 2, close=value, volume=1000, source="TEST")


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["database"] == "healthy"


def test_security_creation_and_retrieval(db, client):
    created = SecurityRepository(db).create(SecurityCreate(exchange="NSE", symbol="ACME", trading_symbol="ACME-EQ", company_name="Acme Demo Ltd"))
    db.commit()
    response = client.get("/api/v1/securities/acme")
    assert response.status_code == 200
    assert response.json()["id"] == str(created.id)
    assert response.json()["company_name"] == "Acme Demo Ltd"


def test_duplicate_price_is_database_protected(db):
    security = make_security()
    db.add(security)
    db.flush()
    db.add_all([make_price(security.id, date(2025, 1, 2)), make_price(security.id, date(2025, 1, 2))])
    with pytest.raises(IntegrityError):
        db.commit()


def test_ohlcv_validation_flags_impossible_rows():
    issues = validate_ohlcv(open_=Decimal("120"), high=Decimal("110"), low=Decimal("115"), close=Decimal("-1"), volume=-10)
    assert "high is below low" in issues
    assert "prices must be non-negative" in issues
    assert "volume must be non-negative" in issues


def test_stock_split_adjustment():
    security_id = uuid.uuid4()
    before = make_price(security_id, date(2025, 1, 31), "200")
    after = make_price(security_id, date(2025, 2, 3), "101")
    action = CorporateAction(security_id=security_id, action_type="STOCK_SPLIT", ex_date=date(2025, 2, 3), ratio_numerator=Decimal("2"), ratio_denominator=Decimal("1"), source="TEST")
    adjusted = PriceAdjustmentService().adjust([before, after], [action])
    assert adjusted[0].close == Decimal("100.0000")
    assert adjusted[0].volume == 2000
    assert adjusted[1].close == Decimal("101.0000")


def test_bonus_adjustment():
    security_id = uuid.uuid4()
    before = make_price(security_id, date(2025, 2, 7), "180")
    action = CorporateAction(security_id=security_id, action_type="BONUS", ex_date=date(2025, 2, 10), ratio_numerator=Decimal("1"), ratio_denominator=Decimal("1"), source="TEST")
    adjusted = PriceAdjustmentService().adjust([before], [action])
    assert adjusted[0].close == Decimal("90.0000")
    assert adjusted[0].volume == 2000
