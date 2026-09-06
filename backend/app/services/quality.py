from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DailyPrice, DataIngestionRun, Security, TradingCalendar
from app.quality.checks import QualityCheck, QualityStatus, missing_sessions, stale_sessions, validate_ohlcv

logger = structlog.get_logger(__name__)


class DataQualityService:
    def __init__(self, session: Session):
        self.session = session

    def run(self, *, as_of: date | None = None) -> tuple[QualityStatus, list[QualityCheck], date | None, datetime | None]:
        as_of = as_of or date.today()
        checks = [self._ohlc_check(), self._duplicate_check(), self._missing_session_check(), self._freshness_check(as_of)]
        overall = QualityStatus.HEALTHY
        if any(check.status == QualityStatus.FAILED for check in checks):
            overall = QualityStatus.FAILED
        elif any(check.status == QualityStatus.WARNING for check in checks):
            overall = QualityStatus.WARNING
        if overall != QualityStatus.HEALTHY:
            log = logger.error if overall == QualityStatus.FAILED else logger.warning
            log("data_quality_failure", status=overall.value, failed_checks=[check.name for check in checks if check.status != QualityStatus.HEALTHY])
        latest_data = self.session.scalar(select(func.max(DailyPrice.trading_date)))
        last_ingestion = self.session.scalar(
            select(DataIngestionRun.completed_at).where(DataIngestionRun.status == "SUCCESS").order_by(DataIngestionRun.completed_at.desc()).limit(1)
        )
        return overall, checks, latest_data, last_ingestion

    def _ohlc_check(self) -> QualityCheck:
        issues = 0
        for row in self.session.scalars(select(DailyPrice)):
            issues += len(validate_ohlcv(open_=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume))
        status = QualityStatus.FAILED if issues else QualityStatus.HEALTHY
        return QualityCheck("OHLC validation", status, "No impossible OHLCV rows" if not issues else f"{issues} validation violations", issues)

    def _duplicate_check(self) -> QualityCheck:
        duplicate_groups = self.session.scalar(
            select(func.count()).select_from(
                select(DailyPrice.security_id, DailyPrice.trading_date)
                .group_by(DailyPrice.security_id, DailyPrice.trading_date)
                .having(func.count() > 1)
                .subquery()
            )
        ) or 0
        return QualityCheck("Duplicate detection", QualityStatus.FAILED if duplicate_groups else QualityStatus.HEALTHY, "Unique security/session keys enforced" if not duplicate_groups else f"{duplicate_groups} duplicate groups", int(duplicate_groups))

    def _missing_session_check(self) -> QualityCheck:
        missing_count = 0
        for security in self.session.scalars(select(Security).where(Security.is_active.is_(True))):
            bounds = self.session.execute(
                select(func.min(DailyPrice.trading_date), func.max(DailyPrice.trading_date)).where(DailyPrice.security_id == security.id)
            ).one()
            if not bounds[0] or not bounds[1]:
                continue
            expected = list(self.session.scalars(select(TradingCalendar.trading_date).where(TradingCalendar.exchange == security.exchange, TradingCalendar.is_trading_day.is_(True), TradingCalendar.trading_date.between(bounds[0], bounds[1]))))
            observed = list(self.session.scalars(select(DailyPrice.trading_date).where(DailyPrice.security_id == security.id, DailyPrice.trading_date.between(bounds[0], bounds[1]))))
            missing_count += len(missing_sessions(expected, observed))
        status = QualityStatus.WARNING if missing_count else QualityStatus.HEALTHY
        return QualityCheck("Session coverage", status, "All expected sessions are present" if not missing_count else f"{missing_count} expected sessions are missing", missing_count)

    def _freshness_check(self, as_of: date) -> QualityCheck:
        calendar_latest = self.session.scalar(select(func.max(TradingCalendar.trading_date)).where(TradingCalendar.is_trading_day.is_(True), TradingCalendar.trading_date <= as_of))
        latest_data = self.session.scalar(select(func.max(DailyPrice.trading_date)))
        if calendar_latest is None:
            return QualityCheck("Freshness", QualityStatus.FAILED, "No trading calendar coverage", 1)
        if as_of - calendar_latest > timedelta(days=7):
            return QualityCheck("Freshness", QualityStatus.WARNING, f"Trading calendar ends at {calendar_latest.isoformat()}", 1)
        expected = list(self.session.scalars(select(TradingCalendar.trading_date).where(TradingCalendar.is_trading_day.is_(True), TradingCalendar.trading_date <= calendar_latest)))
        stale_count = stale_sessions(expected, latest_data)
        status = QualityStatus.WARNING if stale_count else QualityStatus.HEALTHY
        return QualityCheck("Freshness", status, "Market data is current to the calendar" if not stale_count else f"Data is {stale_count} trading sessions behind", stale_count)
