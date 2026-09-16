from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import IndexDailyPrice, IndexMembership, MarketIndex


@dataclass(frozen=True, slots=True)
class IndexPriceRecord:
    id: UUID
    index_id: UUID
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    source_mode: str
    source: str
    available_at: datetime
    source_artifact_id: UUID | None
    ingestion_run_id: UUID | None


@dataclass(frozen=True, slots=True)
class IndexPriceCoverageRecord:
    index_id: UUID
    source_mode: str
    first_date: date
    last_date: date
    session_count: int
    first_classifiable_date: date | None


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

    def coverage_start(self, index_id) -> date | None:
        return self.session.scalar(
            select(func.min(IndexMembership.valid_from)).where(IndexMembership.index_id == index_id)
        )

    def assert_historical_coverage(self, index: MarketIndex, requested_start: date) -> None:
        if index.provider != "OFFICIAL_NSE_INDICES_PUBLIC":
            return
        coverage_start = self.coverage_start(index.id)
        if coverage_start is None or requested_start < coverage_start:
            raise ValueError("HISTORICAL_MEMBERSHIP_COVERAGE_INCOMPLETE")

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

    def price_history(
        self,
        index_id: UUID,
        *,
        source_mode: str,
        as_of: datetime | None = None,
    ) -> list[IndexPriceRecord]:
        statement = select(
            IndexDailyPrice.id,
            IndexDailyPrice.index_id,
            IndexDailyPrice.trading_date,
            IndexDailyPrice.open,
            IndexDailyPrice.high,
            IndexDailyPrice.low,
            IndexDailyPrice.close,
            IndexDailyPrice.source_mode,
            IndexDailyPrice.source,
            IndexDailyPrice.available_at,
            IndexDailyPrice.source_artifact_id,
            IndexDailyPrice.ingestion_run_id,
        ).where(
            IndexDailyPrice.index_id == index_id,
            IndexDailyPrice.source_mode == source_mode,
        )
        if as_of is not None:
            statement = statement.where(IndexDailyPrice.available_at <= as_of)
        statement = statement.order_by(
            IndexDailyPrice.trading_date,
            IndexDailyPrice.available_at,
            IndexDailyPrice.id,
        )
        return [IndexPriceRecord(*row) for row in self.session.execute(statement)]

    def price_coverage_catalog(self) -> list[IndexPriceCoverageRecord]:
        rows = self.session.execute(
            select(
                IndexDailyPrice.index_id,
                IndexDailyPrice.source_mode,
                IndexDailyPrice.trading_date,
            ).where(IndexDailyPrice.available_at <= datetime.now(UTC))
            .order_by(
                IndexDailyPrice.index_id,
                IndexDailyPrice.source_mode,
                IndexDailyPrice.trading_date,
            )
        )
        grouped: dict[tuple[UUID, str], list[date]] = {}
        for index_id, source_mode, trading_date in rows:
            grouped.setdefault((index_id, source_mode), []).append(trading_date)
        return [
            IndexPriceCoverageRecord(
                index_id=index_id,
                source_mode=source_mode,
                first_date=dates[0],
                last_date=dates[-1],
                session_count=len(dates),
                first_classifiable_date=dates[199] if len(dates) >= 200 else None,
            )
            for (index_id, source_mode), dates in sorted(
                grouped.items(), key=lambda item: (str(item[0][0]), item[0][1])
            )
        ]
