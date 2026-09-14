from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.data_sources import DataCoverageResponse, DataSourcesResponse
from app.services.data_sources import DataSourceCoverageService


router = APIRouter(prefix="/api/v1", tags=["data-sources"])


@router.get("/data-sources", response_model=DataSourcesResponse)
def data_sources(db: Session = Depends(get_db)) -> DataSourcesResponse:
    return DataSourceCoverageService(db).source_status()


@router.get("/data-coverage", response_model=DataCoverageResponse)
def data_coverage(db: Session = Depends(get_db)) -> DataCoverageResponse:
    return DataSourceCoverageService(db).coverage()

