from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, select

from app.models import (
    CorporateAction,
    DailyPrice,
    DataIngestionRun,
    IndexMembership,
    MarketIndex,
    Security,
    TradingCalendar,
)
from app.scanner import ScannerService
from app.schemas.scanner import MarketScanRequest
from app.technical.definitions import CORE_TECHNICAL_SET, FEATURE_DEFINITIONS
from app.technical.service import TechnicalFeatureService

START = date(2024, 1, 1)
TRANSITION = START + timedelta(days=215)
SCAN_DAY = START + timedelta(days=210)
LATE_SCAN_DAY = START + timedelta(days=219)
IST = ZoneInfo("Asia/Kolkata")


def _security(symbol: str) -> Security:
    return Security(
        exchange="NSE",
        symbol=symbol,
        trading_symbol=f"{symbol}-EQ",
        company_name=f"{symbol.title()} Industries",
        security_type="EQUITY",
        currency="INR",
        is_active=True,
    )


def seed_scanner(db) -> tuple[MarketIndex, dict[str, Security]]:
    market_index = MarketIndex(
        name="Historical Demo 50",
        symbol="HIST50",
        provider="PHASE3",
        exchange="NSE",
    )
    securities = {symbol: _security(symbol) for symbol in ("ALPHA", "BETA", "GAMMA")}
    db.add_all([market_index, *securities.values()])
    db.flush()
    db.add_all(
        [
            IndexMembership(
                index_id=market_index.id,
                security_id=securities["ALPHA"].id,
                valid_from=START,
                valid_to=None,
                source="PHASE3",
            ),
            IndexMembership(
                index_id=market_index.id,
                security_id=securities["BETA"].id,
                valid_from=START,
                valid_to=TRANSITION - timedelta(days=1),
                source="PHASE3",
            ),
            IndexMembership(
                index_id=market_index.id,
                security_id=securities["GAMMA"].id,
                valid_from=TRANSITION,
                valid_to=None,
                source="PHASE3",
            ),
        ]
    )
    for index in range(225):
        trading_date = START + timedelta(days=index)
        db.add(
            TradingCalendar(
                exchange="NSE",
                trading_date=trading_date,
                is_trading_day=True,
                session_open=time(9, 15),
                session_close=time(15, 30),
            )
        )
        economic = Decimal("100") + Decimal(index) / Decimal("10")
        closes = {
            "ALPHA": economic * (Decimal("2") if index < 210 else Decimal("1")),
            "BETA": Decimal("400") - Decimal(index) / Decimal("2"),
            "GAMMA": Decimal("250"),
        }
        for symbol, close in closes.items():
            db.add(
                DailyPrice(
                    security_id=securities[symbol].id,
                    trading_date=trading_date,
                    open=close - Decimal("0.25"),
                    high=close + Decimal("1"),
                    low=close - Decimal("1"),
                    close=close,
                    volume=1_000_000 + index * 1_000 + len(symbol) * 100,
                    traded_value=close * (1_000_000 + index * 1_000 + len(symbol) * 100),
                    source="PHASE3",
                )
            )
    db.add(
        CorporateAction(
            security_id=securities["ALPHA"].id,
            action_type="STOCK_SPLIT",
            announcement_date=SCAN_DAY - timedelta(days=10),
            ex_date=SCAN_DAY,
            ratio_numerator=2,
            ratio_denominator=1,
            source="PHASE3",
            available_at=datetime.combine(SCAN_DAY - timedelta(days=10), time(23, 59, 59), tzinfo=IST),
        )
    )
    db.add(
        DataIngestionRun(
            dataset_code="phase3_scanner_fixture",
            dataset_version="2024.v1",
            provider="PHASE3",
            status="SUCCESS",
            completed_at=datetime(2024, 12, 31, tzinfo=UTC),
            records_written=675,
        )
    )
    db.commit()
    return market_index, securities


def scan_request(
    *,
    observation_date: date = SCAN_DAY,
    as_of: datetime | None = None,
    adjustment_policy: str = "ADJUSTED",
    filters: list[dict] | None = None,
    universe: str = "HIST50",
    feature_set_version: str = "1",
) -> MarketScanRequest:
    return MarketScanRequest(
        universe=universe,
        observation_date=observation_date,
        as_of=as_of or datetime.combine(observation_date, time(15, 30), tzinfo=IST),
        adjustment_policy=adjustment_policy,
        feature_set_version=feature_set_version,
        filters=filters or [{"feature": "RET_1D", "operator": ">", "value": "0"}],
    )


def test_scanner_metadata_uses_registry_and_exposes_universes(client, db):
    seed_scanner(db)
    response = client.get("/api/v1/scanner/metadata")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["features"]) == len(CORE_TECHNICAL_SET.feature_codes) == 36
    assert len(FEATURE_DEFINITIONS) == 38
    assert [feature["code"] for feature in payload["features"]] == list(
        CORE_TECHNICAL_SET.feature_codes
    )
    assert payload["feature_set"]["version"] == CORE_TECHNICAL_SET.version
    assert payload["latest_observation_date"] == (START + timedelta(days=224)).isoformat()
    assert payload["universes"][0]["symbol"] == "HIST50"
    boolean_feature = next(item for item in payload["features"] if item["value_type"] == "BOOLEAN")
    assert boolean_feature["supported_operators"] == ["="]


def test_scanner_applies_one_filter(db):
    seed_scanner(db)
    response = ScannerService(db).scan(scan_request())
    assert [item.symbol for item in response.results] == ["ALPHA"]
    assert response.matched_count == 1


def test_scanner_applies_multiple_conditions_with_and_semantics(db):
    seed_scanner(db)
    response = ScannerService(db).scan(
        scan_request(
            filters=[
                {"feature": "RET_1D", "operator": ">", "value": "0"},
                {"feature": "RSI_14", "operator": ">=", "value": "50"},
                {"feature": "ABOVE_SMA_20", "operator": "=", "value": True},
            ]
        )
    )
    assert response.logic == "AND"
    assert [item.symbol for item in response.results] == ["ALPHA"]


@pytest.mark.parametrize(
    ("feature", "operator", "value", "upper_value", "expected"),
    [
        ("RET_1D", ">", "0", None, ["ALPHA"]),
        ("RET_1D", ">=", "0", None, ["ALPHA"]),
        ("RET_1D", "<", "0", None, ["BETA"]),
        ("RET_1D", "<=", "0", None, ["BETA"]),
        ("ABOVE_SMA_20", "=", True, None, ["ALPHA"]),
        ("RET_1D", "between", "-0.01", "0", ["BETA"]),
    ],
)
def test_scanner_comparison_operators(db, feature, operator, value, upper_value, expected):
    seed_scanner(db)
    condition = {"feature": feature, "operator": operator, "value": value}
    if upper_value is not None:
        condition["upper_value"] = upper_value
    response = ScannerService(db).scan(scan_request(filters=[condition]))
    assert [item.symbol for item in response.results] == expected


def test_scanner_rejects_unknown_feature(client, db):
    seed_scanner(db)
    payload = scan_request(filters=[{"feature": "SECRET_ALPHA", "operator": ">", "value": "0"}]).model_dump(mode="json")
    response = client.post("/api/v1/scanner/scan", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown feature: SECRET_ALPHA"


def test_scanner_rejects_unknown_operator(client, db):
    seed_scanner(db)
    payload = scan_request().model_dump(mode="json")
    payload["filters"][0]["operator"] = "contains"
    response = client.post("/api/v1/scanner/scan", json=payload)
    assert response.status_code == 422


def test_scanner_rejects_malformed_between(client, db):
    seed_scanner(db)
    payload = scan_request().model_dump(mode="json")
    payload["filters"] = [{"feature": "RSI_14", "operator": "between", "value": "30"}]
    response = client.post("/api/v1/scanner/scan", json=payload)
    assert response.status_code == 422
    assert "between requires upper_value" in str(response.json())


def test_scanner_rejects_incompatible_boolean_operator(client, db):
    seed_scanner(db)
    payload = scan_request(filters=[{"feature": "ABOVE_SMA_20", "operator": ">", "value": True}]).model_dump(mode="json")
    response = client.post("/api/v1/scanner/scan", json=payload)
    assert response.status_code == 422
    assert "supports only the = operator" in response.json()["detail"]


def test_scanner_resolves_historical_membership_without_current_state_leakage(db):
    seed_scanner(db)
    before = ScannerService(db).scan(
        scan_request(filters=[{"feature": "TRUE_RANGE", "operator": ">=", "value": "0"}])
    )
    after = ScannerService(db).scan(
        scan_request(
            observation_date=LATE_SCAN_DAY,
            filters=[{"feature": "TRUE_RANGE", "operator": ">=", "value": "0"}],
        )
    )
    assert [item.symbol for item in before.results] == ["ALPHA", "BETA"]
    assert [item.symbol for item in after.results] == ["ALPHA", "GAMMA"]


def test_scanner_enforces_exchange_close_availability(db):
    seed_scanner(db)
    response = ScannerService(db).scan(
        scan_request(as_of=datetime.combine(SCAN_DAY, time(15, 29, 59), tzinfo=IST))
    )
    assert response.results == []
    assert response.unavailable_security_count == 2


def test_future_action_does_not_change_historical_scan_or_fingerprint(db):
    _, securities = seed_scanner(db)
    service = ScannerService(db)
    before = service.scan(scan_request())
    db.add(
        CorporateAction(
            security_id=securities["ALPHA"].id,
            action_type="BONUS",
            ex_date=SCAN_DAY + timedelta(days=1),
            ratio_numerator=1,
            ratio_denominator=1,
            source="FUTURE",
            available_at=datetime.combine(SCAN_DAY, time(15), tzinfo=IST),
        )
    )
    db.commit()
    after = service.scan(scan_request())
    assert after.results == before.results
    assert after.scan_fingerprint == before.scan_fingerprint


def test_future_action_revision_does_not_leak_into_earlier_as_of(db):
    _, securities = seed_scanner(db)
    service = ScannerService(db)
    request = scan_request()
    before = service.scan(request)
    original = db.scalar(
        select(CorporateAction).where(
            CorporateAction.security_id == securities["ALPHA"].id,
            CorporateAction.action_type == "STOCK_SPLIT",
        )
    )
    assert original is not None
    revision_available_at = datetime.combine(
        SCAN_DAY + timedelta(days=1),
        time(10),
        tzinfo=IST,
    )
    db.add(
        CorporateAction(
            security_id=securities["ALPHA"].id,
            action_type="STOCK_SPLIT",
            announcement_date=original.announcement_date,
            ex_date=original.ex_date,
            ratio_numerator=4,
            ratio_denominator=1,
            source="PHASE3_CORRECTION",
            available_at=revision_available_at,
            supersedes_action_id=original.id,
        )
    )
    db.commit()

    repeated_at_original_clock = service.scan(request)
    after_correction = service.scan(scan_request(as_of=revision_available_at))

    assert repeated_at_original_clock.results == before.results
    assert repeated_at_original_clock.scan_fingerprint == before.scan_fingerprint
    assert after_correction.scan_fingerprint != before.scan_fingerprint


def test_future_price_change_does_not_change_historical_scan_or_fingerprint(db):
    _, securities = seed_scanner(db)
    service = ScannerService(db)
    before = service.scan(scan_request())
    future_price = db.scalar(
        select(DailyPrice).where(
            DailyPrice.security_id == securities["ALPHA"].id,
            DailyPrice.trading_date == SCAN_DAY + timedelta(days=1),
        )
    )
    assert future_price is not None
    future_price.close = future_price.high
    db.commit()
    after = service.scan(scan_request())
    assert after.results == before.results
    assert after.scan_fingerprint == before.scan_fingerprint


def test_scanner_adjustment_policy_changes_split_day_result(db):
    seed_scanner(db)
    adjusted = ScannerService(db).scan(scan_request(adjustment_policy="ADJUSTED"))
    raw = ScannerService(db).scan(scan_request(adjustment_policy="RAW"))
    assert [item.symbol for item in adjusted.results] == ["ALPHA"]
    assert raw.results == []
    assert adjusted.scan_fingerprint != raw.scan_fingerprint


def test_scanner_retains_feature_and_dataset_versions(db):
    seed_scanner(db)
    response = ScannerService(db).scan(scan_request())
    result = response.results[0]
    assert response.feature_set.version == "1"
    assert response.feature_versions["RET_1D"] == "1"
    assert result.feature_versions == {"RET_1D": "1"}
    assert result.dataset.dataset_version == "2024.v1"
    assert result.input_fingerprint == result.dataset.fingerprint
    assert result.as_of == response.as_of


def test_scanner_results_are_deterministically_sorted(db):
    seed_scanner(db)
    response = ScannerService(db).scan(
        scan_request(filters=[{"feature": "TRUE_RANGE", "operator": ">=", "value": "0"}])
    )
    assert response.result_order == "SYMBOL_ASC"
    assert [item.symbol for item in response.results] == ["ALPHA", "BETA"]


def test_scanner_returns_typed_empty_result(db):
    seed_scanner(db)
    response = ScannerService(db).scan(
        scan_request(filters=[{"feature": "RSI_14", "operator": ">", "value": "100"}])
    )
    assert response.matched_count == 0
    assert response.results == []
    assert response.universe_member_count == 2


def test_scanner_counts_insufficient_history_as_unavailable(db):
    seed_scanner(db)
    early = START + timedelta(days=5)
    response = ScannerService(db).scan(
        scan_request(
            observation_date=early,
            filters=[{"feature": "SMA_200", "operator": ">", "value": "0"}],
        )
    )
    assert response.results == []
    assert response.unavailable_security_count == 2


def test_scanner_matched_values_equal_authoritative_single_security_result(db):
    _, securities = seed_scanner(db)
    scan = ScannerService(db).scan(scan_request())
    single = TechnicalFeatureService(db).compute(
        securities["ALPHA"],
        start_date=SCAN_DAY,
        end_date=SCAN_DAY,
        adjustment_policy="ADJUSTED",
        as_of=datetime.combine(SCAN_DAY, time(15, 30), tzinfo=IST),
    )
    assert scan.results[0].matched_values["RET_1D"] == single.items[0].values["RET_1D"]


def test_scanner_repeated_request_has_stable_reproducibility_fingerprint(db):
    seed_scanner(db)
    request = scan_request()
    first = ScannerService(db).scan(request)
    second = ScannerService(db).scan(request)
    assert first.scan_fingerprint == second.scan_fingerprint
    assert first.results[0].input_fingerprint == second.results[0].input_fingerprint


def test_scanner_handles_universe_with_no_historical_members(db):
    seed_scanner(db)
    empty = MarketIndex(name="Empty Historic", symbol="EMPTY", provider="PHASE3", exchange="NSE")
    db.add(empty)
    db.commit()
    response = ScannerService(db).scan(scan_request(universe="EMPTY"))
    assert response.universe_member_count == 0
    assert response.results == []


def test_scanner_requires_an_exact_observation_row(db):
    seed_scanner(db)
    missing_day = START + timedelta(days=225)
    response = ScannerService(db).scan(
        scan_request(observation_date=missing_day, as_of=datetime.combine(missing_day, time(15, 30), tzinfo=IST))
    )
    assert response.results == []
    assert response.unavailable_security_count == 2


def test_scanner_rejects_naive_as_of(client, db):
    seed_scanner(db)
    payload = scan_request().model_dump(mode="json")
    payload["as_of"] = f"{SCAN_DAY.isoformat()}T15:30:00"
    response = client.post("/api/v1/scanner/scan", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == "as_of must include a timezone offset"


def test_scanner_rejects_observation_after_as_of(client, db):
    seed_scanner(db)
    payload = scan_request().model_dump(mode="json")
    payload["as_of"] = datetime.combine(SCAN_DAY - timedelta(days=1), time(23, 59), tzinfo=IST).isoformat()
    response = client.post("/api/v1/scanner/scan", json=payload)
    assert response.status_code == 422
    assert "cannot be after" in response.json()["detail"]


def test_scanner_returns_not_found_for_unknown_universe_and_version(client, db):
    seed_scanner(db)
    unknown_universe = client.post(
        "/api/v1/scanner/scan",
        json=scan_request(universe="UNKNOWN").model_dump(mode="json"),
    )
    unknown_version = client.post(
        "/api/v1/scanner/scan",
        json=scan_request(feature_set_version="999").model_dump(mode="json"),
    )
    assert unknown_universe.status_code == 404
    assert unknown_version.status_code == 404


def test_scanner_query_count_is_bounded_for_one_batch(db):
    seed_scanner(db)
    select_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        ScannerService(db).scan(scan_request())
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    assert select_count == 7
