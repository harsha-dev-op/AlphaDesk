from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.regimes.definitions import MarketRegime, RegimeClassification, SourceMode
from app.schemas.research_history import (
    CompositionBacktestRequest,
    CompositionBacktestResponse,
)


class RegimeDefinitionMetadata(BaseModel):
    code: str
    version: str
    display_name: str
    description: str
    regimes: list[MarketRegime]
    required_feature_codes: list[str]
    volatility_percentile_window: int
    volatility_percentile: Decimal
    minimum_prior_volatility_observations: int
    percentile_rule: str
    classification_precedence: list[RegimeClassification]
    rule_explanation: str


class BenchmarkCoverage(BaseModel):
    benchmark_id: UUID
    benchmark_symbol: str
    benchmark_name: str
    benchmark_provider: str
    source_mode: SourceMode
    first_available_date: date | None
    last_available_date: date | None
    session_count: int
    enough_history_for_regime: bool
    first_classifiable_date: date | None


class BenchmarkMetadata(BaseModel):
    id: UUID
    symbol: str
    name: str
    provider: str
    exchange: str
    coverage: list[BenchmarkCoverage]


class RegimeMetadataResponse(BaseModel):
    definitions: list[RegimeDefinitionMetadata]
    benchmarks: list[BenchmarkMetadata]
    default_benchmark: str
    supported_source_modes: list[SourceMode]
    research_disclaimer: str


class RegimeHistoryRequest(BaseModel):
    benchmark: str = Field(default="NIFTY200", min_length=1, max_length=128)
    start_date: date
    end_date: date
    source_mode: SourceMode
    regime_definition_code: str = "MARKET_REGIME_4_STATE"
    regime_definition_version: str = "1"
    as_of: datetime | None = None

    @field_validator("benchmark")
    @classmethod
    def strip_benchmark(cls, value: str) -> str:
        return value.strip()

    @field_validator("regime_definition_code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("regime_definition_version")
    @classmethod
    def strip_version(cls, value: str) -> str:
        return value.strip()

    @field_validator("as_of")
    @classmethod
    def validate_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must include a timezone offset")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> RegimeHistoryRequest:
        if self.start_date > self.end_date:
            raise ValueError("start_date cannot be after end_date")
        if (self.end_date - self.start_date).days > 5475:
            raise ValueError("Regime history range cannot exceed 5475 calendar days")
        if self.as_of is not None and self.as_of.date() < self.start_date:
            raise ValueError("as_of cannot be earlier than start_date")
        return self


class RegimeEvaluation(BaseModel):
    observation_date: date
    available_at: datetime
    status: Literal["CLASSIFIED", "INSUFFICIENT_HISTORY"]
    classification: RegimeClassification
    regime: MarketRegime | None
    close: Decimal
    sma_50: Decimal | None
    sma_200: Decimal | None
    momentum_3m_63d: Decimal | None
    volatility_20: Decimal | None
    historical_volatility_threshold: Decimal | None
    prior_valid_volatility_count: int
    high_volatility_triggered: bool
    bull_conditions_met: bool
    bear_conditions_met: bool
    reasons: list[str]
    evaluation_fingerprint: str


class RegimeTransition(BaseModel):
    transition_date: date
    previous_classification: RegimeClassification
    new_classification: RegimeClassification
    previous_regime_duration_sessions: int
    benchmark_symbol: str


class RegimeDistribution(BaseModel):
    classification: RegimeClassification
    session_count: int
    percentage_of_classified_sessions: Decimal | None


class RegimeHistoryTimings(BaseModel):
    repository_load_ms: float
    feature_generation_ms: float
    classification_ms: float
    response_build_ms: float
    total_service_ms: float


class RegimeHistoryResponse(BaseModel):
    availability: Literal["AVAILABLE", "UNAVAILABLE"]
    normalized_request: RegimeHistoryRequest
    benchmark: BenchmarkMetadata
    definition: RegimeDefinitionMetadata
    coverage: BenchmarkCoverage
    classifications: list[RegimeEvaluation]
    distributions: list[RegimeDistribution]
    insufficient_history_sessions: int
    latest_classification: RegimeEvaluation | None
    latest_regime_start_date: date | None
    latest_regime_duration_sessions: int
    transitions: list[RegimeTransition]
    regime_timeline_fingerprint: str
    benchmark_dataset_fingerprint: str
    timings: RegimeHistoryTimings
    warnings: list[str]
    research_disclaimer: str


class RegimeResearchAttributionRequest(BaseModel):
    backtest: CompositionBacktestRequest
    benchmark: str = Field(default="NIFTY200", min_length=1, max_length=128)
    source_mode: SourceMode
    regime_definition_code: str = "MARKET_REGIME_4_STATE"
    regime_definition_version: str = "1"
    as_of: datetime | None = None

    model_config = ConfigDict(allow_inf_nan=False)

    @field_validator("benchmark")
    @classmethod
    def strip_benchmark(cls, value: str) -> str:
        return value.strip()

    @field_validator("regime_definition_code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("regime_definition_version")
    @classmethod
    def strip_version(cls, value: str) -> str:
        return value.strip()

    @field_validator("as_of")
    @classmethod
    def validate_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must include a timezone offset")
        return value


class RegimeAttributionMetrics(BaseModel):
    classification: RegimeClassification | Literal["OVERALL"]
    signal_count: int
    executed_trade_count: int
    skipped_setup_count: int
    winning_trade_count: int
    losing_trade_count: int
    breakeven_trade_count: int
    win_rate: Decimal | None
    gross_profit: Decimal
    gross_loss: Decimal
    net_pnl: Decimal
    mean_net_return_pct: Decimal | None
    median_net_return_pct: Decimal | None
    average_holding_sessions: Decimal | None
    profit_factor: Decimal | None
    expectancy: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None


class RegimeAttributionTimings(BaseModel):
    historical_preparation_ms: float
    backtest_execution_ms: float
    regime_timeline_ms: float
    attribution_ms: float
    total_service_ms: float


class RegimeResearchAttributionResponse(BaseModel):
    normalized_request: RegimeResearchAttributionRequest
    backtest: CompositionBacktestResponse
    regime_history: RegimeHistoryResponse
    overall: RegimeAttributionMetrics
    buckets: list[RegimeAttributionMetrics]
    attributed_trade_count: int
    trade_count_reconciled: bool
    attributed_net_pnl: Decimal
    net_pnl_reconciled: bool
    regime_attribution_fingerprint: str
    timings: RegimeAttributionTimings
    warnings: list[str]
    research_disclaimer: str


__all__ = [name for name in globals() if name.startswith(("Benchmark", "Regime"))]
