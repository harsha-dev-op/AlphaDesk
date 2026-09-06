from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.scanner import ScannerNotFoundError, ScannerService, ScannerValidationError
from app.schemas.scanner import MarketScanRequest, MarketScanResponse, ScannerMetadataResponse

router = APIRouter(prefix="/api/v1/scanner", tags=["market-scanner"])


@router.get("/metadata", response_model=ScannerMetadataResponse)
def scanner_metadata(db: Session = Depends(get_db)) -> ScannerMetadataResponse:
    return ScannerService(db).metadata()


@router.post("/scan", response_model=MarketScanResponse)
def scan_market(request: MarketScanRequest, db: Session = Depends(get_db)) -> MarketScanResponse:
    try:
        return ScannerService(db).scan(request)
    except ScannerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ScannerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
