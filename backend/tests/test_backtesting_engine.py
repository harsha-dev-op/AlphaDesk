from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, select

from app.backtests.analytics import calculate_analytics
from app.backtests.costs import IndiaCashDeliveryCostCalculator, cost_fingerprint
from app.backtests.definitions import INDIA_NSE_CASH_DELIVERY_2026_09_V1, NEXT_OPEN_FIXED_HOLD_V1
from app.backtests.registry import BACKTEST_PROFILES, COST_MODELS
from app.backtests.service import BacktestService
from app.backtests.simulator import SetupEvent, SimulationSettings, TradeSimulator
from app.models import CorporateAction, DailyPrice, DataIngestionRun, IndexMembership, MarketIndex, Security, TradingCalendar
from app.repositories.securities import PriceRecord
from app.schemas.backtests import BacktestRunRequest
from app.schemas.strategies import StrategyEvaluationRequest
from app.strategies.service import StrategyService
from app.strategies.registry import find_strategy
from app.technical.calculators import PricePoint, calculate_feature_frame
from app.technical.definitions import CORE_TECHNICAL_SET, FEATURE_DEFINITIONS, STRATEGY_TECHNICAL_SET
from app.technical.series import HistoricalSecuritySeries, HistoricalTechnicalSeriesService
from app.technical.service import TechnicalFeatureService

IST = ZoneInfo("Asia/Kolkata")


def _sessions(count: int, start: date = date(2024, 1, 2)) -> list[date]:
    output: list[date] = []
    current = start
    while len(output) < count:
        if current.weekday() < 5:
            output.append(current)
        current += timedelta(days=1)
    return output


def seed_backtest_data(db, *, count: int = 260):
    sessions = _sessions(count)
    securities = {
        symbol: Security(
            id=uuid.uuid4(),
            exchange="NSE",
            symbol=symbol,
            trading_symbol=f"{symbol}-EQ",
            company_name=f"{symbol} Research Ltd",
            is_active=True,
        )
        for symbol in ("ALPHA", "OLDCO", "NEWCO")
    }
    universe = MarketIndex(name="Backtest Research 100", symbol="BT100", provider="TEST", exchange="NSE")
    db.add_all([*securities.values(), universe])
    db.flush()
    db.add_all(
        [
            IndexMembership(index_id=universe.id, security_id=securities["ALPHA"].id, valid_from=sessions[0], source="TEST"),
            IndexMembership(index_id=universe.id, security_id=securities["OLDCO"].id, valid_from=sessions[0], valid_to=sessions[219], source="TEST"),
            IndexMembership(index_id=universe.id, security_id=securities["NEWCO"].id, valid_from=sessions[220], source="TEST"),
        ]
    )
    for index, day in enumerate(sessions):
        db.add(TradingCalendar(exchange="NSE", trading_date=day, is_trading_day=True, session_open=time(9, 15), session_close=time(15, 30)))
        for offset, security in enumerate(securities.values()):
            close = Decimal("80") + Decimal(index) * Decimal("0.35") + Decimal(offset * 3)
            volume = 1_000_000 + index * 1_000
            db.add(
                DailyPrice(
                    security_id=security.id,
                    trading_date=day,
                    open=close - Decimal("0.20"),
                    high=close + Decimal("0.50"),
                    low=close - Decimal("0.50"),
                    close=close,
                    volume=volume,
                    traded_value=close * volume,
                    source="BACKTEST_TEST",
                )
            )
    db.add(DataIngestionRun(dataset_code="backtest_fixture", dataset_version="v1", provider="TEST", status="SUCCESS", completed_at=datetime(2024, 12, 31, tzinfo=UTC), records_written=count * 3))
    db.commit()
    return sessions, universe, securities


def permissive_request(sessions: list[date], **overrides) -> BacktestRunRequest:
    payload = {
        "strategy_code": "MOMENTUM_TREND",
        "strategy_version": "1",
        "parameter_overrides": {
            "min_momentum_3m": Decimal("-1"),
            "min_momentum_6m": Decimal("-1"),
            "min_volume_ratio_20": Decimal("0"),
        },
        "universe": "BT100",
        "start_date": sessions[198],
        "end_date": sessions[-1],
        "adjustment_policy": "RAW",
        "slippage_bps": Decimal("0"),
    }
    payload.update(overrides)
    return BacktestRunRequest(**payload)


def _calendar(days: list[date]) -> list[TradingCalendar]:
    return [
        TradingCalendar(exchange="NSE", trading_date=day, is_trading_day=True, session_open=time(9, 15), session_close=time(15, 30))
        for day in days
    ]


def _series(security: Security, days: list[date], rows: dict[int, tuple[str, str, str, str]], actions=()) -> HistoricalSecuritySeries:
    prices = []
    for index, (open_price, high, low, close) in rows.items():
        prices.append(
            PriceRecord(
                security_id=security.id,
                trading_date=days[index],
                open=Decimal(open_price),
                high=Decimal(high),
                low=Decimal(low),
                close=Decimal(close),
                volume=1000,
                traded_value=None,
                source="TEST",
            )
        )
    return HistoricalSecuritySeries(
        security=security,
        prices=tuple(prices),
        action_history=tuple(actions),
        observations={},
        dataset_fingerprint="dataset",
        quality_warnings=(),
    )


def _simulate(rows, *, holding=1, notional="100000", stop=None, target=None, actions=(), delay=5, slippage="0"):
    days = _sessions(8)
    security = Security(id=uuid.uuid4(), exchange="NSE", symbol="SIM", trading_symbol="SIM-EQ", company_name="Simulation Ltd")
    setup = SetupEvent(
        security_id=security.id,
        symbol=security.symbol,
        company_name=security.company_name,
        signal_date=days[0],
        signal_decision_at=datetime.combine(days[0], time(15, 30), tzinfo=IST),
        strategy_code="MOMENTUM_TREND",
        strategy_version="1",
        strategy_fingerprint="strategy",
        signal_result_fingerprint="signal",
    )
    cost = IndiaCashDeliveryCostCalculator(
        INDIA_NSE_CASH_DELIVERY_2026_09_V1,
        brokerage_per_order_inr=Decimal(0),
        brokerage_rate=Decimal(0),
        dp_charge_per_scrip_sell_day_inr=Decimal(0),
    )
    result = TradeSimulator().simulate(
        [setup],
        series_by_security={security.id: _series(security, days, rows, actions)},
        sessions=_calendar(days),
        profile=NEXT_OPEN_FIXED_HOLD_V1,
        settings=SimulationSettings(
            adjustment_policy="RAW",
            holding_sessions=holding,
            trade_notional_inr=Decimal(notional),
            slippage_bps=Decimal(slippage),
            stop_loss_pct=Decimal(stop) if stop else None,
            profit_target_pct=Decimal(target) if target else None,
            max_exit_delay_sessions=delay,
            profile_fingerprint="profile",
            cost_fingerprint="cost",
        ),
        costs=cost,
    )
    return days, result


def test_profile_and_cost_registries_are_immutable_and_strategy_features_unchanged():
    assert len(BACKTEST_PROFILES) == len(COST_MODELS) == 1
    with pytest.raises(TypeError):
        BACKTEST_PROFILES[("NEW", "1")] = NEXT_OPEN_FIXED_HOLD_V1  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        NEXT_OPEN_FIXED_HOLD_V1.profile_version = "2"  # type: ignore[misc]
    assert len(CORE_TECHNICAL_SET.feature_codes) == 36
    assert len(STRATEGY_TECHNICAL_SET.feature_codes) == 38
    assert "EMA_200" not in FEATURE_DEFINITIONS


def test_cost_model_exact_rates_rounding_taxable_base_and_side_rules():
    calculator = IndiaCashDeliveryCostCalculator(
        INDIA_NSE_CASH_DELIVERY_2026_09_V1,
        brokerage_per_order_inr=Decimal(0),
        brokerage_rate=Decimal(0),
        dp_charge_per_scrip_sell_day_inr=Decimal(0),
    )
    buy = calculator.calculate(Decimal("100000"), "BUY")
    sell = calculator.calculate(Decimal("100000"), "SELL")
    assert buy.stt == sell.stt == Decimal("100.00")
    assert buy.exchange_transaction_charge == Decimal("3.07")
    assert buy.sebi_charge == Decimal("0.10")
    assert buy.gst == Decimal("0.57")
    assert buy.stamp_duty == Decimal("15.00")
    assert sell.stamp_duty == Decimal("0.00")
    assert buy.total_charges == Decimal("118.74")
    assert sell.total_charges == Decimal("103.74")


def test_brokerage_and_dp_overrides_are_separate_and_fingerprinted():
    calculator = IndiaCashDeliveryCostCalculator(
        INDIA_NSE_CASH_DELIVERY_2026_09_V1,
        brokerage_per_order_inr=Decimal("20"),
        brokerage_rate=Decimal("0.0001"),
        dp_charge_per_scrip_sell_day_inr=Decimal("15.50"),
    )
    sell = calculator.calculate(Decimal("100000"), "SELL")
    assert sell.brokerage == Decimal("30.00")
    assert sell.dp_charge == Decimal("15.50")
    baseline = cost_fingerprint(INDIA_NSE_CASH_DELIVERY_2026_09_V1, brokerage_per_order_inr=Decimal(0), brokerage_rate=Decimal(0), dp_charge_per_scrip_sell_day_inr=Decimal(0))
    changed = cost_fingerprint(INDIA_NSE_CASH_DELIVERY_2026_09_V1, brokerage_per_order_inr=Decimal(20), brokerage_rate=Decimal(0), dp_charge_per_scrip_sell_day_inr=Decimal(0))
    assert baseline != changed


def test_next_open_entry_and_holding_one_exits_following_session_open():
    days, result = _simulate({1: ("100", "105", "95", "102"), 2: ("110", "112", "108", "111")})
    trade = result.trades[0]
    assert trade.entry_date == days[1]
    assert trade.raw_entry_price == Decimal("100.0000")
    assert trade.exit_date == days[2]
    assert trade.raw_exit_price == Decimal("110.0000")
    assert trade.holding_sessions == 1
    assert trade.exit_reason == "TIME_EXIT"


def test_entry_and_exit_slippage_are_adverse_and_decimal_safe():
    _, result = _simulate({1: ("100", "105", "95", "102"), 2: ("110", "112", "108", "111")}, slippage="5")
    trade = result.trades[0]
    assert trade.slipped_entry_price == Decimal("100.0500")
    assert trade.slipped_exit_price == Decimal("109.9450")
    assert trade.entry_quantity == 999


def test_integer_quantity_and_insufficient_notional_skip():
    _, result = _simulate({1: ("300", "301", "299", "300"), 2: ("300", "301", "299", "300")}, notional="1000")
    assert result.trades[0].entry_quantity == 3
    _, skipped = _simulate({1: ("300", "301", "299", "300")}, notional="100")
    assert skipped.trades == ()
    assert skipped.skipped_reasons == {"INSUFFICIENT_NOTIONAL_FOR_ONE_SHARE": 1}


def test_missing_immediate_entry_is_skipped_without_later_fill():
    _, result = _simulate({2: ("100", "101", "99", "100")})
    assert result.trades == ()
    assert result.skipped_reasons == {"ENTRY_PRICE_UNAVAILABLE": 1}


def test_stop_target_ambiguity_is_conservative_stop_first():
    _, result = _simulate({1: ("100", "120", "80", "105")}, stop="0.10", target="0.10")
    trade = result.trades[0]
    assert trade.exit_reason == "STOP_LOSS"
    assert trade.raw_exit_price == Decimal("90.0000")
    assert "AMBIGUOUS_INTRADAY_PATH_STOP_FIRST" in trade.warnings


@pytest.mark.parametrize(
    ("row", "stop", "target", "reason", "exit_price"),
    [
        (("85", "95", "80", "90"), "0.10", None, "STOP_LOSS", Decimal("85.0000")),
        (("115", "120", "110", "116"), None, "0.10", "PROFIT_TARGET", Decimal("115.0000")),
        (("100", "101", "89", "95"), "0.10", None, "STOP_LOSS", Decimal("90.0000")),
        (("100", "111", "99", "108"), None, "0.10", "PROFIT_TARGET", Decimal("110.0000")),
    ],
)
def test_stop_target_gap_and_touch_rules(row, stop, target, reason, exit_price):
    if Decimal(row[0]) != Decimal("100"):
        rows = {1: ("100", "101", "99", "100"), 2: row}
        holding = 2
    else:
        rows = {1: row}
        holding = 1
    _, result = _simulate(rows, stop=stop, target=target, holding=holding)
    assert result.trades[0].exit_reason == reason
    assert result.trades[0].raw_exit_price == exit_price


@pytest.mark.parametrize(("action_type", "numerator", "denominator"), [("STOCK_SPLIT", "2", "1"), ("BONUS", "1", "1")])
def test_split_and_bonus_adjust_quantity_and_avoid_fake_pnl(action_type, numerator, denominator):
    days = _sessions(8)
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
    _, result = _simulate(
        {1: ("100", "101", "99", "100"), 2: ("50", "51", "49", "50"), 3: ("50", "51", "49", "50")},
        holding=2,
        actions=(action,),
    )
    trade = result.trades[0]
    assert trade.quantity == Decimal("2000")
    assert trade.raw_exit_price == Decimal("50.0000")
    assert trade.gross_pnl == Decimal("0.00")
    assert trade.corporate_action_events[0].action_type == action_type


def test_split_adjusts_stop_reference_before_intraday_touch():
    days = _sessions(8)
    action = CorporateAction(
        id=uuid.uuid4(),
        security_id=uuid.uuid4(),
        action_type="STOCK_SPLIT",
        ex_date=days[2],
        ratio_numerator=2,
        ratio_denominator=1,
        source="TEST",
        available_at=datetime.combine(days[0], time(12), tzinfo=IST),
    )
    _, result = _simulate(
        {1: ("100", "101", "99", "100"), 2: ("48", "50", "44", "46")},
        holding=3,
        stop="0.10",
        actions=(action,),
    )
    trade = result.trades[0]
    assert trade.exit_reason == "STOP_LOSS"
    assert trade.stop_price == Decimal("45.0000")
    assert trade.quantity == Decimal("2000")
    assert trade.raw_exit_price == Decimal("45.0000")


def test_missing_scheduled_exit_search_is_bounded_and_typed():
    _, delayed = _simulate({1: ("100", "101", "99", "100"), 4: ("105", "106", "104", "105")}, holding=1, delay=1)
    assert delayed.trades[0].exit_date is None
    assert delayed.trades[0].exit_reason == "EXIT_PRICE_UNAVAILABLE"
    _, found = _simulate({1: ("100", "101", "99", "100"), 4: ("105", "106", "104", "105")}, holding=1, delay=2)
    assert found.trades[0].exit_reason == "TIME_EXIT"


def test_end_of_test_forces_last_available_close_and_no_final_signal_entry():
    days, result = _simulate({1: ("100", "101", "99", "100"), 7: ("105", "106", "104", "105")}, holding=20)
    assert result.trades[0].exit_reason == "FORCED_END_OF_TEST"
    assert result.trades[0].exit_date == days[7]


def test_historical_series_matches_authoritative_latest_for_raw_and_adjusted(db):
    sessions, _, securities = seed_backtest_data(db)
    action = CorporateAction(
        security_id=securities["ALPHA"].id,
        action_type="STOCK_SPLIT",
        ex_date=sessions[220],
        ratio_numerator=2,
        ratio_denominator=1,
        source="TEST",
        available_at=datetime.combine(sessions[210], time(12), tzinfo=IST),
    )
    db.add(action)
    db.commit()
    day = sessions[230]
    as_of = datetime.combine(day, time(15, 30), tzinfo=IST)
    calendar_entry = db.scalar(select(TradingCalendar).where(TradingCalendar.trading_date == day))
    assert calendar_entry is not None
    for policy in ("RAW", "ADJUSTED"):
        historical = HistoricalTechnicalSeriesService(db).compute_batch(
            [securities["ALPHA"]],
            start_date=day,
            end_date=day,
            adjustment_policy=policy,
            feature_set=STRATEGY_TECHNICAL_SET,
            calendar_entries={("NSE", day): calendar_entry},
        )
        latest = TechnicalFeatureService(db).compute(
            securities["ALPHA"],
            start_date=day,
            end_date=day,
            adjustment_policy=policy,
            as_of=as_of,
            feature_set=STRATEGY_TECHNICAL_SET,
        )
        assert historical.series[0].observations[day].values == latest.items[0].values


def test_selected_historical_feature_path_matches_full_authoritative_frame():
    points = [
        PricePoint(
            trading_date=day,
            open=Decimal("100") + Decimal(index) / Decimal(10),
            high=Decimal("101") + Decimal(index) / Decimal(10),
            low=Decimal("99") + Decimal(index) / Decimal(10),
            close=Decimal("100.5") + Decimal(index) / Decimal(10),
            volume=1_000_000 + index,
            traded_value=Decimal("100000000") + index,
            source="TEST",
        )
        for index, day in enumerate(_sessions(220))
    ]
    full = calculate_feature_frame(points)
    for strategy_code in ("MOMENTUM_TREND", "BREAKOUT_20D", "MEAN_REVERSION_PULLBACK"):
        definition = find_strategy(strategy_code, "1")
        assert definition is not None
        selected = calculate_feature_frame(points, definition.required_feature_codes)
        for index in (0, 19, 49, 199, 219):
            assert {code: selected[index][code] for code in definition.required_feature_codes} == {
                code: full[index][code] for code in definition.required_feature_codes
            }


def test_backtest_replay_membership_next_open_overlap_analytics_and_determinism(db):
    sessions, _, _ = seed_backtest_data(db)
    service = BacktestService(db)
    request = permissive_request(sessions, holding_sessions=3)
    first = service.run(request)
    second = service.run(request)
    assert first.setup_count > first.executed_trade_count > 0
    assert first.skipped_setup_reasons["ALREADY_OPEN_POSITION"] > 0
    assert any(trade.symbol == "OLDCO" and trade.signal_date <= sessions[219] for trade in first.trades)
    assert any(trade.symbol == "NEWCO" and trade.signal_date >= sessions[220] for trade in first.trades)
    assert all(trade.entry_date > trade.signal_date for trade in first.trades)
    assert first.config_fingerprint == second.config_fingerprint
    assert first.dataset_fingerprint == second.dataset_fingerprint
    assert first.run_fingerprint == second.run_fingerprint
    assert first.analytics.closed_trade_count > 0
    assert first.yearly_breakdown
    alpha_trades = [trade for trade in first.trades if trade.symbol == "ALPHA"]
    assert len(alpha_trades) > 1
    assert all(later.signal_date >= earlier.exit_date for earlier, later in zip(alpha_trades, alpha_trades[1:]) if earlier.exit_date)


@pytest.mark.parametrize(
    ("strategy_code", "parameter_overrides"),
    [
        ("BREAKOUT_20D", {"min_breakout_pct_20": Decimal("-1"), "min_volume_ratio_20": Decimal("0"), "min_close_location": Decimal("0"), "max_atr_pct_14": Decimal("2")}),
        ("MEAN_REVERSION_PULLBACK", {"max_rsi_14": Decimal("100"), "max_distance_sma_20": Decimal("2"), "max_return_5d": Decimal("5"), "max_atr_pct_14": Decimal("2")}),
    ],
)
def test_breakout_and_mean_reversion_historical_replay(strategy_code, parameter_overrides, db):
    sessions, _, _ = seed_backtest_data(db)
    response = BacktestService(db).run(
        permissive_request(
            sessions,
            strategy_code=strategy_code,
            parameter_overrides=parameter_overrides,
            start_date=sessions[210],
            end_date=sessions[230],
            holding_sessions=2,
        )
    )
    assert response.setup_count > 0
    assert response.executed_trade_count > 0


def test_future_price_changes_outcome_not_prior_signal_fingerprint(db):
    sessions, _, securities = seed_backtest_data(db)
    request = permissive_request(sessions, start_date=sessions[210], end_date=sessions[240], holding_sessions=1)
    before = BacktestService(db).run(request)
    first_trade = next(trade for trade in before.trades if trade.symbol == "ALPHA")
    future = db.scalar(select(DailyPrice).where(DailyPrice.security_id == securities["ALPHA"].id, DailyPrice.trading_date == first_trade.entry_date))
    assert future is not None
    future.open += Decimal("1")
    future.high += Decimal("1")
    db.commit()
    after = BacktestService(db).run(request)
    changed = next(trade for trade in after.trades if trade.symbol == "ALPHA" and trade.signal_date == first_trade.signal_date)
    assert changed.signal_result_fingerprint == first_trade.signal_result_fingerprint
    assert changed.raw_entry_price != first_trade.raw_entry_price


def test_future_action_revision_does_not_change_preavailability_signal(db):
    sessions, _, securities = seed_backtest_data(db)
    original = CorporateAction(
        security_id=securities["ALPHA"].id,
        action_type="STOCK_SPLIT",
        ex_date=sessions[228],
        ratio_numerator=2,
        ratio_denominator=1,
        source="ORIGINAL",
        available_at=datetime.combine(sessions[220], time(12), tzinfo=IST),
    )
    db.add(original)
    db.commit()
    request = permissive_request(sessions, start_date=sessions[218], end_date=sessions[235], holding_sessions=1)
    before = BacktestService(db).run(request)
    earlier = next(trade for trade in before.trades if trade.symbol == "ALPHA" and trade.signal_date < sessions[225])
    db.add(
        CorporateAction(
            security_id=securities["ALPHA"].id,
            action_type="STOCK_SPLIT",
            ex_date=sessions[228],
            ratio_numerator=4,
            ratio_denominator=1,
            source="REVISION",
            available_at=datetime.combine(sessions[225], time(12), tzinfo=IST),
            supersedes_action_id=original.id,
        )
    )
    db.commit()
    after = BacktestService(db).run(request)
    unchanged = next(trade for trade in after.trades if trade.symbol == "ALPHA" and trade.signal_date == earlier.signal_date)
    assert unchanged.signal_result_fingerprint == earlier.signal_result_fingerprint


def test_phase4_strategy_consistency_for_selected_historical_date(db):
    sessions, _, _ = seed_backtest_data(db)
    day = sessions[220]
    request = permissive_request(sessions, start_date=day, end_date=sessions[222])
    backtest = BacktestService(db).run(request)
    phase4 = StrategyService(db).evaluate(
        StrategyEvaluationRequest(**{
            "strategy_code": request.strategy_code,
            "strategy_version": request.strategy_version,
            "universe": request.universe,
            "observation_date": day,
            "as_of": datetime.combine(day, time(15, 30), tzinfo=IST),
            "adjustment_policy": request.adjustment_policy,
            "parameter_overrides": request.parameter_overrides,
        })
    )
    matched_symbols = {item.symbol for item in phase4.results if item.matched}
    backtest_symbols = {trade.symbol for trade in backtest.trades if trade.signal_date == day}
    assert backtest_symbols <= matched_symbols
    assert matched_symbols


def test_fingerprint_sensitivity_holdout_and_trade_preview_use_full_analytics(db):
    sessions, _, _ = seed_backtest_data(db)
    service = BacktestService(db)
    baseline = service.run(permissive_request(sessions, trade_detail_limit=1, out_of_sample_start_date=sessions[230]))
    changed = service.run(permissive_request(sessions, holding_sessions=4, slippage_bps=Decimal("10")))
    assert baseline.trades_truncated is True
    assert baseline.returned_trade_count == 1
    assert baseline.analytics.executed_trade_count == baseline.total_trade_count > 1
    assert [item.label for item in baseline.holdout_breakdown] == ["IN_SAMPLE", "OUT_OF_SAMPLE"]
    assert baseline.config_fingerprint != changed.config_fingerprint
    assert baseline.run_fingerprint != changed.run_fingerprint


def test_analytics_zero_denominators_and_profit_factor_are_safe():
    empty = calculate_analytics([], signal_count=0, executable_setup_count=0)
    assert empty.win_rate is None
    assert empty.profit_factor is None
    assert "NO_TRADES" in empty.warnings


def test_trade_analytics_profit_factor_expectancy_percentiles_and_excursions():
    _, result = _simulate({1: ("100", "105", "95", "100"), 2: ("100", "101", "99", "100")})
    base = result.trades[0]
    winner = base.model_copy(update={"net_pnl": Decimal("100"), "gross_pnl": Decimal("110"), "total_costs": Decimal("10"), "net_return_pct": Decimal("0.10"), "gross_return_pct": Decimal("0.11")})
    loser = base.model_copy(update={"trade_id": "loss", "net_pnl": Decimal("-50"), "gross_pnl": Decimal("-40"), "total_costs": Decimal("10"), "net_return_pct": Decimal("-0.05"), "gross_return_pct": Decimal("-0.04")})
    analytics = calculate_analytics([winner, loser], signal_count=2, executable_setup_count=2)
    assert analytics.profit_factor == Decimal("2.00000000")
    assert analytics.expectancy_per_trade == Decimal("25.00")
    assert analytics.median_net_return == Decimal("0.02500000")
    assert base.maximum_adverse_excursion == Decimal("-0.05000000")
    assert base.maximum_favorable_excursion == Decimal("0.05000000")


def test_backtest_api_metadata_run_validation_and_ordering(db, client):
    sessions, _, _ = seed_backtest_data(db)
    metadata = client.get("/api/v1/backtests/metadata")
    assert metadata.status_code == 200
    body = metadata.json()
    assert body["profiles"][0]["profile_code"] == "NEXT_OPEN_FIXED_HOLD"
    assert body["cost_models"][0]["stt_buy_rate"] == "0.001"
    response = client.post("/api/v1/backtests/run", json=permissive_request(sessions).model_dump(mode="json"))
    assert response.status_code == 200
    trades = response.json()["trades"]
    assert trades == sorted(trades, key=lambda item: (item["entry_date"], item["symbol"].casefold(), item["trade_id"]))
    invalid = permissive_request(sessions).model_dump(mode="json")
    invalid["profile_code"] = "UNKNOWN"
    assert client.post("/api/v1/backtests/run", json=invalid).status_code == 404
    invalid["profile_code"] = "NEXT_OPEN_FIXED_HOLD"
    invalid["strategy_code"] = "UNKNOWN"
    assert client.post("/api/v1/backtests/run", json=invalid).status_code == 404
    invalid["strategy_code"] = "MOMENTUM_TREND"
    invalid["cost_model_code"] = "UNKNOWN"
    assert client.post("/api/v1/backtests/run", json=invalid).status_code == 404
    invalid["cost_model_code"] = "INDIA_NSE_CASH_DELIVERY_2026_09"
    invalid["start_date"], invalid["end_date"] = invalid["end_date"], invalid["start_date"]
    assert client.post("/api/v1/backtests/run", json=invalid).status_code == 422


def test_backtest_query_growth_is_bounded_by_security_chunks(db):
    sessions, _, _ = seed_backtest_data(db)
    select_count = 0
    engine = db.get_bind()

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal select_count
        if statement.lstrip().lower().startswith("select"):
            select_count += 1

    event.listen(engine, "after_cursor_execute", count_selects)
    try:
        BacktestService(db).run(permissive_request(sessions))
    finally:
        event.remove(engine, "after_cursor_execute", count_selects)
    assert select_count == 6
