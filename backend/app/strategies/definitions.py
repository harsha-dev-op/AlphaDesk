from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

StrategyOperator = Literal[">", ">=", "<", "<=", "="]
ParameterValue = Decimal | bool


@dataclass(frozen=True, slots=True)
class StrategyParameterDefinition:
    code: str
    display_name: str
    description: str
    value_type: Literal["DECIMAL", "BOOLEAN"]
    default_value: ParameterValue
    minimum: Decimal | None = None
    maximum: Decimal | None = None


@dataclass(frozen=True, slots=True)
class StrategyRuleDefinition:
    feature_code: str
    operator: StrategyOperator
    parameter_code: str


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    strategy_code: str
    strategy_version: str
    display_name: str
    description: str
    direction: Literal["LONG_ONLY"]
    research_horizon: str
    required_feature_set: str
    required_feature_set_version: str
    default_adjustment_policy: Literal["ADJUSTED"]
    evaluation_timing_policy: Literal["EOD_AFTER_CLOSE"]
    parameters: tuple[StrategyParameterDefinition, ...]
    rules: tuple[StrategyRuleDefinition, ...]
    minimum_warmup_observations: int
    status: Literal["ACTIVE"] = "ACTIVE"

    @property
    def required_feature_codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(rule.feature_code for rule in self.rules))


def _decimal_parameter(
    code: str,
    display_name: str,
    description: str,
    default: str,
    minimum: str,
    maximum: str,
) -> StrategyParameterDefinition:
    return StrategyParameterDefinition(
        code=code,
        display_name=display_name,
        description=description,
        value_type="DECIMAL",
        default_value=Decimal(default),
        minimum=Decimal(minimum),
        maximum=Decimal(maximum),
    )


def _boolean_parameter(code: str, display_name: str, description: str) -> StrategyParameterDefinition:
    return StrategyParameterDefinition(
        code=code,
        display_name=display_name,
        description=description,
        value_type="BOOLEAN",
        default_value=True,
    )


MOMENTUM_TREND_V1 = StrategyDefinition(
    strategy_code="MOMENTUM_TREND",
    strategy_version="1",
    display_name="Momentum Trend",
    description="Sustained medium-term upward price strength inside an established positive trend.",
    direction="LONG_ONLY",
    research_horizon="MEDIUM_TERM",
    required_feature_set="ALPHADESK_STRATEGY_TECHNICAL",
    required_feature_set_version="1",
    default_adjustment_policy="ADJUSTED",
    evaluation_timing_policy="EOD_AFTER_CLOSE",
    parameters=(
        _decimal_parameter("min_momentum_3m", "Minimum 3-month momentum", "Minimum 63-session close momentum.", "0.10", "-1", "5"),
        _decimal_parameter("min_momentum_6m", "Minimum 6-month momentum", "Minimum 126-session close momentum.", "0.15", "-1", "10"),
        _boolean_parameter("require_above_sma_50", "Require close above SMA 50", "Require the close to be above its 50-session average."),
        _boolean_parameter("require_above_sma_200", "Require close above SMA 200", "Require the close to be above its 200-session average."),
        _boolean_parameter("require_sma_50_above_200", "Require SMA 50 above SMA 200", "Require positive long-term moving-average ordering."),
        _decimal_parameter("min_volume_ratio_20", "Minimum 20-session volume ratio", "Minimum current volume relative to its 20-session average.", "1.00", "0", "20"),
    ),
    rules=(
        StrategyRuleDefinition("MOM_3M_63D", ">=", "min_momentum_3m"),
        StrategyRuleDefinition("MOM_6M_126D", ">=", "min_momentum_6m"),
        StrategyRuleDefinition("ABOVE_SMA_50", "=", "require_above_sma_50"),
        StrategyRuleDefinition("ABOVE_SMA_200", "=", "require_above_sma_200"),
        StrategyRuleDefinition("SMA_50_ABOVE_200", "=", "require_sma_50_above_200"),
        StrategyRuleDefinition("VOLUME_RATIO_20", ">=", "min_volume_ratio_20"),
    ),
    minimum_warmup_observations=200,
)


BREAKOUT_20D_V1 = StrategyDefinition(
    strategy_code="BREAKOUT_20D",
    strategy_version="1",
    display_name="20-Day Breakout",
    description="Close above the preceding 20-session high with trend, volume, close-location, and volatility confirmation.",
    direction="LONG_ONLY",
    research_horizon="SHORT_TO_MEDIUM_TERM",
    required_feature_set="ALPHADESK_STRATEGY_TECHNICAL",
    required_feature_set_version="1",
    default_adjustment_policy="ADJUSTED",
    evaluation_timing_policy="EOD_AFTER_CLOSE",
    parameters=(
        _decimal_parameter("min_breakout_pct_20", "Minimum breakout percent", "Minimum close displacement above the prior 20-session high.", "0.00", "-1", "5"),
        _boolean_parameter("require_above_sma_50", "Require close above SMA 50", "Require the close to be above its 50-session average."),
        _boolean_parameter("require_sma_20_above_50", "Require SMA 20 above SMA 50", "Require positive intermediate moving-average ordering."),
        _decimal_parameter("min_volume_ratio_20", "Minimum 20-session volume ratio", "Minimum current volume relative to its 20-session average.", "1.50", "0", "20"),
        _decimal_parameter("min_close_location", "Minimum close location", "Minimum close position inside the current session range.", "0.70", "0", "1"),
        _decimal_parameter("max_atr_pct_14", "Maximum ATR percent", "Volatility guard limiting 14-session ATR relative to close.", "0.08", "0", "2"),
    ),
    rules=(
        StrategyRuleDefinition("BREAKOUT_PCT_20", ">=", "min_breakout_pct_20"),
        StrategyRuleDefinition("ABOVE_SMA_50", "=", "require_above_sma_50"),
        StrategyRuleDefinition("SMA_20_ABOVE_50", "=", "require_sma_20_above_50"),
        StrategyRuleDefinition("VOLUME_RATIO_20", ">=", "min_volume_ratio_20"),
        StrategyRuleDefinition("CLOSE_LOCATION", ">=", "min_close_location"),
        StrategyRuleDefinition("ATR_PCT_14", "<=", "max_atr_pct_14"),
    ),
    minimum_warmup_observations=50,
)


MEAN_REVERSION_PULLBACK_V1 = StrategyDefinition(
    strategy_code="MEAN_REVERSION_PULLBACK",
    strategy_version="1",
    display_name="Mean-Reversion Pullback",
    description="Short-term oversold pullback conditions inside a stronger long-term upward structure.",
    direction="LONG_ONLY",
    research_horizon="SHORT_TERM",
    required_feature_set="ALPHADESK_STRATEGY_TECHNICAL",
    required_feature_set_version="1",
    default_adjustment_policy="ADJUSTED",
    evaluation_timing_policy="EOD_AFTER_CLOSE",
    parameters=(
        _decimal_parameter("max_rsi_14", "Maximum RSI 14", "Maximum Wilder RSI value.", "35", "0", "100"),
        _decimal_parameter("max_distance_sma_20", "Maximum distance from SMA 20", "Maximum close distance from the 20-session average.", "-0.03", "-1", "2"),
        _decimal_parameter("max_return_5d", "Maximum 5-session return", "Maximum five-session simple return.", "-0.03", "-1", "5"),
        _boolean_parameter("require_above_sma_200", "Require close above SMA 200", "Require the close to remain above its 200-session average."),
        _boolean_parameter("require_sma_50_above_200", "Require SMA 50 above SMA 200", "Require positive long-term moving-average ordering."),
        _decimal_parameter("max_atr_pct_14", "Maximum ATR percent", "Volatility guard limiting 14-session ATR relative to close.", "0.06", "0", "2"),
    ),
    rules=(
        StrategyRuleDefinition("RSI_14", "<=", "max_rsi_14"),
        StrategyRuleDefinition("DISTANCE_SMA_20", "<=", "max_distance_sma_20"),
        StrategyRuleDefinition("RET_5D", "<=", "max_return_5d"),
        StrategyRuleDefinition("ABOVE_SMA_200", "=", "require_above_sma_200"),
        StrategyRuleDefinition("SMA_50_ABOVE_200", "=", "require_sma_50_above_200"),
        StrategyRuleDefinition("ATR_PCT_14", "<=", "max_atr_pct_14"),
    ),
    minimum_warmup_observations=200,
)


INITIAL_STRATEGIES = (
    MOMENTUM_TREND_V1,
    BREAKOUT_20D_V1,
    MEAN_REVERSION_PULLBACK_V1,
)
