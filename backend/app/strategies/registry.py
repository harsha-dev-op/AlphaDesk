from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType

from app.strategies.definitions import INITIAL_STRATEGIES, StrategyDefinition
from app.technical.registry import FEATURE_DEFINITIONS, find_feature_set


def build_strategy_registry(
    definitions: tuple[StrategyDefinition, ...],
) -> MappingProxyType[tuple[str, str], StrategyDefinition]:
    registry: dict[tuple[str, str], StrategyDefinition] = {}
    for definition in definitions:
        key = (definition.strategy_code, definition.strategy_version)
        if key in registry:
            raise ValueError(f"Duplicate strategy definition: {definition.strategy_code} v{definition.strategy_version}")
        feature_set = find_feature_set(
            definition.required_feature_set,
            definition.required_feature_set_version,
        )
        if feature_set is None:
            raise ValueError(f"Unknown required feature set for {definition.strategy_code}")
        parameters = {parameter.code: parameter for parameter in definition.parameters}
        if len(parameters) != len(definition.parameters):
            raise ValueError(f"Duplicate parameter definition in {definition.strategy_code}")
        for parameter in definition.parameters:
            if parameter.value_type == "BOOLEAN":
                if not isinstance(parameter.default_value, bool):
                    raise ValueError(f"Invalid boolean default: {parameter.code}")
            elif isinstance(parameter.default_value, bool) or not isinstance(parameter.default_value, Decimal):
                raise ValueError(f"Invalid decimal default: {parameter.code}")
            elif (
                parameter.minimum is not None
                and parameter.default_value < parameter.minimum
            ) or (
                parameter.maximum is not None
                and parameter.default_value > parameter.maximum
            ):
                raise ValueError(f"Default outside bounds: {parameter.code}")
        for rule in definition.rules:
            feature = FEATURE_DEFINITIONS.get(rule.feature_code)
            if feature is None or rule.feature_code not in feature_set.feature_codes:
                raise ValueError(f"Unknown required feature: {rule.feature_code}")
            parameter = parameters.get(rule.parameter_code)
            if parameter is None:
                raise ValueError(f"Unknown rule parameter: {rule.parameter_code}")
            if feature.output_type != parameter.value_type:
                raise ValueError(f"Feature/parameter type mismatch: {rule.feature_code}")
            if feature.output_type == "BOOLEAN" and rule.operator != "=":
                raise ValueError(f"Boolean rule must use equality: {rule.feature_code}")
        registry[key] = definition
    return MappingProxyType(registry)


STRATEGY_DEFINITIONS = build_strategy_registry(INITIAL_STRATEGIES)


def strategy_catalog() -> tuple[StrategyDefinition, ...]:
    return tuple(STRATEGY_DEFINITIONS.values())


def find_strategy(code: str, version: str) -> StrategyDefinition | None:
    return STRATEGY_DEFINITIONS.get((code.strip().upper(), version.strip()))
