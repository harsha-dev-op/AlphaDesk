from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event

from app.backtests.costs import IndiaCashDeliveryCostCalculator
from app.backtests.definitions import INDIA_NSE_CASH_DELIVERY_2026_09_V1, NEXT_OPEN_FIXED_HOLD_V1
from app.backtests.fingerprints import fingerprint
from app.backtests.simulator import SetupEvent
from app.models import CorporateAction, DailyPrice, DataIngestionRun, IndexMembership, MarketIndex, Security, TradingCalendar
from app.portfolio.analytics import calculate_portfolio_metrics, calculate_segment_metrics, drawdown_details
from app.portfolio.definitions import LONG_ONLY_EQUAL_SLOT_PORTFOLIO_V1
from app.portfolio.registry import PORTFOLIO_POLICIES, build_portfolio_policy_registry
from app.portfolio.service import PortfolioService, policy_metadata
from app.portfolio.simulator import PortfolioSimulationSettings, PortfolioSimulator
from app.repositories.securities import PriceRecord
from app.schemas.portfolio import DailyPortfolioSnapshot, PortfolioRunRequest
from app.technical.definitions import CORE_TECHNICAL_SET, FEATURE_DEFINITIONS
from app.technical.series import HistoricalSecuritySeries

IST = ZoneInfo("Asia/Kolkata")


def _days(count: int, start: date = date(2024, 1, 2)) -> list[date]:
    output: list[date] = []
    current = start
    while len(output) < count:
        if current.weekday() < 5:
            output.append(current)
        current += timedelta(days=1)
    return output


def _security(symbol: str) -> Security:
    return Security(
        id=uuid.uuid4(),
        exchange="NSE",
        symbol=symbol,
        trading_symbol=f"{symbol}-EQ",
        company_name=f"{symbol} Research Ltd",
    )


def _series(
    security: Security,
    days: list[date],
    rows: dict[int, tuple[str, str, str, str]],
    actions: tuple[CorporateAction, ...] = (),
) -> HistoricalSecuritySeries:
    prices = tuple(
        PriceRecord(
            security_id=security.id,
            trading_date=days[index],
            open=Decimal(values[0]),
            high=Decimal(values[1]),
            low=Decimal(values[2]),
            close=Decimal(values[3]),
            volume=1_000_000,
            traded_value=None,
            source="PORTFOLIO_TEST",
        )
        for index, values in sorted(rows.items())
    )
    return HistoricalSecuritySeries(
        security=security,
        prices=prices,
        action_history=actions,
        observations={},
        dataset_fingerprint=fingerprint(symbol_payload(security.symbol, rows)),
        quality_warnings=(),
    )


def symbol_payload(symbol: str, rows: object) -> dict[str, object]:
    return {"symbol": symbol, "rows": rows}


def _setup(security: Security, signal_day: date, suffix: str = "") -> SetupEvent:
    signal_fingerprint = fingerprint({"symbol": security.symbol, "day": signal_day, "suffix": suffix})
    return SetupEvent(
        security_id=security.id,
        symbol=security.symbol,
        company_name=security.company_name,
        signal_date=signal_day,
        signal_decision_at=datetime.combine(signal_day, time(15, 30), tzinfo=IST),
        strategy_code="MOMENTUM_TREND",
        strategy_version="1",
        strategy_fingerprint="strategy",
        signal_result_fingerprint=signal_fingerprint,
    )


def _simulate(
    symbols: dict[str, dict[int, tuple[str, str, str, str]]],
    setup_specs: list[tuple[str, int, str]],
    *,
    holding: int = 2,
    capital: str = "1000",
    max_positions: int = 2,
    max_weight: str = "1",
    max_exposure: str = "1",
    reserve: str = "0",
    stop: str | None = None,
    target: str | None = None,
    actions: dict[str, tuple[CorporateAction, ...]] | None = None,
    brokerage: str = "0",
    dp: str = "0",
):
    days = _days(8)
    securities = {symbol: _security(symbol) for symbol in symbols}
    series = {
        security.id: _series(security, days, symbols[symbol], (actions or {}).get(symbol, ()))
        for symbol, security in securities.items()
    }
    setups = [_setup(securities[symbol], days[signal_index], suffix) for symbol, signal_index, suffix in setup_specs]
    calculator = IndiaCashDeliveryCostCalculator(
        INDIA_NSE_CASH_DELIVERY_2026_09_V1,
        brokerage_per_order_inr=Decimal(brokerage),
        brokerage_rate=Decimal(0),
        dp_charge_per_scrip_sell_day_inr=Decimal(dp),
    )
    result = PortfolioSimulator().simulate(
        setups,
        series_by_security=series,
        sessions=[
            TradingCalendar(
                exchange="NSE",
                trading_date=day,
                is_trading_day=True,
                session_open=time(9, 15),
                session_close=time(15, 30),
            )
            for day in days
        ],
        profile=NEXT_OPEN_FIXED_HOLD_V1,
        cost_definition=INDIA_NSE_CASH_DELIVERY_2026_09_V1,
        policy=LONG_ONLY_EQUAL_SLOT_PORTFOLIO_V1,
        settings=PortfolioSimulationSettings(
            start_date=days[0],
            adjustment_policy="RAW",
            holding_sessions=holding,
            slippage_bps=Decimal(0),
            stop_loss_pct=Decimal(stop) if stop else None,
            profit_target_pct=Decimal(target) if target else None,
            max_exit_delay_sessions=2,
            initial_capital=Decimal(capital),
            max_concurrent_positions=max_positions,
            max_position_weight=Decimal(max_weight),
            max_gross_exposure=Decimal(max_exposure),
            minimum_cash_reserve_pct=Decimal(reserve),
            profile_fingerprint="profile",
            cost_fingerprint="cost",
            policy_fingerprint="policy",
        ),
        costs=calculator,
    )
    return days, securities, result


def _flat_rows(price: str = "100") -> dict[int, tuple[str, str, str, str]]:
    return {index: (price, price, price, price) for index in range(8)}


def test_portfolio_registry_is_immutable_versioned_and_core_registry_unchanged():
    assert len(PORTFOLIO_POLICIES) == 1
    with pytest.raises(TypeError):
        PORTFOLIO_POLICIES[("NEW", "1")] = LONG_ONLY_EQUAL_SLOT_PORTFOLIO_V1  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        LONG_ONLY_EQUAL_SLOT_PORTFOLIO_V1.policy_version = "2"  # type: ignore[misc]
    with pytest.raises(ValueError, match="Duplicate"):
        build_portfolio_policy_registry(
            (LONG_ONLY_EQUAL_SLOT_PORTFOLIO_V1, LONG_ONLY_EQUAL_SLOT_PORTFOLIO_V1)
        )
    assert len(CORE_TECHNICAL_SET.feature_codes) == 36
    assert "EMA_200" not in FEATURE_DEFINITIONS


def test_policy_fingerprint_is_stable_and_material_definition_changes_it():
    first = policy_metadata(LONG_ONLY_EQUAL_SLOT_PORTFOLIO_V1).policy_fingerprint
    second = policy_metadata(LONG_ONLY_EQUAL_SLOT_PORTFOLIO_V1).policy_fingerprint
    changed = policy_metadata(
        replace(LONG_ONLY_EQUAL_SLOT_PORTFOLIO_V1, default_max_concurrent_positions=11)
    ).policy_fingerprint
    assert first == second
    assert first != changed


def test_largest_affordable_quantity_includes_exact_buy_costs():
    calculator = IndiaCashDeliveryCostCalculator(
        INDIA_NSE_CASH_DELIVERY_2026_09_V1,
        brokerage_per_order_inr=Decimal(0),
        brokerage_rate=Decimal(0),
        dp_charge_per_scrip_sell_day_inr=Decimal(0),
    )
    quantity, costs = PortfolioSimulator.largest_affordable_quantity(
        Decimal("1000"), Decimal("100"), calculator
    )
    assert quantity == 9
    assert costs is not None
    assert Decimal(quantity) * Decimal("100") + costs.total_charges <= Decimal("1000")


def test_shared_cash_ledger_reconciles_and_cash_never_negative():
    _, _, result = _simulate({"ALPHA": _flat_rows()}, [("ALPHA", 0, "")], max_positions=1)
    assert result.positions
    assert min(snapshot.cash for snapshot in result.snapshots) >= 0
    assert sum((event.net_cash_change for event in result.ledger_events), Decimal(0)) == result.snapshots[-1].cash
    assert all(
        snapshot.portfolio_equity == snapshot.cash + snapshot.gross_market_value
        for snapshot in result.snapshots
    )


def test_equal_slot_and_max_weight_bound_entry_allocation():
    _, _, result = _simulate(
        {"ALPHA": _flat_rows()},
        [("ALPHA", 0, "")],
        capital="1000",
        max_positions=2,
        max_weight="0.40",
    )
    position = result.positions[0]
    assert position.initial_quantity == 3
    assert position.entry_portfolio_weight <= Decimal("0.40")


@pytest.mark.parametrize(
    ("max_weight", "max_exposure", "reserve", "reason"),
    [
        ("0.05", "1", "0", "MAX_POSITION_WEIGHT_LIMIT"),
        ("1", "0.05", "0", "GROSS_EXPOSURE_LIMIT"),
        ("1", "1", "0.95", "INSUFFICIENT_PORTFOLIO_CASH"),
    ],
)
def test_deterministic_allocation_rejection_reasons(max_weight, max_exposure, reserve, reason):
    _, _, result = _simulate(
        {"ALPHA": _flat_rows()},
        [("ALPHA", 0, "")],
        capital="1000",
        max_positions=1,
        max_weight=max_weight,
        max_exposure=max_exposure,
        reserve=reserve,
    )
    assert result.positions == ()
    assert result.rejected_candidates[0].reason == reason


def test_same_day_oversubscription_uses_symbol_order_not_future_return():
    rows = {"ZETA": _flat_rows("10"), "ALPHA": _flat_rows("100")}
    _, _, result = _simulate(rows, [("ZETA", 0, ""), ("ALPHA", 0, "")], max_positions=1)
    assert [position.symbol for position in result.positions] == ["ALPHA"]
    assert result.rejected_candidates[0].symbol == "ZETA"
    assert result.rejected_candidates[0].reason == "PORTFOLIO_CAPACITY_REACHED"


def test_open_exit_cash_and_capacity_are_available_for_same_open_reentry():
    rows = {"ALPHA": _flat_rows("100"), "BETA": _flat_rows("100")}
    days, _, result = _simulate(
        rows,
        [("ALPHA", 0, "first"), ("BETA", 1, "second")],
        holding=1,
        max_positions=1,
    )
    assert [position.symbol for position in result.positions] == ["ALPHA", "BETA"]
    alpha_exit_events = [
        event for event in result.ledger_events if event.symbol == "ALPHA" and event.event_type == "EXIT_COST"
    ]
    beta_entry_events = [
        event for event in result.ledger_events if event.symbol == "BETA" and event.event_type == "ENTRY_PRINCIPAL"
    ]
    assert alpha_exit_events[0].session_date == beta_entry_events[0].session_date == days[2]
    assert alpha_exit_events[0].sequence < beta_entry_events[0].sequence


def test_intraday_exit_cash_does_not_retroactively_fund_open_entry():
    alpha = _flat_rows("100")
    alpha[2] = ("100", "101", "80", "90")
    _, _, result = _simulate(
        {"ALPHA": alpha, "BETA": _flat_rows("100")},
        [("ALPHA", 0, "first"), ("BETA", 1, "second")],
        holding=5,
        max_positions=1,
        stop="0.10",
    )
    assert [position.symbol for position in result.positions] == ["ALPHA"]
    assert any(
        item.symbol == "BETA" and item.reason == "PORTFOLIO_CAPACITY_REACHED"
        for item in result.rejected_candidates
    )
    assert result.positions[0].exit_reason == "STOP_LOSS"


def test_one_open_position_per_security_no_pyramiding_then_reentry_after_open_exit():
    rows = {"ALPHA": _flat_rows("100")}
    _, _, blocked = _simulate(
        rows,
        [("ALPHA", 0, "first"), ("ALPHA", 1, "second")],
        holding=3,
        max_positions=2,
    )
    assert len(blocked.positions) == 1
    assert blocked.rejected_candidates[0].reason == "ALREADY_OPEN_POSITION"
    _, _, reentered = _simulate(
        rows,
        [("ALPHA", 0, "first"), ("ALPHA", 1, "second")],
        holding=1,
        max_positions=1,
    )
    assert len(reentered.positions) == 2


@pytest.mark.parametrize(
    ("action_type", "numerator", "denominator"),
    [("STOCK_SPLIT", "2", "1"), ("BONUS", "1", "1")],
)
def test_split_and_bonus_preserve_portfolio_economic_continuity(action_type, numerator, denominator):
    days = _days(8)
    action = CorporateAction(
        id=uuid.uuid4(),
        security_id=uuid.uuid4(),
        action_type=action_type,
        ex_date=days[2],
        ratio_numerator=Decimal(numerator),
        ratio_denominator=Decimal(denominator),
        source="TEST",
        available_at=datetime.combine(days[0], time(12), tzinfo=IST),
    )
    rows = _flat_rows("100")
    for index in range(2, 8):
        rows[index] = ("50", "50", "50", "50")
    _, _, result = _simulate(
        {"ALPHA": rows},
        [("ALPHA", 0, "")],
        holding=3,
        max_positions=1,
        actions={"ALPHA": (action,)},
    )
    position = result.positions[0]
    assert position.current_quantity == Decimal(position.initial_quantity * 2)
    assert position.gross_pnl == Decimal("0.00")
    assert position.corporate_action_events[0].action_type == action_type


def test_split_adjusts_stop_reference_before_portfolio_intraday_exit():
    days = _days(8)
    action = CorporateAction(
        id=uuid.uuid4(),
        security_id=uuid.uuid4(),
        action_type="STOCK_SPLIT",
        ex_date=days[2],
        ratio_numerator=Decimal(2),
        ratio_denominator=Decimal(1),
        source="TEST",
        available_at=datetime.combine(days[0], time(12), tzinfo=IST),
    )
    rows = _flat_rows("100")
    rows[2] = ("48", "50", "44", "46")
    _, _, result = _simulate(
        {"ALPHA": rows},
        [("ALPHA", 0, "")],
        holding=4,
        max_positions=1,
        stop="0.10",
        actions={"ALPHA": (action,)},
    )
    assert result.positions[0].exit_reason == "STOP_LOSS"
    assert result.positions[0].stop_price == Decimal("45.0000")


def test_mark_to_last_uses_no_future_close_and_surfaces_staleness():
    rows = _flat_rows("100")
    rows.pop(2)
    rows[3] = ("500", "500", "500", "500")
    days, _, result = _simulate(
        {"ALPHA": rows},
        [("ALPHA", 0, "")],
        holding=5,
        max_positions=1,
    )
    missing_snapshot = next(item for item in result.snapshots if item.session_date == days[2])
    assert missing_snapshot.gross_market_value == result.positions[0].initial_quantity * Decimal("100")
    assert missing_snapshot.stale_mark_count == 1
    assert missing_snapshot.warnings == ["STALE_MARK_PRICE:ALPHA"]


def test_forced_end_closes_remaining_position_and_final_equity_equals_cash():
    _, _, result = _simulate(
        {"ALPHA": _flat_rows("100")},
        [("ALPHA", 6, "")],
        holding=20,
        max_positions=1,
    )
    assert result.positions[0].exit_reason == "FORCED_END_OF_TEST"
    assert result.snapshots[-1].gross_market_value == 0
    assert result.snapshots[-1].portfolio_equity == result.snapshots[-1].cash


def test_actual_portfolio_quantity_drives_brokerage_and_dp_costs():
    _, _, result = _simulate(
        {"ALPHA": _flat_rows("100")},
        [("ALPHA", 0, "")],
        capital="1000",
        max_positions=1,
        brokerage="20",
        dp="15.50",
    )
    position = result.positions[0]
    assert position.initial_quantity < 10
    assert position.entry_costs.brokerage == Decimal("20.00")
    assert position.exit_costs is not None
    assert position.exit_costs.dp_charge == Decimal("15.50")


def _snapshots(equities: list[str]) -> list[DailyPortfolioSnapshot]:
    days = _days(len(equities))
    result = []
    previous: Decimal | None = None
    peak = Decimal(0)
    for day, value in zip(days, equities):
        equity = Decimal(value)
        peak = max(peak, equity)
        result.append(
            DailyPortfolioSnapshot(
                session_date=day,
                cash=equity,
                gross_market_value=Decimal(0),
                portfolio_equity=equity,
                realized_pnl_to_date=equity - Decimal(equities[0]),
                unrealized_pnl=Decimal(0),
                daily_costs=Decimal(0),
                cumulative_costs=Decimal(0),
                open_position_count=0,
                gross_exposure_pct=Decimal(0),
                cash_pct=Decimal(1),
                daily_return=equity / previous - 1 if previous else None,
                drawdown_pct=equity / peak - 1,
                stale_mark_count=0,
                warnings=[],
            )
        )
        previous = equity
    return result


def test_drawdown_peak_trough_recovery_and_duration_are_from_equity_curve():
    snapshots = _snapshots(["100", "110", "90", "120"])
    drawdown = drawdown_details(snapshots)
    assert drawdown.max_drawdown_pct == Decimal("-0.18181818")
    assert drawdown.max_drawdown_inr == Decimal("20.00")
    assert drawdown.peak_date == snapshots[1].session_date
    assert drawdown.trough_date == snapshots[2].session_date
    assert drawdown.recovery_date == snapshots[3].session_date
    assert drawdown.duration_sessions == 2


def test_risk_metrics_use_sample_volatility_and_safe_undefined_results():
    varying = calculate_portfolio_metrics(
        _snapshots(["100", "101", "99", "103"]),
        [],
        initial_capital=Decimal("100"),
        annual_risk_free_rate=Decimal(0),
    )
    assert varying.annualized_volatility is not None
    assert varying.sharpe_ratio is not None
    assert varying.sortino_ratio is not None
    flat = calculate_portfolio_metrics(
        _snapshots(["100", "100", "100"]),
        [],
        initial_capital=Decimal("100"),
        annual_risk_free_rate=Decimal(0),
    )
    assert flat.sharpe_ratio is None
    assert flat.sortino_ratio is None
    assert flat.calmar_ratio is None


def test_oos_segment_uses_actual_boundary_equity_without_cash_reset():
    snapshots = _snapshots(["100", "110", "105", "120"])
    segment = calculate_segment_metrics(
        "OUT_OF_SAMPLE",
        snapshots[2:],
        annual_risk_free_rate=Decimal(0),
    )
    assert segment.starting_equity == Decimal("105")
    assert segment.ending_equity == Decimal("120")
    assert segment.total_return == Decimal("0.14285714")


def seed_portfolio_service_data(db, *, security_count: int = 3):
    days = _days(230)
    universe = MarketIndex(name="Portfolio Test 100", symbol="PF100", provider="TEST", exchange="NSE")
    securities = [_security(f"P{index:02d}") for index in range(security_count)]
    db.add_all([universe, *securities])
    db.flush()
    for security in securities:
        db.add(IndexMembership(index_id=universe.id, security_id=security.id, valid_from=days[0], source="TEST"))
    for index, day in enumerate(days):
        db.add(
            TradingCalendar(
                exchange="NSE",
                trading_date=day,
                is_trading_day=True,
                session_open=time(9, 15),
                session_close=time(15, 30),
            )
        )
        for offset, security in enumerate(securities):
            close = Decimal("80") + Decimal(index) * Decimal("0.40") + Decimal(offset)
            db.add(
                DailyPrice(
                    security_id=security.id,
                    trading_date=day,
                    open=close - Decimal("0.10"),
                    high=close + Decimal("0.50"),
                    low=close - Decimal("0.50"),
                    close=close,
                    volume=1_000_000 + index * 1000,
                    traded_value=close * Decimal(1_000_000 + index * 1000),
                    source="PORTFOLIO_TEST",
                )
            )
    db.add(
        DataIngestionRun(
            dataset_code="portfolio_fixture",
            dataset_version="v1",
            provider="TEST",
            status="SUCCESS",
            completed_at=datetime(2025, 1, 1, tzinfo=UTC),
            records_written=len(days) * len(securities),
        )
    )
    db.commit()
    return days, securities


def _portfolio_request(days, **overrides):
    payload = {
        "strategy_code": "MOMENTUM_TREND",
        "strategy_version": "1",
        "parameter_overrides": {
            "min_momentum_3m": Decimal("-1"),
            "min_momentum_6m": Decimal("-1"),
            "min_volume_ratio_20": Decimal("0"),
        },
        "universe": "PF100",
        "start_date": days[198],
        "end_date": days[-1],
        "adjustment_policy": "RAW",
        "slippage_bps": Decimal(0),
        "holding_sessions": 3,
        "max_concurrent_positions": 2,
        "max_position_weight": Decimal("0.50"),
    }
    payload.update(overrides)
    return PortfolioRunRequest(**payload)


def test_portfolio_service_is_deterministic_oos_continuous_and_fingerprint_sensitive(db):
    days, _ = seed_portfolio_service_data(db)
    service = PortfolioService(db)
    request = _portfolio_request(days, out_of_sample_start_date=days[220], position_detail_limit=1)
    first = service.run(request)
    second = service.run(request)
    changed_requests = [
        _portfolio_request(days, initial_capital_inr=Decimal("2000000")),
        _portfolio_request(days, max_concurrent_positions=1),
        _portfolio_request(days, max_gross_exposure=Decimal("0.75")),
        _portfolio_request(days, minimum_cash_reserve_pct=Decimal("0.10")),
        _portfolio_request(days, risk_free_rate_annual=Decimal("0.05")),
        _portfolio_request(days, brokerage_per_order_inr=Decimal("10")),
    ]
    changed_fingerprints = {service.run(item).config_fingerprint for item in changed_requests}
    assert first.run_fingerprint == second.run_fingerprint
    assert first.config_fingerprint == second.config_fingerprint
    assert first.dataset_fingerprint == second.dataset_fingerprint
    assert first.config_fingerprint not in changed_fingerprints
    assert len(changed_fingerprints) == len(changed_requests)
    assert first.accepted_entry_count > 0
    assert first.positions_truncated is True
    assert [segment.label for segment in first.oos_metrics] == ["IN_SAMPLE", "OUT_OF_SAMPLE"]
    assert first.oos_metrics[1].starting_equity == next(
        item.portfolio_equity
        for item in first.daily_equity_curve
        if item.session_date >= days[220]
    )


def test_future_price_changes_later_equity_not_earlier_allocation(db):
    days, securities = seed_portfolio_service_data(db)
    request = _portfolio_request(days)
    before = PortfolioService(db).run(request)
    first = before.positions[0]
    earlier_position_ids = [item.position_id for item in before.positions if item.entry_date < days[225]]
    future = next(
        price
        for price in securities[0].prices
        if price.trading_date == days[228]
    )
    future.close += Decimal("10")
    future.high += Decimal("10")
    db.commit()
    after = PortfolioService(db).run(request)
    assert [item.position_id for item in after.positions if item.entry_date < days[225]] == earlier_position_ids
    assert after.dataset_fingerprint != before.dataset_fingerprint
    assert first.signal_fingerprint == after.positions[0].signal_fingerprint


def test_future_corporate_action_is_excluded_from_portfolio_run(db):
    days, securities = seed_portfolio_service_data(db)
    request = _portfolio_request(days)
    before = PortfolioService(db).run(request)
    db.add(
        CorporateAction(
            security_id=securities[0].id,
            action_type="BONUS",
            ex_date=days[-1] + timedelta(days=1),
            ratio_numerator=Decimal(1),
            ratio_denominator=Decimal(1),
            source="FUTURE_ACTION",
            available_at=datetime.combine(days[-1], time(12), tzinfo=IST),
        )
    )
    db.commit()
    after = PortfolioService(db).run(request)
    assert after.dataset_fingerprint == before.dataset_fingerprint
    assert after.run_fingerprint == before.run_fingerprint


def test_future_action_revision_preserves_preavailability_portfolio_signal(db):
    days, securities = seed_portfolio_service_data(db)
    original = CorporateAction(
        security_id=securities[0].id,
        action_type="STOCK_SPLIT",
        ex_date=days[228],
        ratio_numerator=Decimal(2),
        ratio_denominator=Decimal(1),
        source="ORIGINAL",
        available_at=datetime.combine(days[220], time(12), tzinfo=IST),
    )
    db.add(original)
    db.commit()
    request = _portfolio_request(days)
    before = PortfolioService(db).run(request)
    earlier = next(
        item
        for item in before.positions
        if item.security_id == securities[0].id and item.signal_date < days[225]
    )
    db.add(
        CorporateAction(
            security_id=securities[0].id,
            action_type="STOCK_SPLIT",
            ex_date=days[228],
            ratio_numerator=Decimal(4),
            ratio_denominator=Decimal(1),
            source="REVISION",
            available_at=datetime.combine(days[225], time(12), tzinfo=IST),
            supersedes_action_id=original.id,
        )
    )
    db.commit()
    after = PortfolioService(db).run(request)
    unchanged = next(
        item
        for item in after.positions
        if item.security_id == earlier.security_id and item.signal_date == earlier.signal_date
    )
    assert unchanged.signal_fingerprint == earlier.signal_fingerprint
    assert after.dataset_fingerprint != before.dataset_fingerprint
    assert after.run_fingerprint != before.run_fingerprint


def test_portfolio_api_validation_ordering_preview_and_bounded_queries(db, client):
    days, _ = seed_portfolio_service_data(db)
    assert client.get("/api/v1/portfolio/metadata").status_code == 200
    request = _portfolio_request(days, position_detail_limit=1, ledger_detail_limit=2)
    select_count = 0
    engine = db.get_bind()

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal select_count
        if statement.lstrip().lower().startswith("select"):
            select_count += 1

    event.listen(engine, "after_cursor_execute", count_selects)
    try:
        response = client.post("/api/v1/portfolio/run", json=request.model_dump(mode="json"))
    finally:
        event.remove(engine, "after_cursor_execute", count_selects)
    assert response.status_code == 200
    body = response.json()
    assert body["positions_truncated"] is True
    assert body["ledger_truncated"] is True
    assert body["positions"] == sorted(
        body["positions"], key=lambda item: (item["entry_date"], item["symbol"].casefold(), item["position_id"])
    )
    assert select_count == 6
    invalid = request.model_dump(mode="json")
    invalid["portfolio_policy_code"] = "UNKNOWN"
    assert client.post("/api/v1/portfolio/run", json=invalid).status_code == 404
    invalid["portfolio_policy_code"] = "LONG_ONLY_EQUAL_SLOT_PORTFOLIO"
    invalid["initial_capital_inr"] = 0
    assert client.post("/api/v1/portfolio/run", json=invalid).status_code == 422
