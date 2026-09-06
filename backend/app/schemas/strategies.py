from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.schemas.scanner import ScannerUniverseMetadata
from app.schemas.technical import DatasetContextResponse

StrategyScalar = Annotated[StrictBool | Decimal, Field(union_mode="left_to_right")]
StrategyOperator = Literal[">", ">=", "<", "<=", "="]


class StrategyParameterMetadata(BaseModel):
    code: str
    display_name: str
    description: str
    value_type: Literal["DECIMAL", "BOOLEAN"]
    default_value: StrategyScalar
    minimum: Decimal | None
    maximum: Decimal | None


class StrategyRuleMetadata(BaseModel):
    feature_code: str
    feature_version: str
    operator: StrategyOperator
    parameter_code: str
    default_expected_value: StrategyScalar
    unit: str


class StrategyMetadata(BaseModel):
    strategy_code: str
    strategy_version: str
    display_name: str
    description: str
    direction: Literal["LONG_ONLY"]
    research_horizon: str
    required_feature_set: str
    required_feature_set_version: str
    required_feature_codes: list[str]
    default_adjustment_policy: Literal["ADJUSTED"]
    evaluation_timing_policy: Literal["EOD_AFTER_CLOSE"]
    parameters: list[StrategyParameterMetadata]
    rules: list[StrategyRuleMetadata]
    minimum_warmup_observations: int
    status: Literal["ACTIVE"]
    default_strategy_fingerprint: str


class StrategyCatalogResponse(BaseModel):
    strategies: list[StrategyMetadata]
    universes: list[ScannerUniverseMetadata]
    adjustment_policies: list[Literal["RAW", "ADJUSTED"]]
    latest_observation_date: date | None
    research_disclaimer: str


class StrategyEvaluationRequest(BaseModel):
    strategy_code: str = Field(min_length=1, max_length=64)
    strategy_version: str = Field(default="1", min_length=1, max_length=32)
    universe: str = Field(min_length=1, max_length=128)
    observation_date: date
    as_of: datetime
    adjustment_policy: Literal["RAW", "ADJUSTED"] = "ADJUSTED"
    parameter_overrides: dict[str, StrategyScalar] = Field(default_factory=dict)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "strategy_code": "MOMENTUM_TREND",
                "strategy_version": "1",
                "universe": "NIFTYDEMO100",
                "observation_date": "2025-03-31",
                "as_of": "2025-03-31T15:30:00+05:30",
                "adjustment_policy": "ADJUSTED",
                "parameter_overrides": {"min_momentum_3m": "0.12"},
            }
        }
    )

    @field_validator("strategy_code")
    @classmethod
    def normalize_strategy_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("strategy_version", "universe")
    @classmethod
    def strip_identifier(cls, value: str) -> str:
        return value.strip()


class StrategyConditionResult(BaseModel):
    feature_code: str
    feature_version: str
    operator: StrategyOperator
    expected_value: StrategyScalar
    actual_value: bool | Decimal | None
    passed: bool
    unit: str


class StrategySecurityResult(BaseModel):
    security_id: UUID
    symbol: str
    company_name: str
    exchange: str
    observation_date: date
    as_of: datetime
    available_at: datetime | None
    strategy_code: str
    strategy_version: str
    direction: Literal["LONG_ONLY"]
    matched: bool
    required_feature_values: dict[str, bool | Decimal | None]
    conditions: list[StrategyConditionResult]
    passed_condition_count: int
    total_condition_count: int
    adjustment_policy: Literal["RAW", "ADJUSTED"]
    feature_set_code: str
    feature_set_version: str
    feature_versions: dict[str, str]
    dataset: DatasetContextResponse
    input_fingerprint: str
    strategy_fingerprint: str
    result_fingerprint: str
    explanation: str
    warnings: list[str]


class StrategyEvaluationTiming(BaseModel):
    universe_resolution_ms: float
    feature_computation_ms: float
    condition_evaluation_ms: float
    response_build_ms: float
    total_service_ms: float


class StrategyEvaluationResponse(BaseModel):
    universe: ScannerUniverseMetadata
    observation_date: date
    as_of: datetime
    executed_at: datetime
    strategy: StrategyMetadata
    effective_parameters: dict[str, StrategyScalar]
    adjustment_policy: Literal["RAW", "ADJUSTED"]
    universe_member_count: int
    evaluated_security_count: int
    unavailable_security_count: int
    matched_count: int
    result_order: Literal["SYMBOL_ASC"] = "SYMBOL_ASC"
    evaluation_fingerprint: str
    warnings: list[str]
    timings: StrategyEvaluationTiming
    results: list[StrategySecurityResult]
