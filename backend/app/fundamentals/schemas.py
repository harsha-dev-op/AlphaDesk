from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, field_validator


Status = Literal["READY", "PARTIAL", "UNAVAILABLE"]
Scope = Literal["CONSOLIDATED", "STANDALONE"]


class ConceptMetadata(BaseModel):
    code: str
    statement: str
    fact_kind: str
    aliases: list[str]


class FundamentalsMetadataResponse(BaseModel):
    concept_registry_code: str
    concept_registry_version: str
    metric_engine_code: str
    metric_engine_version: str
    relative_strength_code: str
    relative_strength_version: str
    concepts: list[ConceptMetadata]
    metrics: dict[str, str]
    relative_strength_periods: dict[str, int]
    preferred_scope: Scope = "CONSOLIDATED"


class FundamentalFactResponse(BaseModel):
    normalized_concept: str | None
    source_concept: str
    value: Decimal | None
    unit: str
    scale: int
    fact_kind: str
    value_nature: str
    period_start: date
    period_end: date


class FilingResponse(BaseModel):
    id: UUID
    source_filing_id: str | None
    filing_type: str
    reporting_frequency: str
    period_start: date
    period_end: date
    fiscal_year: int
    fiscal_quarter: int | None
    scope: Scope
    audit_status: str
    submission_at: datetime
    available_at: datetime
    revision_status: str
    supersedes_filing_id: UUID | None
    parser_version: str
    normalized_fingerprint: str
    facts: list[FundamentalFactResponse]


class SecurityFundamentalsResponse(BaseModel):
    security_id: UUID
    symbol: str
    requested_scope: Scope
    effective_scope: Scope | None
    fallback_used: bool
    status: Status
    as_of: datetime
    filings: list[FilingResponse]
    warnings: list[str]


class FundamentalMetricResponse(BaseModel):
    code: str
    version: str
    value: Decimal | None
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    reason: str | None
    as_of: datetime
    source_scope: Scope | None
    underlying_periods: list[date]


class FundamentalMetricsResponse(BaseModel):
    security_id: UUID
    symbol: str
    as_of: datetime
    source_scope: Scope | None
    metrics: list[FundamentalMetricResponse]


class PeerResponse(BaseModel):
    security_id: UUID
    symbol: str
    company_name: str


class ClassificationResponse(BaseModel):
    security_id: UUID
    symbol: str
    status: Status
    as_of: datetime
    snapshot_date: date | None
    available_at: datetime | None
    macro_economic_sector: str | None
    sector: str | None
    industry: str | None
    basic_industry: str | None
    source: str | None
    parser_version: str | None
    normalized_fingerprint: str | None
    sector_benchmark_symbol: str | None
    sector_benchmark_status: Literal["AVAILABLE", "UNAVAILABLE"]
    peers: dict[str, list[PeerResponse]]
    warnings: list[str]


class RelativeStrengthMetricResponse(BaseModel):
    code: str
    version: str
    sessions: int
    value: Decimal | None
    percentile: Decimal | None
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    reason: str | None
    start_date: date | None
    end_date: date | None
    eligible_security_count: int


class RelativeStrengthResponse(BaseModel):
    security_id: UUID
    symbol: str
    as_of: datetime
    universe: str
    benchmark: str
    definition: str
    metrics: list[RelativeStrengthMetricResponse]
    sector_benchmark: str | None
    sector_metrics_status: Literal["AVAILABLE", "UNAVAILABLE"]
    warnings: list[str]


class ResearchSummaryResponse(BaseModel):
    security_id: UUID
    symbol: str
    company_name: str
    as_of: datetime
    latest_market_date: date | None
    latest_close: Decimal | None
    fundamental_status: Status
    classification_status: Status
    relative_strength_status: Status
    warnings: list[str]


class AsOfQuery(BaseModel):
    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must include a timezone offset")
        return value
