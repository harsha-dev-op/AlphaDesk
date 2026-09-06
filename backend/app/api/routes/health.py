import structlog
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import get_db
from app.schemas.api import HealthResponse

router = APIRouter(tags=["system"])
logger = structlog.get_logger(__name__)


@router.get("/health", response_model=HealthResponse)
def health(response: Response, db: Session = Depends(get_db)) -> HealthResponse:
    database_status = "healthy"
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        database_status = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.error("database_connection_failed", error_type=type(exc).__name__)
    return HealthResponse(status="healthy" if database_status == "healthy" else "degraded", api="healthy", database=database_status, version=get_settings().app_version)
