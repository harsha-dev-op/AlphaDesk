from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import MarketIndex
from app.repositories.indices import IndexRepository
from app.repositories.securities import SecurityRepository
from app.schemas.scanner import ScannerUniverseMetadata
from app.schemas.strategies import (
    StrategyCatalogResponse,
    StrategyConditionResult,
    StrategyEvaluationRequest,
    StrategyEvaluationResponse,
    StrategyEvaluationTiming,
    StrategyMetadata,
    StrategyParameterMetadata,
    StrategyRuleMetadata,
    StrategySecurityResult,
)
from app.strategies.definitions import ParameterValue, StrategyDefinition, StrategyOperator
from app.strategies.registry import find_strategy, strategy_catalog
from app.technical.registry import FEATURE_DEFINITIONS, find_feature_set
from app.technical.service import MARKET_TIMEZONE, TechnicalFeatureService

RESEARCH_DISCLAIMER = "Research setups only — not investment recommendations. No orders are created."


class StrategyValidationError(ValueError):
    pass


class StrategyNotFoundError(ValueError):
    pass


def _universe_metadata(index: MarketIndex) -> ScannerUniverseMetadata:
    return ScannerUniverseMetadata(
        id=index.id,
        symbol=index.symbol,
        name=index.name,
        provider=index.provider,
        exchange=index.exchange,
    )


def _normalized_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonical(value: object) -> object:
    if isinstance(value, Decimal):
        return _normalized_decimal(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(_canonical(payload), separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def normalize_parameters(
    definition: StrategyDefinition,
    overrides: dict[str, ParameterValue],
) -> dict[str, ParameterValue]:
    parameter_definitions = {parameter.code: parameter for parameter in definition.parameters}
    unknown = sorted(set(overrides) - set(parameter_definitions))
    if unknown:
        raise StrategyValidationError(f"Unknown strategy parameter: {', '.join(unknown)}")

    effective: dict[str, ParameterValue] = {}
    for parameter in definition.parameters:
        value = overrides.get(parameter.code, parameter.default_value)
        if parameter.value_type == "BOOLEAN":
            if not isinstance(value, bool):
                raise StrategyValidationError(f"Parameter {parameter.code} requires a boolean value")
        else:
            if isinstance(value, bool) or not isinstance(value, Decimal):
                raise StrategyValidationError(f"Parameter {parameter.code} requires a decimal value")
            if not value.is_finite():
                raise StrategyValidationError(f"Parameter {parameter.code} must be finite")
            if parameter.minimum is not None and value < parameter.minimum:
                raise StrategyValidationError(
                    f"Parameter {parameter.code} must be at least {_normalized_decimal(parameter.minimum)}"
                )
            if parameter.maximum is not None and value > parameter.maximum:
                raise StrategyValidationError(
                    f"Parameter {parameter.code} must be at most {_normalized_decimal(parameter.maximum)}"
                )
        effective[parameter.code] = value
    return effective


def strategy_fingerprint(
    definition: StrategyDefinition,
    parameters: dict[str, ParameterValue],
    adjustment_policy: str,
) -> str:
    return _fingerprint(
        {
            "strategy_code": definition.strategy_code,
            "strategy_version": definition.strategy_version,
            "parameters": parameters,
            "rules": [
                {
                    "feature_code": rule.feature_code,
                    "feature_version": FEATURE_DEFINITIONS[rule.feature_code].version,
                    "operator": rule.operator,
                    "parameter_code": rule.parameter_code,
                }
                for rule in definition.rules
            ],
            "required_feature_set": definition.required_feature_set,
            "required_feature_set_version": definition.required_feature_set_version,
            "required_feature_versions": {
                code: FEATURE_DEFINITIONS[code].version
                for code in definition.required_feature_codes
            },
            "adjustment_policy": adjustment_policy,
        }
    )


def strategy_result_fingerprint(
    *,
    definition_fingerprint: str,
    observation_date: object,
    as_of: datetime,
    security_id: object,
    input_fingerprint: str,
    feature_state: dict[str, Decimal | bool | None],
    matched: bool,
) -> str:
    """Build the stable Phase 4 security-result identity for shared consumers."""
    return _fingerprint(
        {
            "strategy_fingerprint": definition_fingerprint,
            "observation_date": str(observation_date),
            "as_of": as_of,
            "security_id": str(security_id),
            "input_fingerprint": input_fingerprint,
            "feature_state": feature_state,
            "matched": matched,
        }
    )


def strategy_metadata(definition: StrategyDefinition) -> StrategyMetadata:
    defaults = {parameter.code: parameter.default_value for parameter in definition.parameters}
    parameters_by_code = {parameter.code: parameter for parameter in definition.parameters}
    return StrategyMetadata(
        strategy_code=definition.strategy_code,
        strategy_version=definition.strategy_version,
        display_name=definition.display_name,
        description=definition.description,
        direction=definition.direction,
        research_horizon=definition.research_horizon,
        required_feature_set=definition.required_feature_set,
        required_feature_set_version=definition.required_feature_set_version,
        required_feature_codes=list(definition.required_feature_codes),
        default_adjustment_policy=definition.default_adjustment_policy,
        evaluation_timing_policy=definition.evaluation_timing_policy,
        parameters=[
            StrategyParameterMetadata(
                code=parameter.code,
                display_name=parameter.display_name,
                description=parameter.description,
                value_type=parameter.value_type,
                default_value=parameter.default_value,
                minimum=parameter.minimum,
                maximum=parameter.maximum,
            )
            for parameter in definition.parameters
        ],
        rules=[
            StrategyRuleMetadata(
                feature_code=rule.feature_code,
                feature_version=FEATURE_DEFINITIONS[rule.feature_code].version,
                operator=rule.operator,
                parameter_code=rule.parameter_code,
                default_expected_value=parameters_by_code[rule.parameter_code].default_value,
                unit=FEATURE_DEFINITIONS[rule.feature_code].unit,
            )
            for rule in definition.rules
        ],
        minimum_warmup_observations=definition.minimum_warmup_observations,
        status=definition.status,
        default_strategy_fingerprint=strategy_fingerprint(
            definition,
            defaults,
            definition.default_adjustment_policy,
        ),
    )


def _condition_passes(
    actual: Decimal | bool | None,
    operator: StrategyOperator,
    expected: ParameterValue,
) -> bool:
    if actual is None:
        return False
    if operator == "=":
        return actual == expected
    if isinstance(actual, bool) or isinstance(expected, bool):
        return False
    if operator == ">":
        return actual > expected
    if operator == ">=":
        return actual >= expected
    if operator == "<":
        return actual < expected
    if operator == "<=":
        return actual <= expected
    raise StrategyValidationError(f"Unsupported strategy operator: {operator}")


def evaluate_strategy_conditions(
    definition: StrategyDefinition,
    parameters: dict[str, ParameterValue],
    values: dict[str, Decimal | bool | None],
    feature_versions: dict[str, str],
) -> list[StrategyConditionResult]:
    """Evaluate one immutable strategy against an already-computed feature row."""
    return [
        StrategyConditionResult(
            feature_code=rule.feature_code,
            feature_version=feature_versions[rule.feature_code],
            operator=rule.operator,
            expected_value=parameters[rule.parameter_code],
            actual_value=values[rule.feature_code],
            passed=_condition_passes(
                values[rule.feature_code],
                rule.operator,
                parameters[rule.parameter_code],
            ),
            unit=FEATURE_DEFINITIONS[rule.feature_code].unit,
        )
        for rule in definition.rules
    ]


def _display_value(value: Decimal | bool | None) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return str(value).lower()
    return _normalized_decimal(value)


class StrategyService:
    def __init__(self, session: Session):
        self.indices = IndexRepository(session)
        self.securities = SecurityRepository(session)
        self.technical = TechnicalFeatureService(session)

    def catalog(self) -> StrategyCatalogResponse:
        return StrategyCatalogResponse(
            strategies=[strategy_metadata(definition) for definition in strategy_catalog()],
            universes=[_universe_metadata(index) for index in self.indices.list()],
            adjustment_policies=["RAW", "ADJUSTED"],
            latest_observation_date=self.securities.latest_price_date(),
            research_disclaimer=RESEARCH_DISCLAIMER,
        )

    @staticmethod
    def _resolve_strategy(code: str, version: str) -> StrategyDefinition:
        definition = find_strategy(code, version)
        if definition is not None:
            return definition
        if any(item.strategy_code == code for item in strategy_catalog()):
            raise StrategyNotFoundError(f"Unsupported strategy version: {code} v{version}")
        raise StrategyNotFoundError(f"Unknown strategy: {code}")

    def evaluate(self, request: StrategyEvaluationRequest) -> StrategyEvaluationResponse:
        started = time.perf_counter()
        if request.as_of.tzinfo is None or request.as_of.utcoffset() is None:
            raise StrategyValidationError("as_of must include a timezone offset")
        if request.observation_date > request.as_of.astimezone(MARKET_TIMEZONE).date():
            raise StrategyValidationError("observation_date cannot be after the evaluation as_of date")

        definition = self._resolve_strategy(request.strategy_code, request.strategy_version)
        feature_set = find_feature_set(
            definition.required_feature_set,
            definition.required_feature_set_version,
        )
        if feature_set is None:
            raise StrategyValidationError("Required strategy feature set is unavailable")
        parameters = normalize_parameters(definition, request.parameter_overrides)
        definition_fingerprint = strategy_fingerprint(
            definition,
            parameters,
            request.adjustment_policy,
        )

        phase = time.perf_counter()
        market_index = self.indices.get_by_name_or_symbol(request.universe)
        if market_index is None:
            raise StrategyNotFoundError("Universe not found")
        memberships = self.indices.members_as_of(market_index.id, request.observation_date)
        securities_by_id = {membership.security.id: membership.security for membership in memberships}
        securities = sorted(
            securities_by_id.values(),
            key=lambda security: (security.symbol.casefold(), str(security.id)),
        )
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

        phase = time.perf_counter()
        evaluated: list[tuple[object, object, list[StrategyConditionResult], list[str], object]] = []
        unavailable_count = 0
        for security, response in zip(securities, feature_responses, strict=True):
            observation = (
                response.items[0]
                if response.items
                and response.items[0].observation_date == request.observation_date
                and response.items[0].available_at <= request.as_of
                else None
            )
            values = {
                code: observation.values.get(code) if observation is not None else None
                for code in definition.required_feature_codes
            }
            missing = [code for code, value in values.items() if value is None]
            warnings = list(response.quality_warnings)
            if missing:
                unavailable_count += 1
                warnings.append(
                    "INSUFFICIENT_FEATURE_HISTORY: required feature values unavailable: "
                    + ", ".join(missing)
                )
            conditions = evaluate_strategy_conditions(
                definition,
                parameters,
                values,
                response.feature_versions,
            )
            evaluated.append((security, response, conditions, warnings, observation))
        condition_evaluation_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        results: list[StrategySecurityResult] = []
        for security, response, conditions, warnings, observation in evaluated:
            required_values = {
                condition.feature_code: condition.actual_value for condition in conditions
            }
            passed_count = sum(condition.passed for condition in conditions)
            matched = passed_count == len(conditions)
            first_failed = next((condition for condition in conditions if not condition.passed), None)
            explanation = (
                f"Matched because {passed_count} of {len(conditions)} required "
                f"{definition.display_name} v{definition.strategy_version} conditions passed."
                if matched
                else (
                    "Not matched because required point-in-time feature history was unavailable."
                    if first_failed is not None and first_failed.actual_value is None
                    else (
                        f"Not matched because {first_failed.feature_code} was "
                        f"{_display_value(first_failed.actual_value)} while the strategy requires "
                        f"{first_failed.operator} {_display_value(first_failed.expected_value)}."
                    )
                )
            )
            result_fingerprint = strategy_result_fingerprint(
                definition_fingerprint=definition_fingerprint,
                observation_date=request.observation_date,
                as_of=request.as_of,
                security_id=security.id,
                input_fingerprint=response.dataset.fingerprint,
                feature_state=required_values,
                matched=matched,
            )
            results.append(
                StrategySecurityResult(
                    security_id=security.id,
                    symbol=security.symbol,
                    company_name=security.company_name,
                    exchange=security.exchange,
                    observation_date=request.observation_date,
                    as_of=request.as_of,
                    available_at=observation.available_at if observation is not None else None,
                    strategy_code=definition.strategy_code,
                    strategy_version=definition.strategy_version,
                    direction=definition.direction,
                    matched=matched,
                    required_feature_values=required_values,
                    conditions=conditions,
                    passed_condition_count=passed_count,
                    total_condition_count=len(conditions),
                    adjustment_policy=request.adjustment_policy,
                    feature_set_code=response.feature_set.code,
                    feature_set_version=response.feature_set.version,
                    feature_versions={
                        code: response.feature_versions[code]
                        for code in definition.required_feature_codes
                    },
                    dataset=response.dataset,
                    input_fingerprint=response.dataset.fingerprint,
                    strategy_fingerprint=definition_fingerprint,
                    result_fingerprint=result_fingerprint,
                    explanation=explanation,
                    warnings=warnings,
                )
            )
        evaluation_fingerprint = _fingerprint(
            {
                "strategy_fingerprint": definition_fingerprint,
                "universe_id": str(market_index.id),
                "observation_date": request.observation_date.isoformat(),
                "as_of": request.as_of,
                "result_fingerprints": [result.result_fingerprint for result in results],
            }
        )
        response_build_ms = (time.perf_counter() - phase) * 1_000
        total_service_ms = (time.perf_counter() - started) * 1_000
        response_warnings = sorted({warning for result in results for warning in result.warnings})

        return StrategyEvaluationResponse(
            universe=_universe_metadata(market_index),
            observation_date=request.observation_date,
            as_of=request.as_of,
            executed_at=datetime.now(UTC),
            strategy=strategy_metadata(definition),
            effective_parameters=parameters,
            adjustment_policy=request.adjustment_policy,
            universe_member_count=len(securities),
            evaluated_security_count=len(results),
            unavailable_security_count=unavailable_count,
            matched_count=sum(result.matched for result in results),
            evaluation_fingerprint=evaluation_fingerprint,
            warnings=response_warnings,
            timings=StrategyEvaluationTiming(
                universe_resolution_ms=round(universe_resolution_ms, 3),
                feature_computation_ms=round(feature_computation_ms, 3),
                condition_evaluation_ms=round(condition_evaluation_ms, 3),
                response_build_ms=round(response_build_ms, 3),
                total_service_ms=round(total_service_ms, 3),
            ),
            results=results,
        )
