from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.fundamentals import FundamentalIntelligenceService, FundamentalNotFoundError
from app.fundamentals.schemas import (
    ClassificationResponse,
    FundamentalMetricsResponse,
    FundamentalsMetadataResponse,
    RelativeStrengthResponse,
    ResearchSummaryResponse,
    SecurityFundamentalsResponse,
)

router = APIRouter(tags=["fundamental-sector-intelligence"])


def _as_of(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=422, detail="as_of must include a timezone offset")
    return value


def _not_found(exc: FundamentalNotFoundError) -> None:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/v1/fundamentals/metadata", response_model=FundamentalsMetadataResponse)
def metadata(db: Session = Depends(get_db)) -> FundamentalsMetadataResponse:
    return FundamentalIntelligenceService(db).metadata()


@router.get("/api/v1/securities/{symbol}/fundamentals", response_model=SecurityFundamentalsResponse)
def fundamentals(
    symbol: str,
    as_of: datetime = Query(...),
    scope: Literal["CONSOLIDATED", "STANDALONE"] = "CONSOLIDATED",
    db: Session = Depends(get_db),
) -> SecurityFundamentalsResponse:
    try:
        return FundamentalIntelligenceService(db).fundamentals(symbol, as_of=_as_of(as_of), scope=scope)
    except FundamentalNotFoundError as exc:
        _not_found(exc)


@router.get("/api/v1/securities/{symbol}/fundamental-metrics", response_model=FundamentalMetricsResponse)
def metrics(symbol: str, as_of: datetime = Query(...), db: Session = Depends(get_db)) -> FundamentalMetricsResponse:
    try:
        return FundamentalIntelligenceService(db).metrics(symbol, as_of=_as_of(as_of))
    except FundamentalNotFoundError as exc:
        _not_found(exc)


@router.get("/api/v1/securities/{symbol}/classification", response_model=ClassificationResponse)
def classification(symbol: str, as_of: datetime = Query(...), db: Session = Depends(get_db)) -> ClassificationResponse:
    try:
        return FundamentalIntelligenceService(db).classification(symbol, as_of=_as_of(as_of))
    except FundamentalNotFoundError as exc:
        _not_found(exc)


@router.get("/api/v1/securities/{symbol}/relative-strength", response_model=RelativeStrengthResponse)
def relative_strength(symbol: str, as_of: datetime = Query(...), db: Session = Depends(get_db)) -> RelativeStrengthResponse:
    try:
        return FundamentalIntelligenceService(db).relative_strength(symbol, as_of=_as_of(as_of))
    except FundamentalNotFoundError as exc:
        _not_found(exc)


@router.get("/api/v1/securities/{symbol}/research-summary", response_model=ResearchSummaryResponse)
def research_summary(symbol: str, as_of: datetime = Query(...), db: Session = Depends(get_db)) -> ResearchSummaryResponse:
    try:
        return FundamentalIntelligenceService(db).research_summary(symbol, as_of=_as_of(as_of))
    except FundamentalNotFoundError as exc:
        _not_found(exc)
