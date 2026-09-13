from __future__ import annotations

from types import MappingProxyType

from app.backtests.registry import find_profile
from app.portfolio.definitions import LONG_ONLY_EQUAL_SLOT_PORTFOLIO_V1, PortfolioPolicyDefinition


def build_portfolio_policy_registry(
    definitions: tuple[PortfolioPolicyDefinition, ...],
) -> MappingProxyType[tuple[str, str], PortfolioPolicyDefinition]:
    registry: dict[tuple[str, str], PortfolioPolicyDefinition] = {}
    for definition in definitions:
        key = (definition.policy_code, definition.policy_version)
        if key in registry:
            raise ValueError(f"Duplicate portfolio policy: {definition.policy_code} v{definition.policy_version}")
        if any(find_profile(code, "1") is None for code in definition.compatible_profiles):
            raise ValueError(f"Unknown compatible profile for {definition.policy_code}")
        if definition.default_initial_capital_inr <= 0 or definition.default_max_concurrent_positions < 1:
            raise ValueError(f"Invalid capital or capacity for {definition.policy_code}")
        if not 0 < definition.default_max_position_weight <= 1:
            raise ValueError(f"Invalid maximum position weight for {definition.policy_code}")
        if not 0 < definition.default_max_gross_exposure <= 1:
            raise ValueError(f"Invalid gross exposure for {definition.policy_code}")
        if not 0 <= definition.default_minimum_cash_reserve_pct < 1:
            raise ValueError(f"Invalid cash reserve for {definition.policy_code}")
        registry[key] = definition
    return MappingProxyType(registry)


PORTFOLIO_POLICIES = build_portfolio_policy_registry((LONG_ONLY_EQUAL_SLOT_PORTFOLIO_V1,))


def portfolio_policy_catalog() -> tuple[PortfolioPolicyDefinition, ...]:
    return tuple(PORTFOLIO_POLICIES.values())


def find_portfolio_policy(code: str, version: str) -> PortfolioPolicyDefinition | None:
    return PORTFOLIO_POLICIES.get((code.strip().upper(), version.strip()))


__all__ = [
    "PORTFOLIO_POLICIES",
    "build_portfolio_policy_registry",
    "find_portfolio_policy",
    "portfolio_policy_catalog",
]
