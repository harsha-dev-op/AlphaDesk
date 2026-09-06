from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.api import QualityCheckResponse, QualityStatusResponse
from app.services.quality import DataQualityService

router = APIRouter(prefix="/api/v1/data-quality", tags=["data-quality"])


@router.get("/status", response_model=QualityStatusResponse)
def quality_status(db: Session = Depends(get_db)) -> QualityStatusResponse:
    overall, checks, latest_data, last_ingestion = DataQualityService(db).run()
    return QualityStatusResponse(status=overall.value, checked_at=datetime.now(UTC), latest_data_date=latest_data, last_successful_ingestion=last_ingestion, checks=[QualityCheckResponse(name=check.name, status=check.status.value, message=check.message, issue_count=check.issue_count) for check in checks])
