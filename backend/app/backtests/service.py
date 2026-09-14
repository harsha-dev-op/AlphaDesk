from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.backtests.analytics import calculate_analytics, period_breakdown
from app.backtests.costs import (
    IndiaCashDeliveryCostCalculator,
    aggregate_cost_breakdowns,
    cost_fingerprint,
    cost_model_metadata,
)
from app.backtests.definitions import BacktestProfileDefinition, CostModelDefinition
from app.backtests.fingerprints import fingerprint
from app.backtests.registry import cost_model_catalog, find_cost_model, find_profile, profile_catalog
from app.backtests.simulator import SetupEvent, SimulationSettings, TradeSimulator
from app.models import IndexMembership, MarketIndex, Security, TradingCalendar
from app.repositories.calendar import TradingCalendarRepository
from app.repositories.indices import IndexRepository
from app.repositories.securities import SecurityRepository
from app.schemas.backtests import (
    AggregateCostBreakdown,
    BacktestMetadataResponse,
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestTimings,
    DatasetMetadata,
    ParameterBounds,
    ProfileMetadata,
    SimulatedTrade,
)
from app.schemas.scanner import ScannerUniverseMetadata
from app.strategies.registry import strategy_catalog
from app.strategies.definitions import ParameterValue, StrategyDefinition
from app.strategies.service import (
    StrategyService,
    _condition_passes,
    normalize_parameters,
    strategy_fingerprint,
    strategy_metadata,
)
from app.technical.definitions import FeatureSetDefinition
from app.technical.registry import FEATURE_DEFINITIONS, find_feature_set
from app.technical.series import HistoricalFeatureBatch, HistoricalSecuritySeries, HistoricalTechnicalSeriesService

RESEARCH_DISCLAIMER = (
    "Historical simulation for research only — not investment advice. "
    "Independent fixed-notional trades are not a capital-constrained portfolio backtest."
)
SAMPLE_WARNING_RULES = {"VERY_LOW_SAMPLE_SIZE_BELOW": 10, "LOW_SAMPLE_SIZE_BELOW": 30}
HARD_LIMITS = {"MAX_CALENDAR_DAYS": 3650, "MAX_TRADE_DETAIL_LIMIT": 5000, "MAX_EXIT_DELAY_SESSIONS": 20}


class BacktestValidationError(ValueError):
    pass


class BacktestNotFoundError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedBacktestResearch:
    request: BacktestRunRequest
    normalized_request: BacktestRunRequest
    definition: StrategyDefinition
    feature_set: FeatureSetDefinition
    parameters: dict[str, ParameterValue]
    profile: BacktestProfileDefinition
    cost_definition: CostModelDefinition
    holding_sessions: int
    strategy_fingerprint: str
    profile_fingerprint: str
    cost_fingerprint: str
    config_fingerprint: str
    market_index: MarketIndex
    memberships: tuple[IndexMembership, ...]
    securities: tuple[Security, ...]
    sessions: tuple[TradingCalendar, ...]
    series_batch: HistoricalFeatureBatch
    series_by_security: dict[UUID, HistoricalSecuritySeries]
    setups: tuple[SetupEvent, ...]
    dataset_fingerprint: str
    universe_and_calendar_ms: float
    condition_evaluation_ms: float


def _universe_metadata(index: MarketIndex) -> ScannerUniverseMetadata:
    return ScannerUniverseMetadata(
        id=index.id,
        symbol=index.symbol,
        name=index.name,
        provider=index.provider,
        exchange=index.exchange,
    )


def _profile_payload(profile: BacktestProfileDefinition) -> dict[str, object]:
    return {
        "profile_code": profile.profile_code,
        "profile_version": profile.profile_version,
        "compatible_strategies": profile.compatible_strategies,
        "default_holding_sessions": profile.default_holding_sessions,
        "entry_timing": profile.entry_timing,
        "exit_timing": profile.exit_timing,
        "default_slippage_bps": profile.default_slippage_bps,
        "default_trade_notional_inr": profile.default_trade_notional_inr,
        "quantity_policy": profile.quantity_policy,
        "stop_policy": profile.stop_policy,
        "target_policy": profile.target_policy,
        "overlap_policy": profile.overlap_policy,
        "end_policy": profile.end_policy,
        "missing_entry_policy": profile.missing_entry_policy,
        "missing_exit_policy": profile.missing_exit_policy,
        "default_max_exit_delay_sessions": profile.default_max_exit_delay_sessions,
        "cost_model_code": profile.cost_model_code,
        "cost_model_version": profile.cost_model_version,
    }


def profile_metadata(profile: BacktestProfileDefinition) -> ProfileMetadata:
    return ProfileMetadata(
        profile_code=profile.profile_code,
        profile_version=profile.profile_version,
        display_name=profile.display_name,
        compatible_strategies=list(profile.compatible_strategies),
        default_holding_sessions=dict(profile.default_holding_sessions),
        entry_timing=profile.entry_timing,
        exit_timing=profile.exit_timing,
        default_slippage_bps=profile.default_slippage_bps,
        default_trade_notional_inr=profile.default_trade_notional_inr,
        quantity_policy=profile.quantity_policy,
        stop_policy=profile.stop_policy,
        target_policy=profile.target_policy,
        overlap_policy=profile.overlap_policy,
        end_policy=profile.end_policy,
        missing_entry_policy=profile.missing_entry_policy,
        missing_exit_policy=profile.missing_exit_policy,
        default_max_exit_delay_sessions=profile.default_max_exit_delay_sessions,
        cost_model_code=profile.cost_model_code,
        cost_model_version=profile.cost_model_version,
        profile_fingerprint=fingerprint(_profile_payload(profile)),
    )


def aggregate_costs(trades: list[SimulatedTrade], *, broker_costs_excluded: bool) -> AggregateCostBreakdown:
    legs = [trade.entry_cost for trade in trades]
    legs.extend(trade.exit_cost for trade in trades if trade.exit_cost is not None)
    return aggregate_cost_breakdowns(legs, broker_costs_excluded=broker_costs_excluded)


def _membership_active(membership: IndexMembership, day: date) -> bool:
    return membership.valid_from <= day and (membership.valid_to is None or membership.valid_to >= day)


class BacktestService:
    def __init__(self, session: Session):
        self.indices = IndexRepository(session)
        self.securities = SecurityRepository(session)
        self.calendar = TradingCalendarRepository(session)
        self.series = HistoricalTechnicalSeriesService(session)
        self.simulator = TradeSimulator()

    def metadata(self) -> BacktestMetadataResponse:
        universes = [_universe_metadata(index) for index in self.indices.list()]
        return BacktestMetadataResponse(
            profiles=[profile_metadata(profile) for profile in profile_catalog()],
            cost_models=[cost_model_metadata(model) for model in cost_model_catalog()],
            strategies=[strategy_metadata(definition) for definition in strategy_catalog()],
            universes=universes,
            adjustment_policies=["RAW", "ADJUSTED"],
            parameter_bounds={
                "holding_sessions": ParameterBounds(minimum=1, maximum=252, default=None),
                "trade_notional_inr": ParameterBounds(minimum=Decimal("1"), maximum=Decimal("1000000000"), default=Decimal("100000")),
                "slippage_bps": ParameterBounds(minimum=Decimal("0"), maximum=Decimal("1000"), default=Decimal("5")),
                "stop_loss_pct": ParameterBounds(minimum=Decimal("0.00000001"), maximum=Decimal("0.99999999"), default=None),
                "profit_target_pct": ParameterBounds(minimum=Decimal("0.00000001"), maximum=Decimal("10"), default=None),
                "max_exit_delay_sessions": ParameterBounds(minimum=0, maximum=20, default=5),
                "trade_detail_limit": ParameterBounds(minimum=0, maximum=5000, default=500),
            },
            overlap_policies=["ONE_OPEN_TRADE_PER_SECURITY"],
            end_policies=["FORCE_CLOSE_LAST_AVAILABLE_CLOSE"],
            sample_size_warning_rules=SAMPLE_WARNING_RULES,
            hard_request_limits=HARD_LIMITS,
            latest_observation_date=self.securities.latest_price_date(),
            research_disclaimer=RESEARCH_DISCLAIMER,
        )

    @staticmethod
    def _resolve_profile(code: str, version: str) -> BacktestProfileDefinition:
        profile = find_profile(code, version)
        if profile is not None:
            return profile
        if any(item.profile_code == code for item in profile_catalog()):
            raise BacktestNotFoundError(f"Unsupported backtest profile version: {code} v{version}")
        raise BacktestNotFoundError(f"Unknown backtest profile: {code}")

    def prepare_research(self, request: BacktestRunRequest) -> PreparedBacktestResearch:
        """Build the authoritative PIT setup stream and reusable historical inputs."""
        definition = StrategyService._resolve_strategy(request.strategy_code, request.strategy_version)
        feature_set = find_feature_set(definition.required_feature_set, definition.required_feature_set_version)
        if feature_set is None:
            raise BacktestValidationError("Required strategy feature set is unavailable")
        parameters = normalize_parameters(definition, request.parameter_overrides)
        profile = self._resolve_profile(request.profile_code, request.profile_version)
        if definition.strategy_code not in profile.compatible_strategies:
            raise BacktestValidationError("Backtest profile is not compatible with the selected strategy")
        cost_definition = find_cost_model(request.cost_model_code, request.cost_model_version)
        if cost_definition is None:
            if any(item.code == request.cost_model_code for item in cost_model_catalog()):
                raise BacktestNotFoundError(
                    f"Unsupported cost model version: {request.cost_model_code} v{request.cost_model_version}"
                )
            raise BacktestNotFoundError(f"Unknown cost model: {request.cost_model_code}")
        holding_sessions = request.holding_sessions or profile.holding_sessions_for(definition.strategy_code)
        normalized_request = request.model_copy(
            update={"holding_sessions": holding_sessions, "parameter_overrides": parameters}
        )
        strategy_definition_fingerprint = strategy_fingerprint(
            definition,
            parameters,
            request.adjustment_policy,
        )
        profile_definition_fingerprint = fingerprint(_profile_payload(profile))
        effective_cost_fingerprint = cost_fingerprint(
            cost_definition,
            brokerage_per_order_inr=request.brokerage_per_order_inr,
            brokerage_rate=request.brokerage_rate,
            dp_charge_per_scrip_sell_day_inr=request.dp_charge_per_scrip_sell_day_inr,
        )
        config_fingerprint = fingerprint(
            {
                "strategy_fingerprint": strategy_definition_fingerprint,
                "universe": request.universe,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "adjustment_policy": request.adjustment_policy,
                "profile_fingerprint": profile_definition_fingerprint,
                "holding_sessions": holding_sessions,
                "trade_notional_inr": request.trade_notional_inr,
                "slippage_bps": request.slippage_bps,
                "stop_loss_pct": request.stop_loss_pct,
                "profit_target_pct": request.profit_target_pct,
                "overlap_policy": profile.overlap_policy,
                "end_policy": profile.end_policy,
                "missing_entry_policy": profile.missing_entry_policy,
                "missing_exit_policy": profile.missing_exit_policy,
                "max_exit_delay_sessions": request.max_exit_delay_sessions,
                "cost_fingerprint": effective_cost_fingerprint,
                "out_of_sample_start_date": request.out_of_sample_start_date,
            }
        )

        phase = time.perf_counter()
        market_index = self.indices.get_by_name_or_symbol(request.universe)
        if market_index is None:
            raise BacktestNotFoundError("Universe not found")
        try:
            self.indices.assert_historical_coverage(market_index, request.start_date)
        except ValueError as exc:
            raise BacktestValidationError(str(exc)) from exc
        memberships = self.indices.memberships_between(market_index.id, request.start_date, request.end_date)
        securities = sorted(
            {membership.security.id: membership.security for membership in memberships}.values(),
            key=lambda security: (security.symbol.casefold(), str(security.id)),
        )
        session_rows = [
            entry
            for entry in self.calendar.entries(market_index.exchange, request.start_date, request.end_date)
            if entry.is_trading_day
        ]
        calendar_entries = {(entry.exchange, entry.trading_date): entry for entry in session_rows}
        universe_and_calendar_ms = (time.perf_counter() - phase) * 1_000

        series_batch = self.series.compute_batch(
            securities,
            start_date=request.start_date,
            end_date=request.end_date,
            adjustment_policy=request.adjustment_policy,
            feature_set=feature_set,
            calendar_entries=calendar_entries,
            required_feature_codes=definition.required_feature_codes,
        )
        series_by_security = {item.security.id: item for item in series_batch.series}

        phase = time.perf_counter()
        setups: list[SetupEvent] = []
        for calendar_entry in session_rows:
            day = calendar_entry.trading_date
            active_ids = {
                membership.security_id
                for membership in memberships
                if _membership_active(membership, day)
            }
            for security in securities:
                if security.id not in active_ids:
                    continue
                observation = series_by_security[security.id].observations.get(day)
                if observation is None:
                    continue
                values = {code: observation.values.get(code) for code in definition.required_feature_codes}
                matched = all(
                    _condition_passes(
                        values[rule.feature_code],
                        rule.operator,
                        parameters[rule.parameter_code],
                    )
                    for rule in definition.rules
                )
                if not matched:
                    continue
                result_fingerprint = fingerprint(
                    {
                        "strategy_fingerprint": strategy_definition_fingerprint,
                        "observation_date": day,
                        "as_of": observation.available_at,
                        "security_id": str(security.id),
                        "input_fingerprint": observation.input_fingerprint,
                        "feature_state": values,
                        "matched": True,
                    }
                )
                setups.append(
                    SetupEvent(
                        security_id=security.id,
                        symbol=security.symbol,
                        company_name=security.company_name,
                        signal_date=day,
                        signal_decision_at=observation.available_at,
                        strategy_code=definition.strategy_code,
                        strategy_version=definition.strategy_version,
                        strategy_fingerprint=strategy_definition_fingerprint,
                        signal_result_fingerprint=result_fingerprint,
                    )
                )
        condition_evaluation_ms = (time.perf_counter() - phase) * 1_000

        dataset_fingerprint = fingerprint(
            {
                "universe_id": str(market_index.id),
                "memberships": [
                    {
                        "id": str(item.id),
                        "security_id": str(item.security_id),
                        "valid_from": item.valid_from,
                        "valid_to": item.valid_to,
                        "source": item.source,
                    }
                    for item in memberships
                ],
                "calendar": [
                    {
                        "date": item.trading_date,
                        "open": item.session_open.isoformat() if item.session_open else None,
                        "close": item.session_close.isoformat() if item.session_close else None,
                        "type": item.session_type,
                    }
                    for item in session_rows
                ],
                "security_series": {
                    str(item.security.id): item.dataset_fingerprint for item in series_batch.series
                },
                "feature_set": feature_set.code,
                "feature_set_version": feature_set.version,
                "feature_versions": {
                    code: FEATURE_DEFINITIONS[code].version for code in feature_set.feature_codes
                },
            }
        )
        return PreparedBacktestResearch(
            request=request,
            normalized_request=normalized_request,
            definition=definition,
            feature_set=feature_set,
            parameters=parameters,
            profile=profile,
            cost_definition=cost_definition,
            holding_sessions=holding_sessions,
            strategy_fingerprint=strategy_definition_fingerprint,
            profile_fingerprint=profile_definition_fingerprint,
            cost_fingerprint=effective_cost_fingerprint,
            config_fingerprint=config_fingerprint,
            market_index=market_index,
            memberships=tuple(memberships),
            securities=tuple(securities),
            sessions=tuple(session_rows),
            series_batch=series_batch,
            series_by_security=series_by_security,
            setups=tuple(setups),
            dataset_fingerprint=dataset_fingerprint,
            universe_and_calendar_ms=universe_and_calendar_ms,
            condition_evaluation_ms=condition_evaluation_ms,
        )

    def run(self, request: BacktestRunRequest) -> BacktestRunResponse:
        started = time.perf_counter()
        prepared = self.prepare_research(request)
        definition = prepared.definition
        parameters = prepared.parameters
        profile = prepared.profile
        cost_definition = prepared.cost_definition
        holding_sessions = prepared.holding_sessions
        normalized_request = prepared.normalized_request
        strategy_definition_fingerprint = prepared.strategy_fingerprint
        profile_definition_fingerprint = prepared.profile_fingerprint
        effective_cost_fingerprint = prepared.cost_fingerprint
        config_fingerprint = prepared.config_fingerprint
        market_index = prepared.market_index
        memberships = prepared.memberships
        securities = prepared.securities
        session_rows = prepared.sessions
        series_batch = prepared.series_batch
        series_by_security = prepared.series_by_security
        setups = prepared.setups
        dataset_fingerprint = prepared.dataset_fingerprint
        universe_and_calendar_ms = prepared.universe_and_calendar_ms
        condition_evaluation_ms = prepared.condition_evaluation_ms

        phase = time.perf_counter()
        calculator = IndiaCashDeliveryCostCalculator(
            cost_definition,
            brokerage_per_order_inr=request.brokerage_per_order_inr,
            brokerage_rate=request.brokerage_rate,
            dp_charge_per_scrip_sell_day_inr=request.dp_charge_per_scrip_sell_day_inr,
        )
        simulation = self.simulator.simulate(
            setups,
            series_by_security=series_by_security,
            sessions=session_rows,
            profile=profile,
            settings=SimulationSettings(
                adjustment_policy=request.adjustment_policy,
                holding_sessions=holding_sessions,
                trade_notional_inr=request.trade_notional_inr,
                slippage_bps=request.slippage_bps,
                stop_loss_pct=request.stop_loss_pct,
                profit_target_pct=request.profit_target_pct,
                max_exit_delay_sessions=request.max_exit_delay_sessions,
                profile_fingerprint=profile_definition_fingerprint,
                cost_fingerprint=effective_cost_fingerprint,
            ),
            costs=calculator,
        )
        trades = list(simulation.trades)
        trade_simulation_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        analytics = calculate_analytics(
            trades,
            signal_count=len(setups),
            executable_setup_count=len(trades),
        )
        setup_years = Counter(str(setup.signal_date.year) for setup in setups)
        trade_years: dict[str, list[SimulatedTrade]] = {}
        for trade in trades:
            trade_years.setdefault(str(trade.signal_date.year), []).append(trade)
        yearly = [
            period_breakdown(year, trade_years.get(year, []), setup_count=setup_years[year])
            for year in sorted(setup_years)
        ]
        holdout = []
        if request.out_of_sample_start_date is not None:
            in_sample_trades = [trade for trade in trades if trade.signal_date < request.out_of_sample_start_date]
            out_sample_trades = [trade for trade in trades if trade.signal_date >= request.out_of_sample_start_date]
            in_setup_count = sum(setup.signal_date < request.out_of_sample_start_date for setup in setups)
            out_setup_count = len(setups) - in_setup_count
            holdout = [
                period_breakdown("IN_SAMPLE", in_sample_trades, setup_count=in_setup_count),
                period_breakdown("OUT_OF_SAMPLE", out_sample_trades, setup_count=out_setup_count),
            ]
        broker_costs_excluded = (
            request.brokerage_per_order_inr == 0
            and request.brokerage_rate == 0
            and request.dp_charge_per_scrip_sell_day_inr == 0
        )
        aggregate_cost_breakdown = aggregate_costs(trades, broker_costs_excluded=broker_costs_excluded)
        cost_and_analytics_ms = (time.perf_counter() - phase) * 1_000

        run_fingerprint = fingerprint(
            {
                "config_fingerprint": config_fingerprint,
                "dataset_fingerprint": dataset_fingerprint,
                "setup_fingerprints": [setup.signal_result_fingerprint for setup in setups],
                "trade_fingerprints": [trade.trade_fingerprint for trade in trades],
                "skipped_setup_reasons": simulation.skipped_reasons,
            }
        )
        warnings = sorted(
            {
                *analytics.warnings,
                *(warning for item in series_batch.series for warning in item.quality_warnings),
                *(warning for trade in trades for warning in trade.warnings),
                *(["NO_SIGNALS"] if not setups else []),
                *(["SIGNALS_WITHOUT_EXECUTABLE_TRADES"] if setups and not trades else []),
                *(["BROKER_SPECIFIC_COSTS_EXCLUDED"] if broker_costs_excluded else []),
                *(["TRADE_PREVIEW_TRUNCATED"] if len(trades) > request.trade_detail_limit else []),
            }
        )
        preview = trades[: request.trade_detail_limit]
        response_phase = time.perf_counter()
        response = BacktestRunResponse(
            normalized_request=normalized_request,
            strategy=strategy_metadata(definition),
            effective_parameters=parameters,
            profile=profile_metadata(profile),
            cost_model=cost_model_metadata(
                cost_definition,
                brokerage_per_order_inr=request.brokerage_per_order_inr,
                brokerage_rate=request.brokerage_rate,
                dp_charge_per_scrip_sell_day_inr=request.dp_charge_per_scrip_sell_day_inr,
            ),
            universe=_universe_metadata(market_index),
            dataset=DatasetMetadata(
                dataset_code=series_batch.ingestion_run.dataset_code if series_batch.ingestion_run else "UNVERSIONED_LOCAL",
                dataset_version=series_batch.ingestion_run.dataset_version if series_batch.ingestion_run else "unversioned",
                provider=series_batch.ingestion_run.provider if series_batch.ingestion_run else market_index.provider,
                security_count=len(securities),
                price_row_count=sum(len(item.prices) for item in series_batch.series),
                corporate_action_revision_count=sum(len(item.action_history) for item in series_batch.series),
                membership_interval_count=len(memberships),
                trading_session_count=len(session_rows),
            ),
            config_fingerprint=config_fingerprint,
            dataset_fingerprint=dataset_fingerprint,
            run_fingerprint=run_fingerprint,
            historical_sessions_processed=len(session_rows),
            setup_count=len(setups),
            executable_setup_count=len(trades),
            executed_trade_count=len(trades),
            skipped_setup_count=sum(simulation.skipped_reasons.values()),
            skipped_setup_reasons=simulation.skipped_reasons,
            analytics=analytics,
            cost_analytics=aggregate_cost_breakdown,
            yearly_breakdown=yearly,
            holdout_breakdown=holdout,
            warnings=warnings,
            total_trade_count=len(trades),
            returned_trade_count=len(preview),
            trades_truncated=len(preview) < len(trades),
            trades=preview,
            timings=BacktestTimings(
                data_load_ms=round(universe_and_calendar_ms + series_batch.repository_load_ms, 3),
                feature_series_ms=round(series_batch.calculation_ms, 3),
                condition_evaluation_ms=round(condition_evaluation_ms, 3),
                trade_simulation_ms=round(trade_simulation_ms, 3),
                cost_and_analytics_ms=round(cost_and_analytics_ms, 3),
                response_build_ms=0,
                total_service_ms=0,
            ),
            executed_at=datetime.now(UTC),
        )
        response.timings.response_build_ms = round((time.perf_counter() - response_phase) * 1_000, 3)
        response.timings.total_service_ms = round((time.perf_counter() - started) * 1_000, 3)
        return response


__all__ = [
    "BacktestNotFoundError",
    "BacktestService",
    "BacktestValidationError",
    "PreparedBacktestResearch",
    "aggregate_costs",
    "profile_metadata",
]
