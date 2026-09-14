from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.repositories.indices import IndexRepository
from app.schemas.api import IndexMemberResponse, IndexMembersResponse, IndexResponse, SecurityResponse

router = APIRouter(prefix="/api/v1/indices", tags=["indices"])


@router.get("", response_model=list[IndexResponse])
def list_indices(db: Session = Depends(get_db)) -> list[IndexResponse]:
    return [IndexResponse.model_validate(item) for item in IndexRepository(db).list()]


@router.get("/{index}/members", response_model=IndexMembersResponse)
def index_members(index: str, as_of: date | None = None, db: Session = Depends(get_db)) -> IndexMembersResponse:
    repository = IndexRepository(db)
    market_index = repository.get_by_name_or_symbol(index)
    if not market_index:
        raise HTTPException(status_code=404, detail="Index not found")
    query_date = as_of or date.today()
    try:
        repository.assert_historical_coverage(market_index, query_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    memberships = repository.members_as_of(market_index.id, query_date)
    return IndexMembersResponse(index=IndexResponse.model_validate(market_index), as_of=query_date, members=[IndexMemberResponse(security=SecurityResponse.model_validate(item.security), valid_from=item.valid_from, valid_to=item.valid_to, source=item.source) for item in memberships])
