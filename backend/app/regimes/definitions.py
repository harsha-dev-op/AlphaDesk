from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


MarketRegime = Literal[
    "TRENDING_BULL",
    "TRENDING_BEAR",
    "SIDEWAYS",
    "HIGH_VOLATILITY",
]
RegimeClassification = Literal[
    "TRENDING_BULL",
    "TRENDING_BEAR",
    "SIDEWAYS",
    "HIGH_VOLATILITY",
    "INSUFFICIENT_HISTORY",
]
SourceMode = Literal["DEMO", "OFFICIAL"]


@dataclass(frozen=True, slots=True)
class MarketRegimeDefinition:
    code: str
    version: str
    display_name: str
    description: str
    required_feature_codes: tuple[str, ...]
    volatility_percentile_window: int
    volatility_percentile: Decimal
    minimum_prior_volatility_observations: int
    percentile_rule: Literal["NEAREST_RANK_PRIOR_VALID_OBSERVATIONS"]
    classification_precedence: tuple[RegimeClassification, ...]


MARKET_REGIME_4_STATE_V1 = MarketRegimeDefinition(
    code="MARKET_REGIME_4_STATE",
    version="1",
    display_name="Four-state point-in-time market regime",
    description=(
        "Explainable benchmark classifier using trend, momentum, and relative "
        "20-session volatility. It is descriptive research analytics, not a forecast."
    ),
    required_feature_codes=("SMA_50", "SMA_200", "MOM_3M_63D", "VOLATILITY_20"),
    volatility_percentile_window=252,
    volatility_percentile=Decimal("0.80"),
    minimum_prior_volatility_observations=126,
    percentile_rule="NEAREST_RANK_PRIOR_VALID_OBSERVATIONS",
    classification_precedence=(
        "INSUFFICIENT_HISTORY",
        "HIGH_VOLATILITY",
        "TRENDING_BULL",
        "TRENDING_BEAR",
        "SIDEWAYS",
    ),
)

REGIME_DEFINITIONS = {
    (MARKET_REGIME_4_STATE_V1.code, MARKET_REGIME_4_STATE_V1.version): (
        MARKET_REGIME_4_STATE_V1
    )
}
DEFAULT_BENCHMARK = "NIFTY200"


def find_regime_definition(code: str, version: str) -> MarketRegimeDefinition | None:
    return REGIME_DEFINITIONS.get((code, version))


__all__ = [
    "DEFAULT_BENCHMARK",
    "MARKET_REGIME_4_STATE_V1",
    "MarketRegime",
    "MarketRegimeDefinition",
    "REGIME_DEFINITIONS",
    "RegimeClassification",
    "SourceMode",
    "find_regime_definition",
]
