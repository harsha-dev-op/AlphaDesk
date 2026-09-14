from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import (
    CorporateAction,
    DailyPrice,
    DataIngestionRun,
    IngestionIssue,
    Security,
    SourceArtifact,
    TradingCalendar,
)
from app.quality.checks import QualityCheck, QualityStatus, missing_sessions, stale_sessions, validate_ohlcv

logger = structlog.get_logger(__name__)


class DataQualityService:
    def __init__(self, session: Session):
        self.session = session

    def run(self, *, as_of: date | None = None) -> tuple[QualityStatus, list[QualityCheck], date | None, datetime | None]:
        as_of = as_of or date.today()
        checks = [
            self._ohlc_check(),
            self._duplicate_check(),
            self._missing_session_check(),
            self._unknown_calendar_check(),
            self._freshness_check(as_of),
            *self._ingestion_checks(),
        ]
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
            select(DataIngestionRun.completed_at)
            .where(DataIngestionRun.status.in_(("SUCCEEDED", "SUCCESS", "PARTIAL")))
            .order_by(DataIngestionRun.completed_at.desc())
            .limit(1)
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
        bounds = list(
            self.session.execute(
                select(
                    Security.id,
                    Security.exchange,
                    func.min(DailyPrice.trading_date),
                    func.max(DailyPrice.trading_date),
                )
                .join(DailyPrice, DailyPrice.security_id == Security.id)
                .where(Security.is_active.is_(True))
                .group_by(Security.id, Security.exchange)
            )
        )
        if not bounds:
            return QualityCheck("Session coverage", QualityStatus.HEALTHY, "No bounded price series require session checks")
        earliest = min(row[2] for row in bounds)
        latest = max(row[3] for row in bounds)
        exchanges = {row[1] for row in bounds}
        calendar_by_exchange: dict[str, list[date]] = {exchange: [] for exchange in exchanges}
        for exchange, trading_date in self.session.execute(
            select(TradingCalendar.exchange, TradingCalendar.trading_date)
            .where(
                TradingCalendar.exchange.in_(exchanges),
                TradingCalendar.is_trading_day.is_(True),
                TradingCalendar.trading_date.between(earliest, latest),
            )
            .order_by(TradingCalendar.exchange, TradingCalendar.trading_date)
        ):
            calendar_by_exchange[exchange].append(trading_date)
        security_ids = {row[0] for row in bounds}
        observed_by_security: dict[object, list[date]] = {security_id: [] for security_id in security_ids}
        for security_id, trading_date in self.session.execute(
            select(DailyPrice.security_id, DailyPrice.trading_date)
            .where(
                DailyPrice.security_id.in_(security_ids),
                DailyPrice.trading_date.between(earliest, latest),
            )
            .order_by(DailyPrice.security_id, DailyPrice.trading_date)
        ):
            observed_by_security[security_id].append(trading_date)
        missing_count = 0
        for security_id, exchange, series_start, series_end in bounds:
            expected = [
                day
                for day in calendar_by_exchange[exchange]
                if series_start <= day <= series_end
            ]
            missing_count += len(missing_sessions(expected, observed_by_security[security_id]))
        status = QualityStatus.WARNING if missing_count else QualityStatus.HEALTHY
        return QualityCheck("Session coverage", status, "All expected sessions are present" if not missing_count else f"{missing_count} expected sessions are missing", missing_count)

    def _unknown_calendar_check(self) -> QualityCheck:
        unknown_count = int(
            self.session.scalar(
                select(func.count(func.distinct(DailyPrice.trading_date)))
                .select_from(DailyPrice)
                .join(Security, Security.id == DailyPrice.security_id)
                .outerjoin(
                    TradingCalendar,
                    and_(
                        TradingCalendar.exchange == Security.exchange,
                        TradingCalendar.trading_date == DailyPrice.trading_date,
                    ),
                )
                .where(TradingCalendar.id.is_(None))
            )
            or 0
        )
        status = QualityStatus.WARNING if unknown_count else QualityStatus.HEALTHY
        return QualityCheck(
            "Calendar evidence",
            status,
            "Every observed price session has calendar evidence"
            if not unknown_count
            else f"{unknown_count} observed price sessions have unknown calendar status",
            unknown_count,
        )

    def _ingestion_checks(self) -> list[QualityCheck]:
        artifact_counts = {
            str(status): int(count)
            for status, count in self.session.execute(
                select(SourceArtifact.parse_status, func.count()).group_by(
                    SourceArtifact.parse_status
                )
            )
        }
        failed = artifact_counts.get("FAILED", 0) + artifact_counts.get("CONFLICT", 0)
        partial = artifact_counts.get("PARTIAL", 0)
        artifact_status = (
            QualityStatus.FAILED
            if failed
            else QualityStatus.WARNING
            if partial
            else QualityStatus.HEALTHY
        )
        artifact_message = "All recorded source artifacts completed cleanly"
        if failed:
            artifact_message = f"{failed} failed or conflicting source artifacts require review"
        elif partial:
            artifact_message = f"{partial} source artifacts completed with partial acceptance"

        issue_counts = {
            str(code): int(count)
            for code, count in self.session.execute(
                select(IngestionIssue.code, func.count()).group_by(IngestionIssue.code)
            )
        }
        membership_issue_count = sum(
            issue_counts.get(code, 0)
            for code in (
                "UNEXPECTED_MEMBER_COUNT",
                "UNKNOWN_INDEX_SECURITY",
                "INDEX_SNAPSHOT_CONFLICT",
            )
        )
        action_issue_count = sum(
            issue_counts.get(code, 0)
            for code in (
                "AMBIGUOUS_ACTION_RATIO",
                "UNSUPPORTED_CORPORATE_ACTION",
                "AVAILABILITY_TIMESTAMP_UNKNOWN",
                "DUPLICATE_CORPORATE_ACTION",
                "CORPORATE_ACTION_REVISION_UNRESOLVED",
            )
        )
        unprovenanced_actions = int(
            self.session.scalar(
                select(func.count())
                .select_from(CorporateAction)
                .where(
                    CorporateAction.data_origin == "OFFICIAL_NSE_PUBLIC",
                    CorporateAction.available_at.is_(None),
                )
            )
            or 0
        )
        return [
            QualityCheck("Source artifacts", artifact_status, artifact_message, failed + partial),
            QualityCheck(
                "Index membership ingestion",
                QualityStatus.WARNING if membership_issue_count else QualityStatus.HEALTHY,
                "No current-snapshot membership issues are recorded"
                if not membership_issue_count
                else f"{membership_issue_count} membership validation issues are recorded",
                membership_issue_count,
            ),
            QualityCheck(
                "Corporate-action PIT coverage",
                QualityStatus.FAILED
                if unprovenanced_actions
                else QualityStatus.WARNING
                if action_issue_count
                else QualityStatus.HEALTHY,
                f"{unprovenanced_actions} promoted official actions lack available_at"
                if unprovenanced_actions
                else "All promoted official actions have point-in-time availability"
                if not action_issue_count
                else f"{action_issue_count} actions were quarantined or rejected",
                unprovenanced_actions + action_issue_count,
            ),
        ]

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
