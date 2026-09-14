from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class DataSourceDefinitionResponse(BaseModel):
    artifact_type: str
    provider: str
    owner: str
    landing_page: str
    artifact_kind: str
    parser_code: str
    parser_version: str
    automation_suitability: str
    point_in_time_limit: str
    latest_artifact_status: str | None
    latest_source_date: date | None
    latest_imported_at: datetime | None


class DataSourcesResponse(BaseModel):
    mode: Literal["DEMO", "OFFICIAL_NSE", "MIXED", "UNKNOWN"]
    sources: list[DataSourceDefinitionResponse]
    redistribution_notice: str


class ArtifactSummaryResponse(BaseModel):
    id: UUID
    provider: str
    artifact_type: str
    source_date: date
    imported_at: datetime
    sha256: str
    parser_code: str
    parser_version: str
    parse_status: str
    row_count: int
    accepted_row_count: int
    rejected_row_count: int
    warning_count: int


class IngestionIssueResponse(BaseModel):
    severity: Literal["ERROR", "WARNING", "INFO"]
    code: str
    message: str
    row_key: str | None
    created_at: datetime


class IndexCoverageResponse(BaseModel):
    symbol: str
    label: str
    current_snapshot_present: bool
    snapshot_as_of: date | None
    member_count: int
    coverage_start: date | None
    coverage_end: date | None
    coverage_kind: Literal["NONE", "CURRENT_SNAPSHOT_ONLY", "BOUNDED_SNAPSHOT_SEQUENCE"]
    warning: str | None


class DataCoverageResponse(BaseModel):
    mode: Literal["DEMO", "OFFICIAL_NSE", "MIXED", "UNKNOWN"]
    provider: str | None
    last_successful_ingestion: datetime | None
    earliest_official_price_session: date | None
    latest_official_price_session: date | None
    official_security_count: int
    official_daily_price_count: int
    demo_security_count: int
    demo_daily_price_count: int
    source_artifact_count: int
    corporate_actions_promoted: int
    corporate_actions_quarantined: int
    missing_official_sessions: int
    index_coverage: list[IndexCoverageResponse]
    latest_artifacts: list[ArtifactSummaryResponse]
    latest_issues: list[IngestionIssueResponse]
    warnings: list[str]

