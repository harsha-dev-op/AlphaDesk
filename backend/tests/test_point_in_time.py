from datetime import date
from decimal import Decimal

from app.models import FundamentalReport, IndexMembership, MarketIndex, Security
from app.repositories.fundamentals import FundamentalRepository
from app.repositories.indices import IndexRepository


def security(symbol: str) -> Security:
    return Security(exchange="NSE", symbol=symbol, trading_symbol=f"{symbol}-EQ", company_name=symbol, security_type="EQUITY", currency="INR", is_active=True)


def test_historical_index_membership_lookup(db):
    alpha, beta = security("ALPHA"), security("BETA")
    index = MarketIndex(name="Demo 100", symbol="DEMO100", provider="TEST", exchange="NSE")
    db.add_all([alpha, beta, index])
    db.flush()
    db.add_all([
        IndexMembership(index_id=index.id, security_id=alpha.id, valid_from=date(2024, 1, 1), valid_to=None, source="TEST"),
        IndexMembership(index_id=index.id, security_id=beta.id, valid_from=date(2024, 1, 1), valid_to=date(2024, 6, 30), source="TEST"),
    ])
    db.commit()
    january = IndexRepository(db).members_as_of(index.id, date(2024, 1, 15))
    july = IndexRepository(db).members_as_of(index.id, date(2024, 7, 1))
    assert {item.security.symbol for item in january} == {"ALPHA", "BETA"}
    assert {item.security.symbol for item in july} == {"ALPHA"}


def test_fundamentals_do_not_leak_future_reports(db):
    company = security("PITCO")
    db.add(company)
    db.flush()
    db.add_all([
        FundamentalReport(security_id=company.id, fiscal_period_start=date(2024, 4, 1), fiscal_period_end=date(2024, 6, 30), fiscal_year=2025, fiscal_quarter=1, reported_date=date(2024, 8, 1), effective_date=date(2024, 8, 1), revenue=Decimal("100"), source="TEST"),
        FundamentalReport(security_id=company.id, fiscal_period_start=date(2024, 7, 1), fiscal_period_end=date(2024, 9, 30), fiscal_year=2025, fiscal_quarter=2, reported_date=date(2024, 11, 5), effective_date=date(2024, 11, 5), revenue=Decimal("150"), source="TEST"),
    ])
    db.commit()
    repository = FundamentalRepository(db)
    assert repository.get_latest_as_of(company.id, date(2024, 7, 31)) is None
    assert repository.get_latest_as_of(company.id, date(2024, 9, 1)).revenue == Decimal("100.00")
    assert repository.get_latest_as_of(company.id, date(2024, 11, 5)).revenue == Decimal("150.00")
