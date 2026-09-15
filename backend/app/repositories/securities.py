from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import CorporateAction, DailyPrice, Security
from app.schemas.api import SecurityCreate


class CorporateActionRevisionError(ValueError):
    """Raised when an eligible corporate-action revision chain is invalid."""


@dataclass(frozen=True, slots=True)
class PriceRecord:
    """Lightweight read model for batch technical computation."""

    security_id: UUID
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    traded_value: Decimal | None
    source: str
    data_origin: str = "UNKNOWN"
    source_artifact_id: UUID | None = None
    ingestion_run_id: UUID | None = None


class SecurityRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, payload: SecurityCreate) -> Security:
        security = Security(**payload.model_dump())
        self.session.add(security)
        self.session.flush()
        return security

    def list(self, *, page: int, page_size: int, query: str | None = None) -> tuple[list[Security], int]:
        filters = []
        if query:
            pattern = f"%{query.strip()}%"
            filters.append(or_(Security.symbol.ilike(pattern), Security.company_name.ilike(pattern)))
        statement = select(Security).where(*filters).order_by(Security.symbol).offset((page - 1) * page_size).limit(page_size)
        total_statement = select(func.count()).select_from(Security).where(*filters)
        return list(self.session.scalars(statement)), int(self.session.scalar(total_statement) or 0)

    def get_by_symbol(self, symbol: str) -> Security | None:
        return self.session.scalar(select(Security).where(func.lower(Security.symbol) == symbol.lower()))

    def get(self, security_id: UUID) -> Security | None:
        return self.session.get(Security, security_id)

    def latest_price_date(self) -> date | None:
        return self.session.scalar(select(func.max(DailyPrice.trading_date)))

    def prices(self, security_id: UUID, *, start: date | None, end: date | None) -> list[DailyPrice]:
        statement = select(DailyPrice).where(DailyPrice.security_id == security_id)
        if start:
            statement = statement.where(DailyPrice.trading_date >= start)
        if end:
            statement = statement.where(DailyPrice.trading_date <= end)
        return list(self.session.scalars(statement.order_by(DailyPrice.trading_date)))

    def prices_for_securities(
        self,
        security_ids: list[UUID],
        *,
        end: date,
        start: date | None = None,
    ) -> dict[UUID, list[PriceRecord]]:
        grouped: dict[UUID, list[PriceRecord]] = {security_id: [] for security_id in security_ids}
        if not security_ids:
            return grouped
        statement = (
            select(
                DailyPrice.security_id,
                DailyPrice.trading_date,
                DailyPrice.open,
                DailyPrice.high,
                DailyPrice.low,
                DailyPrice.close,
                DailyPrice.volume,
                DailyPrice.traded_value,
                DailyPrice.source,
                DailyPrice.data_origin,
                DailyPrice.source_artifact_id,
                DailyPrice.ingestion_run_id,
            )
            .where(DailyPrice.security_id.in_(security_ids), DailyPrice.trading_date <= end)
            .order_by(DailyPrice.security_id, DailyPrice.trading_date)
        )
        if start is not None:
            statement = statement.where(DailyPrice.trading_date >= start)
        for row in self.session.execute(statement):
            record = PriceRecord(*row)
            grouped[record.security_id].append(record)
        return grouped

    def corporate_action_history_for_securities(
        self,
        security_ids: list[UUID],
        *,
        as_of: datetime,
    ) -> dict[UUID, list[CorporateAction]]:
        """Load eligible revision rows without collapsing their historical states."""
        grouped = {security_id: [] for security_id in security_ids}
        if not security_ids:
            return grouped
        rows = self.session.scalars(
            select(CorporateAction)
            .where(
                CorporateAction.security_id.in_(security_ids),
                CorporateAction.available_at <= as_of,
            )
            .order_by(
                CorporateAction.security_id,
                CorporateAction.available_at,
                CorporateAction.id,
            )
        )
        for action in rows:
            grouped[action.security_id].append(action)
        return grouped

    @staticmethod
    def _resolve_action_revisions(actions: list[CorporateAction]) -> list[CorporateAction]:
        """Return the latest eligible row in each append-only revision chain."""
        by_id = {action.id: action for action in actions}
        superseded: set[UUID] = set()

        for action in actions:
            predecessor_id = action.supersedes_action_id
            if predecessor_id is None:
                continue
            if predecessor_id == action.id:
                raise CorporateActionRevisionError("Corporate actions cannot supersede themselves")
            predecessor = by_id.get(predecessor_id)
            if predecessor is None:
                # The predecessor may be outside this security batch or unavailable
                # at this as-of instant. In either case this row begins no eligible
                # chain here and must not hide data that was not yet knowable.
                continue
            if predecessor.security_id != action.security_id:
                raise CorporateActionRevisionError("Corporate-action revisions must retain security_id")
            superseded.add(predecessor_id)

        for action in actions:
            visited: set[UUID] = set()
            current = action
            while current.supersedes_action_id in by_id:
                if current.id in visited:
                    raise CorporateActionRevisionError("Corporate-action revision cycle detected")
                visited.add(current.id)
                current = by_id[current.supersedes_action_id]

        return sorted(
            (action for action in actions if action.id not in superseded),
            key=lambda action: (action.ex_date, action.available_at, str(action.id)),
        )

    def corporate_actions(
        self,
        security_id: UUID,
        *,
        as_of: datetime,
        after: date | None = None,
        through: date | None = None,
    ) -> list[CorporateAction]:
        grouped = self.corporate_actions_for_securities([security_id], as_of=as_of)
        actions = grouped[security_id]
        if after:
            actions = [action for action in actions if action.ex_date > after]
        if through:
            actions = [action for action in actions if action.ex_date <= through]
        return actions

    def corporate_actions_for_securities(
        self,
        security_ids: list[UUID],
        *,
        as_of: datetime,
        through: date | None = None,
    ) -> dict[UUID, list[CorporateAction]]:
        grouped = {security_id: [] for security_id in security_ids}
        if not security_ids:
            return grouped
        statement = (
            select(CorporateAction)
            .where(
                CorporateAction.security_id.in_(security_ids),
                CorporateAction.available_at <= as_of,
            )
            .order_by(
                CorporateAction.security_id,
                CorporateAction.available_at,
                CorporateAction.id,
            )
        )
        resolved = self._resolve_action_revisions(list(self.session.scalars(statement)))
        for action in resolved:
            if through is None or action.ex_date <= through:
                grouped[action.security_id].append(action)
        return grouped
