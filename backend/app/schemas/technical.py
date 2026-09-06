from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class FeatureDefinitionResponse(BaseModel):
    code: str
    version: str
    name: str
    category: str
    description: str
    formula: str
    required_fields: list[str]
    lookback_sessions: int
    minimum_observations: int
    output_type: Literal["DECIMAL", "BOOLEAN"]
    unit: str
    parameters: dict[str, int | str]
    adjustment_support: str
    availability_semantics: str


class FeatureCatalogResponse(BaseModel):
    definitions: list[FeatureDefinitionResponse]
    count: int


class FeatureSetResponse(BaseModel):
    code: str
    version: str
    name: str
    description: str
    feature_codes: list[str]
    default_adjustment_policy: Literal["ADJUSTED"]
    is_active: bool


class FeatureSetCatalogResponse(BaseModel):
    feature_sets: list[FeatureSetResponse]


class DatasetContextResponse(BaseModel):
    dataset_code: str
    dataset_version: str
    provider: str
    earliest_observation: date | None
    latest_observation: date | None
    fingerprint: str


class FeatureObservationResponse(BaseModel):
    observation_date: date
    available_at: datetime
    values: dict[str, bool | Decimal | None]
    unavailable: dict[str, str]


class SecurityFeatureSeriesResponse(BaseModel):
    security_id: UUID
    symbol: str
    exchange: str
    feature_set: FeatureSetResponse
    feature_versions: dict[str, str]
    adjustment_policy: Literal["RAW", "ADJUSTED"]
    dataset: DatasetContextResponse
    requested_start: date | None
    requested_end: date | None
    as_of: datetime
    computed_at: datetime
    quality_warnings: list[str]
    items: list[FeatureObservationResponse]
