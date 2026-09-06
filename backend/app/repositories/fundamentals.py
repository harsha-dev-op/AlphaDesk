from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FundamentalReport


class FundamentalRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_latest_as_of(self, security_id: UUID, simulation_date: date) -> FundamentalReport | None:
        """Return the latest fiscal period whose exact version was public by simulation_date."""
        return self.session.scalar(
            select(FundamentalReport)
            .where(
                FundamentalReport.security_id == security_id,
                FundamentalReport.reported_date <= simulation_date,
                FundamentalReport.effective_date <= simulation_date,
            )
            .order_by(FundamentalReport.fiscal_period_end.desc(), FundamentalReport.effective_date.desc())
            .limit(1)
        )
