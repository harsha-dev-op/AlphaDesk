from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select

from app.backtests.costs import IndiaCashDeliveryCostCalculator
from app.models import (
    CorporateAction,
    DailyPrice,
    DataIngestionRun,
    IndexDailyPrice,
    IndexMembership,
    MarketIndex,
    ResearchExperiment,
    ResearchExperimentRun,
    Security,
    TradingCalendar,
)
from app.research.historical import HistoricalCompositionService
from app.regimes.schemas import RegimeResearchAttributionRequest
from app.regimes.service import MarketRegimeService
from app.regimes.definitions import MARKET_REGIME_4_STATE_V1, REGIME_DEFINITIONS
from app.research.service import ResearchService
from app.schemas.research import CompositionEvaluationRequest, ExperimentCreateRequest
from app.schemas.research_history import (
    CompositionBacktestRequest,
    CompositionPortfolioRequest,
)
from app.technical.definitions import STRATEGY_TECHNICAL_SET
from app.technical.series import HistoricalTechnicalSeriesService
from app.technical.service import TechnicalFeatureService


IST = ZoneInfo("Asia/Kolkata")
PHASE9_GOLDEN_NAMESPACE = uuid.UUID("1e5f1875-953b-43dc-b0aa-16dc754a5654")


def _sessions(count: int, start: date = date(2023, 1, 2)) -> list[date]:
    output: list[date] = []
    current = start
    while len(output) < count:
        if current.weekday() < 5:
            output.append(current)
        current += timedelta(days=1)
    return output


def _security(symbol: str) -> Security:
    return Security(
        id=uuid.uuid5(PHASE9_GOLDEN_NAMESPACE, f"security:{symbol}"),
        exchange="NSE",
        symbol=symbol,
        trading_symbol=f"{symbol}-EQ",
        company_name=f"{symbol} Phase 9 Research",
        security_type="EQUITY",
        currency="INR",
        is_active=True,
    )


def seed_history(db, *, security_count: int = 3, session_count: int = 230):
    sessions = _sessions(session_count)
    universe = MarketIndex(
        id=uuid.uuid5(PHASE9_GOLDEN_NAMESPACE, "index:P9HIST"),
        name="Phase 9 Historical",
        symbol="P9HIST",
        provider="TEST",
        exchange="NSE",
    )
    securities = [_security(f"P9{index:03d}") for index in range(security_count)]
    db.add_all([universe, *securities])
    db.flush()
    for security in securities:
        db.add(
            IndexMembership(
                id=uuid.uuid5(
                    PHASE9_GOLDEN_NAMESPACE,
                    f"membership:{universe.id}:{security.id}:{sessions[0]}",
                ),
                index_id=universe.id,
                security_id=security.id,
                valid_from=sessions[0],
                source="PHASE9_TEST",
            )
        )
    for session_index, day in enumerate(sessions):
        db.add(
            TradingCalendar(
                id=uuid.uuid5(PHASE9_GOLDEN_NAMESPACE, f"calendar:{day}"),
                exchange="NSE",
                trading_date=day,
                is_trading_day=True,
                session_open=time(9, 15),
                session_close=time(15, 30),
            )
        )
        for security_index, security in enumerate(securities):
            close = (
                Decimal("75")
                + Decimal(session_index) * Decimal("0.40")
                + Decimal(security_index)
            )
            volume = 1_000_000 + session_index * 2_000 + security_index
            db.add(
                DailyPrice(
                    id=uuid.uuid5(
                        PHASE9_GOLDEN_NAMESPACE,
                        f"price:{security.id}:{day}",
                    ),
                    security_id=security.id,
                    trading_date=day,
                    open=close - Decimal("0.10"),
                    high=close + Decimal("0.60"),
                    low=close - Decimal("0.60"),
                    close=close,
                    volume=volume,
                    traded_value=close * volume,
                    source="PHASE9_TEST",
                )
            )
    db.add(
        DataIngestionRun(
            id=uuid.uuid5(PHASE9_GOLDEN_NAMESPACE, "ingestion:phase9_fixture:v1"),
            dataset_code="phase9_fixture",
            dataset_version="v1",
            provider="TEST",
            status="SUCCESS",
            completed_at=datetime(2025, 1, 1, tzinfo=UTC),
            records_written=session_count * security_count,
        )
    )
    db.commit()
    return sessions, universe, securities


def _components(*, reverse: bool = False, momentum_floor: str = "-1"):
    components = [
        {
            "strategy_code": "MOMENTUM_TREND",
            "strategy_version": "1",
            "parameter_overrides": {
                "min_momentum_3m": momentum_floor,
                "min_momentum_6m": "-1",
                "min_volume_ratio_20": "0",
            },
        },
        {
            "strategy_code": "BREAKOUT_20D",
            "strategy_version": "1",
            "parameter_overrides": {
                "min_breakout_pct_20": "-1",
                "min_volume_ratio_20": "0",
                "min_close_location": "0",
                "max_atr_pct_14": "2",
            },
        },
        {
            "strategy_code": "MEAN_REVERSION_PULLBACK",
            "strategy_version": "1",
            "parameter_overrides": {
                "max_rsi_14": "100",
                "max_distance_sma_20": "2",
                "max_return_5d": "5",
                "max_atr_pct_14": "2",
            },
        },
    ]
    return list(reversed(components)) if reverse else components


def inline_composition(sessions, **overrides) -> CompositionEvaluationRequest:
    payload = {
        "policy_code": "CONSENSUS_N_OF_M",
        "policy_version": "1",
        "universe": "P9HIST",
        "observation_date": sessions[-1],
        "as_of": datetime.combine(sessions[-1], time(15, 30), tzinfo=IST),
        "adjustment_policy": "RAW",
        "required_match_count": 2,
        "components": _components(),
    }
    payload.update(overrides)
    return CompositionEvaluationRequest(**payload)


def backtest_request(sessions, **overrides) -> CompositionBacktestRequest:
    payload = {
        "source": {"inline_composition": inline_composition(sessions)},
        "universe": "P9HIST",
        "start_date": sessions[198],
        "end_date": sessions[-1],
        "adjustment_policy": "RAW",
        "holding_sessions": 3,
        "slippage_bps": "0",
        "trade_notional_inr": "100000",
    }
    payload.update(overrides)
    return CompositionBacktestRequest(**payload)


def portfolio_request(sessions, **overrides) -> CompositionPortfolioRequest:
    payload = {
        "source": {"inline_composition": inline_composition(sessions)},
        "universe": "P9HIST",
        "start_date": sessions[198],
        "end_date": sessions[-1],
        "adjustment_policy": "RAW",
        "holding_sessions": 3,
        "slippage_bps": "0",
        "initial_capital_inr": "1000000",
        "max_concurrent_positions": 2,
        "max_position_weight": "0.50",
    }
    payload.update(overrides)
    return CompositionPortfolioRequest(**payload)


def seed_regime_benchmark(db, sessions, universe):
    for session_index, day in enumerate(sessions):
        close = Decimal("1000") + Decimal(session_index) * Decimal("1.50")
        db.add(
            IndexDailyPrice(
                id=uuid.uuid5(
                    PHASE9_GOLDEN_NAMESPACE,
                    f"regime-price:{universe.id}:{day}",
                ),
                index_id=universe.id,
                trading_date=day,
                open=close - Decimal("0.50"),
                high=close + Decimal("2"),
                low=close - Decimal("2"),
                close=close,
                source_mode="DEMO",
                source="PHASE11_TEST",
                available_at=datetime.combine(day, time(15, 30), tzinfo=IST),
            )
        )
    db.commit()


def test_phase9_golden_outputs_are_byte_stable_before_performance_changes(db):
    """Freeze representative Phase 9 identities and research outputs for Phase 10."""
    sessions, _, _ = seed_history(db)
    service = HistoricalCompositionService(db)
    prepared = service.prepare(backtest_request(sessions))
    backtest = service.run_backtest(backtest_request(sessions))
    portfolio = service.run_portfolio(portfolio_request(sessions))

    assert prepared.dataset_fingerprint == (
        "0690382e07d5d811ecdacc8a8b9a9e519069f8ebc309b009e12fafb2a687609a"
    )
    assert prepared.signal_fingerprint == (
        "8aba5569eb08304fa5860aa841c8e6f3bccc6a82afe80e4fb04a567578fef58f"
    )
    assert (len(prepared.outcomes), len(prepared.setups)) == (96, 93)
    assert prepared.outcomes[0].result_fingerprint == (
        "3a693ae86d60c18cdaa15db6540f4b75de02faf8e8143e4f0e7c48e82dda113a"
    )
    assert prepared.outcomes[-1].result_fingerprint == (
        "f9c3090294741445c914cede1149c717f87270495363dd089f602c391561118a"
    )
    assert prepared.setups[0].signal_result_fingerprint == (
        "d0056d8780c4815c54f00390259ef808d816effb51c499fce46fdad6fdfe4061"
    )
    assert prepared.setups[-1].signal_result_fingerprint == (
        "f9c3090294741445c914cede1149c717f87270495363dd089f602c391561118a"
    )

    assert backtest.backtest_config_fingerprint == (
        "5108a58594e7193f5e1c5dea7b6c1a0199f53076c209b090cec596ec33bcb5fe"
    )
    assert backtest.backtest_run_fingerprint == (
        "0f809d3bb50df5582eb908f377064d82500489830be719355409702d8bf30a24"
    )
    assert backtest.total_trade_count == 24
    assert [backtest.trades[0].trade_fingerprint, backtest.trades[-1].trade_fingerprint] == [
        "ab1aa592a6180741a556411239afb5911590416a888d62a77dd3e16c6ce18144",
        "823ce30427b607122c729d97129960acdc3c3d288178fbb0426f4a3ed427580a",
    ]
    assert backtest.cost_analytics.total == Decimal("5360.69")
    assert backtest.analytics.total_net_pnl == Decimal("11210.71")
    assert backtest.analytics.average_net_return == Decimal("0.00467518")

    assert portfolio.portfolio_config_fingerprint == (
        "321543572a599705a0f5e9db01d19dd74239bcc06b1332818f0d1e1a9782f917"
    )
    assert portfolio.portfolio_run_fingerprint == (
        "4c15c5cb6d4047909ae007779b7bc8c43f7e14688f9588ac4a54669c58eb5fce"
    )
    assert (portfolio.total_position_count, portfolio.total_ledger_event_count) == (20, 81)
    assert [
        portfolio.positions[0].position_fingerprint,
        portfolio.positions[-1].position_fingerprint,
    ] == [
        "8622df8380b0aeae86834b777b55d87b01534212508533e950ddd86caf6f11f8",
        "c4c1e5cb1aa8136cfa4b081c05f953ec56ac936c73ab85bdccf97281c8eb8893",
    ]
    assert len(portfolio.daily_equity_curve) == 32
    assert portfolio.daily_equity_curve[-1].portfolio_equity == Decimal("1051627.18")
    assert portfolio.metrics.total_portfolio_return == Decimal("0.05162718")
    assert portfolio.metrics.sharpe_ratio == Decimal("22.78150219")
    assert portfolio.cost_analytics.total == Decimal("22835.52")


def test_shared_prepared_context_preserves_complete_backtest_and_portfolio_outputs(db):
    sessions, _, _ = seed_history(db)
    backtest_input = backtest_request(sessions)
    portfolio_input = portfolio_request(sessions)
    service = HistoricalCompositionService(db)

    independent_backtest = service.run_backtest(backtest_input)
    independent_portfolio = service.run_portfolio(portfolio_input)
    prepared = service.prepare(backtest_input)
    shared_backtest = service.run_backtest_prepared(backtest_input, prepared)
    shared_portfolio = service.run_portfolio_prepared(portfolio_input, prepared)

    non_deterministic_fields = {"executed_at", "timings"}
    assert shared_backtest.model_dump(exclude=non_deterministic_fields) == (
        independent_backtest.model_dump(exclude=non_deterministic_fields)
    )
    assert shared_portfolio.model_dump(exclude=non_deterministic_fields) == (
        independent_portfolio.model_dump(exclude=non_deterministic_fields)
    )
    assert shared_backtest.historical_signal_fingerprint == (
        shared_portfolio.historical_signal_fingerprint
    )


def test_regime_attribution_reuses_signal_date_and_reconciles_authoritative_trades(db):
    sessions, universe, _ = seed_history(db)
    seed_regime_benchmark(db, sessions, universe)
    request = backtest_request(sessions)
    historical = HistoricalCompositionService(db)
    baseline = historical.run_backtest(request)
    baseline_portfolio = historical.run_portfolio(portfolio_request(sessions))
    prepared = historical.prepare(request)
    execution = historical.execute_backtest_prepared(request, prepared)

    attribution = MarketRegimeService(db).research_attribution(
        RegimeResearchAttributionRequest(
            backtest=request,
            benchmark=universe.symbol,
            source_mode="DEMO",
        )
    )
    regime_by_date = {
        item.observation_date: item.classification
        for item in attribution.regime_history.classifications
    }
    expected_trades = Counter(
        regime_by_date.get(trade.signal_date, "INSUFFICIENT_HISTORY")
        for trade in execution.trades
    )
    assert {
        item.classification: item.executed_trade_count for item in attribution.buckets
    } == {key: expected_trades[key] for key in (
        "TRENDING_BULL",
        "TRENDING_BEAR",
        "SIDEWAYS",
        "HIGH_VOLATILITY",
        "INSUFFICIENT_HISTORY",
    )}
    assert all(trade.signal_date < trade.entry_date for trade in execution.trades)
    assert attribution.trade_count_reconciled
    assert attribution.net_pnl_reconciled
    assert attribution.attributed_trade_count == baseline.total_trade_count
    assert attribution.attributed_net_pnl == baseline.analytics.total_net_pnl
    assert attribution.backtest.historical_signal_fingerprint == baseline.historical_signal_fingerprint
    assert attribution.backtest.backtest_run_fingerprint == baseline.backtest_run_fingerprint
    assert sum(item.signal_count for item in attribution.buckets) == len(prepared.setups)
    assert attribution.overall.executed_trade_count == baseline.total_trade_count
    after_portfolio = historical.run_portfolio(portfolio_request(sessions))
    assert after_portfolio.portfolio_run_fingerprint == baseline_portfolio.portfolio_run_fingerprint
    assert sum(item.winning_trade_count for item in attribution.buckets) == attribution.overall.winning_trade_count
    assert sum(item.losing_trade_count for item in attribution.buckets) == attribution.overall.losing_trade_count


def test_regime_definition_version_changes_only_overlay_fingerprint(db):
    sessions, universe, _ = seed_history(db)
    seed_regime_benchmark(db, sessions, universe)
    service = MarketRegimeService(db)
    baseline_request = RegimeResearchAttributionRequest(
        backtest=backtest_request(sessions),
        benchmark=universe.symbol,
        source_mode="DEMO",
    )
    baseline = service.research_attribution(baseline_request)
    variant = replace(
        MARKET_REGIME_4_STATE_V1,
        version="TEST_VARIANT",
        volatility_percentile=Decimal("0.75"),
    )
    REGIME_DEFINITIONS[(variant.code, variant.version)] = variant
    try:
        changed = service.research_attribution(
            baseline_request.model_copy(
                update={"regime_definition_version": variant.version}
            )
        )
    finally:
        REGIME_DEFINITIONS.pop((variant.code, variant.version), None)
    assert changed.regime_attribution_fingerprint != baseline.regime_attribution_fingerprint
    assert changed.regime_history.regime_timeline_fingerprint != baseline.regime_history.regime_timeline_fingerprint
    assert changed.backtest.historical_signal_fingerprint == baseline.backtest.historical_signal_fingerprint
    assert changed.backtest.backtest_run_fingerprint == baseline.backtest.backtest_run_fingerprint


def test_regime_attribution_saved_inline_order_and_repeated_runs_are_identical(db):
    sessions, universe, _ = seed_history(db)
    seed_regime_benchmark(db, sessions, universe)
    research = ResearchService(db)
    created = research.create_experiment(
        ExperimentCreateRequest(
            name="Phase 11 saved attribution source",
            composition=inline_composition(sessions),
        )
    )
    service = MarketRegimeService(db)
    inline_request = RegimeResearchAttributionRequest(
        backtest=backtest_request(sessions),
        benchmark=universe.symbol,
        source_mode="DEMO",
    )
    reversed_request = RegimeResearchAttributionRequest(
        backtest=backtest_request(
            sessions,
            source={
                "inline_composition": inline_composition(
                    sessions, components=_components(reverse=True)
                )
            },
        ),
        benchmark=universe.symbol,
        source_mode="DEMO",
    )
    saved_request = RegimeResearchAttributionRequest(
        backtest=backtest_request(
            sessions, source={"experiment_id": created.experiment.id}
        ),
        benchmark=universe.symbol,
        source_mode="DEMO",
    )
    inline = service.research_attribution(inline_request)
    repeated = service.research_attribution(inline_request)
    reordered = service.research_attribution(reversed_request)
    saved = service.research_attribution(saved_request)

    assert inline.regime_attribution_fingerprint == repeated.regime_attribution_fingerprint
    assert inline.regime_attribution_fingerprint == reordered.regime_attribution_fingerprint
    assert inline.regime_attribution_fingerprint == saved.regime_attribution_fingerprint
    assert inline.backtest.historical_signal_fingerprint == saved.backtest.historical_signal_fingerprint
    assert inline.backtest.backtest_run_fingerprint == saved.backtest.backtest_run_fingerprint


def test_regime_attribution_is_invariant_to_row_order_and_ineligible_future_data(db, monkeypatch):
    sessions, universe, securities = seed_history(db)
    seed_regime_benchmark(db, sessions, universe)
    request = RegimeResearchAttributionRequest(
        backtest=backtest_request(sessions),
        benchmark=universe.symbol,
        source_mode="DEMO",
    )
    canonical_service = MarketRegimeService(db)
    canonical = canonical_service.research_attribution(request)

    reordered_service = MarketRegimeService(db)
    original = reordered_service.indices.price_history
    monkeypatch.setattr(
        reordered_service.indices,
        "price_history",
        lambda *args, **kwargs: list(reversed(original(*args, **kwargs))),
    )
    reordered = reordered_service.research_attribution(request)
    assert reordered.regime_attribution_fingerprint == canonical.regime_attribution_fingerprint

    future_security = _security("P9FUTURE")
    db.add(future_security)
    db.flush()
    db.add(
        IndexMembership(
            index_id=universe.id,
            security_id=future_security.id,
            valid_from=sessions[-1] + timedelta(days=1),
            source="PHASE11_FUTURE",
        )
    )
    db.add(
        CorporateAction(
            security_id=securities[0].id,
            action_type="STOCK_SPLIT",
            announcement_date=sessions[-1] + timedelta(days=1),
            ex_date=sessions[-1] + timedelta(days=5),
            ratio_numerator=2,
            ratio_denominator=1,
            source="PHASE11_FUTURE",
            available_at=datetime.combine(
                sessions[-1] + timedelta(days=1), time(15, 30), tzinfo=IST
            ),
        )
    )
    db.commit()
    after = MarketRegimeService(db).research_attribution(request)
    assert after.regime_attribution_fingerprint == canonical.regime_attribution_fingerprint
    assert after.backtest.historical_signal_fingerprint == canonical.backtest.historical_signal_fingerprint
    assert after.backtest.backtest_run_fingerprint == canonical.backtest.backtest_run_fingerprint


def test_regime_attribution_api_returns_additive_overlay(db, client):
    sessions, universe, _ = seed_history(db)
    seed_regime_benchmark(db, sessions, universe)
    payload = RegimeResearchAttributionRequest(
        backtest=backtest_request(sessions),
        benchmark=universe.symbol,
        source_mode="DEMO",
    ).model_dump(mode="json")
    response = client.post("/api/v1/regimes/research-attribution", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["trade_count_reconciled"] is True
    assert body["net_pnl_reconciled"] is True
    assert len(body["regime_attribution_fingerprint"]) == 64


def test_range_composition_generates_diagnostics_and_next_open_trades(db):
    sessions, _, securities = seed_history(db)
    result = HistoricalCompositionService(db).run_backtest(backtest_request(sessions))
    assert result.diagnostics.eligible_evaluations == len(securities) * 32
    assert result.diagnostics.matched_setups > 0
    assert result.diagnostics.non_matches == 0
    assert result.diagnostics.insufficient_history == len(securities)
    assert result.executed_trade_count > 0
    assert result.skipped_setup_reasons["ALREADY_OPEN_POSITION"] > 0
    assert all(
        trade.entry_date == sessions[sessions.index(trade.signal_date) + 1]
        for trade in result.trades
    )
    assert all(
        trade.holding_sessions == 3
        for trade in result.trades
        if trade.exit_reason == "TIME_EXIT"
    )
    assert all(trade.holding_sessions <= 3 for trade in result.trades)
    assert result.execution_policy.policy_code == "COMPOSITION_NEXT_OPEN_FIXED_HOLD"
    assert result.execution_policy.default_holding_sessions == 20


def test_historical_status_matches_phase7_single_date_and_features_match_phase2(db):
    sessions, _, securities = seed_history(db)
    historical = HistoricalCompositionService(db).prepare(backtest_request(sessions))
    sample_day = sessions[205]
    point_request = inline_composition(
        sessions,
        observation_date=sample_day,
        as_of=datetime.combine(sample_day, time(15, 30), tzinfo=IST),
    )
    point = ResearchService(db).evaluate(point_request)
    historical_status = {
        item.symbol: item.status
        for item in historical.outcomes
        if item.observation_date == sample_day
    }
    assert historical_status == {item.symbol: item.status for item in point.results}

    phase2 = TechnicalFeatureService(db).compute(
        securities[0],
        start_date=sample_day,
        end_date=sample_day,
        adjustment_policy="RAW",
        as_of=datetime.combine(sample_day, time(15, 30), tzinfo=IST),
        feature_set=STRATEGY_TECHNICAL_SET,
    )
    range_values = historical.series_by_security[securities[0].id].observations[
        sample_day
    ].values
    assert range_values == {
        code: phase2.items[0].values[code]
        for code in historical.resolved.union_feature_codes
    }


def test_full_historical_feature_frame_matches_phase2_on_multiple_dates(db):
    sessions, _, securities = seed_history(db)
    calendar = {
        (item.exchange, item.trading_date): item
        for item in db.scalars(select(TradingCalendar)).all()
    }
    batch = HistoricalTechnicalSeriesService(db).compute_batch(
        [securities[0]],
        start_date=sessions[198],
        end_date=sessions[-1],
        adjustment_policy="RAW",
        feature_set=STRATEGY_TECHNICAL_SET,
        calendar_entries=calendar,
    )
    representative = STRATEGY_TECHNICAL_SET.feature_codes
    for sample_day in (sessions[199], sessions[205], sessions[-1]):
        point = TechnicalFeatureService(db).compute(
            securities[0],
            start_date=sample_day,
            end_date=sample_day,
            adjustment_policy="RAW",
            as_of=datetime.combine(sample_day, time(15, 30), tzinfo=IST),
            feature_set=STRATEGY_TECHNICAL_SET,
        )
        values = batch.series[0].observations[sample_day].values
        assert {code: values[code] for code in representative} == {
            code: point.items[0].values[code] for code in representative
        }


def test_component_order_and_repeated_run_are_deterministic(db):
    sessions, _, _ = seed_history(db)
    service = HistoricalCompositionService(db)
    first = service.run_backtest(backtest_request(sessions))
    reversed_source = inline_composition(sessions, components=_components(reverse=True))
    second = service.run_backtest(
        backtest_request(sessions, source={"inline_composition": reversed_source})
    )
    repeated = service.run_backtest(backtest_request(sessions))
    assert first.source.composition_config_fingerprint == second.source.composition_config_fingerprint
    assert first.historical_signal_fingerprint == second.historical_signal_fingerprint
    assert [item.signal_date for item in first.trades] == [
        item.signal_date for item in second.trades
    ]
    assert first.backtest_run_fingerprint == repeated.backtest_run_fingerprint


def test_repository_membership_row_order_does_not_change_results(db, monkeypatch):
    sessions, _, _ = seed_history(db)
    canonical = HistoricalCompositionService(db).prepare(backtest_request(sessions))
    reordered_service = HistoricalCompositionService(db)
    original = reordered_service.indices.memberships_between
    monkeypatch.setattr(
        reordered_service.indices,
        "memberships_between",
        lambda *args, **kwargs: list(reversed(original(*args, **kwargs))),
    )
    reordered = reordered_service.prepare(backtest_request(sessions))
    assert reordered.dataset_fingerprint == canonical.dataset_fingerprint
    assert reordered.signal_fingerprint == canonical.signal_fingerprint
    assert reordered.outcomes == canonical.outcomes


def test_saved_experiment_and_inline_sources_share_signal_without_persistence(db):
    sessions, _, _ = seed_history(db)
    research = ResearchService(db)
    created = research.create_experiment(
        ExperimentCreateRequest(
            name="Phase 9 immutable source",
            composition=inline_composition(sessions),
        )
    )
    run_count = db.scalar(select(func.count()).select_from(ResearchExperimentRun))
    inline = HistoricalCompositionService(db).run_backtest(backtest_request(sessions))
    saved = HistoricalCompositionService(db).run_backtest(
        backtest_request(
            sessions,
            source={"experiment_id": created.experiment.id},
        )
    )
    assert saved.source.source_type == "SAVED_EXPERIMENT"
    assert saved.source.composition_config_fingerprint == created.experiment.config_fingerprint
    assert saved.historical_signal_fingerprint == inline.historical_signal_fingerprint
    assert db.scalar(select(func.count()).select_from(ResearchExperimentRun)) == run_count
    assert db.scalar(select(func.count()).select_from(ResearchExperiment)) == 1


def test_signal_and_execution_fingerprint_boundaries(db):
    sessions, _, _ = seed_history(db)
    service = HistoricalCompositionService(db)
    hold_three = service.run_backtest(backtest_request(sessions, holding_sessions=3))
    hold_five = service.run_backtest(backtest_request(sessions, holding_sessions=5))
    assert hold_three.historical_signal_fingerprint == hold_five.historical_signal_fingerprint
    assert hold_three.backtest_config_fingerprint != hold_five.backtest_config_fingerprint
    assert hold_three.backtest_run_fingerprint != hold_five.backtest_run_fingerprint

    capital_one = service.run_portfolio(portfolio_request(sessions, initial_capital_inr="1000000"))
    capital_two = service.run_portfolio(portfolio_request(sessions, initial_capital_inr="2000000"))
    assert capital_one.historical_signal_fingerprint == capital_two.historical_signal_fingerprint
    assert capital_one.portfolio_config_fingerprint != capital_two.portfolio_config_fingerprint
    assert capital_one.portfolio_run_fingerprint != capital_two.portfolio_run_fingerprint


def test_n_and_strategy_parameter_change_signal_fingerprint(db):
    sessions, _, _ = seed_history(db)
    service = HistoricalCompositionService(db)
    base = service.run_backtest(backtest_request(sessions))
    n_three_source = inline_composition(sessions, required_match_count=3)
    n_three = service.run_backtest(
        backtest_request(sessions, source={"inline_composition": n_three_source})
    )
    changed_source = inline_composition(
        sessions, components=_components(momentum_floor="0.50")
    )
    changed = service.run_backtest(
        backtest_request(sessions, source={"inline_composition": changed_source})
    )
    assert base.historical_signal_fingerprint != n_three.historical_signal_fingerprint
    assert base.historical_signal_fingerprint != changed.historical_signal_fingerprint


def test_future_price_membership_and_action_revision_do_not_change_closed_range(db):
    sessions, universe, securities = seed_history(db)
    service = HistoricalCompositionService(db)
    request = backtest_request(sessions, end_date=sessions[-2])
    before = service.run_backtest(request)
    future_day = sessions[-1] + timedelta(days=1)
    while future_day.weekday() >= 5:
        future_day += timedelta(days=1)
    future_security = _security("P9FUTURE")
    db.add(future_security)
    db.flush()
    db.add_all(
        [
            IndexMembership(
                index_id=universe.id,
                security_id=future_security.id,
                valid_from=future_day,
                source="FUTURE",
            ),
            DailyPrice(
                security_id=securities[0].id,
                trading_date=future_day,
                open=999,
                high=1001,
                low=998,
                close=1000,
                volume=1,
                source="FUTURE",
            ),
            CorporateAction(
                security_id=securities[0].id,
                action_type="STOCK_SPLIT",
                ex_date=sessions[-5],
                ratio_numerator=2,
                ratio_denominator=1,
                source="FUTURE_REVISION",
                available_at=datetime.combine(future_day, time(10), tzinfo=IST),
            ),
        ]
    )
    db.commit()
    after = service.run_backtest(request)
    assert after.historical_dataset_fingerprint == before.historical_dataset_fingerprint
    assert after.historical_signal_fingerprint == before.historical_signal_fingerprint
    assert after.backtest_run_fingerprint == before.backtest_run_fingerprint


def test_raw_adjusted_signals_are_separate_and_future_action_is_pit_safe(db):
    sessions, _, securities = seed_history(db)
    db.add(
        CorporateAction(
            security_id=securities[0].id,
            action_type="STOCK_SPLIT",
            ex_date=sessions[205],
            ratio_numerator=2,
            ratio_denominator=1,
            source="KNOWN",
            available_at=datetime.combine(sessions[204], time(10), tzinfo=IST),
        )
    )
    db.commit()
    service = HistoricalCompositionService(db)
    raw = service.run_backtest(backtest_request(sessions, adjustment_policy="RAW"))
    adjusted = service.run_backtest(
        backtest_request(sessions, adjustment_policy="ADJUSTED")
    )
    assert raw.historical_dataset_fingerprint != adjusted.historical_dataset_fingerprint
    assert raw.historical_signal_fingerprint != adjusted.historical_signal_fingerprint
    assert all(trade.adjustment_policy == "RAW" for trade in raw.trades)
    assert all(trade.raw_entry_price > 0 for trade in adjusted.trades)


def test_backtest_costs_and_force_close_reuse_phase5(db):
    sessions, _, _ = seed_history(db)
    result = HistoricalCompositionService(db).run_backtest(
        backtest_request(
            sessions,
            holding_sessions=20,
            brokerage_per_order_inr="10",
        )
    )
    assert any(item.exit_reason == "FORCED_END_OF_TEST" for item in result.trades)
    total = sum(
        (
            item.entry_cost.total_charges
            + (item.exit_cost.total_charges if item.exit_cost else Decimal(0))
        )
        for item in result.trades
    )
    assert result.cost_analytics.total == total
    calculator = IndiaCashDeliveryCostCalculator(
        result.cost_model,
        brokerage_per_order_inr=Decimal("10"),
        brokerage_rate=Decimal(0),
        dp_charge_per_scrip_sell_day_inr=Decimal(0),
    )
    assert result.trades[0].entry_cost == calculator.calculate(
        result.trades[0].deployed_notional, "BUY"
    )


def test_missing_immediate_next_open_is_skipped_without_later_fill(db):
    sessions, _, securities = seed_history(db)
    missing = db.scalar(
        select(DailyPrice).where(
            DailyPrice.security_id == securities[0].id,
            DailyPrice.trading_date == sessions[200],
        )
    )
    db.delete(missing)
    db.commit()
    result = HistoricalCompositionService(db).run_backtest(backtest_request(sessions))
    assert result.skipped_setup_reasons["ENTRY_PRICE_UNAVAILABLE"] >= 1
    assert not any(
        item.symbol == securities[0].symbol
        and item.signal_date == sessions[199]
        for item in result.trades
    )


def test_composition_trade_stop_and_corporate_action_paths_reuse_phase5(db):
    sessions, _, securities = seed_history(db)
    stop_day = db.scalar(
        select(DailyPrice).where(
            DailyPrice.security_id == securities[0].id,
            DailyPrice.trading_date == sessions[200],
        )
    )
    stop_day.low = stop_day.open * Decimal("0.80")
    db.add(
        CorporateAction(
            security_id=securities[1].id,
                action_type="BONUS",
            ex_date=sessions[202],
            ratio_numerator=1,
            ratio_denominator=1,
            source="PHASE9_KNOWN",
            available_at=datetime.combine(sessions[198], time(10), tzinfo=IST),
        )
    )
    db.commit()
    result = HistoricalCompositionService(db).run_backtest(
        backtest_request(sessions, holding_sessions=5, stop_loss_pct="0.05")
    )
    assert any(
        item.symbol == securities[0].symbol and item.exit_reason == "STOP_LOSS"
        for item in result.trades
    )
    assert any(
        item.symbol == securities[1].symbol and item.corporate_action_events
        for item in result.trades
    )


def test_portfolio_uses_same_signal_stream_and_phase6_ledger(db):
    sessions, _, _ = seed_history(db)
    service = HistoricalCompositionService(db)
    backtest = service.run_backtest(backtest_request(sessions))
    portfolio = service.run_portfolio(
        portfolio_request(
            sessions,
            max_concurrent_positions=1,
            max_position_weight="1",
        )
    )
    assert portfolio.historical_signal_fingerprint == backtest.historical_signal_fingerprint
    assert portfolio.rejected_candidate_reasons["PORTFOLIO_CAPACITY_REACHED"] > 0
    assert portfolio.metrics.maximum_open_positions == 1
    assert portfolio.closed_position_count == portfolio.total_position_count
    assert portfolio.ledger_events[-1].resulting_cash_balance == portfolio.metrics.ending_equity
    assert all(item.initial_quantity >= 1 for item in portfolio.positions)


def test_same_open_exit_releases_capital_for_composition_reentry(db):
    sessions, _, securities = seed_history(db)
    portfolio = HistoricalCompositionService(db).run_portfolio(
        portfolio_request(
            sessions,
            holding_sessions=1,
            max_concurrent_positions=3,
            max_position_weight="0.34",
        )
    )
    security_positions = sorted(
        (item for item in portfolio.positions if item.security_id == securities[0].id),
        key=lambda item: item.entry_date,
    )
    assert any(
        previous.exit_date == following.entry_date
        for previous, following in zip(security_positions, security_positions[1:])
    )


def test_intraday_exit_does_not_retroactively_fund_open_candidates(db):
    sessions, _, securities = seed_history(db)
    stop_day = db.scalar(
        select(DailyPrice).where(
            DailyPrice.security_id == securities[0].id,
            DailyPrice.trading_date == sessions[200],
        )
    )
    stop_day.low = stop_day.open * Decimal("0.80")
    db.commit()
    portfolio = HistoricalCompositionService(db).run_portfolio(
        portfolio_request(
            sessions,
            holding_sessions=5,
            stop_loss_pct="0.05",
            max_concurrent_positions=1,
            max_position_weight="1",
        )
    )
    assert any(item.exit_reason == "STOP_LOSS" for item in portfolio.positions)
    rejected_on_stop_day = [
        item
        for item in portfolio.rejected_candidates
        if item.intended_entry_date == sessions[200]
    ]
    assert rejected_on_stop_day
    assert all(
        item.reason == "PORTFOLIO_CAPACITY_REACHED"
        for item in rejected_on_stop_day
    )
    assert all(event.resulting_cash_balance >= 0 for event in portfolio.ledger_events)


def test_historical_analysis_has_bounded_query_growth(db):
    sessions, _, _ = seed_history(db, security_count=51)
    selects = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(db.bind, "before_cursor_execute", count_selects)
    try:
        HistoricalCompositionService(db).prepare(backtest_request(sessions))
    finally:
        event.remove(db.bind, "before_cursor_execute", count_selects)
    assert selects <= 10


def test_validation_and_unknown_saved_experiment_are_typed(db, client):
    sessions, _, _ = seed_history(db)
    with pytest.raises(ValidationError):
        CompositionBacktestRequest(
            source={
                "inline_composition": inline_composition(sessions),
                "experiment_id": uuid.uuid4(),
            },
            universe="P9HIST",
            start_date=sessions[-1],
            end_date=sessions[0],
        )
    payload = backtest_request(
        sessions, source={"experiment_id": uuid.uuid4()}
    ).model_dump(mode="json")
    response = client.post("/api/v1/research/backtest", json=payload)
    assert response.status_code == 404
    assert response.json() == {"detail": "Research experiment not found"}

    invalid_strategy = inline_composition(
        sessions,
        components=[
            {"strategy_code": "MOMENTUM_TREND", "strategy_version": "999"},
            {"strategy_code": "BREAKOUT_20D", "strategy_version": "1"},
        ],
    )
    invalid_payload = backtest_request(
        sessions, source={"inline_composition": invalid_strategy}
    ).model_dump(mode="json")
    invalid_response = client.post("/api/v1/research/backtest", json=invalid_payload)
    assert invalid_response.status_code == 404
    assert "Unsupported strategy version" in invalid_response.json()["detail"]


def test_read_only_api_endpoints_return_canonical_fingerprints(db, client):
    sessions, _, _ = seed_history(db)
    before = db.scalar(select(func.count()).select_from(ResearchExperimentRun))
    backtest = client.post(
        "/api/v1/research/backtest",
        json=backtest_request(sessions).model_dump(mode="json"),
    )
    portfolio = client.post(
        "/api/v1/research/portfolio",
        json=portfolio_request(sessions).model_dump(mode="json"),
    )
    assert backtest.status_code == 200
    assert portfolio.status_code == 200
    assert (
        backtest.json()["historical_signal_fingerprint"]
        == portfolio.json()["historical_signal_fingerprint"]
    )
    assert db.scalar(select(func.count()).select_from(ResearchExperimentRun)) == before


def test_official_membership_coverage_is_not_backcast(db):
    sessions, _, _ = seed_history(db)
    official = MarketIndex(
        name="Official current snapshot",
        symbol="P9OFFICIAL",
        provider="OFFICIAL_NSE_INDICES_PUBLIC",
        exchange="NSE",
    )
    security = _security("P9OFF")
    db.add_all([official, security])
    db.flush()
    db.add(
        IndexMembership(
            index_id=official.id,
            security_id=security.id,
            valid_from=sessions[210],
            source="OFFICIAL",
        )
    )
    db.commit()
    request = backtest_request(
        sessions,
        universe="P9OFFICIAL",
        start_date=sessions[198],
    )
    with pytest.raises(ValueError, match="HISTORICAL_MEMBERSHIP_COVERAGE_INCOMPLETE"):
        HistoricalCompositionService(db).run_backtest(request)


def test_feature_registries_and_ema_200_remain_unchanged():
    assert len(STRATEGY_TECHNICAL_SET.feature_codes) == 38
    assert "EMA_200" not in STRATEGY_TECHNICAL_SET.feature_codes
