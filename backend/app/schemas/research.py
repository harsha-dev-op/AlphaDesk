from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from app.schemas.scanner import ScannerUniverseMetadata
from app.schemas.strategies import StrategyConditionResult, StrategyMetadata

ResearchScalar = Annotated[StrictBool | Decimal, Field(union_mode="left_to_right")]
CompositionStatus = Literal["MATCHED", "NOT_MATCHED", "INSUFFICIENT_FEATURE_HISTORY"]
ReplayStatus = Literal[
    "INITIAL",
    "REPRODUCED",
    "DATASET_DRIFT_DETECTED",
    "ENGINE_OR_RESULT_DRIFT_DETECTED",
]


class CompositionComponentRequest(BaseModel):
    strategy_code: str = Field(min_length=1, max_length=64)
    strategy_version: str = Field(default="1", min_length=1, max_length=32)
    parameter_overrides: dict[str, ResearchScalar] = Field(default_factory=dict)

    @field_validator("strategy_code")
    @classmethod
    def normalize_strategy_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("strategy_version")
    @classmethod
    def strip_version(cls, value: str) -> str:
        return value.strip()


class CompositionEvaluationRequest(BaseModel):
    policy_code: str = Field(default="CONSENSUS_N_OF_M", min_length=1, max_length=64)
    policy_version: str = Field(default="1", min_length=1, max_length=32)
    universe: str = Field(min_length=1, max_length=128)
    observation_date: date
    as_of: datetime
    adjustment_policy: Literal["RAW", "ADJUSTED"] = "ADJUSTED"
    required_match_count: int = Field(ge=1, le=10)
    components: list[CompositionComponentRequest] = Field(min_length=2, max_length=10)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "policy_code": "CONSENSUS_N_OF_M",
                "policy_version": "1",
                "universe": "NIFTYDEMO100",
                "observation_date": "2025-03-31",
                "as_of": "2025-03-31T15:30:00+05:30",
                "adjustment_policy": "ADJUSTED",
                "required_match_count": 2,
                "components": [
                    {"strategy_code": "MOMENTUM_TREND", "strategy_version": "1"},
                    {"strategy_code": "BREAKOUT_20D", "strategy_version": "1"},
                ],
            }
        }
    )

    @field_validator("policy_code")
    @classmethod
    def normalize_policy_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("policy_version", "universe")
    @classmethod
    def strip_identifier(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_component_shape(self):
        if self.required_match_count > len(self.components):
            raise ValueError("required_match_count cannot exceed the number of components")
        keys = [(item.strategy_code, item.strategy_version) for item in self.components]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate strategy components are not allowed")
        return self


class CompositionPolicyMetadata(BaseModel):
    policy_code: str
    policy_version: str
    display_name: str
    description: str
    minimum_components: int
    maximum_components: int
    aggregation_rule: Literal["AT_LEAST_N_MATCHED"]
    insufficient_history_rule: Literal["COULD_CHANGE_THRESHOLD"]
    component_order_policy: Literal["CANONICAL_STRATEGY_PARAMETERS"]
    policy_fingerprint: str


class CompositionMetadataResponse(BaseModel):
    policies: list[CompositionPolicyMetadata]
    strategies: list[StrategyMetadata]
    universes: list[ScannerUniverseMetadata]
    adjustment_policies: list[Literal["RAW", "ADJUSTED"]]
    latest_observation_date: date | None
    research_disclaimer: str


class CompositionComponentConfiguration(BaseModel):
    strategy: StrategyMetadata
    effective_parameters: dict[str, ResearchScalar]
    strategy_fingerprint: str


class CompositionComponentResult(BaseModel):
    strategy_code: str
    strategy_version: str
    status: CompositionStatus
    effective_parameters: dict[str, ResearchScalar]
    required_feature_values: dict[str, bool | Decimal | None]
    conditions: list[StrategyConditionResult]
    passed_condition_count: int
    total_condition_count: int
    strategy_fingerprint: str
    result_fingerprint: str
    warnings: list[str]


class CompositionSecurityResult(BaseModel):
    security_id: UUID
    symbol: str
    company_name: str
    exchange: str
    observation_date: date
    as_of: datetime
    available_at: datetime | None
    status: CompositionStatus
    matched_strategy_count: int
    insufficient_strategy_count: int
    required_match_count: int
    adjustment_policy: Literal["RAW", "ADJUSTED"]
    input_fingerprint: str
    composition_result_fingerprint: str
    component_results: list[CompositionComponentResult]
    warnings: list[str]


class CompositionTiming(BaseModel):
    universe_resolution_ms: float
    feature_computation_ms: float
    component_evaluation_ms: float
    composition_ms: float
    response_build_ms: float
    total_service_ms: float


class CompositionEvaluationResponse(BaseModel):
    normalized_request: CompositionEvaluationRequest
    policy: CompositionPolicyMetadata
    components: list[CompositionComponentConfiguration]
    universe: ScannerUniverseMetadata
    observation_date: date
    as_of: datetime
    executed_at: datetime
    adjustment_policy: Literal["RAW", "ADJUSTED"]
    union_feature_codes: list[str]
    universe_member_count: int
    evaluated_security_count: int
    matched_count: int
    not_matched_count: int
    insufficient_history_count: int
    result_order: Literal["SYMBOL_ASC"] = "SYMBOL_ASC"
    component_order: Literal["STRATEGY_CODE_VERSION_PARAMETERS_ASC"] = (
        "STRATEGY_CODE_VERSION_PARAMETERS_ASC"
    )
    composition_config_fingerprint: str
    composition_dataset_fingerprint: str
    composition_run_fingerprint: str
    engine_provenance: dict[str, object]
    warnings: list[str]
    timings: CompositionTiming
    results: list[CompositionSecurityResult]


class ExperimentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    composition: CompositionEvaluationRequest

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name cannot be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ExperimentRunSummary(BaseModel):
    total_members: int
    matched: int
    not_matched: int
    insufficient_history: int


class ExperimentMemberOutcome(BaseModel):
    symbol: str
    status: CompositionStatus
    matched_strategy_count: int
    insufficient_strategy_count: int
    required_match_count: int
    composition_result_fingerprint: str
    components: list[dict[str, str]]


class ExperimentRunResponse(BaseModel):
    id: UUID
    experiment_id: UUID
    executed_at: datetime
    replay_status: ReplayStatus
    reference_run_id: UUID | None
    normalized_request: CompositionEvaluationRequest
    composition_config_fingerprint: str
    dataset_fingerprint: str
    run_fingerprint: str
    engine_provenance: dict[str, object]
    summary: ExperimentRunSummary
    member_outcomes: list[ExperimentMemberOutcome]
    warnings: list[str]


class ExperimentDefinitionResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    policy_code: str
    policy_version: str
    normalized_request: CompositionEvaluationRequest
    config_fingerprint: str
    created_at: datetime
    latest_run: ExperimentRunResponse | None


class ExperimentListItem(BaseModel):
    id: UUID
    name: str
    description: str | None
    policy_code: str
    policy_version: str
    strategy_count: int
    required_match_count: int
    universe: str
    observation_date: date
    adjustment_policy: Literal["RAW", "ADJUSTED"]
    config_fingerprint: str
    created_at: datetime
    latest_replay_status: ReplayStatus | None
    latest_run_at: datetime | None
    latest_run_fingerprint: str | None


class ExperimentListResponse(BaseModel):
    items: list[ExperimentListItem]
    total: int
    page: int
    page_size: int


class ExperimentRunListResponse(BaseModel):
    items: list[ExperimentRunResponse]
    total: int
    page: int
    page_size: int


class ExperimentCreateResponse(BaseModel):
    experiment: ExperimentDefinitionResponse
    initial_evaluation: CompositionEvaluationResponse


class ExperimentReplayResponse(BaseModel):
    experiment: ExperimentDefinitionResponse
    run: ExperimentRunResponse
    evaluation: CompositionEvaluationResponse


__all__ = [name for name in globals() if name.startswith(("Composition", "Experiment", "Replay"))]
