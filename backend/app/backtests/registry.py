from __future__ import annotations

from types import MappingProxyType

from app.backtests.definitions import (
    INDIA_NSE_CASH_DELIVERY_2026_09_V1,
    NEXT_OPEN_FIXED_HOLD_V1,
    BacktestProfileDefinition,
    CostModelDefinition,
)
from app.strategies.registry import find_strategy


def build_profile_registry(
    definitions: tuple[BacktestProfileDefinition, ...],
) -> MappingProxyType[tuple[str, str], BacktestProfileDefinition]:
    registry: dict[tuple[str, str], BacktestProfileDefinition] = {}
    for definition in definitions:
        key = (definition.profile_code, definition.profile_version)
        if key in registry:
            raise ValueError(f"Duplicate backtest profile: {definition.profile_code} v{definition.profile_version}")
        defaults = dict(definition.default_holding_sessions)
        if set(defaults) != set(definition.compatible_strategies):
            raise ValueError(f"Holding-period coverage mismatch for {definition.profile_code}")
        if any(find_strategy(code, "1") is None for code in definition.compatible_strategies):
            raise ValueError(f"Unknown compatible strategy for {definition.profile_code}")
        if any(value < 1 for value in defaults.values()):
            raise ValueError(f"Invalid holding period for {definition.profile_code}")
        registry[key] = definition
    return MappingProxyType(registry)


def build_cost_model_registry(
    definitions: tuple[CostModelDefinition, ...],
) -> MappingProxyType[tuple[str, str], CostModelDefinition]:
    registry: dict[tuple[str, str], CostModelDefinition] = {}
    for definition in definitions:
        key = (definition.code, definition.version)
        if key in registry:
            raise ValueError(f"Duplicate cost model: {definition.code} v{definition.version}")
        rates = (
            definition.stt_buy_rate,
            definition.stt_sell_rate,
            definition.exchange_transaction_rate,
            definition.sebi_turnover_rate,
            definition.gst_rate,
            definition.stamp_duty_buy_rate,
        )
        if any(rate < 0 for rate in rates):
            raise ValueError(f"Negative rate in {definition.code}")
        registry[key] = definition
    return MappingProxyType(registry)


BACKTEST_PROFILES = build_profile_registry((NEXT_OPEN_FIXED_HOLD_V1,))
COST_MODELS = build_cost_model_registry((INDIA_NSE_CASH_DELIVERY_2026_09_V1,))


def profile_catalog() -> tuple[BacktestProfileDefinition, ...]:
    return tuple(BACKTEST_PROFILES.values())


def cost_model_catalog() -> tuple[CostModelDefinition, ...]:
    return tuple(COST_MODELS.values())


def find_profile(code: str, version: str) -> BacktestProfileDefinition | None:
    return BACKTEST_PROFILES.get((code.strip().upper(), version.strip()))


def find_cost_model(code: str, version: str) -> CostModelDefinition | None:
    return COST_MODELS.get((code.strip().upper(), version.strip()))


__all__ = [
    "BACKTEST_PROFILES",
    "COST_MODELS",
    "build_cost_model_registry",
    "build_profile_registry",
    "cost_model_catalog",
    "find_cost_model",
    "find_profile",
    "profile_catalog",
]
