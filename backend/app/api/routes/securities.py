from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.repositories.securities import SecurityRepository
from app.schemas.api import PriceResponse, PriceSeriesResponse, SecuritiesPage, SecurityResponse
from app.services.adjustments import PriceAdjustmentService

router = APIRouter(prefix="/api/v1/securities", tags=["securities"])


@router.get("", response_model=SecuritiesPage)
def list_securities(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), q: str | None = Query(None, max_length=100), db: Session = Depends(get_db)) -> SecuritiesPage:
    items, total = SecurityRepository(db).list(page=page, page_size=page_size, query=q)
    return SecuritiesPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/{symbol}", response_model=SecurityResponse)
def get_security(symbol: str, db: Session = Depends(get_db)) -> SecurityResponse:
    security = SecurityRepository(db).get_by_symbol(symbol)
    if not security:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Security not found")
    return SecurityResponse.model_validate(security)


@router.get("/{symbol}/prices", response_model=PriceSeriesResponse)
def get_prices(symbol: str, start_date: date | None = None, end_date: date | None = None, view: Literal["raw", "adjusted"] = "raw", db: Session = Depends(get_db)) -> PriceSeriesResponse:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    repository = SecurityRepository(db)
    security = repository.get_by_symbol(symbol)
    if not security:
        raise HTTPException(status_code=404, detail="Security not found")
    prices = repository.prices(security.id, start=start_date, end=end_date)
    if view == "adjusted" and prices:
        actions = repository.corporate_actions(
            security.id,
            as_of=datetime.now(UTC),
            after=prices[0].trading_date,
            through=prices[-1].trading_date,
        )
        items = [PriceResponse(**row.__dict__, view="adjusted") for row in PriceAdjustmentService().adjust(prices, actions)]
    else:
        items = [PriceResponse(trading_date=row.trading_date, open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume, traded_value=row.traded_value, source=row.source, view="raw") for row in prices]
    return PriceSeriesResponse(symbol=security.symbol, view=view, items=items)
