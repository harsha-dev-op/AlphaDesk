from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.regimes import MarketRegimeService, RegimeNotFoundError, RegimeValidationError
from app.regimes.schemas import (
    RegimeHistoryRequest,
    RegimeHistoryResponse,
    RegimeMetadataResponse,
    RegimeResearchAttributionRequest,
    RegimeResearchAttributionResponse,
)
from app.research import (
    ExperimentNotFoundError,
    ResearchInvariantError,
    ResearchValidationError,
)
from app.research.historical import HistoricalResearchNotFoundError
from app.research.service import CompositionPolicyNotFoundError
from app.strategies import StrategyNotFoundError, StrategyValidationError


router = APIRouter(prefix="/api/v1/regimes", tags=["market-regime-research"])


def _raise_domain_error(exc: Exception) -> NoReturn:
    if isinstance(
        exc,
        (
            RegimeNotFoundError,
            CompositionPolicyNotFoundError,
            ExperimentNotFoundError,
            HistoricalResearchNotFoundError,
            StrategyNotFoundError,
        ),
    ):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(
        exc,
        (RegimeValidationError, ResearchValidationError, StrategyValidationError),
    ):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, ResearchInvariantError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


@router.get("/metadata", response_model=RegimeMetadataResponse)
def regime_metadata(db: Session = Depends(get_db)) -> RegimeMetadataResponse:
    return MarketRegimeService(db).metadata()


@router.post("/history", response_model=RegimeHistoryResponse)
def regime_history(
    request: RegimeHistoryRequest,
    db: Session = Depends(get_db),
) -> RegimeHistoryResponse:
    try:
        return MarketRegimeService(db).history(request)
    except (RegimeNotFoundError, RegimeValidationError) as exc:
        _raise_domain_error(exc)


@router.post(
    "/research-attribution", response_model=RegimeResearchAttributionResponse
)
def regime_research_attribution(
    request: RegimeResearchAttributionRequest,
    db: Session = Depends(get_db),
) -> RegimeResearchAttributionResponse:
    try:
        return MarketRegimeService(db).research_attribution(request)
    except (
        RegimeNotFoundError,
        RegimeValidationError,
        CompositionPolicyNotFoundError,
        ExperimentNotFoundError,
        HistoricalResearchNotFoundError,
        ResearchInvariantError,
        ResearchValidationError,
        StrategyNotFoundError,
        StrategyValidationError,
    ) as exc:
        _raise_domain_error(exc)
