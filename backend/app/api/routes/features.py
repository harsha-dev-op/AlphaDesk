from dataclasses import asdict
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.repositories.securities import SecurityRepository
from app.schemas.technical import FeatureCatalogResponse, FeatureDefinitionResponse, FeatureSetCatalogResponse, SecurityFeatureSeriesResponse
from app.technical.registry import CORE_TECHNICAL_SET, feature_catalog, feature_set_catalog, find_feature_set
from app.technical.service import TechnicalFeatureService, feature_set_response

router = APIRouter(prefix="/api/v1", tags=["technical-features"])


@router.get("/features/catalog", response_model=FeatureCatalogResponse)
def get_feature_catalog() -> FeatureCatalogResponse:
    definitions = [FeatureDefinitionResponse(**asdict(feature)) for feature in feature_catalog()]
    return FeatureCatalogResponse(definitions=definitions, count=len(definitions))


@router.get("/feature-sets", response_model=FeatureSetCatalogResponse)
def get_feature_sets() -> FeatureSetCatalogResponse:
    return FeatureSetCatalogResponse(feature_sets=[feature_set_response(feature_set) for feature_set in feature_set_catalog()])


@router.get("/securities/{symbol}/features", response_model=SecurityFeatureSeriesResponse)
def get_security_features(
    symbol: str,
    start_date: date | None = None,
    end_date: date | None = None,
    feature_set: str = Query(CORE_TECHNICAL_SET.code),
    feature_set_version: str = Query(CORE_TECHNICAL_SET.version),
    adjustment_policy: Literal["adjusted", "raw"] = "adjusted",
    as_of: datetime | None = None,
    db: Session = Depends(get_db),
) -> SecurityFeatureSeriesResponse:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    selected_feature_set = find_feature_set(feature_set, feature_set_version)
    if not selected_feature_set:
        raise HTTPException(status_code=404, detail="Feature set or version not found")
    security = SecurityRepository(db).get_by_symbol(symbol)
    if not security:
        raise HTTPException(status_code=404, detail="Security not found")
    try:
        return TechnicalFeatureService(db).compute(
            security,
            start_date=start_date,
            end_date=end_date,
            adjustment_policy=adjustment_policy.upper(),
            as_of=as_of,
            feature_set=selected_feature_set,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
