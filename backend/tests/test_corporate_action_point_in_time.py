from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.data.providers import DemoMarketDataProvider
from app.models import CorporateAction, Security
from app.repositories.securities import CorporateActionRevisionError, SecurityRepository


def _security(symbol: str = "ACTIONCO") -> Security:
    return Security(
        exchange="NSE",
        symbol=symbol,
        trading_symbol=f"{symbol}-EQ",
        company_name=f"{symbol} Test",
        security_type="EQUITY",
        currency="INR",
        is_active=True,
    )


def _at(day: int) -> datetime:
    return datetime(2024, 1, day, 12, tzinfo=UTC)


def test_demo_actions_use_documented_conservative_fallback():
    for action in DemoMarketDataProvider().get_corporate_actions():
        assert action.get("source_published_at") is None
        assert action["available_at"].date() == action["announcement_date"]
        assert action["available_at"].time() == datetime.max.time().replace(microsecond=0)
        assert action["available_at"].utcoffset() is not None


def test_new_action_defaults_availability_to_source_publication_or_receipt(db):
    security = _security()
    db.add(security)
    db.flush()
    published = CorporateAction(
        security_id=security.id,
        action_type="STOCK_SPLIT",
        ex_date=date(2024, 2, 1),
        ratio_numerator=2,
        ratio_denominator=1,
        source="TEST",
        source_published_at=_at(2),
    )
    received = CorporateAction(
        security_id=security.id,
        action_type="BONUS",
        ex_date=date(2024, 3, 1),
        ratio_numerator=1,
        ratio_denominator=1,
        source="TEST",
    )
    db.add_all([published, received])
    db.flush()

    assert published.available_at == published.source_published_at
    assert received.available_at == received.ingested_at


def test_repository_selects_latest_revision_eligible_at_as_of(db):
    security = _security()
    db.add(security)
    db.flush()
    original = CorporateAction(
        security_id=security.id,
        action_type="STOCK_SPLIT",
        ex_date=date(2024, 1, 10),
        ratio_numerator=2,
        ratio_denominator=1,
        source="TEST",
        available_at=_at(1),
    )
    db.add(original)
    db.flush()
    revision = CorporateAction(
        security_id=security.id,
        action_type="STOCK_SPLIT",
        ex_date=date(2024, 1, 10),
        ratio_numerator=3,
        ratio_denominator=1,
        source="TEST_CORRECTION",
        available_at=_at(3),
        supersedes_action_id=original.id,
    )
    db.add(revision)
    db.commit()

    repository = SecurityRepository(db)
    before = repository.corporate_actions(
        security.id,
        as_of=_at(2),
        through=date(2024, 1, 31),
    )
    after = repository.corporate_actions(
        security.id,
        as_of=_at(4),
        through=date(2024, 1, 31),
    )

    assert [action.id for action in before] == [original.id]
    assert [action.id for action in after] == [revision.id]
    assert db.get(CorporateAction, original.id) is original


def test_revision_resolution_precedes_ex_date_horizon_filter(db):
    security = _security()
    db.add(security)
    db.flush()
    original = CorporateAction(
        security_id=security.id,
        action_type="STOCK_SPLIT",
        ex_date=date(2024, 1, 10),
        ratio_numerator=2,
        ratio_denominator=1,
        source="TEST",
        available_at=_at(1),
    )
    db.add(original)
    db.flush()
    db.add(
        CorporateAction(
            security_id=security.id,
            action_type="STOCK_SPLIT",
            ex_date=date(2024, 2, 10),
            ratio_numerator=2,
            ratio_denominator=1,
            source="TEST_CORRECTION",
            available_at=_at(3),
            supersedes_action_id=original.id,
        )
    )
    db.commit()

    repository = SecurityRepository(db)
    assert repository.corporate_actions(
        security.id,
        as_of=_at(2),
        through=date(2024, 1, 31),
    )
    assert repository.corporate_actions(
        security.id,
        as_of=_at(4),
        through=date(2024, 1, 31),
    ) == []


def test_revision_cycle_is_rejected_when_it_becomes_eligible(db):
    security = _security()
    db.add(security)
    db.flush()
    first = CorporateAction(
        security_id=security.id,
        action_type="STOCK_SPLIT",
        ex_date=date(2024, 1, 10),
        ratio_numerator=2,
        ratio_denominator=1,
        source="TEST",
        available_at=_at(1),
    )
    db.add(first)
    db.flush()
    second = CorporateAction(
        security_id=security.id,
        action_type="STOCK_SPLIT",
        ex_date=date(2024, 1, 10),
        ratio_numerator=3,
        ratio_denominator=1,
        source="TEST",
        available_at=_at(2),
        supersedes_action_id=first.id,
    )
    db.add(second)
    db.flush()
    first.supersedes_action_id = second.id
    db.commit()

    with pytest.raises(CorporateActionRevisionError, match="cycle"):
        SecurityRepository(db).corporate_actions(
            security.id,
            as_of=_at(4),
            through=date(2024, 1, 31),
        )


def test_revision_branching_is_database_protected(db):
    security = _security()
    db.add(security)
    db.flush()
    original = CorporateAction(
        security_id=security.id,
        action_type="BONUS",
        ex_date=date(2024, 2, 1),
        ratio_numerator=1,
        ratio_denominator=1,
        source="TEST",
        available_at=_at(1),
    )
    db.add(original)
    db.flush()
    for offset in (1, 2):
        db.add(
            CorporateAction(
                security_id=security.id,
                action_type="BONUS",
                ex_date=date(2024, 2, 1),
                ratio_numerator=offset + 1,
                ratio_denominator=1,
                source="TEST",
                available_at=_at(offset + 1),
                supersedes_action_id=original.id,
            )
        )
    with pytest.raises(IntegrityError):
        db.commit()


def test_cross_security_revision_is_rejected(db):
    first_security = _security("FIRSTCO")
    second_security = _security("SECONDCO")
    db.add_all([first_security, second_security])
    db.flush()
    original = CorporateAction(
        security_id=first_security.id,
        action_type="STOCK_SPLIT",
        ex_date=date(2024, 1, 10),
        ratio_numerator=2,
        ratio_denominator=1,
        source="TEST",
        available_at=_at(1),
    )
    db.add(original)
    db.flush()
    db.add(
        CorporateAction(
            security_id=second_security.id,
            action_type="STOCK_SPLIT",
            ex_date=date(2024, 1, 10),
            ratio_numerator=3,
            ratio_denominator=1,
            source="TEST",
            available_at=_at(2),
            supersedes_action_id=original.id,
        )
    )
    db.commit()

    with pytest.raises(CorporateActionRevisionError, match="retain security_id"):
        SecurityRepository(db).corporate_actions_for_securities(
            [first_security.id, second_security.id],
            as_of=_at(4),
            through=date(2024, 1, 31),
        )


def test_unavailable_revision_does_not_hide_eligible_original(db):
    security = _security()
    db.add(security)
    db.flush()
    original = CorporateAction(
        security_id=security.id,
        action_type="STOCK_SPLIT",
        ex_date=date(2024, 1, 10),
        ratio_numerator=2,
        ratio_denominator=1,
        source="TEST",
        available_at=_at(1),
    )
    db.add(original)
    db.flush()
    revision = CorporateAction(
        security_id=security.id,
        action_type="STOCK_SPLIT",
        ex_date=date(2024, 1, 10),
        ratio_numerator=4,
        ratio_denominator=1,
        source="TEST",
        available_at=_at(4),
        supersedes_action_id=original.id,
    )
    db.add(revision)
    db.commit()

    eligible = SecurityRepository(db).corporate_actions_for_securities(
        [security.id],
        as_of=_at(2),
        through=date(2024, 1, 31),
    )
    assert [action.id for action in eligible[security.id]] == [original.id]
