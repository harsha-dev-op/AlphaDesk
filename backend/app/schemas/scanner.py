from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from app.schemas.technical import DatasetContextResponse, FeatureSetResponse

ScannerOperator = Literal[">", ">=", "<", "<=", "=", "between"]
ScannerScalar = Annotated[StrictBool | Decimal, Field(union_mode="left_to_right")]


class ScannerFilterRequest(BaseModel):
    feature: str = Field(min_length=1, max_length=64)
    operator: ScannerOperator
    value: ScannerScalar
    upper_value: Decimal | None = None

    @field_validator("feature")
    @classmethod
    def normalize_feature(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_range_shape(self):
        if self.operator == "between" and self.upper_value is None:
            raise ValueError("between requires upper_value")
        if self.operator != "between" and self.upper_value is not None:
            raise ValueError("upper_value is only valid with between")
        return self


class MarketScanRequest(BaseModel):
    universe: str = Field(min_length=1, max_length=128)
    observation_date: date
    as_of: datetime
    feature_set: str = "ALPHADESK_CORE_TECHNICAL"
    feature_set_version: str = "1"
    adjustment_policy: Literal["RAW", "ADJUSTED"] = "ADJUSTED"
    logic: Literal["AND"] = "AND"
    filters: list[ScannerFilterRequest] = Field(min_length=1, max_length=20)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "universe": "NIFTYDEMO100",
                "observation_date": "2025-03-31",
                "as_of": "2025-03-31T15:30:00+05:30",
                "feature_set": "ALPHADESK_CORE_TECHNICAL",
                "feature_set_version": "1",
                "adjustment_policy": "ADJUSTED",
                "logic": "AND",
                "filters": [
                    {"feature": "RSI_14", "operator": "<", "value": "40"},
                    {"feature": "VOLUME_RATIO_20", "operator": ">=", "value": "1"},
                ],
            }
        }
    )


class ScannerFeatureMetadata(BaseModel):
    code: str
    display_name: str
    family: str
    version: str
    value_type: Literal["DECIMAL", "BOOLEAN"]
    unit: str
    minimum_observations: int
    supported_operators: list[ScannerOperator]
    scanner_filtering_supported: bool = True


class ScannerOperatorMetadata(BaseModel):
    code: ScannerOperator
    label: str
    requires_upper_value: bool


class ScannerUniverseMetadata(BaseModel):
    id: UUID
    symbol: str
    name: str
    provider: str
    exchange: str


class ScannerMetadataResponse(BaseModel):
    feature_set: FeatureSetResponse
    features: list[ScannerFeatureMetadata]
    operators: list[ScannerOperatorMetadata]
    adjustment_policies: list[Literal["RAW", "ADJUSTED"]]
    universes: list[ScannerUniverseMetadata]
    latest_observation_date: date | None


class ScannerResultResponse(BaseModel):
    security_id: UUID
    symbol: str
    company_name: str
    exchange: str
    observation_date: date
    as_of: datetime
    available_at: datetime
    matched_values: dict[str, bool | Decimal]
    feature_versions: dict[str, str]
    feature_set_code: str
    feature_set_version: str
    adjustment_policy: Literal["RAW", "ADJUSTED"]
    dataset: DatasetContextResponse
    input_fingerprint: str
    quality_warnings: list[str]


class ScannerTimingResponse(BaseModel):
    universe_resolution_ms: float
    feature_computation_ms: float
    predicate_evaluation_ms: float
    response_build_ms: float
    total_service_ms: float


class MarketScanResponse(BaseModel):
    universe: ScannerUniverseMetadata
    observation_date: date
    as_of: datetime
    executed_at: datetime
    feature_set: FeatureSetResponse
    feature_versions: dict[str, str]
    adjustment_policy: Literal["RAW", "ADJUSTED"]
    logic: Literal["AND"]
    filters: list[ScannerFilterRequest]
    universe_member_count: int
    evaluated_security_count: int
    unavailable_security_count: int
    matched_count: int
    result_order: Literal["SYMBOL_ASC"] = "SYMBOL_ASC"
    scan_fingerprint: str
    timings: ScannerTimingResponse
    results: list[ScannerResultResponse]
