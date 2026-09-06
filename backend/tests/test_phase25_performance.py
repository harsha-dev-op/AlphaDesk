from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.benchmarks.technical_features import _points, build_dataset, run_case
from app.models import CorporateAction, DailyPrice, DataIngestionRun, Security, TradingCalendar
from app.services.adjustments import PriceAdjustmentService
from app.technical.calculators import calculate_feature_frame, calculate_latest_feature_snapshot
from app.technical.definitions import CORE_TECHNICAL_SET
from app.technical.service import TechnicalFeatureService


@pytest.mark.parametrize("rows", [1, 14, 15, 20, 21, 60, 61, 126, 200, 201, 2_500])
def test_latest_calculator_matches_full_frame_at_warmup_boundaries(rows):
    prices, _ = build_dataset(0, rows)
    points = _points(prices)
    assert calculate_latest_feature_snapshot(points) == calculate_feature_frame(points)[-1]


def test_latest_calculator_matches_adjusted_full_frame_and_version_is_unchanged():
    prices, actions = build_dataset(4, 2_500)
    points = _points(PriceAdjustmentService().adjust(prices, actions))
    assert calculate_latest_feature_snapshot(points) == calculate_feature_frame(points)[-1]
    assert CORE_TECHNICAL_SET.version == "1"


def test_latest_calculation_is_independent_between_securities():
    first, _ = build_dataset(1, 220)
    second, _ = build_dataset(2, 220)
    first_latest = calculate_latest_feature_snapshot(_points(first))
    second_latest = calculate_latest_feature_snapshot(_points(second))
    assert first_latest != second_latest
    assert calculate_latest_feature_snapshot(_points(first)) == first_latest


def test_future_rows_do_not_change_an_already_emitted_snapshot():
    prices, _ = build_dataset(1, 221)
    before = calculate_latest_feature_snapshot(_points(prices[:220]))
    after_frame = calculate_feature_frame(_points(prices))
    assert after_frame[219] == before


def test_benchmark_is_deterministic_and_raw_adjusted_policies_are_distinct():
    adjusted_first = run_case(1, 1_800, "latest", "ADJUSTED", False)
    adjusted_second = run_case(1, 1_800, "latest", "ADJUSTED", False)
    raw = run_case(1, 1_800, "latest", "RAW", False)
    assert adjusted_first.digest == adjusted_second.digest
    assert adjusted_first.digest != raw.digest


def _seed_batch(db, security_count: int = 3) -> list[Security]:
    start = date(2024, 1, 1)
    for index in range(220):
        db.add(
            TradingCalendar(
                exchange="NSE",
                trading_date=start + timedelta(days=index),
                is_trading_day=True,
                session_open=time(9, 15),
                session_close=time(15, 30),
            )
        )
    securities: list[Security] = []
    for security_number in range(security_count):
        security = Security(
            exchange="NSE",
            symbol=f"BATCH{security_number}",
            trading_symbol=f"BATCH{security_number}-EQ",
            company_name=f"Batch {security_number}",
            security_type="EQUITY",
            currency="INR",
            is_active=True,
        )
        db.add(security)
        db.flush()
        securities.append(security)
        for index in range(220):
            price = Decimal(100 + security_number + index)
            if security_number == 0 and index < 100:
                price *= 2
            db.add(
                DailyPrice(
                    security_id=security.id,
                    trading_date=start + timedelta(days=index),
                    open=price,
                    high=price + 1,
                    low=price - 1,
                    close=price,
                    volume=1_000 + security_number * 100 + index,
                    traded_value=price * (1_000 + security_number * 100 + index),
                    source="PHASE25",
                )
            )
        if security_number == 0:
            db.add(
                CorporateAction(
                    security_id=security.id,
                    action_type="STOCK_SPLIT",
                    ex_date=start + timedelta(days=100),
                    ratio_numerator=2,
                    ratio_denominator=1,
                    source="PHASE25",
                    available_at=datetime(2024, 1, 1, tzinfo=UTC),
                )
            )
    db.add(
        DataIngestionRun(
            dataset_code="phase25_fixture",
            dataset_version="v1",
            provider="PHASE25",
            status="SUCCESS",
            completed_at=datetime(2024, 12, 31, tzinfo=UTC),
            records_written=220 * security_count,
        )
    )
    db.commit()
    return securities


@pytest.mark.parametrize("adjustment_policy", ["RAW", "ADJUSTED"])
def test_batch_latest_matches_single_security_full_history_latest(db, adjustment_policy):
    securities = _seed_batch(db)
    as_of = datetime(2025, 1, 1, tzinfo=UTC)
    service = TechnicalFeatureService(db)
    batch = service.compute_latest_batch(securities, adjustment_policy=adjustment_policy, as_of=as_of)
    for security, batch_response in zip(securities, batch, strict=True):
        single = service.compute(
            security,
            start_date=None,
            end_date=None,
            adjustment_policy=adjustment_policy,
            as_of=as_of,
        )
        assert batch_response.items[0].values == single.items[-1].values
        assert batch_response.items[0].unavailable == single.items[-1].unavailable
        assert batch_response.dataset == single.dataset
        assert batch_response.quality_warnings == single.quality_warnings


def test_batch_latest_database_reads_are_bounded_per_chunk(db):
    securities = _seed_batch(db, security_count=5)
    select_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        TechnicalFeatureService(db).compute_latest_batch(
            securities,
            adjustment_policy="RAW",
            as_of=datetime(2025, 1, 1, tzinfo=UTC),
            batch_size=50,
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    assert select_count == 5


def test_batch_latest_respects_session_close_without_lookahead(db):
    security = _seed_batch(db, security_count=1)[0]
    last_day = date(2024, 1, 1) + timedelta(days=219)
    before_close = datetime.combine(last_day, time(15, 29, 59), tzinfo=TechnicalFeatureService(db)._available_at(security, last_day).tzinfo)
    response = TechnicalFeatureService(db).compute_latest_batch(
        [security],
        adjustment_policy="RAW",
        as_of=before_close,
    )[0]
    assert response.items[0].observation_date == last_day - timedelta(days=1)


def test_batch_latest_validates_contract_inputs(db):
    service = TechnicalFeatureService(db)
    with pytest.raises(ValueError, match="RAW or ADJUSTED"):
        service.compute_latest_batch([], adjustment_policy="INVALID", as_of=datetime.now(UTC))
    with pytest.raises(ValueError, match="batch_size"):
        service.compute_latest_batch([], adjustment_policy="RAW", as_of=datetime.now(UTC), batch_size=500)
    with pytest.raises(ValueError, match="timezone"):
        service.compute_latest_batch([], adjustment_policy="RAW", as_of=datetime.now(), batch_size=50)
