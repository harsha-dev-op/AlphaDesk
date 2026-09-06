from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unavailable"]
    api: Literal["healthy", "degraded", "unavailable"]
    database: Literal["healthy", "degraded", "unavailable"]
    version: str


class SecurityCreate(BaseModel):
    exchange: str
    symbol: str
    trading_symbol: str
    company_name: str
    isin: str | None = None
    security_type: str = "EQUITY"
    sector: str | None = None
    industry: str | None = None
    currency: str = "INR"
    listing_date: date | None = None
    delisting_date: date | None = None
    is_active: bool = True


class SecurityResponse(SecurityCreate):
    id: UUID
    model_config = ConfigDict(from_attributes=True)


class SecuritiesPage(BaseModel):
    items: list[SecurityResponse]
    total: int
    page: int
    page_size: int


class PriceResponse(BaseModel):
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    traded_value: Decimal | None
    source: str
    view: Literal["raw", "adjusted"]
    adjustment_factor: Decimal = Decimal("1")


class PriceSeriesResponse(BaseModel):
    symbol: str
    view: Literal["raw", "adjusted"]
    items: list[PriceResponse]


class IndexResponse(BaseModel):
    id: UUID
    name: str
    symbol: str
    provider: str
    exchange: str
    model_config = ConfigDict(from_attributes=True)


class IndexMemberResponse(BaseModel):
    security: SecurityResponse
    valid_from: date
    valid_to: date | None
    source: str


class IndexMembersResponse(BaseModel):
    index: IndexResponse
    as_of: date
    members: list[IndexMemberResponse]


class QualityCheckResponse(BaseModel):
    name: str
    status: Literal["HEALTHY", "WARNING", "FAILED"]
    message: str
    issue_count: int = 0


class QualityStatusResponse(BaseModel):
    status: Literal["HEALTHY", "WARNING", "FAILED"]
    checked_at: datetime
    latest_data_date: date | None
    last_successful_ingestion: datetime | None
    checks: list[QualityCheckResponse]


class FundamentalResponse(BaseModel):
    id: UUID
    security_id: UUID
    fiscal_period_start: date
    fiscal_period_end: date
    fiscal_year: int
    fiscal_quarter: int | None
    reported_date: date
    effective_date: date
    revenue: Decimal | None
    ebitda: Decimal | None
    operating_profit: Decimal | None
    net_profit: Decimal | None
    eps: Decimal | None
    total_assets: Decimal | None
    total_debt: Decimal | None
    cash: Decimal | None
    equity: Decimal | None
    source: str
    model_config = ConfigDict(from_attributes=True)
