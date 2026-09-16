from __future__ import annotations

import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time as clock
from typing import Literal
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
from app.backtests.fingerprints import fingerprint, fingerprint_canonical
from app.backtests.registry import cost_model_catalog, find_cost_model, find_profile
from app.backtests.service import aggregate_costs, profile_metadata
from app.backtests.simulator import SetupEvent, SimulationSettings, TradeSimulator
from app.core.config import get_settings
from app.models import IndexMembership, MarketIndex, Security, TradingCalendar
from app.portfolio.analytics import calculate_portfolio_metrics, calculate_segment_metrics
from app.portfolio.definitions import PortfolioPolicyDefinition
from app.portfolio.registry import find_portfolio_policy, portfolio_policy_catalog
from app.portfolio.service import policy_metadata
from app.portfolio.simulator import PortfolioSimulationSettings, PortfolioSimulator
from app.repositories.calendar import TradingCalendarRepository
from app.repositories.indices import IndexRepository
from app.research.composition import consensus_status
from app.research.historical_definitions import (
    CompositionExecutionPolicyDefinition,
    find_composition_execution_policy,
)
from app.research.repository import ResearchExperimentRepository
from app.research.service import (
    ExperimentNotFoundError,
    ResearchInvariantError,
    ResearchService,
    ResearchValidationError,
    ResolvedCompositionConfiguration,
    _universe_metadata,
    policy_metadata as composition_policy_metadata,
)
from app.schemas.backtests import DatasetMetadata, PeriodBreakdown, SimulatedTrade
from app.schemas.research import (
    CompositionComponentConfiguration,
    CompositionEvaluationRequest,
    CompositionStatus,
)
from app.schemas.research_history import (
    CompositionBacktestRequest,
    CompositionBacktestResponse,
    CompositionExecutionPolicyMetadata,
    CompositionPortfolioRequest,
    CompositionPortfolioResponse,
    HistoricalComponentOutcome,
    HistoricalCompositionOutcome,
    HistoricalCompositionSourceMetadata,
    HistoricalResearchTimings,
    HistoricalSignalDiagnostics,
)
from app.strategies.service import (
    evaluate_prepared_strategy,
    prepared_strategy_result_fingerprint,
    prepare_strategy_evaluation,
    strategy_fingerprint,
    strategy_metadata,
)
from app.technical.registry import FEATURE_DEFINITIONS
from app.technical.series import (
    HistoricalFeatureBatch,
    HistoricalSecuritySeries,
    HistoricalTechnicalSeriesService,
)
from app.technical.service import MARKET_TIMEZONE


RESEARCH_DISCLAIMER = (
    "Composition-aware historical simulation for research only — not predictive, "
    "not investment advice, and not connected to live trading or order execution."
)
MAX_UNIVERSE_SECURITIES = 500
MAX_TRADING_SESSIONS = 3000


class HistoricalResearchNotFoundError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _ComponentOutcome:
    strategy_code: str
    strategy_version: str
    status: CompositionStatus
    result_fingerprint: str


@dataclass(frozen=True, slots=True)
class _CompositionOutcome:
    security_id: UUID
    symbol: str
    observation_date: date
    status: CompositionStatus
    matched_count: int
    insufficient_count: int
    required_count: int
    result_fingerprint: str
    components: tuple[_ComponentOutcome, ...]


@dataclass(frozen=True, slots=True)
class PreparedHistoricalComposition:
    normalized_request: CompositionBacktestRequest | CompositionPortfolioRequest
    source: HistoricalCompositionSourceMetadata
    resolved: ResolvedCompositionConfiguration
    execution_policy: CompositionExecutionPolicyDefinition
    market_index: MarketIndex
    memberships: tuple[IndexMembership, ...]
    securities: tuple[Security, ...]
    sessions: tuple[TradingCalendar, ...]
    series_batch: HistoricalFeatureBatch
    series_by_security: dict[UUID, HistoricalSecuritySeries]
    outcomes: tuple[_CompositionOutcome, ...]
    setups: tuple[SetupEvent, ...]
    dataset_fingerprint: str
    signal_fingerprint: str
    engine_provenance: dict[str, object]
    source_resolution_ms: float
    universe_and_calendar_ms: float
    composition_evaluation_ms: float
    signal_fingerprint_ms: float


@dataclass(frozen=True, slots=True)
class HistoricalBacktestExecution:
    """Internal full-fidelity execution result for research overlays."""

    response: CompositionBacktestResponse
    trades: tuple[SimulatedTrade, ...]


def _membership_active(membership: IndexMembership, day: date) -> bool:
    return membership.valid_from <= day and (
        membership.valid_to is None or membership.valid_to >= day
    )


def _decision_at(day: date, calendar_entry: TradingCalendar) -> datetime:
    return datetime.combine(
        day,
        calendar_entry.session_close or clock(23, 59, 59),
        tzinfo=MARKET_TIMEZONE,
    )


def _execution_policy_metadata(
    definition: CompositionExecutionPolicyDefinition,
) -> CompositionExecutionPolicyMetadata:
    return CompositionExecutionPolicyMetadata(
        **asdict(definition),
        policy_fingerprint=fingerprint(asdict(definition)),
    )


def _dataset_metadata(prepared: PreparedHistoricalComposition) -> DatasetMetadata:
    run = prepared.series_batch.ingestion_run
    return DatasetMetadata(
        dataset_code=run.dataset_code if run else "UNVERSIONED_LOCAL",
        dataset_version=run.dataset_version if run else "unversioned",
        provider=run.provider if run else prepared.market_index.provider,
        security_count=len(prepared.securities),
        price_row_count=sum(len(item.prices) for item in prepared.series_batch.series),
        corporate_action_revision_count=sum(
            len(item.action_history) for item in prepared.series_batch.series
        ),
        membership_interval_count=len(prepared.memberships),
        trading_session_count=len(prepared.sessions),
    )


def _component_configurations(
    resolved: ResolvedCompositionConfiguration,
    adjustment_policy: str,
) -> list[CompositionComponentConfiguration]:
    return [
        CompositionComponentConfiguration(
            strategy=strategy_metadata(definition),
            effective_parameters=parameters,
            strategy_fingerprint=strategy_fingerprint(
                definition, parameters, adjustment_policy
            ),
        )
        for definition, parameters in resolved.components
    ]


class HistoricalCompositionService:
    """One canonical historical composition stream with Phase 5/6 execution adapters."""

    def __init__(self, session: Session):
        self.session = session
        self.indices = IndexRepository(session)
        self.calendar = TradingCalendarRepository(session)
        self.research = ResearchService(session)
        self.experiments = ResearchExperimentRepository(session)
        self.series = HistoricalTechnicalSeriesService(session)
        self.trade_simulator = TradeSimulator()
        self.portfolio_simulator = PortfolioSimulator(self.trade_simulator)

    @staticmethod
    def _resolve_execution_policy(
        code: str,
        version: str,
    ) -> CompositionExecutionPolicyDefinition:
        definition = find_composition_execution_policy(code, version)
        if definition is not None:
            return definition
        if code == "COMPOSITION_NEXT_OPEN_FIXED_HOLD":
            raise HistoricalResearchNotFoundError(
                f"Unsupported composition execution policy version: {code} v{version}"
            )
        raise HistoricalResearchNotFoundError(
            f"Unknown composition execution policy: {code}"
        )

    @staticmethod
    def _resolve_cost_model(code: str, version: str) -> CostModelDefinition:
        definition = find_cost_model(code, version)
        if definition is not None:
            return definition
        if any(item.code == code for item in cost_model_catalog()):
            raise HistoricalResearchNotFoundError(
                f"Unsupported cost model version: {code} v{version}"
            )
        raise HistoricalResearchNotFoundError(f"Unknown cost model: {code}")

    @staticmethod
    def _resolve_portfolio_policy(code: str, version: str) -> PortfolioPolicyDefinition:
        definition = find_portfolio_policy(code, version)
        if definition is not None:
            return definition
        if any(item.policy_code == code for item in portfolio_policy_catalog()):
            raise HistoricalResearchNotFoundError(
                f"Unsupported portfolio policy version: {code} v{version}"
            )
        raise HistoricalResearchNotFoundError(f"Unknown portfolio policy: {code}")

    def _resolve_source(
        self,
        request: CompositionBacktestRequest | CompositionPortfolioRequest,
    ) -> tuple[ResolvedCompositionConfiguration, HistoricalCompositionSourceMetadata]:
        if request.source.inline_composition is not None:
            resolved = self.research.resolve_composition(
                request.source.inline_composition
            )
            source_type: Literal["INLINE_COMPOSITION", "SAVED_EXPERIMENT"] = (
                "INLINE_COMPOSITION"
            )
            experiment_id = None
            experiment_name = None
            experiment_description = None
            config_fingerprint = resolved.config_fingerprint
        else:
            experiment_id_value = request.source.experiment_id
            if experiment_id_value is None:
                raise ResearchInvariantError("Saved experiment source is missing its UUID")
            experiment = self.experiments.get(experiment_id_value)
            if experiment is None:
                raise ExperimentNotFoundError("Research experiment not found")
            saved = CompositionEvaluationRequest.model_validate(
                experiment.normalized_request
            )
            resolved = self.research.resolve_composition(saved)
            if resolved.config_fingerprint != experiment.config_fingerprint:
                raise ResearchInvariantError(
                    "Saved experiment configuration fingerprint no longer validates"
                )
            source_type = "SAVED_EXPERIMENT"
            experiment_id = experiment.id
            experiment_name = experiment.name
            experiment_description = experiment.description
            config_fingerprint = experiment.config_fingerprint

        metadata = HistoricalCompositionSourceMetadata(
            source_type=source_type,
            experiment_id=experiment_id,
            experiment_name=experiment_name,
            experiment_description=experiment_description,
            composition_config_fingerprint=config_fingerprint,
            normalized_composition=resolved.normalized_request,
            composition_definition_fields=[
                "policy_code",
                "policy_version",
                "required_match_count",
                "components",
            ],
            preserved_point_evaluation_fields=[
                "universe",
                "observation_date",
                "as_of",
                "adjustment_policy",
            ],
            historical_run_fields=[
                "universe",
                "start_date",
                "end_date",
                "adjustment_policy",
                "execution_policy_code",
                "execution_policy_version",
                "holding_sessions",
            ],
        )
        return resolved, metadata

    def prepare(
        self,
        request: CompositionBacktestRequest | CompositionPortfolioRequest,
    ) -> PreparedHistoricalComposition:
        phase = time.perf_counter()
        resolved, source = self._resolve_source(request)
        execution_policy = self._resolve_execution_policy(
            request.execution_policy_code, request.execution_policy_version
        )
        if not (
            execution_policy.minimum_holding_sessions
            <= request.holding_sessions
            <= execution_policy.maximum_holding_sessions
        ):
            raise ResearchValidationError(
                "holding_sessions is outside the composition execution policy bounds"
            )
        normalized_source = request.source.model_copy(
            update={
                "inline_composition": (
                    resolved.normalized_request
                    if request.source.inline_composition is not None
                    else None
                )
            }
        )
        normalized_request = request.model_copy(update={"source": normalized_source})
        source_resolution_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        market_index = self.indices.get_by_name_or_symbol(request.universe)
        if market_index is None:
            raise HistoricalResearchNotFoundError("Universe not found")
        try:
            self.indices.assert_historical_coverage(market_index, request.start_date)
        except ValueError as exc:
            raise ResearchValidationError(str(exc)) from exc
        memberships = sorted(
            self.indices.memberships_between(
                market_index.id, request.start_date, request.end_date
            ),
            key=lambda item: (
                item.valid_from,
                item.valid_to or date.max,
                item.security.symbol.casefold(),
                str(item.security_id),
                str(item.id),
            ),
        )
        securities = sorted(
            {item.security.id: item.security for item in memberships}.values(),
            key=lambda item: (item.symbol.casefold(), str(item.id)),
        )
        if not securities:
            raise ResearchValidationError(
                "Historical universe has no members in the requested range"
            )
        sessions = sorted(
            (
                item
                for item in self.calendar.entries(
                    market_index.exchange, request.start_date, request.end_date
                )
                if item.is_trading_day
            ),
            key=lambda item: (item.trading_date, str(item.id)),
        )
        if not sessions:
            raise ResearchValidationError(
                "Historical analysis range contains no trading sessions"
            )
        if len(securities) > MAX_UNIVERSE_SECURITIES:
            raise ResearchValidationError(
                "Historical analysis universe exceeds 500 securities"
            )
        if len(sessions) > MAX_TRADING_SESSIONS:
            raise ResearchValidationError(
                "Historical analysis range exceeds 3000 trading sessions"
            )
        calendar_entries = {
            (item.exchange, item.trading_date): item for item in sessions
        }
        universe_and_calendar_ms = (time.perf_counter() - phase) * 1_000

        series_batch = self.series.compute_batch(
            securities,
            start_date=request.start_date,
            end_date=request.end_date,
            adjustment_policy=request.adjustment_policy,
            feature_set=resolved.feature_set,
            calendar_entries=calendar_entries,
            required_feature_codes=resolved.union_feature_codes,
        )
        series_by_security = {item.security.id: item for item in series_batch.series}

        phase = time.perf_counter()
        feature_versions = {
            code: FEATURE_DEFINITIONS[code].version
            for code in resolved.union_feature_codes
        }
        component_fingerprints = {
            (definition.strategy_code, definition.strategy_version): strategy_fingerprint(
                definition, parameters, request.adjustment_policy
            )
            for definition, parameters in resolved.components
        }
        component_plans = {
            (definition.strategy_code, definition.strategy_version): (
                prepare_strategy_evaluation(definition, parameters)
            )
            for definition, parameters in resolved.components
        }
        outcomes: list[_CompositionOutcome] = []
        setups: list[SetupEvent] = []
        for calendar_entry in sessions:
            day = calendar_entry.trading_date
            active_ids = {
                membership.security_id
                for membership in memberships
                if _membership_active(membership, day)
            }
            for security in securities:
                if security.id not in active_ids:
                    continue
                series = series_by_security[security.id]
                observation = series.observations.get(day)
                input_fingerprint = (
                    observation.input_fingerprint
                    if observation is not None
                    else fingerprint(
                        {
                            "security_dataset_fingerprint": series.dataset_fingerprint,
                            "observation_date": day,
                            "observation": "MISSING",
                        }
                    )
                )
                decision_at = (
                    observation.available_at
                    if observation is not None
                    else _decision_at(day, calendar_entry)
                )
                component_outcomes: list[_ComponentOutcome] = []
                for definition, _parameters in resolved.components:
                    component_key = (
                        definition.strategy_code,
                        definition.strategy_version,
                    )
                    values, missing, matched = evaluate_prepared_strategy(
                        component_plans[component_key],
                        observation.values if observation is not None else None,
                    )
                    component_status: CompositionStatus = (
                        "INSUFFICIENT_FEATURE_HISTORY"
                        if missing
                        else ("MATCHED" if matched else "NOT_MATCHED")
                    )
                    component_fp = prepared_strategy_result_fingerprint(
                        prepared=component_plans[component_key],
                        definition_fingerprint=component_fingerprints[
                            component_key
                        ],
                        observation_date=day,
                        as_of=decision_at,
                        security_id=security.id,
                        input_fingerprint=input_fingerprint,
                        feature_state=values,
                        matched=matched,
                    )
                    component_outcomes.append(
                        _ComponentOutcome(
                            strategy_code=definition.strategy_code,
                            strategy_version=definition.strategy_version,
                            status=component_status,
                            result_fingerprint=component_fp,
                        )
                    )
                matched_count = sum(
                    item.status == "MATCHED" for item in component_outcomes
                )
                insufficient_count = sum(
                    item.status == "INSUFFICIENT_FEATURE_HISTORY"
                    for item in component_outcomes
                )
                status = consensus_status(
                    matched_count=matched_count,
                    insufficient_count=insufficient_count,
                    required_count=resolved.normalized_request.required_match_count,
                )
                result_fp = fingerprint_canonical(
                    {
                        "composition_config_fingerprint": source.composition_config_fingerprint,
                        "security_id": str(security.id),
                        "observation_date": day.isoformat(),
                        "input_fingerprint": input_fingerprint,
                        "status": status,
                        "matched_count": matched_count,
                        "insufficient_count": insufficient_count,
                        "component_result_fingerprints": [
                            item.result_fingerprint for item in component_outcomes
                        ],
                    }
                )
                outcomes.append(
                    _CompositionOutcome(
                        security_id=security.id,
                        symbol=security.symbol,
                        observation_date=day,
                        status=status,
                        matched_count=matched_count,
                        insufficient_count=insufficient_count,
                        required_count=resolved.normalized_request.required_match_count,
                        result_fingerprint=result_fp,
                        components=tuple(component_outcomes),
                    )
                )
                if status == "MATCHED":
                    setups.append(
                        SetupEvent(
                            security_id=security.id,
                            symbol=security.symbol,
                            company_name=security.company_name,
                            signal_date=day,
                            signal_decision_at=decision_at,
                            strategy_code=resolved.policy.policy_code,
                            strategy_version=resolved.policy.policy_version,
                            strategy_fingerprint=source.composition_config_fingerprint,
                            signal_result_fingerprint=result_fp,
                        )
                    )
        outcomes.sort(
            key=lambda item: (
                item.observation_date,
                item.symbol.casefold(),
                str(item.security_id),
            )
        )
        setups.sort(
            key=lambda item: (
                item.signal_date,
                item.symbol.casefold(),
                str(item.security_id),
            )
        )
        composition_evaluation_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        dataset_fingerprint = fingerprint(
            {
                "universe": {
                    "id": str(market_index.id),
                    "symbol": market_index.symbol,
                    "provider": market_index.provider,
                },
                "memberships": [
                    {
                        "id": str(item.id),
                        "security_id": str(item.security_id),
                        "valid_from": item.valid_from,
                        "valid_to": item.valid_to,
                        "source": item.source,
                        "data_origin": item.data_origin,
                        "source_artifact_id": str(item.source_artifact_id)
                        if item.source_artifact_id
                        else None,
                    }
                    for item in memberships
                ],
                "calendar": [
                    {
                        "date": item.trading_date,
                        "open": item.session_open.isoformat()
                        if item.session_open
                        else None,
                        "close": item.session_close.isoformat()
                        if item.session_close
                        else None,
                        "type": item.session_type,
                        "data_origin": item.data_origin,
                        "source_artifact_id": str(item.source_artifact_id)
                        if item.source_artifact_id
                        else None,
                    }
                    for item in sessions
                ],
                "security_series": {
                    str(item.security.id): item.dataset_fingerprint
                    for item in sorted(
                        series_batch.series, key=lambda value: str(value.security.id)
                    )
                },
                "price_lineage": {
                    str(item.security.id): fingerprint(
                        [
                            {
                                "date": row.trading_date,
                                "source": row.source,
                                "data_origin": row.data_origin,
                                "source_artifact_id": str(row.source_artifact_id)
                                if row.source_artifact_id
                                else None,
                                "ingestion_run_id": str(row.ingestion_run_id)
                                if row.ingestion_run_id
                                else None,
                            }
                            for row in item.prices
                        ]
                    )
                    for item in sorted(
                        series_batch.series, key=lambda value: str(value.security.id)
                    )
                },
                "corporate_action_lineage": {
                    str(item.security.id): fingerprint(
                        [
                            {
                                "id": str(action.id),
                                "available_at": action.available_at,
                                "data_origin": action.data_origin,
                                "source_artifact_id": str(action.source_artifact_id)
                                if action.source_artifact_id
                                else None,
                                "ingestion_run_id": str(action.ingestion_run_id)
                                if action.ingestion_run_id
                                else None,
                            }
                            for action in item.action_history
                        ]
                    )
                    for item in sorted(
                        series_batch.series, key=lambda value: str(value.security.id)
                    )
                },
                "feature_set": {
                    "code": resolved.feature_set.code,
                    "version": resolved.feature_set.version,
                    "feature_versions": feature_versions,
                },
            }
        )
        engine_provenance = {
            **self.research._engine_provenance(
                resolved.policy,
                list(resolved.components),
                request.adjustment_policy,
                resolved.feature_set,
                resolved.union_feature_codes,
            ),
            "historical_composition_engine": "ALPHADESK_HISTORICAL_COMPOSITION",
            "historical_composition_engine_version": "1",
            "alphadesk_app_version": get_settings().app_version,
        }
        signal_fingerprint = fingerprint(
            {
                "composition_config_fingerprint": source.composition_config_fingerprint,
                "universe_id": str(market_index.id),
                "start_date": request.start_date,
                "end_date": request.end_date,
                "adjustment_policy": request.adjustment_policy,
                "dataset_fingerprint": dataset_fingerprint,
                "engine_provenance": engine_provenance,
                "ordered_outcome_fingerprints": [
                    item.result_fingerprint for item in outcomes
                ],
            }
        )
        signal_fingerprint_ms = (time.perf_counter() - phase) * 1_000
        return PreparedHistoricalComposition(
            normalized_request=normalized_request,
            source=source,
            resolved=resolved,
            execution_policy=execution_policy,
            market_index=market_index,
            memberships=tuple(memberships),
            securities=tuple(securities),
            sessions=tuple(sessions),
            series_batch=series_batch,
            series_by_security=series_by_security,
            outcomes=tuple(outcomes),
            setups=tuple(setups),
            dataset_fingerprint=dataset_fingerprint,
            signal_fingerprint=signal_fingerprint,
            engine_provenance=engine_provenance,
            source_resolution_ms=source_resolution_ms,
            universe_and_calendar_ms=universe_and_calendar_ms,
            composition_evaluation_ms=composition_evaluation_ms,
            signal_fingerprint_ms=signal_fingerprint_ms,
        )

    @staticmethod
    def _diagnostics(
        prepared: PreparedHistoricalComposition,
        limit: int,
    ) -> HistoricalSignalDiagnostics:
        preview = prepared.outcomes[:limit]
        return HistoricalSignalDiagnostics(
            eligible_evaluations=len(prepared.outcomes),
            matched_setups=len(prepared.setups),
            non_matches=sum(item.status == "NOT_MATCHED" for item in prepared.outcomes),
            insufficient_history=sum(
                item.status == "INSUFFICIENT_FEATURE_HISTORY"
                for item in prepared.outcomes
            ),
            returned_outcomes=len(preview),
            outcomes_truncated=len(preview) < len(prepared.outcomes),
            outcomes=[
                HistoricalCompositionOutcome(
                    security_id=item.security_id,
                    symbol=item.symbol,
                    observation_date=item.observation_date,
                    status=item.status,
                    matched_strategy_count=item.matched_count,
                    insufficient_strategy_count=item.insufficient_count,
                    required_match_count=item.required_count,
                    composition_result_fingerprint=item.result_fingerprint,
                    components=[
                        HistoricalComponentOutcome(
                            strategy_code=component.strategy_code,
                            strategy_version=component.strategy_version,
                            status=component.status,
                            result_fingerprint=component.result_fingerprint,
                        )
                        for component in item.components
                    ],
                )
                for item in preview
            ],
        )

    @staticmethod
    def _profile(prepared: PreparedHistoricalComposition) -> BacktestProfileDefinition:
        profile = find_profile(
            prepared.execution_policy.backtest_profile_code,
            prepared.execution_policy.backtest_profile_version,
        )
        if profile is None:
            raise ResearchInvariantError(
                "Composition execution policy references an unavailable Phase 5 profile"
            )
        return profile

    def _assert_prepared_compatible(
        self,
        request: CompositionBacktestRequest | CompositionPortfolioRequest,
        prepared: PreparedHistoricalComposition,
    ) -> None:
        expected = prepared.normalized_request
        shared_fields = (
            "universe",
            "start_date",
            "end_date",
            "adjustment_policy",
            "execution_policy_code",
            "execution_policy_version",
            "holding_sessions",
        )
        if any(getattr(request, field) != getattr(expected, field) for field in shared_fields):
            raise ResearchInvariantError(
                "Prepared historical context is incompatible with the execution request"
            )
        if request.source.inline_composition is not None:
            resolved = self.research.resolve_composition(request.source.inline_composition)
            compatible_source = (
                prepared.source.source_type == "INLINE_COMPOSITION"
                and resolved.config_fingerprint
                == prepared.source.composition_config_fingerprint
            )
        else:
            compatible_source = (
                prepared.source.source_type == "SAVED_EXPERIMENT"
                and request.source.experiment_id == prepared.source.experiment_id
            )
        if not compatible_source:
            raise ResearchInvariantError(
                "Prepared historical context has a different composition source"
            )

    @staticmethod
    def _normalized_execution_request(
        request: CompositionBacktestRequest | CompositionPortfolioRequest,
        prepared: PreparedHistoricalComposition,
    ) -> CompositionBacktestRequest | CompositionPortfolioRequest:
        normalized_source = request.source.model_copy(
            update={
                "inline_composition": (
                    prepared.resolved.normalized_request
                    if request.source.inline_composition is not None
                    else None
                )
            }
        )
        return request.model_copy(update={"source": normalized_source})

    def run_backtest(
        self,
        request: CompositionBacktestRequest,
    ) -> CompositionBacktestResponse:
        started = time.perf_counter()
        prepared = self.prepare(request)
        return self._execute_backtest_prepared(
            request, prepared, started=started
        ).response

    def run_backtest_prepared(
        self,
        request: CompositionBacktestRequest,
        prepared: PreparedHistoricalComposition,
    ) -> CompositionBacktestResponse:
        """Run Phase 5 from an explicit request-scoped historical signal context."""
        self._assert_prepared_compatible(request, prepared)
        return self._execute_backtest_prepared(
            request,
            prepared,
            started=time.perf_counter(),
        ).response

    def execute_backtest_prepared(
        self,
        request: CompositionBacktestRequest,
        prepared: PreparedHistoricalComposition,
    ) -> HistoricalBacktestExecution:
        """Execute once and retain every trade for deterministic analytics overlays."""
        self._assert_prepared_compatible(request, prepared)
        return self._execute_backtest_prepared(
            request,
            prepared,
            started=time.perf_counter(),
        )

    def _execute_backtest_prepared(
        self,
        request: CompositionBacktestRequest,
        prepared: PreparedHistoricalComposition,
        *,
        started: float,
    ) -> HistoricalBacktestExecution:
        normalized_request = CompositionBacktestRequest.model_validate(
            self._normalized_execution_request(request, prepared).model_dump()
        )
        profile = self._profile(prepared)
        cost_definition = self._resolve_cost_model(
            request.cost_model_code, request.cost_model_version
        )
        profile_fp = fingerprint(asdict(profile))
        effective_cost_fp = cost_fingerprint(
            cost_definition,
            brokerage_per_order_inr=request.brokerage_per_order_inr,
            brokerage_rate=request.brokerage_rate,
            dp_charge_per_scrip_sell_day_inr=request.dp_charge_per_scrip_sell_day_inr,
        )
        calculator = IndiaCashDeliveryCostCalculator(
            cost_definition,
            brokerage_per_order_inr=request.brokerage_per_order_inr,
            brokerage_rate=request.brokerage_rate,
            dp_charge_per_scrip_sell_day_inr=request.dp_charge_per_scrip_sell_day_inr,
        )

        phase = time.perf_counter()
        simulation = self.trade_simulator.simulate(
            prepared.setups,
            series_by_security=prepared.series_by_security,
            sessions=prepared.sessions,
            profile=profile,
            settings=SimulationSettings(
                adjustment_policy=request.adjustment_policy,
                holding_sessions=request.holding_sessions,
                trade_notional_inr=request.trade_notional_inr,
                slippage_bps=request.slippage_bps,
                stop_loss_pct=request.stop_loss_pct,
                profit_target_pct=request.profit_target_pct,
                max_exit_delay_sessions=request.max_exit_delay_sessions,
                profile_fingerprint=profile_fp,
                cost_fingerprint=effective_cost_fp,
            ),
            costs=calculator,
        )
        execution_ms = (time.perf_counter() - phase) * 1_000
        trades = list(simulation.trades)

        phase = time.perf_counter()
        analytics = calculate_analytics(
            trades,
            signal_count=len(prepared.setups),
            executable_setup_count=len(trades),
        )
        setup_years = Counter(str(item.signal_date.year) for item in prepared.setups)
        trade_years: dict[str, list[SimulatedTrade]] = {}
        for trade in trades:
            trade_years.setdefault(str(trade.signal_date.year), []).append(trade)
        yearly = [
            period_breakdown(
                year, trade_years.get(year, []), setup_count=setup_years[year]
            )
            for year in sorted(setup_years)
        ]
        holdout: list[PeriodBreakdown] = []
        if request.out_of_sample_start_date is not None:
            boundary = request.out_of_sample_start_date
            holdout = [
                period_breakdown(
                    "IN_SAMPLE",
                    [item for item in trades if item.signal_date < boundary],
                    setup_count=sum(
                        item.signal_date < boundary for item in prepared.setups
                    ),
                ),
                period_breakdown(
                    "OUT_OF_SAMPLE",
                    [item for item in trades if item.signal_date >= boundary],
                    setup_count=sum(
                        item.signal_date >= boundary for item in prepared.setups
                    ),
                ),
            ]
        broker_costs_excluded = (
            request.brokerage_per_order_inr == 0
            and request.brokerage_rate == 0
            and request.dp_charge_per_scrip_sell_day_inr == 0
        )
        cost_analytics = aggregate_costs(
            trades, broker_costs_excluded=broker_costs_excluded
        )
        analytics_ms = (time.perf_counter() - phase) * 1_000

        config_fp = fingerprint(
            {
                "historical_signal_fingerprint": prepared.signal_fingerprint,
                "execution_policy": asdict(prepared.execution_policy),
                "holding_sessions": request.holding_sessions,
                "profile_fingerprint": profile_fp,
                "cost_fingerprint": effective_cost_fp,
                "trade_notional_inr": request.trade_notional_inr,
                "slippage_bps": request.slippage_bps,
                "stop_loss_pct": request.stop_loss_pct,
                "profit_target_pct": request.profit_target_pct,
                "max_exit_delay_sessions": request.max_exit_delay_sessions,
                "end_policy": profile.end_policy,
                "out_of_sample_start_date": request.out_of_sample_start_date,
            }
        )
        run_fp = fingerprint(
            {
                "backtest_config_fingerprint": config_fp,
                "historical_dataset_fingerprint": prepared.dataset_fingerprint,
                "trade_fingerprints": [item.trade_fingerprint for item in trades],
                "skipped_setup_reasons": simulation.skipped_reasons,
            }
        )
        preview = trades[: request.trade_detail_limit]
        warnings = sorted(
            {
                *analytics.warnings,
                *(
                    warning
                    for item in prepared.series_batch.series
                    for warning in item.quality_warnings
                ),
                *(warning for item in trades for warning in item.warnings),
                *(["NO_SIGNALS"] if not prepared.setups else []),
                *(
                    ["SIGNALS_WITHOUT_EXECUTABLE_TRADES"]
                    if prepared.setups and not trades
                    else []
                ),
                *(["BROKER_SPECIFIC_COSTS_EXCLUDED"] if broker_costs_excluded else []),
                *(
                    ["TRADE_PREVIEW_TRUNCATED"]
                    if len(preview) < len(trades)
                    else []
                ),
            }
        )
        response_phase = time.perf_counter()
        response = CompositionBacktestResponse(
            normalized_request=normalized_request,
            source=prepared.source,
            composition_policy=composition_policy_metadata(prepared.resolved.policy),
            components=_component_configurations(
                prepared.resolved, request.adjustment_policy
            ),
            execution_policy=_execution_policy_metadata(prepared.execution_policy),
            profile=profile_metadata(profile),
            cost_model=cost_model_metadata(
                cost_definition,
                brokerage_per_order_inr=request.brokerage_per_order_inr,
                brokerage_rate=request.brokerage_rate,
                dp_charge_per_scrip_sell_day_inr=request.dp_charge_per_scrip_sell_day_inr,
            ),
            universe=_universe_metadata(prepared.market_index),
            dataset=_dataset_metadata(prepared),
            historical_signal_fingerprint=prepared.signal_fingerprint,
            historical_dataset_fingerprint=prepared.dataset_fingerprint,
            backtest_config_fingerprint=config_fp,
            backtest_run_fingerprint=run_fp,
            engine_provenance=prepared.engine_provenance,
            diagnostics=self._diagnostics(
                prepared, request.diagnostic_detail_limit
            ),
            skipped_setup_count=sum(simulation.skipped_reasons.values()),
            skipped_setup_reasons=simulation.skipped_reasons,
            executed_trade_count=len(trades),
            analytics=analytics,
            cost_analytics=cost_analytics,
            yearly_breakdown=yearly,
            holdout_breakdown=holdout,
            warnings=warnings,
            total_trade_count=len(trades),
            returned_trade_count=len(preview),
            trades_truncated=len(preview) < len(trades),
            trades=preview,
            timings=HistoricalResearchTimings(
                source_resolution_ms=round(prepared.source_resolution_ms, 3),
                universe_and_calendar_ms=round(
                    prepared.universe_and_calendar_ms, 3
                ),
                data_load_ms=round(prepared.series_batch.repository_load_ms, 3),
                feature_generation_ms=round(
                    prepared.series_batch.calculation_ms, 3
                ),
                composition_evaluation_ms=round(
                    prepared.composition_evaluation_ms, 3
                ),
                signal_fingerprint_ms=round(prepared.signal_fingerprint_ms, 3),
                execution_ms=round(execution_ms, 3),
                analytics_ms=round(analytics_ms, 3),
                response_build_ms=0,
                total_service_ms=0,
            ),
            executed_at=datetime.now(UTC),
            research_disclaimer=RESEARCH_DISCLAIMER,
        )
        response.timings.response_build_ms = round(
            (time.perf_counter() - response_phase) * 1_000, 3
        )
        response.timings.total_service_ms = round(
            (time.perf_counter() - started) * 1_000, 3
        )
        return HistoricalBacktestExecution(response=response, trades=tuple(trades))

    def run_portfolio(
        self,
        request: CompositionPortfolioRequest,
    ) -> CompositionPortfolioResponse:
        started = time.perf_counter()
        prepared = self.prepare(request)
        return self._run_portfolio_prepared(request, prepared, started=started)

    def run_portfolio_prepared(
        self,
        request: CompositionPortfolioRequest,
        prepared: PreparedHistoricalComposition,
    ) -> CompositionPortfolioResponse:
        """Run Phase 6 from an explicit request-scoped historical signal context."""
        self._assert_prepared_compatible(request, prepared)
        return self._run_portfolio_prepared(
            request,
            prepared,
            started=time.perf_counter(),
        )

    def _run_portfolio_prepared(
        self,
        request: CompositionPortfolioRequest,
        prepared: PreparedHistoricalComposition,
        *,
        started: float,
    ) -> CompositionPortfolioResponse:
        normalized_request = CompositionPortfolioRequest.model_validate(
            self._normalized_execution_request(request, prepared).model_dump()
        )
        profile = self._profile(prepared)
        cost_definition = self._resolve_cost_model(
            request.cost_model_code, request.cost_model_version
        )
        portfolio_policy = self._resolve_portfolio_policy(
            request.portfolio_policy_code, request.portfolio_policy_version
        )
        if profile.profile_code not in portfolio_policy.compatible_profiles:
            raise ResearchValidationError(
                "Portfolio policy is incompatible with the composition execution profile"
            )
        profile_fp = fingerprint(asdict(profile))
        portfolio_policy_fp = fingerprint(asdict(portfolio_policy))
        effective_cost_fp = cost_fingerprint(
            cost_definition,
            brokerage_per_order_inr=request.brokerage_per_order_inr,
            brokerage_rate=request.brokerage_rate,
            dp_charge_per_scrip_sell_day_inr=request.dp_charge_per_scrip_sell_day_inr,
        )
        calculator = IndiaCashDeliveryCostCalculator(
            cost_definition,
            brokerage_per_order_inr=request.brokerage_per_order_inr,
            brokerage_rate=request.brokerage_rate,
            dp_charge_per_scrip_sell_day_inr=request.dp_charge_per_scrip_sell_day_inr,
        )

        phase = time.perf_counter()
        simulation = self.portfolio_simulator.simulate(
            prepared.setups,
            series_by_security=prepared.series_by_security,
            sessions=prepared.sessions,
            profile=profile,
            cost_definition=cost_definition,
            policy=portfolio_policy,
            settings=PortfolioSimulationSettings(
                start_date=request.start_date,
                adjustment_policy=request.adjustment_policy,
                holding_sessions=request.holding_sessions,
                slippage_bps=request.slippage_bps,
                stop_loss_pct=request.stop_loss_pct,
                profit_target_pct=request.profit_target_pct,
                max_exit_delay_sessions=request.max_exit_delay_sessions,
                initial_capital=request.initial_capital_inr,
                max_concurrent_positions=request.max_concurrent_positions,
                max_position_weight=request.max_position_weight,
                max_gross_exposure=request.max_gross_exposure,
                minimum_cash_reserve_pct=request.minimum_cash_reserve_pct,
                profile_fingerprint=profile_fp,
                cost_fingerprint=effective_cost_fp,
                policy_fingerprint=portfolio_policy_fp,
            ),
            costs=calculator,
        )
        execution_ms = (time.perf_counter() - phase) * 1_000
        positions = list(simulation.positions)
        snapshots = list(simulation.snapshots)

        phase = time.perf_counter()
        metrics = calculate_portfolio_metrics(
            snapshots,
            positions,
            initial_capital=request.initial_capital_inr,
            annual_risk_free_rate=request.risk_free_rate_annual,
        )
        oos_metrics = []
        if request.out_of_sample_start_date is not None:
            boundary = request.out_of_sample_start_date
            oos_metrics = [
                calculate_segment_metrics(
                    "IN_SAMPLE",
                    [item for item in snapshots if item.session_date < boundary],
                    annual_risk_free_rate=request.risk_free_rate_annual,
                ),
                calculate_segment_metrics(
                    "OUT_OF_SAMPLE",
                    [item for item in snapshots if item.session_date >= boundary],
                    annual_risk_free_rate=request.risk_free_rate_annual,
                ),
            ]
        broker_costs_excluded = (
            request.brokerage_per_order_inr == 0
            and request.brokerage_rate == 0
            and request.dp_charge_per_scrip_sell_day_inr == 0
        )
        cost_analytics = aggregate_cost_breakdowns(
            [item.entry_costs for item in positions]
            + [item.exit_costs for item in positions if item.exit_costs is not None],
            broker_costs_excluded=broker_costs_excluded,
        )
        analytics_ms = (time.perf_counter() - phase) * 1_000

        config_fp = fingerprint(
            {
                "historical_signal_fingerprint": prepared.signal_fingerprint,
                "execution_policy": asdict(prepared.execution_policy),
                "holding_sessions": request.holding_sessions,
                "profile_fingerprint": profile_fp,
                "cost_fingerprint": effective_cost_fp,
                "slippage_bps": request.slippage_bps,
                "stop_loss_pct": request.stop_loss_pct,
                "profit_target_pct": request.profit_target_pct,
                "max_exit_delay_sessions": request.max_exit_delay_sessions,
                "portfolio_policy_fingerprint": portfolio_policy_fp,
                "initial_capital_inr": request.initial_capital_inr,
                "max_concurrent_positions": request.max_concurrent_positions,
                "max_position_weight": request.max_position_weight,
                "max_gross_exposure": request.max_gross_exposure,
                "minimum_cash_reserve_pct": request.minimum_cash_reserve_pct,
                "risk_free_rate_annual": request.risk_free_rate_annual,
                "out_of_sample_start_date": request.out_of_sample_start_date,
            }
        )
        ledger_fp = fingerprint(
            [item.model_dump(mode="json") for item in simulation.ledger_events]
        )
        equity_fp = fingerprint(
            [item.model_dump(mode="json") for item in snapshots]
        )
        run_fp = fingerprint(
            {
                "portfolio_config_fingerprint": config_fp,
                "historical_dataset_fingerprint": prepared.dataset_fingerprint,
                "accepted_positions": [
                    item.position_fingerprint for item in positions
                ],
                "rejected_candidates": [
                    item.candidate_fingerprint
                    for item in simulation.rejected_candidates
                ],
                "cash_ledger_fingerprint": ledger_fp,
                "daily_equity_fingerprint": equity_fp,
            }
        )
        rejection_counts = dict(
            sorted(
                Counter(
                    item.reason for item in simulation.rejected_candidates
                ).items()
            )
        )
        position_preview = positions[: request.position_detail_limit]
        ledger_preview = list(
            simulation.ledger_events[: request.ledger_detail_limit]
        )
        rejected_preview = list(
            simulation.rejected_candidates[: request.position_detail_limit]
        )
        warnings = sorted(
            {
                *simulation.warnings,
                *metrics.warnings,
                *(
                    warning
                    for item in prepared.series_batch.series
                    for warning in item.quality_warnings
                ),
                *(warning for item in oos_metrics for warning in item.warnings),
                *(["NO_SETUPS"] if not prepared.setups else []),
                *(
                    ["SETUPS_WITHOUT_ALLOCATED_POSITIONS"]
                    if prepared.setups and not positions
                    else []
                ),
                *(["BROKER_SPECIFIC_COSTS_EXCLUDED"] if broker_costs_excluded else []),
                *(
                    ["POSITION_PREVIEW_TRUNCATED"]
                    if len(position_preview) < len(positions)
                    else []
                ),
                *(
                    ["LEDGER_PREVIEW_TRUNCATED"]
                    if len(ledger_preview) < len(simulation.ledger_events)
                    else []
                ),
            }
        )
        response_phase = time.perf_counter()
        response = CompositionPortfolioResponse(
            normalized_request=normalized_request,
            source=prepared.source,
            composition_policy=composition_policy_metadata(prepared.resolved.policy),
            components=_component_configurations(
                prepared.resolved, request.adjustment_policy
            ),
            execution_policy=_execution_policy_metadata(prepared.execution_policy),
            profile=profile_metadata(profile),
            cost_model=cost_model_metadata(
                cost_definition,
                brokerage_per_order_inr=request.brokerage_per_order_inr,
                brokerage_rate=request.brokerage_rate,
                dp_charge_per_scrip_sell_day_inr=request.dp_charge_per_scrip_sell_day_inr,
            ),
            portfolio_policy=policy_metadata(portfolio_policy),
            universe=_universe_metadata(prepared.market_index),
            dataset=_dataset_metadata(prepared),
            historical_signal_fingerprint=prepared.signal_fingerprint,
            historical_dataset_fingerprint=prepared.dataset_fingerprint,
            portfolio_config_fingerprint=config_fp,
            portfolio_run_fingerprint=run_fp,
            engine_provenance=prepared.engine_provenance,
            diagnostics=self._diagnostics(
                prepared, request.diagnostic_detail_limit
            ),
            accepted_entry_count=len(positions),
            rejected_candidate_count=len(simulation.rejected_candidates),
            rejected_candidate_reasons=rejection_counts,
            closed_position_count=sum(item.net_pnl is not None for item in positions),
            metrics=metrics,
            cost_analytics=cost_analytics,
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
            rejected_candidates_truncated=(
                len(rejected_preview) < len(simulation.rejected_candidates)
            ),
            rejected_candidates=rejected_preview,
            timings=HistoricalResearchTimings(
                source_resolution_ms=round(prepared.source_resolution_ms, 3),
                universe_and_calendar_ms=round(
                    prepared.universe_and_calendar_ms, 3
                ),
                data_load_ms=round(prepared.series_batch.repository_load_ms, 3),
                feature_generation_ms=round(
                    prepared.series_batch.calculation_ms, 3
                ),
                composition_evaluation_ms=round(
                    prepared.composition_evaluation_ms, 3
                ),
                signal_fingerprint_ms=round(prepared.signal_fingerprint_ms, 3),
                execution_ms=round(execution_ms, 3),
                analytics_ms=round(analytics_ms, 3),
                response_build_ms=0,
                total_service_ms=0,
            ),
            executed_at=datetime.now(UTC),
            research_disclaimer=RESEARCH_DISCLAIMER,
        )
        response.timings.response_build_ms = round(
            (time.perf_counter() - response_phase) * 1_000, 3
        )
        response.timings.total_service_ms = round(
            (time.perf_counter() - started) * 1_000, 3
        )
        return response


__all__ = [
    "HistoricalCompositionService",
    "HistoricalResearchNotFoundError",
    "PreparedHistoricalComposition",
]
