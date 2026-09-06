from __future__ import annotations

from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import IndexMembership, MarketIndex


class IndexRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(self) -> list[MarketIndex]:
        return list(self.session.scalars(select(MarketIndex).order_by(MarketIndex.name)))

    def get_by_name_or_symbol(self, identifier: str) -> MarketIndex | None:
        return self.session.scalar(
            select(MarketIndex).where(
                or_(func.lower(MarketIndex.symbol) == identifier.lower(), func.lower(MarketIndex.name) == identifier.lower())
            )
        )

    def members_as_of(self, index_id, as_of: date) -> list[IndexMembership]:
        statement = (
            select(IndexMembership)
            .options(joinedload(IndexMembership.security))
            .where(
                IndexMembership.index_id == index_id,
                IndexMembership.valid_from <= as_of,
                or_(IndexMembership.valid_to.is_(None), IndexMembership.valid_to >= as_of),
            )
            .order_by(IndexMembership.security_id)
        )
        return list(self.session.scalars(statement))

    def memberships_between(self, index_id, start: date, end: date) -> list[IndexMembership]:
        statement = (
            select(IndexMembership)
            .options(joinedload(IndexMembership.security))
            .where(
                IndexMembership.index_id == index_id,
                IndexMembership.valid_from <= end,
                or_(IndexMembership.valid_to.is_(None), IndexMembership.valid_to >= start),
            )
            .order_by(
                IndexMembership.valid_from,
                IndexMembership.security_id,
                IndexMembership.id,
            )
        )
        return list(self.session.scalars(statement))
