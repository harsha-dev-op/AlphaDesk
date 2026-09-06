from app.technical.definitions import CORE_TECHNICAL_SET, FEATURE_DEFINITIONS, FEATURE_SETS, STRATEGY_TECHNICAL_SET, FeatureDefinition, FeatureSetDefinition


def feature_catalog() -> tuple[FeatureDefinition, ...]:
    return tuple(FEATURE_DEFINITIONS.values())


def feature_set_catalog() -> tuple[FeatureSetDefinition, ...]:
    return tuple(FEATURE_SETS.values())


def find_feature_set(code: str, version: str) -> FeatureSetDefinition | None:
    return FEATURE_SETS.get((code, version))


__all__ = ["CORE_TECHNICAL_SET", "STRATEGY_TECHNICAL_SET", "FEATURE_DEFINITIONS", "feature_catalog", "feature_set_catalog", "find_feature_set"]
