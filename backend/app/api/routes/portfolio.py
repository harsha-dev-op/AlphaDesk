from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.backtests import BacktestNotFoundError, BacktestValidationError
from app.database.session import get_db
from app.portfolio import PortfolioNotFoundError, PortfolioService, PortfolioValidationError
from app.portfolio.simulator import PortfolioInvariantError
from app.schemas.portfolio import PortfolioMetadataResponse, PortfolioRunRequest, PortfolioRunResponse
from app.strategies import StrategyNotFoundError, StrategyValidationError

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio-research"])


@router.get("/metadata", response_model=PortfolioMetadataResponse)
def portfolio_metadata(db: Session = Depends(get_db)) -> PortfolioMetadataResponse:
    return PortfolioService(db).metadata()


@router.post("/run", response_model=PortfolioRunResponse)
def run_portfolio(
    request: PortfolioRunRequest,
    db: Session = Depends(get_db),
) -> PortfolioRunResponse:
    try:
        return PortfolioService(db).run(request)
    except (BacktestNotFoundError, PortfolioNotFoundError, StrategyNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        BacktestValidationError,
        PortfolioInvariantError,
        PortfolioValidationError,
        StrategyValidationError,
    ) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
