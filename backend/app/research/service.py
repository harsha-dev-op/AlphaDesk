from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.backtests.fingerprints import fingerprint
from app.core.config import get_settings
from app.models import MarketIndex, ResearchExperiment, ResearchExperimentRun
from app.repositories.indices import IndexRepository
from app.repositories.securities import SecurityRepository
from app.research.composition import consensus_status
from app.research.definitions import CompositionPolicyDefinition
from app.research.registry import composition_policy_catalog, find_composition_policy
from app.research.repository import ResearchExperimentRepository
from app.schemas.research import (
    CompositionComponentConfiguration,
    CompositionComponentRequest,
    CompositionComponentResult,
    CompositionEvaluationRequest,
    CompositionEvaluationResponse,
    CompositionMetadataResponse,
    CompositionPolicyMetadata,
    CompositionSecurityResult,
    CompositionTiming,
    ExperimentCreateRequest,
    ExperimentCreateResponse,
    ExperimentDefinitionResponse,
    ExperimentListItem,
    ExperimentListResponse,
    ExperimentMemberOutcome,
    ExperimentReplayResponse,
    ExperimentRunListResponse,
    ExperimentRunResponse,
    ExperimentRunSummary,
    ReplayStatus,
)
from app.schemas.scanner import ScannerUniverseMetadata
from app.strategies.definitions import ParameterValue, StrategyDefinition
from app.strategies.registry import find_strategy, strategy_catalog
from app.strategies.service import (
    RESEARCH_DISCLAIMER,
    StrategyNotFoundError,
    evaluate_strategy_conditions,
    normalize_parameters,
    strategy_fingerprint,
    strategy_metadata,
    strategy_result_fingerprint,
)
from app.technical.definitions import FeatureSetDefinition
from app.technical.registry import FEATURE_DEFINITIONS, feature_set_catalog, find_feature_set
from app.technical.service import MARKET_TIMEZONE, TechnicalFeatureService


class ResearchValidationError(ValueError):
    pass


class CompositionPolicyNotFoundError(ValueError):
    pass


class ExperimentNotFoundError(ValueError):
    pass


class ResearchInvariantError(RuntimeError):
    pass


class ResearchPersistenceError(RuntimeError):
    pass


def _universe_metadata(index: MarketIndex) -> ScannerUniverseMetadata:
    return ScannerUniverseMetadata(
        id=index.id,
        symbol=index.symbol,
        name=index.name,
        provider=index.provider,
        exchange=index.exchange,
    )


def policy_metadata(definition: CompositionPolicyDefinition) -> CompositionPolicyMetadata:
    return CompositionPolicyMetadata(
        **asdict(definition),
        policy_fingerprint=fingerprint(asdict(definition)),
    )


def _component_sort_key(
    item: tuple[StrategyDefinition, dict[str, ParameterValue]],
) -> tuple[str, str, str]:
    definition, parameters = item
    return (
        definition.strategy_code,
        definition.strategy_version,
        fingerprint(parameters),
    )


def _composition_feature_set(
    definitions: list[StrategyDefinition],
    union_feature_codes: tuple[str, ...],
) -> FeatureSetDefinition:
    required_sets = {
        (definition.required_feature_set, definition.required_feature_set_version)
        for definition in definitions
    }
    if len(required_sets) == 1:
        code, version = next(iter(required_sets))
        feature_set = find_feature_set(code, version)
        if feature_set is None:
            raise ResearchInvariantError("A component's registered feature set is unavailable")
        if set(union_feature_codes).issubset(feature_set.feature_codes):
            return feature_set

    candidates = [
        item
        for item in feature_set_catalog()
        if set(union_feature_codes).issubset(item.feature_codes)
    ]
    if not candidates:
        raise ResearchInvariantError("No authoritative feature set covers the component dependency union")
    return min(candidates, key=lambda item: (len(item.feature_codes), item.code, item.version))


class ResearchService:
    def __init__(self, session: Session):
        self.session = session
        self.indices = IndexRepository(session)
        self.securities = SecurityRepository(session)
        self.technical = TechnicalFeatureService(session)
        self.experiments = ResearchExperimentRepository(session)

    def metadata(self) -> CompositionMetadataResponse:
        return CompositionMetadataResponse(
            policies=[policy_metadata(item) for item in composition_policy_catalog()],
            strategies=[strategy_metadata(item) for item in strategy_catalog()],
            universes=[_universe_metadata(item) for item in self.indices.list()],
            adjustment_policies=["RAW", "ADJUSTED"],
            latest_observation_date=self.securities.latest_price_date(),
            research_disclaimer=RESEARCH_DISCLAIMER,
        )

    @staticmethod
    def _resolve_policy(code: str, version: str) -> CompositionPolicyDefinition:
        policy = find_composition_policy(code, version)
        if policy is not None:
            return policy
        if any(item.policy_code == code for item in composition_policy_catalog()):
            raise CompositionPolicyNotFoundError(
                f"Unsupported composition policy version: {code} v{version}"
            )
        raise CompositionPolicyNotFoundError(f"Unknown composition policy: {code}")

    @staticmethod
    def _resolve_strategy(code: str, version: str) -> StrategyDefinition:
        definition = find_strategy(code, version)
        if definition is not None:
            return definition
        if any(item.strategy_code == code for item in strategy_catalog()):
            raise StrategyNotFoundError(f"Unsupported strategy version: {code} v{version}")
        raise StrategyNotFoundError(f"Unknown strategy: {code}")

    def _resolve_components(
        self,
        request: CompositionEvaluationRequest,
        policy: CompositionPolicyDefinition,
    ) -> list[tuple[StrategyDefinition, dict[str, ParameterValue]]]:
        if not policy.minimum_components <= len(request.components) <= policy.maximum_components:
            raise ResearchValidationError(
                f"{policy.policy_code} v{policy.policy_version} requires between "
                f"{policy.minimum_components} and {policy.maximum_components} components"
            )
        if not 1 <= request.required_match_count <= len(request.components):
            raise ResearchValidationError(
                "required_match_count must be between 1 and the number of components"
            )
        keys = [(item.strategy_code, item.strategy_version) for item in request.components]
        if len(keys) != len(set(keys)):
            raise ResearchValidationError("Duplicate strategy components are not allowed")

        resolved: list[tuple[StrategyDefinition, dict[str, ParameterValue]]] = []
        for component in request.components:
            definition = self._resolve_strategy(
                component.strategy_code, component.strategy_version
            )
            resolved.append(
                (definition, normalize_parameters(definition, component.parameter_overrides))
            )
        return sorted(resolved, key=_component_sort_key)

    @staticmethod
    def _normalized_request(
        request: CompositionEvaluationRequest,
        components: list[tuple[StrategyDefinition, dict[str, ParameterValue]]],
    ) -> CompositionEvaluationRequest:
        return CompositionEvaluationRequest(
            policy_code=request.policy_code,
            policy_version=request.policy_version,
            universe=request.universe,
            observation_date=request.observation_date,
            as_of=request.as_of,
            adjustment_policy=request.adjustment_policy,
            required_match_count=request.required_match_count,
            components=[
                CompositionComponentRequest(
                    strategy_code=definition.strategy_code,
                    strategy_version=definition.strategy_version,
                    parameter_overrides=parameters,
                )
                for definition, parameters in components
            ],
        )

    @staticmethod
    def _engine_provenance(
        policy: CompositionPolicyDefinition,
        components: list[tuple[StrategyDefinition, dict[str, ParameterValue]]],
        adjustment_policy: str,
        feature_set: FeatureSetDefinition,
        union_feature_codes: tuple[str, ...],
    ) -> dict[str, object]:
        return {
            "alphadesk_app_version": get_settings().app_version,
            "composition_engine": "ALPHADESK_RESEARCH_COMPOSITION",
            "composition_engine_version": "1",
            "policy_fingerprint": fingerprint(asdict(policy)),
            "strategy_fingerprints": {
                f"{definition.strategy_code}:v{definition.strategy_version}": strategy_fingerprint(
                    definition, parameters, adjustment_policy
                )
                for definition, parameters in components
            },
            "feature_set": {"code": feature_set.code, "version": feature_set.version},
            "feature_versions": {
                code: FEATURE_DEFINITIONS[code].version for code in union_feature_codes
            },
        }

    def evaluate(self, request: CompositionEvaluationRequest) -> CompositionEvaluationResponse:
        started = time.perf_counter()
        if request.as_of.tzinfo is None or request.as_of.utcoffset() is None:
            raise ResearchValidationError("as_of must include a timezone offset")
        if request.observation_date > request.as_of.astimezone(MARKET_TIMEZONE).date():
            raise ResearchValidationError(
                "observation_date cannot be after the evaluation as_of date"
            )

        policy = self._resolve_policy(request.policy_code, request.policy_version)
        components = self._resolve_components(request, policy)
        normalized_request = self._normalized_request(request, components)
        union_feature_codes = tuple(
            sorted({code for definition, _ in components for code in definition.required_feature_codes})
        )
        feature_set = _composition_feature_set(
            [definition for definition, _ in components], union_feature_codes
        )
        config_fingerprint = fingerprint(
            {
                "policy_code": policy.policy_code,
                "policy_version": policy.policy_version,
                "normalized_request": normalized_request.model_dump(),
            }
        )

        phase = time.perf_counter()
        market_index = self.indices.get_by_name_or_symbol(request.universe)
        if market_index is None:
            raise StrategyNotFoundError("Universe not found")
        try:
            self.indices.assert_historical_coverage(market_index, request.observation_date)
        except ValueError as exc:
            raise ResearchValidationError(str(exc)) from exc
        memberships = self.indices.members_as_of(market_index.id, request.observation_date)
        securities_by_id = {item.security.id: item.security for item in memberships}
        securities = sorted(
            securities_by_id.values(), key=lambda item: (item.symbol.casefold(), str(item.id))
        )
        if not securities:
            raise ResearchValidationError("Historical universe has no members for observation_date")
        universe_resolution_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        feature_responses = self.technical.compute_latest_batch(
            securities,
            adjustment_policy=request.adjustment_policy,
            as_of=request.as_of,
            observation_date=request.observation_date,
            feature_set=feature_set,
        )
        feature_computation_ms = (time.perf_counter() - phase) * 1_000

        component_fingerprints = {
            (definition.strategy_code, definition.strategy_version): strategy_fingerprint(
                definition, parameters, request.adjustment_policy
            )
            for definition, parameters in components
        }
        component_configurations = [
            CompositionComponentConfiguration(
                strategy=strategy_metadata(definition),
                effective_parameters=parameters,
                strategy_fingerprint=component_fingerprints[
                    (definition.strategy_code, definition.strategy_version)
                ],
            )
            for definition, parameters in components
        ]

        phase = time.perf_counter()
        pending: list[tuple[object, object, object, list[CompositionComponentResult], list[str]]] = []
        for security, feature_response in zip(securities, feature_responses, strict=True):
            observation = (
                feature_response.items[0]
                if feature_response.items
                and feature_response.items[0].observation_date == request.observation_date
                and feature_response.items[0].available_at <= request.as_of
                else None
            )
            component_results: list[CompositionComponentResult] = []
            member_warnings = list(feature_response.quality_warnings)
            for definition, parameters in components:
                values = {
                    code: observation.values.get(code) if observation is not None else None
                    for code in definition.required_feature_codes
                }
                missing = [code for code, value in values.items() if value is None]
                conditions = evaluate_strategy_conditions(
                    definition,
                    parameters,
                    values,
                    feature_response.feature_versions,
                )
                matched = not missing and all(item.passed for item in conditions)
                status = (
                    "INSUFFICIENT_FEATURE_HISTORY"
                    if missing
                    else ("MATCHED" if matched else "NOT_MATCHED")
                )
                warnings: list[str] = []
                if missing:
                    warning = (
                        "INSUFFICIENT_FEATURE_HISTORY: required feature values unavailable: "
                        + ", ".join(missing)
                    )
                    warnings.append(warning)
                    member_warnings.append(warning)
                definition_fingerprint = component_fingerprints[
                    (definition.strategy_code, definition.strategy_version)
                ]
                component_results.append(
                    CompositionComponentResult(
                        strategy_code=definition.strategy_code,
                        strategy_version=definition.strategy_version,
                        status=status,
                        effective_parameters=parameters,
                        required_feature_values=values,
                        conditions=conditions,
                        passed_condition_count=sum(item.passed for item in conditions),
                        total_condition_count=len(conditions),
                        strategy_fingerprint=definition_fingerprint,
                        result_fingerprint=strategy_result_fingerprint(
                            definition_fingerprint=definition_fingerprint,
                            observation_date=request.observation_date,
                            as_of=request.as_of,
                            security_id=security.id,
                            input_fingerprint=feature_response.dataset.fingerprint,
                            feature_state=values,
                            matched=matched,
                        ),
                        warnings=warnings,
                    )
                )
            pending.append(
                (security, feature_response, observation, component_results, sorted(set(member_warnings)))
            )
        component_evaluation_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        results: list[CompositionSecurityResult] = []
        for security, feature_response, observation, component_results, warnings in pending:
            matched_count = sum(item.status == "MATCHED" for item in component_results)
            insufficient_count = sum(
                item.status == "INSUFFICIENT_FEATURE_HISTORY" for item in component_results
            )
            status = consensus_status(
                matched_count=matched_count,
                insufficient_count=insufficient_count,
                required_count=request.required_match_count,
            )
            result_fingerprint = fingerprint(
                {
                    "composition_config_fingerprint": config_fingerprint,
                    "security_id": str(security.id),
                    "input_fingerprint": feature_response.dataset.fingerprint,
                    "status": status,
                    "matched_count": matched_count,
                    "insufficient_count": insufficient_count,
                    "component_result_fingerprints": [
                        item.result_fingerprint for item in component_results
                    ],
                }
            )
            results.append(
                CompositionSecurityResult(
                    security_id=security.id,
                    symbol=security.symbol,
                    company_name=security.company_name,
                    exchange=security.exchange,
                    observation_date=request.observation_date,
                    as_of=request.as_of,
                    available_at=observation.available_at if observation is not None else None,
                    status=status,
                    matched_strategy_count=matched_count,
                    insufficient_strategy_count=insufficient_count,
                    required_match_count=request.required_match_count,
                    adjustment_policy=request.adjustment_policy,
                    input_fingerprint=feature_response.dataset.fingerprint,
                    composition_result_fingerprint=result_fingerprint,
                    component_results=component_results,
                    warnings=warnings,
                )
            )
        composition_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        dataset_fingerprint = fingerprint(
            {
                "universe_id": str(market_index.id),
                "historical_membership_date": request.observation_date,
                "as_of": request.as_of,
                "adjustment_policy": request.adjustment_policy,
                "members": [
                    {
                        "security_id": str(result.security_id),
                        "input_fingerprint": result.input_fingerprint,
                    }
                    for result in results
                ],
            }
        )
        engine_provenance = self._engine_provenance(
            policy,
            components,
            request.adjustment_policy,
            feature_set,
            union_feature_codes,
        )
        run_fingerprint = fingerprint(
            {
                "composition_config_fingerprint": config_fingerprint,
                "composition_dataset_fingerprint": dataset_fingerprint,
                "engine_provenance": engine_provenance,
                "composition_result_fingerprints": [
                    item.composition_result_fingerprint for item in results
                ],
            }
        )
        response_warnings = sorted({warning for item in results for warning in item.warnings})
        response_build_ms = (time.perf_counter() - phase) * 1_000
        total_service_ms = (time.perf_counter() - started) * 1_000

        return CompositionEvaluationResponse(
            normalized_request=normalized_request,
            policy=policy_metadata(policy),
            components=component_configurations,
            universe=_universe_metadata(market_index),
            observation_date=request.observation_date,
            as_of=request.as_of,
            executed_at=datetime.now(UTC),
            adjustment_policy=request.adjustment_policy,
            union_feature_codes=list(union_feature_codes),
            universe_member_count=len(securities),
            evaluated_security_count=len(results),
            matched_count=sum(item.status == "MATCHED" for item in results),
            not_matched_count=sum(item.status == "NOT_MATCHED" for item in results),
            insufficient_history_count=sum(
                item.status == "INSUFFICIENT_FEATURE_HISTORY" for item in results
            ),
            composition_config_fingerprint=config_fingerprint,
            composition_dataset_fingerprint=dataset_fingerprint,
            composition_run_fingerprint=run_fingerprint,
            engine_provenance=engine_provenance,
            warnings=response_warnings,
            timings=CompositionTiming(
                universe_resolution_ms=round(universe_resolution_ms, 3),
                feature_computation_ms=round(feature_computation_ms, 3),
                component_evaluation_ms=round(component_evaluation_ms, 3),
                composition_ms=round(composition_ms, 3),
                response_build_ms=round(response_build_ms, 3),
                total_service_ms=round(total_service_ms, 3),
            ),
            results=results,
        )

    @staticmethod
    def _summary(evaluation: CompositionEvaluationResponse) -> dict[str, int]:
        return {
            "total_members": evaluation.evaluated_security_count,
            "matched": evaluation.matched_count,
            "not_matched": evaluation.not_matched_count,
            "insufficient_history": evaluation.insufficient_history_count,
        }

    @staticmethod
    def _member_outcomes(evaluation: CompositionEvaluationResponse) -> list[dict[str, object]]:
        return [
            {
                "symbol": member.symbol,
                "status": member.status,
                "matched_strategy_count": member.matched_strategy_count,
                "insufficient_strategy_count": member.insufficient_strategy_count,
                "required_match_count": member.required_match_count,
                "composition_result_fingerprint": member.composition_result_fingerprint,
                "components": [
                    {
                        "strategy_code": component.strategy_code,
                        "strategy_version": component.strategy_version,
                        "status": component.status,
                        "strategy_fingerprint": component.strategy_fingerprint,
                        "result_fingerprint": component.result_fingerprint,
                    }
                    for component in member.component_results
                ],
            }
            for member in evaluation.results
        ]

    @staticmethod
    def _stored_request(evaluation: CompositionEvaluationResponse) -> dict[str, object]:
        return evaluation.normalized_request.model_dump(mode="json")

    def _new_run(
        self,
        *,
        experiment_id: UUID,
        evaluation: CompositionEvaluationResponse,
        replay_status: ReplayStatus,
        reference_run_id: UUID | None,
    ) -> ResearchExperimentRun:
        return ResearchExperimentRun(
            experiment_id=experiment_id,
            executed_at=evaluation.executed_at,
            replay_status=replay_status,
            reference_run_id=reference_run_id,
            normalized_request=self._stored_request(evaluation),
            composition_config_fingerprint=evaluation.composition_config_fingerprint,
            dataset_fingerprint=evaluation.composition_dataset_fingerprint,
            run_fingerprint=evaluation.composition_run_fingerprint,
            engine_provenance=evaluation.engine_provenance,
            result_summary=self._summary(evaluation),
            member_outcomes=self._member_outcomes(evaluation),
            warnings=list(evaluation.warnings),
        )

    @staticmethod
    def _run_response(run: ResearchExperimentRun) -> ExperimentRunResponse:
        return ExperimentRunResponse(
            id=run.id,
            experiment_id=run.experiment_id,
            executed_at=run.executed_at,
            replay_status=run.replay_status,
            reference_run_id=run.reference_run_id,
            normalized_request=CompositionEvaluationRequest.model_validate(run.normalized_request),
            composition_config_fingerprint=run.composition_config_fingerprint,
            dataset_fingerprint=run.dataset_fingerprint,
            run_fingerprint=run.run_fingerprint,
            engine_provenance=run.engine_provenance,
            summary=ExperimentRunSummary.model_validate(run.result_summary),
            member_outcomes=[
                ExperimentMemberOutcome.model_validate(item) for item in run.member_outcomes
            ],
            warnings=list(run.warnings),
        )

    @classmethod
    def _experiment_response(
        cls,
        experiment: ResearchExperiment,
        latest_run: ResearchExperimentRun | None,
    ) -> ExperimentDefinitionResponse:
        return ExperimentDefinitionResponse(
            id=experiment.id,
            name=experiment.name,
            description=experiment.description,
            policy_code=experiment.policy_code,
            policy_version=experiment.policy_version,
            normalized_request=CompositionEvaluationRequest.model_validate(
                experiment.normalized_request
            ),
            config_fingerprint=experiment.config_fingerprint,
            created_at=experiment.created_at,
            latest_run=cls._run_response(latest_run) if latest_run is not None else None,
        )

    def create_experiment(self, request: ExperimentCreateRequest) -> ExperimentCreateResponse:
        evaluation = self.evaluate(request.composition)
        experiment = ResearchExperiment(
            name=request.name,
            description=request.description,
            policy_code=evaluation.policy.policy_code,
            policy_version=evaluation.policy.policy_version,
            normalized_request=self._stored_request(evaluation),
            config_fingerprint=evaluation.composition_config_fingerprint,
        )
        try:
            self.experiments.add_experiment(experiment)
            run = self.experiments.add_run(
                self._new_run(
                    experiment_id=experiment.id,
                    evaluation=evaluation,
                    replay_status="INITIAL",
                    reference_run_id=None,
                )
            )
            self.session.commit()
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise ResearchPersistenceError("Unable to save the research experiment") from exc
        return ExperimentCreateResponse(
            experiment=self._experiment_response(experiment, run),
            initial_evaluation=evaluation,
        )

    def list_experiments(self, *, page: int, page_size: int) -> ExperimentListResponse:
        experiments, total, latest = self.experiments.list_page(page=page, page_size=page_size)
        items: list[ExperimentListItem] = []
        for experiment in experiments:
            normalized = CompositionEvaluationRequest.model_validate(experiment.normalized_request)
            latest_run = latest.get(experiment.id)
            items.append(
                ExperimentListItem(
                    id=experiment.id,
                    name=experiment.name,
                    description=experiment.description,
                    policy_code=experiment.policy_code,
                    policy_version=experiment.policy_version,
                    strategy_count=len(normalized.components),
                    required_match_count=normalized.required_match_count,
                    universe=normalized.universe,
                    observation_date=normalized.observation_date,
                    adjustment_policy=normalized.adjustment_policy,
                    config_fingerprint=experiment.config_fingerprint,
                    created_at=experiment.created_at,
                    latest_replay_status=latest_run.replay_status if latest_run else None,
                    latest_run_at=latest_run.executed_at if latest_run else None,
                    latest_run_fingerprint=latest_run.run_fingerprint if latest_run else None,
                )
            )
        return ExperimentListResponse(items=items, total=total, page=page, page_size=page_size)

    def experiment_detail(self, experiment_id: UUID) -> ExperimentDefinitionResponse:
        experiment = self.experiments.get(experiment_id)
        if experiment is None:
            raise ExperimentNotFoundError("Research experiment not found")
        return self._experiment_response(
            experiment, self.experiments.latest_run(experiment_id)
        )

    def experiment_runs(
        self,
        experiment_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> ExperimentRunListResponse:
        if self.experiments.get(experiment_id) is None:
            raise ExperimentNotFoundError("Research experiment not found")
        runs, total = self.experiments.runs_page(
            experiment_id, page=page, page_size=page_size
        )
        return ExperimentRunListResponse(
            items=[self._run_response(item) for item in runs],
            total=total,
            page=page,
            page_size=page_size,
        )

    def replay(self, experiment_id: UUID) -> ExperimentReplayResponse:
        experiment = self.experiments.get(experiment_id)
        if experiment is None:
            raise ExperimentNotFoundError("Research experiment not found")
        reference = self.experiments.latest_run(experiment_id)
        if reference is None:
            raise ResearchInvariantError("Saved experiment has no reference run")

        saved_request = CompositionEvaluationRequest.model_validate(experiment.normalized_request)
        evaluation = self.evaluate(saved_request)
        if evaluation.composition_config_fingerprint != experiment.config_fingerprint:
            raise ResearchInvariantError(
                "Saved experiment configuration fingerprint no longer validates"
            )
        if evaluation.composition_dataset_fingerprint != reference.dataset_fingerprint:
            replay_status: ReplayStatus = "DATASET_DRIFT_DETECTED"
        elif evaluation.composition_run_fingerprint != reference.run_fingerprint:
            replay_status = "ENGINE_OR_RESULT_DRIFT_DETECTED"
        else:
            replay_status = "REPRODUCED"

        try:
            run = self.experiments.add_run(
                self._new_run(
                    experiment_id=experiment.id,
                    evaluation=evaluation,
                    replay_status=replay_status,
                    reference_run_id=reference.id,
                )
            )
            self.session.commit()
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise ResearchPersistenceError("Unable to save the experiment replay") from exc
        return ExperimentReplayResponse(
            experiment=self._experiment_response(experiment, run),
            run=self._run_response(run),
            evaluation=evaluation,
        )


__all__ = [
    "CompositionPolicyNotFoundError",
    "ExperimentNotFoundError",
    "ResearchInvariantError",
    "ResearchPersistenceError",
    "ResearchService",
    "ResearchValidationError",
    "policy_metadata",
]
