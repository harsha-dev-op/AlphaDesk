from __future__ import annotations

import time
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.backtests.costs import (
    IndiaCashDeliveryCostCalculator,
    aggregate_cost_breakdowns,
    cost_model_metadata,
)
from app.backtests.fingerprints import fingerprint
from app.backtests.registry import cost_model_catalog, profile_catalog
from app.backtests.service import BacktestService, profile_metadata
from app.portfolio.analytics import calculate_portfolio_metrics, calculate_segment_metrics
from app.portfolio.definitions import PortfolioPolicyDefinition
from app.portfolio.registry import find_portfolio_policy, portfolio_policy_catalog
from app.portfolio.simulator import PortfolioSimulationSettings, PortfolioSimulator
from app.schemas.backtests import AggregateCostBreakdown, BacktestRunRequest, DatasetMetadata, ParameterBounds
from app.schemas.portfolio import (
    PortfolioMetadataResponse,
    PortfolioPolicyMetadata,
    PortfolioPosition,
    PortfolioRunRequest,
    PortfolioRunResponse,
    PortfolioTimings,
)
from app.schemas.scanner import ScannerUniverseMetadata
from app.strategies.registry import strategy_catalog
from app.strategies.service import strategy_metadata

RESEARCH_DISCLAIMER = (
    "Capital-aware historical portfolio simulation for research only — not investment advice. "
    "One finite cash pool, no leverage, no broker connection, and no order execution."
)
HARD_LIMITS = {
    "MAX_CALENDAR_DAYS": 3650,
    "MAX_TRADING_SESSIONS": 3000,
    "MAX_UNIVERSE_SECURITIES": 500,
    "MAX_POSITION_DETAIL_LIMIT": 5000,
    "MAX_LEDGER_DETAIL_LIMIT": 10000,
}


class PortfolioValidationError(ValueError):
    pass


class PortfolioNotFoundError(ValueError):
    pass


def _policy_payload(policy: PortfolioPolicyDefinition) -> dict[str, object]:
    return {
        "policy_code": policy.policy_code,
        "policy_version": policy.policy_version,
        "compatible_profiles": policy.compatible_profiles,
        "default_initial_capital_inr": policy.default_initial_capital_inr,
        "default_max_concurrent_positions": policy.default_max_concurrent_positions,
        "default_max_position_weight": policy.default_max_position_weight,
        "default_max_gross_exposure": policy.default_max_gross_exposure,
        "default_minimum_cash_reserve_pct": policy.default_minimum_cash_reserve_pct,
        "allocation_policy": policy.allocation_policy,
        "candidate_selection_policy": policy.candidate_selection_policy,
        "integer_share_policy": policy.integer_share_policy,
        "same_security_policy": policy.same_security_policy,
        "same_session_event_order": policy.same_session_event_order,
        "end_policy": policy.end_policy,
        "mark_to_market_policy": policy.mark_to_market_policy,
        "risk_free_rate_convention": policy.risk_free_rate_convention,
        "leverage_policy": policy.leverage_policy,
        "rebalancing_policy": policy.rebalancing_policy,
    }


def policy_metadata(policy: PortfolioPolicyDefinition) -> PortfolioPolicyMetadata:
    return PortfolioPolicyMetadata(
        policy_code=policy.policy_code,
        policy_version=policy.policy_version,
        display_name=policy.display_name,
        compatible_profiles=list(policy.compatible_profiles),
        default_initial_capital_inr=policy.default_initial_capital_inr,
        default_max_concurrent_positions=policy.default_max_concurrent_positions,
        default_max_position_weight=policy.default_max_position_weight,
        default_max_gross_exposure=policy.default_max_gross_exposure,
        default_minimum_cash_reserve_pct=policy.default_minimum_cash_reserve_pct,
        allocation_policy=policy.allocation_policy,
        candidate_selection_policy=policy.candidate_selection_policy,
        integer_share_policy=policy.integer_share_policy,
        same_security_policy=policy.same_security_policy,
        same_session_event_order=list(policy.same_session_event_order),
        end_policy=policy.end_policy,
        mark_to_market_policy=policy.mark_to_market_policy,
        risk_free_rate_convention=policy.risk_free_rate_convention,
        leverage_policy=policy.leverage_policy,
        rebalancing_policy=policy.rebalancing_policy,
        policy_fingerprint=fingerprint(_policy_payload(policy)),
    )


def _aggregate_costs(positions: list[PortfolioPosition], *, broker_costs_excluded: bool) -> AggregateCostBreakdown:
    legs = [position.entry_costs for position in positions]
    legs.extend(position.exit_costs for position in positions if position.exit_costs is not None)
    return aggregate_cost_breakdowns(legs, broker_costs_excluded=broker_costs_excluded)


class PortfolioService:
    def __init__(self, session: Session):
        self.backtests = BacktestService(session)
        self.simulator = PortfolioSimulator()

    def metadata(self) -> PortfolioMetadataResponse:
        backtest_metadata = self.backtests.metadata()
        return PortfolioMetadataResponse(
            policies=[policy_metadata(policy) for policy in portfolio_policy_catalog()],
            profiles=[profile_metadata(profile) for profile in profile_catalog()],
            cost_models=[cost_model_metadata(model) for model in cost_model_catalog()],
            strategies=[strategy_metadata(definition) for definition in strategy_catalog()],
            universes=backtest_metadata.universes,
            adjustment_policies=["RAW", "ADJUSTED"],
            parameter_bounds={
                "initial_capital_inr": ParameterBounds(minimum=1, maximum=100000000000, default=1000000),
                "max_concurrent_positions": ParameterBounds(minimum=1, maximum=500, default=10),
                "max_position_weight": ParameterBounds(minimum=Decimal("0.00000001"), maximum=1, default=Decimal("0.10")),
                "max_gross_exposure": ParameterBounds(minimum=Decimal("0.00000001"), maximum=1, default=1),
                "minimum_cash_reserve_pct": ParameterBounds(minimum=0, maximum=Decimal("0.99999999"), default=0),
                "risk_free_rate_annual": ParameterBounds(minimum=Decimal("-0.99999999"), maximum=10, default=0),
                "holding_sessions": ParameterBounds(minimum=1, maximum=252, default=None),
                "slippage_bps": ParameterBounds(minimum=0, maximum=1000, default=5),
                "position_detail_limit": ParameterBounds(minimum=0, maximum=5000, default=500),
                "ledger_detail_limit": ParameterBounds(minimum=0, maximum=10000, default=1000),
            },
            allocation_semantics={
                "target": "session_start_equity / max_concurrent_positions",
                "caps": "position weight, gross exposure room, spendable cash after reserve",
                "affordability": "largest integer quantity whose principal plus exact buy charges fits budget",
                "oversubscription": "signal date then symbol ascending; no outcome-based ranking",
            },
            metric_definitions={
                "daily_return": "equity_t / equity_t-1 - 1",
                "cagr": "(ending / starting)^(365.2425 / elapsed calendar days) - 1",
                "volatility": "sample standard deviation of daily returns × sqrt(252)",
                "sharpe": "mean daily excess return / sample stddev daily excess return × sqrt(252)",
                "sortino": "mean daily excess return × sqrt(252) / downside deviation",
                "turnover": "gross executed entry plus exit turnover / average daily equity",
            },
            hard_request_limits=HARD_LIMITS,
            latest_observation_date=backtest_metadata.latest_observation_date,
            research_disclaimer=RESEARCH_DISCLAIMER,
        )

    @staticmethod
    def _resolve_policy(code: str, version: str) -> PortfolioPolicyDefinition:
        policy = find_portfolio_policy(code, version)
        if policy is not None:
            return policy
        if any(item.policy_code == code for item in portfolio_policy_catalog()):
            raise PortfolioNotFoundError(f"Unsupported portfolio policy version: {code} v{version}")
        raise PortfolioNotFoundError(f"Unknown portfolio policy: {code}")

    def run(self, request: PortfolioRunRequest) -> PortfolioRunResponse:
        started = time.perf_counter()
        policy = self._resolve_policy(request.portfolio_policy_code, request.portfolio_policy_version)
        backtest_request = BacktestRunRequest(
            strategy_code=request.strategy_code,
            strategy_version=request.strategy_version,
            parameter_overrides=request.parameter_overrides,
            universe=request.universe,
            start_date=request.start_date,
            end_date=request.end_date,
            adjustment_policy=request.adjustment_policy,
            profile_code=request.profile_code,
            profile_version=request.profile_version,
            holding_sessions=request.holding_sessions,
            slippage_bps=request.slippage_bps,
            stop_loss_pct=request.stop_loss_pct,
            profit_target_pct=request.profit_target_pct,
            max_exit_delay_sessions=request.max_exit_delay_sessions,
            cost_model_code=request.cost_model_code,
            cost_model_version=request.cost_model_version,
            brokerage_per_order_inr=request.brokerage_per_order_inr,
            brokerage_rate=request.brokerage_rate,
            dp_charge_per_scrip_sell_day_inr=request.dp_charge_per_scrip_sell_day_inr,
            out_of_sample_start_date=request.out_of_sample_start_date,
            trade_detail_limit=0,
        )
        prepared = self.backtests.prepare_research(backtest_request)
        if prepared.profile.profile_code not in policy.compatible_profiles:
            raise PortfolioValidationError("Portfolio policy is incompatible with the selected backtest profile")
        if len(prepared.securities) > HARD_LIMITS["MAX_UNIVERSE_SECURITIES"]:
            raise PortfolioValidationError("Portfolio universe exceeds 500 historical securities")
        if len(prepared.sessions) > HARD_LIMITS["MAX_TRADING_SESSIONS"]:
            raise PortfolioValidationError("Portfolio range exceeds 3000 trading sessions")

        normalized_request = request.model_copy(
            update={
                "holding_sessions": prepared.holding_sessions,
                "parameter_overrides": prepared.parameters,
            }
        )
        policy_fingerprint = fingerprint(_policy_payload(policy))
        config_fingerprint = fingerprint(
            {
                "strategy_fingerprint": prepared.strategy_fingerprint,
                "universe": request.universe,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "adjustment_policy": request.adjustment_policy,
                "profile_fingerprint": prepared.profile_fingerprint,
                "holding_sessions": prepared.holding_sessions,
                "slippage_bps": request.slippage_bps,
                "stop_loss_pct": request.stop_loss_pct,
                "profit_target_pct": request.profit_target_pct,
                "max_exit_delay_sessions": request.max_exit_delay_sessions,
                "cost_fingerprint": prepared.cost_fingerprint,
                "portfolio_policy_fingerprint": policy_fingerprint,
                "initial_capital_inr": request.initial_capital_inr,
                "max_concurrent_positions": request.max_concurrent_positions,
                "max_position_weight": request.max_position_weight,
                "max_gross_exposure": request.max_gross_exposure,
                "minimum_cash_reserve_pct": request.minimum_cash_reserve_pct,
                "candidate_selection_policy": policy.candidate_selection_policy,
                "risk_free_rate_annual": request.risk_free_rate_annual,
                "out_of_sample_start_date": request.out_of_sample_start_date,
            }
        )
        calculator = IndiaCashDeliveryCostCalculator(
            prepared.cost_definition,
            brokerage_per_order_inr=request.brokerage_per_order_inr,
            brokerage_rate=request.brokerage_rate,
            dp_charge_per_scrip_sell_day_inr=request.dp_charge_per_scrip_sell_day_inr,
        )

        simulation_started = time.perf_counter()
        simulation = self.simulator.simulate(
            prepared.setups,
            series_by_security=prepared.series_by_security,
            sessions=prepared.sessions,
            profile=prepared.profile,
            cost_definition=prepared.cost_definition,
            policy=policy,
            settings=PortfolioSimulationSettings(
                start_date=request.start_date,
                adjustment_policy=request.adjustment_policy,
                holding_sessions=prepared.holding_sessions,
                slippage_bps=request.slippage_bps,
                stop_loss_pct=request.stop_loss_pct,
                profit_target_pct=request.profit_target_pct,
                max_exit_delay_sessions=request.max_exit_delay_sessions,
                initial_capital=request.initial_capital_inr,
                max_concurrent_positions=request.max_concurrent_positions,
                max_position_weight=request.max_position_weight,
                max_gross_exposure=request.max_gross_exposure,
                minimum_cash_reserve_pct=request.minimum_cash_reserve_pct,
                profile_fingerprint=prepared.profile_fingerprint,
                cost_fingerprint=prepared.cost_fingerprint,
                policy_fingerprint=policy_fingerprint,
            ),
            costs=calculator,
        )
        simulation_ms = (time.perf_counter() - simulation_started) * 1_000

        analytics_started = time.perf_counter()
        positions = list(simulation.positions)
        snapshots = list(simulation.snapshots)
        metrics = calculate_portfolio_metrics(
            snapshots,
            positions,
            initial_capital=request.initial_capital_inr,
            annual_risk_free_rate=request.risk_free_rate_annual,
        )
        oos_metrics = []
        if request.out_of_sample_start_date is not None:
            oos_metrics = [
                calculate_segment_metrics(
                    "IN_SAMPLE",
                    [item for item in snapshots if item.session_date < request.out_of_sample_start_date],
                    annual_risk_free_rate=request.risk_free_rate_annual,
                ),
                calculate_segment_metrics(
                    "OUT_OF_SAMPLE",
                    [item for item in snapshots if item.session_date >= request.out_of_sample_start_date],
                    annual_risk_free_rate=request.risk_free_rate_annual,
                ),
            ]
        broker_costs_excluded = (
            request.brokerage_per_order_inr == 0
            and request.brokerage_rate == 0
            and request.dp_charge_per_scrip_sell_day_inr == 0
        )
        aggregate_costs = _aggregate_costs(positions, broker_costs_excluded=broker_costs_excluded)
        analytics_ms = (time.perf_counter() - analytics_started) * 1_000

        ledger_fingerprint = fingerprint([item.model_dump(mode="json") for item in simulation.ledger_events])
        daily_equity_fingerprint = fingerprint([item.model_dump(mode="json") for item in snapshots])
        run_fingerprint = fingerprint(
            {
                "config_fingerprint": config_fingerprint,
                "dataset_fingerprint": prepared.dataset_fingerprint,
                "accepted_positions": [item.position_fingerprint for item in positions],
                "rejected_candidates": [item.candidate_fingerprint for item in simulation.rejected_candidates],
                "cash_ledger_fingerprint": ledger_fingerprint,
                "daily_equity_fingerprint": daily_equity_fingerprint,
            }
        )
        rejection_counts = dict(sorted(Counter(item.reason for item in simulation.rejected_candidates).items()))
        position_preview = positions[: request.position_detail_limit]
        ledger_preview = list(simulation.ledger_events[: request.ledger_detail_limit])
        rejected_preview = list(simulation.rejected_candidates[: request.position_detail_limit])
        warnings = sorted(
            {
                *simulation.warnings,
                *metrics.warnings,
                *(warning for item in prepared.series_batch.series for warning in item.quality_warnings),
                *(warning for item in oos_metrics for warning in item.warnings),
                *(["NO_SETUPS"] if not prepared.setups else []),
                *(["SETUPS_WITHOUT_ALLOCATED_POSITIONS"] if prepared.setups and not positions else []),
                *(["BROKER_SPECIFIC_COSTS_EXCLUDED"] if broker_costs_excluded else []),
                *(["POSITION_PREVIEW_TRUNCATED"] if len(position_preview) < len(positions) else []),
                *(["LEDGER_PREVIEW_TRUNCATED"] if len(ledger_preview) < len(simulation.ledger_events) else []),
                *(["REJECTED_CANDIDATE_PREVIEW_TRUNCATED"] if len(rejected_preview) < len(simulation.rejected_candidates) else []),
            }
        )
        response_started = time.perf_counter()
        response = PortfolioRunResponse(
            normalized_request=normalized_request,
            strategy=strategy_metadata(prepared.definition),
            effective_parameters=prepared.parameters,
            profile=profile_metadata(prepared.profile),
            cost_model=cost_model_metadata(
                prepared.cost_definition,
                brokerage_per_order_inr=request.brokerage_per_order_inr,
                brokerage_rate=request.brokerage_rate,
                dp_charge_per_scrip_sell_day_inr=request.dp_charge_per_scrip_sell_day_inr,
            ),
            portfolio_policy=policy_metadata(policy),
            universe=ScannerUniverseMetadata(
                id=prepared.market_index.id,
                symbol=prepared.market_index.symbol,
                name=prepared.market_index.name,
                provider=prepared.market_index.provider,
                exchange=prepared.market_index.exchange,
            ),
            dataset=DatasetMetadata(
                dataset_code=(
                    prepared.series_batch.ingestion_run.dataset_code
                    if prepared.series_batch.ingestion_run
                    else "UNVERSIONED_LOCAL"
                ),
                dataset_version=(
                    prepared.series_batch.ingestion_run.dataset_version
                    if prepared.series_batch.ingestion_run
                    else "unversioned"
                ),
                provider=(
                    prepared.series_batch.ingestion_run.provider
                    if prepared.series_batch.ingestion_run
                    else prepared.market_index.provider
                ),
                security_count=len(prepared.securities),
                price_row_count=sum(len(item.prices) for item in prepared.series_batch.series),
                corporate_action_revision_count=sum(
                    len(item.action_history) for item in prepared.series_batch.series
                ),
                membership_interval_count=len(prepared.memberships),
                trading_session_count=len(prepared.sessions),
            ),
            config_fingerprint=config_fingerprint,
            dataset_fingerprint=prepared.dataset_fingerprint,
            run_fingerprint=run_fingerprint,
            historical_sessions_processed=len(prepared.sessions),
            setup_count=len(prepared.setups),
            accepted_entry_count=len(positions),
            rejected_candidate_count=len(simulation.rejected_candidates),
            rejected_candidate_reasons=rejection_counts,
            closed_position_count=sum(item.net_pnl is not None for item in positions),
            metrics=metrics,
            cost_analytics=aggregate_costs,
            oos_metrics=oos_metrics,
            warnings=warnings,
            daily_equity_curve=snapshots,
            total_position_count=len(positions),
            returned_position_count=len(position_preview),
            positions_truncated=len(position_preview) < len(positions),
            positions=position_preview,
            total_ledger_event_count=len(simulation.ledger_events),
            returned_ledger_event_count=len(ledger_preview),
            ledger_truncated=len(ledger_preview) < len(simulation.ledger_events),
            ledger_events=ledger_preview,
            returned_rejected_candidate_count=len(rejected_preview),
            rejected_candidates_truncated=len(rejected_preview) < len(simulation.rejected_candidates),
            rejected_candidates=rejected_preview,
            timings=PortfolioTimings(
                data_load_ms=round(
                    prepared.universe_and_calendar_ms + prepared.series_batch.repository_load_ms,
                    3,
                ),
                technical_series_ms=round(prepared.series_batch.calculation_ms, 3),
                setup_generation_ms=round(prepared.condition_evaluation_ms, 3),
                allocation_and_accounting_ms=round(simulation_ms, 3),
                risk_analytics_ms=round(analytics_ms, 3),
                response_build_ms=0,
                total_service_ms=0,
            ),
            executed_at=datetime.now(UTC),
        )
        response.timings.response_build_ms = round((time.perf_counter() - response_started) * 1_000, 3)
        response.timings.total_service_ms = round((time.perf_counter() - started) * 1_000, 3)
        return response


__all__ = [
    "PortfolioNotFoundError",
    "PortfolioService",
    "PortfolioValidationError",
    "policy_metadata",
]
