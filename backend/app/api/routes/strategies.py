from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.strategies import StrategyCatalogResponse, StrategyEvaluationRequest, StrategyEvaluationResponse
from app.strategies import StrategyNotFoundError, StrategyService, StrategyValidationError

router = APIRouter(prefix="/api/v1/strategies", tags=["strategy-research"])


@router.get("/catalog", response_model=StrategyCatalogResponse)
def strategy_catalog(db: Session = Depends(get_db)) -> StrategyCatalogResponse:
    return StrategyService(db).catalog()


@router.post("/evaluate", response_model=StrategyEvaluationResponse)
def evaluate_strategy(
    request: StrategyEvaluationRequest,
    db: Session = Depends(get_db),
) -> StrategyEvaluationResponse:
    try:
        return StrategyService(db).evaluate(request)
    except StrategyNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StrategyValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
