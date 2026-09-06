from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.backtests import BacktestNotFoundError, BacktestService, BacktestValidationError
from app.database.session import get_db
from app.schemas.backtests import BacktestMetadataResponse, BacktestRunRequest, BacktestRunResponse
from app.strategies import StrategyNotFoundError, StrategyValidationError

router = APIRouter(prefix="/api/v1/backtests", tags=["backtest-research"])


@router.get("/metadata", response_model=BacktestMetadataResponse)
def backtest_metadata(db: Session = Depends(get_db)) -> BacktestMetadataResponse:
    return BacktestService(db).metadata()


@router.post("/run", response_model=BacktestRunResponse)
def run_backtest(
    request: BacktestRunRequest,
    db: Session = Depends(get_db),
) -> BacktestRunResponse:
    try:
        return BacktestService(db).run(request)
    except (BacktestNotFoundError, StrategyNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (BacktestValidationError, StrategyValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
