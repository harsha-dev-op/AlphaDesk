from __future__ import annotations

from types import MappingProxyType

from app.research.definitions import CONSENSUS_N_OF_M_V1, CompositionPolicyDefinition


def build_composition_policy_registry(
    definitions: tuple[CompositionPolicyDefinition, ...],
) -> MappingProxyType[tuple[str, str], CompositionPolicyDefinition]:
    registry: dict[tuple[str, str], CompositionPolicyDefinition] = {}
    for definition in definitions:
        key = (definition.policy_code, definition.policy_version)
        if key in registry:
            raise ValueError(
                f"Duplicate composition policy: {definition.policy_code} v{definition.policy_version}"
            )
        if definition.minimum_components < 2:
            raise ValueError(f"Invalid minimum component count for {definition.policy_code}")
        if definition.maximum_components < definition.minimum_components:
            raise ValueError(f"Invalid maximum component count for {definition.policy_code}")
        registry[key] = definition
    return MappingProxyType(registry)


COMPOSITION_POLICIES = build_composition_policy_registry((CONSENSUS_N_OF_M_V1,))


def composition_policy_catalog() -> tuple[CompositionPolicyDefinition, ...]:
    return tuple(COMPOSITION_POLICIES.values())


def find_composition_policy(code: str, version: str) -> CompositionPolicyDefinition | None:
    return COMPOSITION_POLICIES.get((code.strip().upper(), version.strip()))


__all__ = [
    "COMPOSITION_POLICIES",
    "build_composition_policy_registry",
    "composition_policy_catalog",
    "find_composition_policy",
]
