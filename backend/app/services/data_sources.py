from __future__ import annotations

from datetime import date

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.ingestion.nse.definitions import ArtifactType, DataOrigin, SOURCE_DEFINITIONS
from app.models import (
    CorporateAction,
    DailyPrice,
    DataIngestionRun,
    IndexMembership,
    IngestionIssue,
    MarketIndex,
    Security,
    SourceArtifact,
    TradingCalendar,
)
from app.schemas.data_sources import (
    ArtifactSummaryResponse,
    DataCoverageResponse,
    DataSourceDefinitionResponse,
    DataSourcesResponse,
    IndexCoverageResponse,
    IngestionIssueResponse,
)


REDISTRIBUTION_NOTICE = (
    "Public website availability does not grant redistribution rights. "
    "Artifacts remain project-local for personal research and are never exposed as bulk downloads."
)


def _mode(official_count: int, demo_count: int, unknown_count: int) -> str:
    if official_count and demo_count:
        return "MIXED"
    if official_count:
        return "OFFICIAL_NSE"
    if demo_count:
        return "DEMO"
    if unknown_count:
        return "UNKNOWN"
    return "UNKNOWN"


class DataSourceCoverageService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def source_status(self) -> DataSourcesResponse:
        ranked = (
            select(
                SourceArtifact.id.label("artifact_id"),
                func.row_number()
                .over(
                    partition_by=SourceArtifact.artifact_type,
                    order_by=(SourceArtifact.imported_at.desc(), SourceArtifact.id.desc()),
                )
                .label("artifact_rank"),
            )
            .subquery()
        )
        latest_rows = list(
            self.session.scalars(
                select(SourceArtifact)
                .join(ranked, ranked.c.artifact_id == SourceArtifact.id)
                .where(ranked.c.artifact_rank == 1)
                .order_by(SourceArtifact.artifact_type)
            )
        )
        latest_by_type: dict[str, SourceArtifact] = {}
        for artifact in latest_rows:
            latest_by_type.setdefault(artifact.artifact_type, artifact)
        origin_counts = self._origin_counts(Security)
        mode = _mode(
            origin_counts.get(DataOrigin.OFFICIAL_NSE_PUBLIC.value, 0),
            origin_counts.get(DataOrigin.DEMO.value, 0),
            origin_counts.get(DataOrigin.UNKNOWN.value, 0),
        )
        sources = []
        for artifact_type, definition in SOURCE_DEFINITIONS.items():
            latest = latest_by_type.get(artifact_type.value)
            sources.append(
                DataSourceDefinitionResponse(
                    artifact_type=artifact_type.value,
                    provider=definition.provider.value,
                    owner=definition.owner,
                    landing_page=definition.landing_page,
                    artifact_kind=definition.artifact_kind,
                    parser_code=definition.parser_code,
                    parser_version=definition.parser_version,
                    automation_suitability=definition.automation_suitability,
                    point_in_time_limit=definition.point_in_time_limit,
                    latest_artifact_status=latest.parse_status if latest else None,
                    latest_source_date=latest.source_date if latest else None,
                    latest_imported_at=latest.imported_at if latest else None,
                )
            )
        return DataSourcesResponse(
            mode=mode,
            sources=sources,
            redistribution_notice=REDISTRIBUTION_NOTICE,
        )

    def coverage(self) -> DataCoverageResponse:
        security_origins = self._origin_counts(Security)
        price_origins = self._origin_counts(DailyPrice)
        official_security_count = security_origins.get(DataOrigin.OFFICIAL_NSE_PUBLIC.value, 0)
        demo_security_count = security_origins.get(DataOrigin.DEMO.value, 0)
        unknown_security_count = security_origins.get(DataOrigin.UNKNOWN.value, 0)
        official_price_count = price_origins.get(DataOrigin.OFFICIAL_NSE_PUBLIC.value, 0)
        demo_price_count = price_origins.get(DataOrigin.DEMO.value, 0)
        mode = _mode(official_security_count + official_price_count, demo_security_count + demo_price_count, unknown_security_count)
        price_bounds = self.session.execute(
            select(func.min(DailyPrice.trading_date), func.max(DailyPrice.trading_date)).where(
                DailyPrice.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value
            )
        ).one()
        latest_success = self.session.scalar(
            select(DataIngestionRun.completed_at)
            .where(DataIngestionRun.status.in_(("SUCCEEDED", "SUCCESS", "PARTIAL")))
            .order_by(DataIngestionRun.completed_at.desc())
            .limit(1)
        )
        artifacts = list(
            self.session.scalars(
                select(SourceArtifact)
                .order_by(SourceArtifact.imported_at.desc(), SourceArtifact.id.desc())
                .limit(25)
            )
        )
        issues = list(
            self.session.scalars(
                select(IngestionIssue)
                .order_by(IngestionIssue.created_at.desc(), IngestionIssue.id.desc())
                .limit(25)
            )
        )
        artifact_count = int(self.session.scalar(select(func.count()).select_from(SourceArtifact)) or 0)
        promoted_actions = int(
            self.session.scalar(
                select(func.count()).select_from(CorporateAction).where(
                    CorporateAction.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value
                )
            )
            or 0
        )
        quarantined_actions = int(
            self.session.scalar(
                select(func.count()).select_from(IngestionIssue).where(
                    IngestionIssue.code == "AVAILABILITY_TIMESTAMP_UNKNOWN"
                )
            )
            or 0
        )
        missing_official_sessions = int(
            self.session.scalar(
                select(func.count())
                .select_from(TradingCalendar)
                .where(
                    TradingCalendar.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value,
                    TradingCalendar.is_trading_day.is_(True),
                    ~exists(
                        select(DailyPrice.id).where(
                            DailyPrice.trading_date == TradingCalendar.trading_date,
                            DailyPrice.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value,
                        )
                    ),
                )
            )
            or 0
        )
        index_coverage = self._index_coverage()
        warnings: list[str] = []
        if any(item.coverage_kind == "CURRENT_SNAPSHOT_ONLY" for item in index_coverage):
            warnings.append("HISTORICAL_MEMBERSHIP_COVERAGE_INCOMPLETE")
        if quarantined_actions:
            warnings.append("CORPORATE_ACTION_AVAILABILITY_INCOMPLETE")
        if missing_official_sessions:
            warnings.append("MISSING_OFFICIAL_TRADING_SESSIONS")
        if official_price_count == 0:
            warnings.append("OFFICIAL_PRICE_HISTORY_NOT_IMPORTED")
        return DataCoverageResponse(
            mode=mode,
            provider="OFFICIAL_NSE_PUBLIC" if official_security_count or official_price_count else None,
            last_successful_ingestion=latest_success,
            earliest_official_price_session=price_bounds[0],
            latest_official_price_session=price_bounds[1],
            official_security_count=official_security_count,
            official_daily_price_count=official_price_count,
            demo_security_count=demo_security_count,
            demo_daily_price_count=demo_price_count,
            source_artifact_count=artifact_count,
            corporate_actions_promoted=promoted_actions,
            corporate_actions_quarantined=quarantined_actions,
            missing_official_sessions=missing_official_sessions,
            index_coverage=index_coverage,
            latest_artifacts=[
                ArtifactSummaryResponse(
                    id=item.id,
                    provider=item.provider,
                    artifact_type=item.artifact_type,
                    source_date=item.source_date,
                    imported_at=item.imported_at,
                    sha256=item.sha256,
                    parser_code=item.parser_code,
                    parser_version=item.parser_version,
                    parse_status=item.parse_status,
                    row_count=item.row_count,
                    accepted_row_count=item.accepted_row_count,
                    rejected_row_count=item.rejected_row_count,
                    warning_count=item.warning_count,
                )
                for item in artifacts
            ],
            latest_issues=[
                IngestionIssueResponse(
                    severity=item.severity,
                    code=item.code,
                    message=item.message,
                    row_key=item.row_key,
                    created_at=item.created_at,
                )
                for item in issues
            ],
            warnings=sorted(set(warnings)),
        )

    def _origin_counts(self, model: type[Security] | type[DailyPrice]) -> dict[str, int]:
        rows = self.session.execute(
            select(model.data_origin, func.count()).group_by(model.data_origin)
        )
        return {str(origin): int(count) for origin, count in rows}

    def _index_coverage(self) -> list[IndexCoverageResponse]:
        results: list[IndexCoverageResponse] = []
        for symbol, label in (("NIFTY200", "NIFTY 200"), ("NIFTY500", "NIFTY 500")):
            market_index = self.session.scalar(
                select(MarketIndex).where(
                    MarketIndex.provider == "OFFICIAL_NSE_INDICES_PUBLIC",
                    MarketIndex.symbol == symbol,
                )
            )
            if market_index is None:
                results.append(
                    IndexCoverageResponse(
                        symbol=symbol,
                        label=label,
                        current_snapshot_present=False,
                        snapshot_as_of=None,
                        member_count=0,
                        coverage_start=None,
                        coverage_end=None,
                        coverage_kind="NONE",
                        warning="CURRENT_SNAPSHOT_NOT_IMPORTED",
                    )
                )
                continue
            dates = self.session.execute(
                select(
                    func.min(IndexMembership.valid_from),
                    func.max(IndexMembership.valid_from),
                    func.count(func.distinct(IndexMembership.valid_from)),
                ).where(IndexMembership.index_id == market_index.id)
            ).one()
            latest_date: date | None = dates[1]
            member_count = 0
            if latest_date is not None:
                member_count = int(
                    self.session.scalar(
                        select(func.count()).select_from(IndexMembership).where(
                            IndexMembership.index_id == market_index.id,
                            IndexMembership.valid_from == latest_date,
                        )
                    )
                    or 0
                )
            snapshot_count = int(dates[2] or 0)
            results.append(
                IndexCoverageResponse(
                    symbol=symbol,
                    label=label,
                    current_snapshot_present=latest_date is not None,
                    snapshot_as_of=latest_date,
                    member_count=member_count,
                    coverage_start=dates[0],
                    coverage_end=None,
                    coverage_kind=(
                        "NONE" if snapshot_count == 0 else "CURRENT_SNAPSHOT_ONLY" if snapshot_count == 1 else "BOUNDED_SNAPSHOT_SEQUENCE"
                    ),
                    warning="HISTORICAL_MEMBERSHIP_COVERAGE_INCOMPLETE" if snapshot_count else "CURRENT_SNAPSHOT_NOT_IMPORTED",
                )
            )
        return results
