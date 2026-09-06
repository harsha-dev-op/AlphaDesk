from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, select

from app.models import CorporateAction, DailyPrice, DataIngestionRun, IndexMembership, MarketIndex, Security, TradingCalendar
from app.schemas.strategies import StrategyEvaluationRequest
from app.strategies.definitions import INITIAL_STRATEGIES
from app.strategies.registry import STRATEGY_DEFINITIONS, build_strategy_registry, find_strategy
from app.strategies.service import StrategyService, StrategyValidationError, normalize_parameters, strategy_fingerprint
from app.technical.calculators import PricePoint, calculate_feature_frame, calculate_latest_feature_snapshot
from app.technical.definitions import CORE_TECHNICAL_SET, FEATURE_DEFINITIONS, STRATEGY_TECHNICAL_SET
from app.technical.service import TechnicalFeatureService

START = date(2024, 1, 1)
OBSERVATION = START + timedelta(days=219)
IST = ZoneInfo("Asia/Kolkata")


def _point(index: int, *, high: Decimal | None = None, close: Decimal | None = None) -> PricePoint:
    resolved_close = close if close is not None else Decimal(100 + index)
    return PricePoint(
        trading_date=START + timedelta(days=index),
        open=resolved_close - Decimal("0.5"),
        high=high if high is not None else resolved_close + Decimal(1),
        low=resolved_close - Decimal(1),
        close=resolved_close,
        volume=1_000 + index,
        traded_value=resolved_close * (1_000 + index),
        source="STRATEGY_TEST",
    )


def _security(symbol: str) -> Security:
    return Security(
        exchange="NSE",
        symbol=symbol,
        trading_symbol=f"{symbol}-EQ",
        company_name=f"{symbol.title()} Research",
        security_type="EQUITY",
        currency="INR",
        is_active=True,
    )


def seed_strategy_data(db) -> tuple[MarketIndex, dict[str, Security]]:
    universe = MarketIndex(name="Strategy Historical", symbol="STRAT100", provider="PHASE4", exchange="NSE")
    securities = {symbol: _security(symbol) for symbol in ("ALPHA", "PULLBACK", "FUTURE")}
    db.add_all([universe, *securities.values()])
    db.flush()
    db.add_all(
        [
            IndexMembership(index_id=universe.id, security_id=securities["ALPHA"].id, valid_from=START, source="PHASE4"),
            IndexMembership(index_id=universe.id, security_id=securities["PULLBACK"].id, valid_from=START, valid_to=OBSERVATION, source="PHASE4"),
            IndexMembership(index_id=universe.id, security_id=securities["FUTURE"].id, valid_from=OBSERVATION + timedelta(days=1), source="PHASE4"),
        ]
    )
    for index in range(225):
        trading_date = START + timedelta(days=index)
        db.add(TradingCalendar(exchange="NSE", trading_date=trading_date, is_trading_day=True, session_close=time(15, 30)))
        alpha_close = Decimal("100") + Decimal(index) * Decimal("0.30")
        if index == 219:
            alpha_close += Decimal("3")
        pullback_close = Decimal("100") + Decimal(index) * Decimal("0.40")
        if index >= 212:
            pullback_close = Decimal("190") - Decimal(index - 212) * Decimal("2.5")
        closes = {"ALPHA": alpha_close, "PULLBACK": pullback_close, "FUTURE": Decimal("125")}
        for symbol, close in closes.items():
            volume = 2_000_000 if symbol == "ALPHA" and index == 219 else 1_000_000 + index * 100
            high = close + (Decimal("0.2") if symbol == "ALPHA" and index == 219 else Decimal("1"))
            db.add(
                DailyPrice(
                    security_id=securities[symbol].id,
                    trading_date=trading_date,
                    open=close - Decimal("0.5"),
                    high=high,
                    low=close - Decimal("1"),
                    close=close,
                    volume=volume,
                    traded_value=close * volume,
                    source="PHASE4",
                )
            )
    db.add(DataIngestionRun(dataset_code="strategy_fixture", dataset_version="2024.v1", provider="PHASE4", status="SUCCESS", completed_at=datetime(2025, 1, 1, tzinfo=UTC), records_written=675))
    db.commit()
    return universe, securities


def evaluation_request(**overrides) -> StrategyEvaluationRequest:
    payload = {
        "strategy_code": "MOMENTUM_TREND",
        "strategy_version": "1",
        "universe": "STRAT100",
        "observation_date": OBSERVATION,
        "as_of": datetime.combine(OBSERVATION, time(15, 30), tzinfo=IST),
        "adjustment_policy": "ADJUSTED",
        "parameter_overrides": {},
    }
    payload.update(overrides)
    return StrategyEvaluationRequest(**payload)


def test_core_feature_set_remains_immutable_and_strategy_set_adds_only_two_features():
    assert len(CORE_TECHNICAL_SET.feature_codes) == 36
    assert len(STRATEGY_TECHNICAL_SET.feature_codes) == 38
    assert STRATEGY_TECHNICAL_SET.feature_codes[-2:] == ("PRIOR_HIGH_20", "BREAKOUT_PCT_20")
    assert "EMA_200" not in FEATURE_DEFINITIONS


def test_prior_high_warmup_and_breakout_formula_exclude_current_session():
    points = [_point(index) for index in range(21)]
    points[-1] = _point(20, high=Decimal("999"), close=Decimal("121"))
    frame = calculate_feature_frame(points)
    assert frame[19]["PRIOR_HIGH_20"] is None
    assert frame[19]["BREAKOUT_PCT_20"] is None
    assert frame[20]["PRIOR_HIGH_20"] == Decimal("120")
    assert float(frame[20]["BREAKOUT_PCT_20"]) == pytest.approx(121 / 120 - 1)


def test_price_structure_latest_and_full_paths_are_identical_and_future_invariant():
    points = [_point(index) for index in range(30)]
    full = calculate_feature_frame(points)[-1]
    latest = calculate_latest_feature_snapshot(points)
    assert latest["PRIOR_HIGH_20"] == full["PRIOR_HIGH_20"]
    assert latest["BREAKOUT_PCT_20"] == full["BREAKOUT_PCT_20"]
    assert calculate_feature_frame(points + [_point(30, high=Decimal("999"))])[-2] == full


def test_price_structure_zero_prior_high_yields_null_breakout():
    points = [_point(index, high=Decimal(0), close=Decimal(0)) for index in range(21)]
    latest = calculate_latest_feature_snapshot(points)
    assert latest["PRIOR_HIGH_20"] == 0
    assert latest["BREAKOUT_PCT_20"] is None


def test_registry_is_unique_immutable_and_feature_validated():
    assert len(STRATEGY_DEFINITIONS) == 3
    assert len(set(STRATEGY_DEFINITIONS)) == 3
    with pytest.raises(TypeError):
        STRATEGY_DEFINITIONS[("NEW", "1")] = INITIAL_STRATEGIES[0]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        INITIAL_STRATEGIES[0].display_name = "Changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="Duplicate strategy"):
        build_strategy_registry((INITIAL_STRATEGIES[0], INITIAL_STRATEGIES[0]))
    invalid = replace(INITIAL_STRATEGIES[0], rules=(replace(INITIAL_STRATEGIES[0].rules[0], feature_code="UNKNOWN"),))
    with pytest.raises(ValueError, match="Unknown required feature"):
        build_strategy_registry((invalid,))


def test_parameter_defaults_validate_and_normalize_deterministically():
    definition = find_strategy("MOMENTUM_TREND", "1")
    assert definition is not None
    defaults = normalize_parameters(definition, {})
    equivalent = normalize_parameters(definition, {"min_momentum_3m": Decimal("0.1000")})
    assert defaults == equivalent
    assert strategy_fingerprint(definition, defaults, "ADJUSTED") == strategy_fingerprint(definition, equivalent, "ADJUSTED")


def test_parameter_override_validation_and_fingerprint_sensitivity():
    definition = find_strategy("MOMENTUM_TREND", "1")
    assert definition is not None
    defaults = normalize_parameters(definition, {})
    overridden = normalize_parameters(definition, {"min_momentum_3m": Decimal("0.11")})
    assert overridden["min_momentum_3m"] == Decimal("0.11")
    assert strategy_fingerprint(definition, defaults, "ADJUSTED") != strategy_fingerprint(definition, overridden, "ADJUSTED")
    with pytest.raises(StrategyValidationError, match="Unknown strategy parameter"):
        normalize_parameters(definition, {"mystery": Decimal(1)})
    with pytest.raises(StrategyValidationError, match="decimal"):
        normalize_parameters(definition, {"min_momentum_3m": True})
    with pytest.raises(StrategyValidationError, match="at most"):
        normalize_parameters(definition, {"min_momentum_3m": Decimal("6")})


def test_strategy_service_returns_all_members_in_symbol_order_with_condition_details(db):
    seed_strategy_data(db)
    response = StrategyService(db).evaluate(evaluation_request())
    assert [item.symbol for item in response.results] == ["ALPHA", "PULLBACK"]
    assert response.result_order == "SYMBOL_ASC"
    assert response.evaluated_security_count == response.universe_member_count == 2
    assert all(item.total_condition_count == 6 for item in response.results)
    assert response.results[0].feature_set_code == STRATEGY_TECHNICAL_SET.code
    assert response.results[0].strategy_fingerprint
    assert response.results[0].result_fingerprint


def test_momentum_matching_nonmatching_boundary_and_insufficient_history(db):
    seed_strategy_data(db)
    service = StrategyService(db)
    baseline = service.evaluate(evaluation_request())
    alpha = next(item for item in baseline.results if item.symbol == "ALPHA")
    assert alpha.matched is True
    tightened = service.evaluate(evaluation_request(parameter_overrides={"min_momentum_3m": Decimal("0.50")}))
    assert next(item for item in tightened.results if item.symbol == "ALPHA").matched is False
    momentum = alpha.required_feature_values["MOM_3M_63D"]
    boundary = service.evaluate(evaluation_request(parameter_overrides={"min_momentum_3m": momentum}))
    assert next(item for item in boundary.results if item.symbol == "ALPHA").conditions[0].passed is True
    early = START + timedelta(days=20)
    insufficient = service.evaluate(evaluation_request(observation_date=early, as_of=datetime.combine(early, time(15, 30), tzinfo=IST)))
    assert all(not item.matched for item in insufficient.results)
    assert all(any("INSUFFICIENT_FEATURE_HISTORY" in warning for warning in item.warnings) for item in insufficient.results)


def test_breakout_rules_cover_exact_prior_high_volume_trend_and_boundary(db):
    seed_strategy_data(db)
    service = StrategyService(db)
    response = service.evaluate(evaluation_request(strategy_code="BREAKOUT_20D"))
    alpha = next(item for item in response.results if item.symbol == "ALPHA")
    assert alpha.matched is True
    by_feature = {condition.feature_code: condition for condition in alpha.conditions}
    assert by_feature["BREAKOUT_PCT_20"].passed is True
    assert by_feature["VOLUME_RATIO_20"].passed is True
    assert by_feature["SMA_20_ABOVE_50"].passed is True
    exact = alpha.required_feature_values["BREAKOUT_PCT_20"]
    boundary = service.evaluate(evaluation_request(strategy_code="BREAKOUT_20D", parameter_overrides={"min_breakout_pct_20": exact}))
    assert next(item for item in boundary.results if item.symbol == "ALPHA").conditions[0].passed is True
    no_volume = service.evaluate(evaluation_request(strategy_code="BREAKOUT_20D", parameter_overrides={"min_volume_ratio_20": Decimal("3")}))
    assert next(item for item in no_volume.results if item.symbol == "ALPHA").matched is False


def test_breakout_no_breakout_and_trend_confirmation_fail_explicitly(db):
    seed_strategy_data(db)
    service = StrategyService(db)
    baseline = service.evaluate(evaluation_request(strategy_code="BREAKOUT_20D"))
    pullback = next(item for item in baseline.results if item.symbol == "PULLBACK")
    assert next(condition for condition in pullback.conditions if condition.feature_code == "BREAKOUT_PCT_20").passed is False
    trend_mismatch = service.evaluate(
        evaluation_request(
            strategy_code="BREAKOUT_20D",
            parameter_overrides={"require_sma_20_above_50": False},
        )
    )
    alpha = next(item for item in trend_mismatch.results if item.symbol == "ALPHA")
    assert next(condition for condition in alpha.conditions if condition.feature_code == "SMA_20_ABOVE_50").passed is False
    assert alpha.matched is False


def test_mean_reversion_exposes_each_guard_as_a_condition(db):
    seed_strategy_data(db)
    response = StrategyService(db).evaluate(evaluation_request(strategy_code="MEAN_REVERSION_PULLBACK"))
    pullback = next(item for item in response.results if item.symbol == "PULLBACK")
    assert pullback.matched is True
    assert {condition.feature_code for condition in pullback.conditions} == {
        "RSI_14", "DISTANCE_SMA_20", "RET_5D", "ABOVE_SMA_200", "SMA_50_ABOVE_200", "ATR_PCT_14"
    }
    assert next(condition for condition in pullback.conditions if condition.feature_code == "ATR_PCT_14").operator == "<="


@pytest.mark.parametrize(
    ("override", "failed_feature"),
    [
        ({"max_rsi_14": Decimal("0")}, "RSI_14"),
        ({"max_distance_sma_20": Decimal("-0.50")}, "DISTANCE_SMA_20"),
        ({"require_above_sma_200": False}, "ABOVE_SMA_200"),
        ({"max_atr_pct_14": Decimal("0")}, "ATR_PCT_14"),
    ],
)
def test_mean_reversion_individual_guards_can_fail(db, override, failed_feature):
    seed_strategy_data(db)
    response = StrategyService(db).evaluate(
        evaluation_request(
            strategy_code="MEAN_REVERSION_PULLBACK",
            parameter_overrides=override,
        )
    )
    pullback = next(item for item in response.results if item.symbol == "PULLBACK")
    assert next(condition for condition in pullback.conditions if condition.feature_code == failed_feature).passed is False
    assert pullback.matched is False


def test_preclose_and_timezone_boundary_exclude_observation_row(db):
    seed_strategy_data(db)
    before = StrategyService(db).evaluate(evaluation_request(as_of=datetime.combine(OBSERVATION, time(15, 29, 59), tzinfo=IST)))
    assert before.unavailable_security_count == 2
    assert all(item.available_at is None for item in before.results)
    utc_close = datetime.combine(OBSERVATION, time(10), tzinfo=UTC)
    at_close = StrategyService(db).evaluate(evaluation_request(as_of=utc_close))
    assert all(item.available_at is not None for item in at_close.results)


def test_historical_membership_and_empty_universe(db):
    universe, _ = seed_strategy_data(db)
    response = StrategyService(db).evaluate(evaluation_request(observation_date=OBSERVATION + timedelta(days=1), as_of=datetime.combine(OBSERVATION + timedelta(days=1), time(15, 30), tzinfo=IST)))
    assert [item.symbol for item in response.results] == ["ALPHA", "FUTURE"]
    empty = MarketIndex(name="Empty", symbol="EMPTY4", provider="PHASE4", exchange="NSE")
    db.add(empty)
    db.commit()
    result = StrategyService(db).evaluate(evaluation_request(universe="EMPTY4"))
    assert result.results == []
    assert result.universe_member_count == 0


def test_future_price_and_future_action_do_not_change_historical_fingerprints(db):
    _, securities = seed_strategy_data(db)
    service = StrategyService(db)
    request = evaluation_request()
    before = service.evaluate(request)
    future_price = db.scalar(select(DailyPrice).where(DailyPrice.security_id == securities["ALPHA"].id, DailyPrice.trading_date == OBSERVATION + timedelta(days=1)))
    assert future_price is not None
    future_price.close = future_price.high
    db.add(CorporateAction(security_id=securities["ALPHA"].id, action_type="BONUS", ex_date=OBSERVATION + timedelta(days=1), ratio_numerator=1, ratio_denominator=1, source="FUTURE", available_at=request.as_of - timedelta(hours=1)))
    db.commit()
    after = service.evaluate(request)
    assert after.evaluation_fingerprint == before.evaluation_fingerprint
    assert [item.result_fingerprint for item in after.results] == [item.result_fingerprint for item in before.results]


def test_historical_input_change_changes_dataset_and_result_fingerprints(db):
    _, securities = seed_strategy_data(db)
    service = StrategyService(db)
    before = service.evaluate(evaluation_request())
    historical = db.scalar(select(DailyPrice).where(DailyPrice.security_id == securities["ALPHA"].id, DailyPrice.trading_date == OBSERVATION - timedelta(days=1)))
    assert historical is not None
    historical.close += Decimal("0.1")
    db.commit()
    after = service.evaluate(evaluation_request())
    assert after.results[0].input_fingerprint != before.results[0].input_fingerprint
    assert after.results[0].result_fingerprint != before.results[0].result_fingerprint


def test_strategy_version_participates_in_definition_fingerprint():
    definition = find_strategy("BREAKOUT_20D", "1")
    assert definition is not None
    parameters = normalize_parameters(definition, {})
    changed_version = replace(definition, strategy_version="2")
    assert strategy_fingerprint(definition, parameters, "ADJUSTED") != strategy_fingerprint(changed_version, parameters, "ADJUSTED")


def test_future_corporate_action_revision_does_not_change_earlier_evaluation(db):
    _, securities = seed_strategy_data(db)
    original = CorporateAction(security_id=securities["ALPHA"].id, action_type="STOCK_SPLIT", ex_date=OBSERVATION, ratio_numerator=2, ratio_denominator=1, source="ORIGINAL", available_at=datetime.combine(OBSERVATION - timedelta(days=2), time(10), tzinfo=IST))
    db.add(original)
    db.commit()
    service = StrategyService(db)
    request = evaluation_request()
    before = service.evaluate(request)
    db.add(CorporateAction(security_id=securities["ALPHA"].id, action_type="STOCK_SPLIT", ex_date=OBSERVATION, ratio_numerator=4, ratio_denominator=1, source="REVISION", available_at=request.as_of + timedelta(days=1), supersedes_action_id=original.id))
    db.commit()
    after = service.evaluate(request)
    assert after.evaluation_fingerprint == before.evaluation_fingerprint


def test_raw_and_adjusted_paths_are_explicit_and_can_produce_distinct_inputs(db):
    _, securities = seed_strategy_data(db)
    db.add(CorporateAction(security_id=securities["ALPHA"].id, action_type="STOCK_SPLIT", ex_date=OBSERVATION, ratio_numerator=2, ratio_denominator=1, source="KNOWN", available_at=datetime.combine(OBSERVATION - timedelta(days=2), time(10), tzinfo=IST)))
    db.commit()
    service = StrategyService(db)
    raw = service.evaluate(evaluation_request(adjustment_policy="RAW"))
    adjusted = service.evaluate(evaluation_request(adjustment_policy="ADJUSTED"))
    assert raw.adjustment_policy == "RAW"
    assert adjusted.adjustment_policy == "ADJUSTED"
    assert raw.evaluation_fingerprint != adjusted.evaluation_fingerprint
    assert raw.results[0].input_fingerprint != adjusted.results[0].input_fingerprint


def test_strategy_catalog_and_evaluation_api_errors(db, client):
    seed_strategy_data(db)
    catalog = client.get("/api/v1/strategies/catalog")
    assert catalog.status_code == 200
    assert [item["strategy_code"] for item in catalog.json()["strategies"]] == ["MOMENTUM_TREND", "BREAKOUT_20D", "MEAN_REVERSION_PULLBACK"]
    assert "not investment recommendations" in catalog.json()["research_disclaimer"]
    payload = evaluation_request().model_dump(mode="json")
    response = client.post("/api/v1/strategies/evaluate", json=payload)
    assert response.status_code == 200
    assert response.json()["results"][0]["symbol"] == "ALPHA"
    payload["strategy_code"] = "UNKNOWN"
    assert client.post("/api/v1/strategies/evaluate", json=payload).status_code == 404
    payload["strategy_code"] = "MOMENTUM_TREND"
    payload["strategy_version"] = "99"
    assert client.post("/api/v1/strategies/evaluate", json=payload).status_code == 404
    payload["strategy_version"] = "1"
    payload["parameter_overrides"] = {"min_momentum_3m": "99"}
    assert client.post("/api/v1/strategies/evaluate", json=payload).status_code == 422


def test_strategy_api_raw_adjusted_and_historical_membership(db, client):
    seed_strategy_data(db)
    payload = evaluation_request(adjustment_policy="RAW").model_dump(mode="json")
    raw = client.post("/api/v1/strategies/evaluate", json=payload)
    assert raw.status_code == 200
    assert raw.json()["adjustment_policy"] == "RAW"
    payload["adjustment_policy"] = "ADJUSTED"
    adjusted = client.post("/api/v1/strategies/evaluate", json=payload)
    assert adjusted.status_code == 200
    assert adjusted.json()["adjustment_policy"] == "ADJUSTED"
    later = OBSERVATION + timedelta(days=1)
    payload["observation_date"] = later.isoformat()
    payload["as_of"] = datetime.combine(later, time(15, 30), tzinfo=IST).isoformat()
    historical = client.post("/api/v1/strategies/evaluate", json=payload)
    assert [item["symbol"] for item in historical.json()["results"]] == ["ALPHA", "FUTURE"]


def test_strategy_api_rejects_naive_as_of_and_future_observation(db, client):
    seed_strategy_data(db)
    payload = evaluation_request().model_dump(mode="json")
    payload["as_of"] = f"{OBSERVATION.isoformat()}T15:30:00"
    response = client.post("/api/v1/strategies/evaluate", json=payload)
    assert response.status_code == 422
    payload["as_of"] = datetime.combine(OBSERVATION - timedelta(days=1), time(15, 30), tzinfo=IST).isoformat()
    response = client.post("/api/v1/strategies/evaluate", json=payload)
    assert response.status_code == 422


def test_strategy_feature_set_is_available_through_authoritative_feature_api(db):
    _, securities = seed_strategy_data(db)
    response = TechnicalFeatureService(db).compute(securities["ALPHA"], start_date=OBSERVATION, end_date=OBSERVATION, adjustment_policy="RAW", as_of=datetime.combine(OBSERVATION, time(15, 30), tzinfo=IST), feature_set=STRATEGY_TECHNICAL_SET)
    assert response.feature_set.code == STRATEGY_TECHNICAL_SET.code
    assert set(response.items[0].values) == set(STRATEGY_TECHNICAL_SET.feature_codes)
    assert response.items[0].values["PRIOR_HIGH_20"] is not None


def test_strategy_query_count_is_bounded_for_one_batch(db):
    seed_strategy_data(db)
    engine = db.get_bind()
    select_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal select_count
        if statement.lstrip().lower().startswith("select"):
            select_count += 1

    event.listen(engine, "after_cursor_execute", count_selects)
    try:
        StrategyService(db).evaluate(evaluation_request())
    finally:
        event.remove(engine, "after_cursor_execute", count_selects)
    assert select_count == 7
