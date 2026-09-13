from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.research import (
    ExperimentNotFoundError,
    ResearchInvariantError,
    ResearchPersistenceError,
    ResearchService,
    ResearchValidationError,
)
from app.research.service import CompositionPolicyNotFoundError
from app.schemas.research import (
    CompositionEvaluationRequest,
    CompositionEvaluationResponse,
    CompositionMetadataResponse,
    ExperimentCreateRequest,
    ExperimentCreateResponse,
    ExperimentDefinitionResponse,
    ExperimentListResponse,
    ExperimentReplayResponse,
    ExperimentRunListResponse,
)
from app.strategies import StrategyNotFoundError, StrategyValidationError

router = APIRouter(prefix="/api/v1/research", tags=["multi-strategy-research"])


def _raise_domain_error(exc: Exception) -> NoReturn:
    if isinstance(
        exc,
        (CompositionPolicyNotFoundError, ExperimentNotFoundError, StrategyNotFoundError),
    ):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (ResearchValidationError, StrategyValidationError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, ResearchInvariantError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ResearchPersistenceError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    raise exc


@router.get("/compositions/metadata", response_model=CompositionMetadataResponse)
def composition_metadata(db: Session = Depends(get_db)) -> CompositionMetadataResponse:
    return ResearchService(db).metadata()


@router.post("/compositions/evaluate", response_model=CompositionEvaluationResponse)
def evaluate_composition(
    request: CompositionEvaluationRequest,
    db: Session = Depends(get_db),
) -> CompositionEvaluationResponse:
    try:
        return ResearchService(db).evaluate(request)
    except (
        CompositionPolicyNotFoundError,
        ResearchInvariantError,
        ResearchValidationError,
        StrategyNotFoundError,
        StrategyValidationError,
    ) as exc:
        _raise_domain_error(exc)


@router.post("/experiments", response_model=ExperimentCreateResponse, status_code=201)
def create_experiment(
    request: ExperimentCreateRequest,
    db: Session = Depends(get_db),
) -> ExperimentCreateResponse:
    try:
        return ResearchService(db).create_experiment(request)
    except (
        CompositionPolicyNotFoundError,
        ResearchInvariantError,
        ResearchPersistenceError,
        ResearchValidationError,
        StrategyNotFoundError,
        StrategyValidationError,
    ) as exc:
        _raise_domain_error(exc)


@router.get("/experiments", response_model=ExperimentListResponse)
def list_experiments(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ExperimentListResponse:
    return ResearchService(db).list_experiments(page=page, page_size=page_size)


@router.get("/experiments/{experiment_id}", response_model=ExperimentDefinitionResponse)
def experiment_detail(
    experiment_id: UUID,
    db: Session = Depends(get_db),
) -> ExperimentDefinitionResponse:
    try:
        return ResearchService(db).experiment_detail(experiment_id)
    except ExperimentNotFoundError as exc:
        _raise_domain_error(exc)


@router.get(
    "/experiments/{experiment_id}/runs", response_model=ExperimentRunListResponse
)
def experiment_runs(
    experiment_id: UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ExperimentRunListResponse:
    try:
        return ResearchService(db).experiment_runs(
            experiment_id, page=page, page_size=page_size
        )
    except ExperimentNotFoundError as exc:
        _raise_domain_error(exc)


@router.post(
    "/experiments/{experiment_id}/runs", response_model=ExperimentReplayResponse
)
def replay_experiment(
    experiment_id: UUID,
    db: Session = Depends(get_db),
) -> ExperimentReplayResponse:
    try:
        return ResearchService(db).replay(experiment_id)
    except (
        CompositionPolicyNotFoundError,
        ExperimentNotFoundError,
        ResearchInvariantError,
        ResearchPersistenceError,
        ResearchValidationError,
        StrategyNotFoundError,
        StrategyValidationError,
    ) as exc:
        _raise_domain_error(exc)
