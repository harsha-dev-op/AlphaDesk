from __future__ import annotations

from datetime import date

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.ingestion.nse.definitions import ArtifactType, DataOrigin, SOURCE_DEFINITIONS
from app.models import (
    CorporateAction,
    DailyPrice,
    DataIngestionRun,
    FundamentalFiling,
    FundamentalFact,
    IndexMembership,
    IngestionIssue,
    IndexDailyPrice,
    MarketIndex,
    Security,
    SecurityIndustryClassification,
    SourceArtifact,
    TradingCalendar,
)
from app.schemas.data_sources import (
    ActivationDatasetResponse,
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
        official_security_count = security_origins.get(DataOrigin.OFFICIAL_NSE_PUBLIC.value, 0)
        demo_security_count = security_origins.get(DataOrigin.DEMO.value, 0)
        unknown_security_count = security_origins.get(DataOrigin.UNKNOWN.value, 0)
        (
            official_price_count,
            official_price_securities,
            official_price_sessions,
            official_price_start,
            official_price_end,
        ) = self.session.execute(
            select(
                func.count(),
                func.count(func.distinct(DailyPrice.security_id)),
                func.count(func.distinct(DailyPrice.trading_date)),
                func.min(DailyPrice.trading_date),
                func.max(DailyPrice.trading_date),
            )
            .join(Security, Security.id == DailyPrice.security_id)
            .where(Security.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value)
        ).one()
        official_price_count = int(official_price_count or 0)
        official_price_securities = int(official_price_securities or 0)
        official_price_sessions = int(official_price_sessions or 0)
        demo_price_count = int(
            self.session.scalar(
                select(func.count()).select_from(DailyPrice).where(
                    DailyPrice.security_id.in_(
                        select(Security.id).where(
                            Security.data_origin == DataOrigin.DEMO.value
                        )
                    )
                )
            )
            or 0
        )
        mode = _mode(official_security_count + official_price_count, demo_security_count + demo_price_count, unknown_security_count)
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
        activation_datasets = self._activation_datasets(
            official_security_count=official_security_count,
            official_price_count=official_price_count,
            official_price_securities=official_price_securities,
            official_price_sessions=official_price_sessions,
            price_start=official_price_start,
            price_end=official_price_end,
            index_coverage=index_coverage,
            promoted_actions=promoted_actions,
            quarantined_actions=quarantined_actions,
        )
        core_statuses = {
            item.code: item.status
            for item in activation_datasets
            if item.code
            in {
                "SECURITY_MASTER",
                "NIFTY_200_MEMBERSHIP",
                "NIFTY_500_MEMBERSHIP",
                "EQUITY_EOD",
                "NIFTY_200_BENCHMARK",
            }
        }
        activation_status = (
            "READY"
            if core_statuses and all(value == "READY" for value in core_statuses.values())
            else "UNAVAILABLE"
            if core_statuses and all(value == "UNAVAILABLE" for value in core_statuses.values())
            else "PARTIAL"
        )
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
            earliest_official_price_session=official_price_start,
            latest_official_price_session=official_price_end,
            official_security_count=official_security_count,
            official_daily_price_count=official_price_count,
            demo_security_count=demo_security_count,
            demo_daily_price_count=demo_price_count,
            source_artifact_count=artifact_count,
            corporate_actions_promoted=promoted_actions,
            corporate_actions_quarantined=quarantined_actions,
            missing_official_sessions=missing_official_sessions,
            activation_status=activation_status,
            activation_datasets=activation_datasets,
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

    def _activation_datasets(
        self,
        *,
        official_security_count: int,
        official_price_count: int,
        official_price_securities: int,
        official_price_sessions: int,
        price_start: date | None,
        price_end: date | None,
        index_coverage: list[IndexCoverageResponse],
        promoted_actions: int,
        quarantined_actions: int,
    ) -> list[ActivationDatasetResponse]:
        membership_by_symbol = {item.symbol: item for item in index_coverage}
        security_artifact_date = self.session.scalar(
            select(func.max(SourceArtifact.source_date)).where(
                SourceArtifact.artifact_type == ArtifactType.SECURITY_MASTER.value,
                SourceArtifact.parse_status.in_(("SUCCEEDED", "PARTIAL")),
            )
        )
        nifty200 = self.session.scalar(
            select(MarketIndex).where(
                MarketIndex.provider == "OFFICIAL_NSE_INDICES_PUBLIC",
                MarketIndex.symbol == "NIFTY200",
            )
        )
        member_ids: list[object] = []
        snapshot_date: date | None = None
        if nifty200 is not None:
            snapshot_date = self.session.scalar(
                select(func.max(IndexMembership.valid_from)).where(
                    IndexMembership.index_id == nifty200.id
                )
            )
            if snapshot_date is not None:
                member_ids = list(
                    self.session.scalars(
                        select(IndexMembership.security_id).where(
                            IndexMembership.index_id == nifty200.id,
                            IndexMembership.valid_from == snapshot_date,
                        )
                    )
                )
        member_price_counts: dict[object, tuple[int, date | None, date | None]] = {}
        if member_ids:
            member_price_counts = {
                security_id: (int(count), first_date, last_date)
                for security_id, count, first_date, last_date in self.session.execute(
                    select(
                        DailyPrice.security_id,
                        func.count(func.distinct(DailyPrice.trading_date)),
                        func.min(DailyPrice.trading_date),
                        func.max(DailyPrice.trading_date),
                    )
                    .where(
                        DailyPrice.security_id.in_(member_ids),
                        DailyPrice.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value,
                    )
                    .group_by(DailyPrice.security_id)
                )
            }
        members_with_any = len(member_price_counts)
        members_with_200 = sum(1 for count, _, _ in member_price_counts.values() if count >= 200)
        members_with_252 = sum(1 for count, _, _ in member_price_counts.values() if count >= 252)
        members_with_target = sum(
            1
            for _, first_date, last_date in member_price_counts.values()
            if first_date is not None
            and first_date <= date(2021, 1, 1)
            and price_end is not None
            and last_date == price_end
        )
        significant_gap_members = sum(
            1
            for count, _, _ in member_price_counts.values()
            if official_price_sessions >= 20 and count < int(official_price_sessions * 0.95)
        )

        benchmark_count = 0
        benchmark_start: date | None = None
        benchmark_end: date | None = None
        regime_ready_date: date | None = None
        benchmark_missing_known_sessions = 0
        benchmark_sessions_without_equity_eod = 0
        if nifty200 is not None:
            benchmark_count, benchmark_start, benchmark_end = self.session.execute(
                select(
                    func.count(),
                    func.min(IndexDailyPrice.trading_date),
                    func.max(IndexDailyPrice.trading_date),
                ).where(
                    IndexDailyPrice.index_id == nifty200.id,
                    IndexDailyPrice.source_mode == "OFFICIAL",
                )
            ).one()
            benchmark_count = int(benchmark_count or 0)
            if benchmark_count >= 200:
                regime_ready_date = self.session.scalar(
                    select(IndexDailyPrice.trading_date)
                    .where(
                        IndexDailyPrice.index_id == nifty200.id,
                        IndexDailyPrice.source_mode == "OFFICIAL",
                    )
                    .order_by(IndexDailyPrice.trading_date)
                    .offset(199)
                    .limit(1)
                )
            benchmark_sessions_without_equity_eod = int(
                self.session.scalar(
                    select(func.count())
                    .select_from(IndexDailyPrice)
                    .where(
                        IndexDailyPrice.index_id == nifty200.id,
                        IndexDailyPrice.source_mode == "OFFICIAL",
                        ~exists(
                            select(DailyPrice.id).where(
                                DailyPrice.trading_date
                                == IndexDailyPrice.trading_date,
                                DailyPrice.data_origin
                                == DataOrigin.OFFICIAL_NSE_PUBLIC.value,
                            )
                        ),
                    )
                )
                or 0
            )
            if benchmark_start is not None and benchmark_end is not None:
                benchmark_missing_known_sessions = int(
                    self.session.scalar(
                        select(func.count())
                        .select_from(TradingCalendar)
                        .where(
                            TradingCalendar.exchange == "NSE",
                            TradingCalendar.data_origin
                            == DataOrigin.OFFICIAL_NSE_PUBLIC.value,
                            TradingCalendar.is_trading_day.is_(True),
                            TradingCalendar.trading_date.between(
                                benchmark_start, benchmark_end
                            ),
                            ~exists(
                                select(IndexDailyPrice.id).where(
                                    IndexDailyPrice.index_id == nifty200.id,
                                    IndexDailyPrice.source_mode == "OFFICIAL",
                                    IndexDailyPrice.trading_date
                                    == TradingCalendar.trading_date,
                                )
                            ),
                        )
                    )
                    or 0
                )

        action_bounds = self.session.execute(
            select(func.min(CorporateAction.ex_date), func.max(CorporateAction.ex_date)).where(
                CorporateAction.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value
            )
        ).one()
        calendar_count, calendar_start, calendar_end = self.session.execute(
            select(
                func.count(),
                func.min(TradingCalendar.trading_date),
                func.max(TradingCalendar.trading_date),
            ).where(
                TradingCalendar.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value
            )
        ).one()
        calendar_count = int(calendar_count or 0)
        calendar_sessions = int(
            self.session.scalar(
                select(func.count()).select_from(TradingCalendar).where(
                    TradingCalendar.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value,
                    TradingCalendar.is_trading_day.is_(True),
                )
            )
            or 0
        )
        filing_count, filing_securities, filing_start, filing_end, filing_period_start, filing_period_end = self.session.execute(
            select(
                func.count(),
                func.count(func.distinct(FundamentalFiling.security_id)),
                func.min(FundamentalFiling.available_at),
                func.max(FundamentalFiling.available_at),
                func.min(FundamentalFiling.period_end),
                func.max(FundamentalFiling.period_end),
            )
        ).one()
        fundamental_fact_count = int(
            self.session.scalar(select(func.count()).select_from(FundamentalFact)) or 0
        )
        consolidated_filing_count = int(
            self.session.scalar(
                select(func.count()).select_from(FundamentalFiling).where(
                    FundamentalFiling.scope == "CONSOLIDATED"
                )
            ) or 0
        )
        classification_count, classification_securities, classification_date = self.session.execute(
            select(
                func.count(),
                func.count(func.distinct(SecurityIndustryClassification.security_id)),
                func.max(SecurityIndustryClassification.snapshot_date),
            )
        ).one()
        nifty200_classified = int(
            self.session.scalar(
                select(func.count(func.distinct(SecurityIndustryClassification.security_id))).where(
                    SecurityIndustryClassification.security_id.in_(member_ids)
                )
            ) or 0
        ) if member_ids else 0
        sector_benchmark_mappings = int(
            self.session.scalar(
                select(func.count())
                .select_from(SecurityIndustryClassification)
                .where(SecurityIndustryClassification.sector_benchmark_index_id.is_not(None))
            )
            or 0
        )
        activated_sector_indices, sector_session_start, sector_session_end = self.session.execute(
            select(
                func.count(func.distinct(IndexDailyPrice.index_id)),
                func.min(IndexDailyPrice.trading_date),
                func.max(IndexDailyPrice.trading_date),
            )
            .join(MarketIndex, MarketIndex.id == IndexDailyPrice.index_id)
            .where(
                MarketIndex.symbol != "NIFTY200",
                MarketIndex.provider == "OFFICIAL_NSE_INDICES_PUBLIC",
                IndexDailyPrice.source_mode == "OFFICIAL",
            )
        ).one()

        def membership_dataset(symbol: str, expected: int) -> ActivationDatasetResponse:
            item = membership_by_symbol[symbol]
            status = "READY" if item.member_count == expected else (
                "PARTIAL" if item.member_count else "UNAVAILABLE"
            )
            warnings = [item.warning] if item.warning else []
            detail = "No valid official current snapshot is imported."
            if item.current_snapshot_present:
                detail = "Current official snapshot only; it is not extended backward."
            elif symbol == "NIFTY500":
                detail = (
                    "The current official upstream snapshot failed strict identity, series, "
                    "and 500-member validation; no partial membership was persisted."
                )
                warnings = ["UPSTREAM_SNAPSHOT_VALIDATION_FAILED"]
            return ActivationDatasetResponse(
                code=f"{symbol.replace('NIFTY', 'NIFTY_')}_MEMBERSHIP",
                label=f"{item.label} current membership",
                status=status,
                row_count=item.member_count,
                item_count=item.member_count,
                coverage_start=item.coverage_start,
                coverage_end=item.coverage_end,
                detail=detail,
                warnings=warnings,
                metrics={
                    "expected_members": expected,
                    "mapped_members": item.member_count,
                    "unmapped_members": max(0, expected - item.member_count),
                },
            )

        equity_status = (
            "READY"
            if len(member_ids) == 200 and members_with_252 == 200
            else "PARTIAL"
            if official_price_count
            else "UNAVAILABLE"
        )
        benchmark_status = (
            "READY" if benchmark_count >= 252 else "PARTIAL" if benchmark_count else "UNAVAILABLE"
        )
        return [
            ActivationDatasetResponse(
                code="SECURITY_MASTER",
                label="Official NSE security master",
                status="READY" if official_security_count else "UNAVAILABLE",
                row_count=official_security_count,
                item_count=official_security_count,
                coverage_start=security_artifact_date,
                coverage_end=security_artifact_date,
                detail="Canonical NSE EQ-series identities with source-artifact provenance.",
                warnings=[] if official_security_count else ["OFFICIAL_SECURITY_MASTER_NOT_IMPORTED"],
                metrics={"official_securities": official_security_count},
            ),
            membership_dataset("NIFTY200", 200),
            membership_dataset("NIFTY500", 500),
            ActivationDatasetResponse(
                code="EQUITY_EOD",
                label="Official NSE cash-market EOD",
                status=equity_status,
                row_count=official_price_count,
                item_count=official_price_securities,
                coverage_start=price_start,
                coverage_end=price_end,
                detail="RAW EQ-series OHLCV; adjusted prices remain derived by AlphaDesk.",
                warnings=(
                    (
                        ["BENCHMARK_SESSION_WITHOUT_EQUITY_EOD"]
                        if benchmark_sessions_without_equity_eod
                        else []
                    )
                    + (
                        []
                        if equity_status == "READY"
                        else ["NIFTY_200_MULTIYEAR_PRICE_COVERAGE_INCOMPLETE"]
                    )
                ),
                metrics={
                    "official_sessions": official_price_sessions,
                    "securities_with_prices": official_price_securities,
                    "nifty200_members": len(member_ids),
                    "nifty200_with_any_price": members_with_any,
                    "nifty200_with_200_sessions": members_with_200,
                    "nifty200_with_252_sessions": members_with_252,
                    "nifty200_with_target_range": members_with_target,
                    "nifty200_with_significant_gaps": significant_gap_members,
                    "benchmark_sessions_without_equity_eod": benchmark_sessions_without_equity_eod,
                },
            ),
            ActivationDatasetResponse(
                code="NIFTY_200_BENCHMARK",
                label="Official NIFTY 200 benchmark OHLC",
                status=benchmark_status,
                row_count=benchmark_count,
                item_count=benchmark_count,
                coverage_start=benchmark_start,
                coverage_end=benchmark_end,
                detail="OFFICIAL source mode; historical rows are knowable from retrieval/import time.",
                warnings=(
                    [] if benchmark_status == "READY" else ["OFFICIAL_BENCHMARK_HISTORY_INCOMPLETE"]
                ),
                metrics={
                    "official_sessions": benchmark_count,
                    "known_calendar_gaps": benchmark_missing_known_sessions,
                    "first_regime_ready_date": (
                        regime_ready_date.isoformat() if regime_ready_date else None
                    ),
                },
            ),
            ActivationDatasetResponse(
                code="CORPORATE_ACTIONS",
                label="Official corporate actions",
                status="PARTIAL" if promoted_actions or quarantined_actions else "UNAVAILABLE",
                row_count=promoted_actions,
                item_count=promoted_actions,
                coverage_start=action_bounds[0],
                coverage_end=action_bounds[1],
                detail="Only rows with trustworthy source publication timestamps are promoted.",
                warnings=["CORPORATE_ACTION_AVAILABILITY_INCOMPLETE"],
                metrics={
                    "promoted": promoted_actions,
                    "quarantined": quarantined_actions,
                },
            ),
            ActivationDatasetResponse(
                code="TRADING_CALENDAR",
                label="Official NSE trading calendar",
                status="PARTIAL" if calendar_count else "UNAVAILABLE",
                row_count=calendar_count,
                item_count=official_price_sessions,
                coverage_start=calendar_start,
                coverage_end=calendar_end,
                detail="Confirmed sessions and imported holidays only; special sessions are never guessed.",
                warnings=[] if calendar_count else ["OFFICIAL_TRADING_CALENDAR_NOT_IMPORTED"],
                metrics={
                    "calendar_rows": calendar_count,
                    "confirmed_sessions": official_price_sessions,
                    "official_origin_session_rows": calendar_sessions,
                },
            ),
            ActivationDatasetResponse(
                code="FUNDAMENTALS",
                label="Official company fundamentals",
                status="PARTIAL" if filing_count else "UNAVAILABLE",
                row_count=int(filing_count or 0),
                item_count=int(filing_securities or 0),
                coverage_start=filing_period_start,
                coverage_end=filing_period_end,
                detail="Append-only point-in-time filings and normalized facts; parser infrastructure alone is not data readiness.",
                warnings=[] if filing_count else ["OFFICIAL_FUNDAMENTALS_NOT_IMPORTED"],
                metrics={
                    "filings": int(filing_count or 0),
                    "facts": fundamental_fact_count,
                    "securities": int(filing_securities or 0),
                    "consolidated_filings": consolidated_filing_count,
                    "standalone_filings": int(filing_count or 0) - consolidated_filing_count,
                    "earliest_availability": filing_start.isoformat() if filing_start else None,
                    "latest_availability": filing_end.isoformat() if filing_end else None,
                    "earliest_period_end": str(filing_period_start) if filing_period_start else None,
                    "latest_period_end": str(filing_period_end) if filing_period_end else None,
                },
            ),
            ActivationDatasetResponse(
                code="INDUSTRY_CLASSIFICATION",
                label="Official industry classification",
                status=(
                    "READY"
                    if len(member_ids) == 200 and nifty200_classified == 200
                    else "PARTIAL" if classification_count else "UNAVAILABLE"
                ),
                row_count=int(classification_count or 0),
                item_count=int(classification_securities or 0),
                coverage_start=classification_date,
                coverage_end=classification_date,
                detail="Official four-level current snapshots; current classifications are never backcast.",
                warnings=[] if classification_count else ["OFFICIAL_CLASSIFICATION_NOT_IMPORTED"],
                metrics={
                    "classifications": int(classification_count or 0),
                    "securities": int(classification_securities or 0),
                    "nifty_200_members": len(member_ids),
                    "nifty_200_classified": nifty200_classified,
                },
            ),
            ActivationDatasetResponse(
                code="SECTOR_BENCHMARKS",
                label="Official sector benchmarks",
                status="PARTIAL" if sector_benchmark_mappings and activated_sector_indices else "UNAVAILABLE",
                row_count=sector_benchmark_mappings,
                item_count=int(activated_sector_indices or 0),
                coverage_start=sector_session_start,
                coverage_end=sector_session_end,
                detail="Only explicit security-classification to official index mappings are eligible; no proxy index is invented.",
                warnings=[] if sector_benchmark_mappings and activated_sector_indices else ["SECTOR_BENCHMARKS_NOT_ACTIVATED"],
                metrics={
                    "explicit_mappings": sector_benchmark_mappings,
                    "activated_indices": int(activated_sector_indices or 0),
                },
            ),
        ]

    def _origin_counts(self, model: type[Security] | type[DailyPrice]) -> dict[str, int]:
        rows = self.session.execute(
            select(model.data_origin, func.count()).group_by(model.data_origin)
        )
        return {str(origin): int(count) for origin, count in rows}

    def _index_coverage(self) -> list[IndexCoverageResponse]:
        results: list[IndexCoverageResponse] = []
        for symbol, label in (("NIFTY200", "NIFTY 200"), ("NIFTY500", "NIFTY 500")):
            unavailable_warning = (
                "UPSTREAM_SNAPSHOT_VALIDATION_FAILED"
                if symbol == "NIFTY500"
                else "CURRENT_SNAPSHOT_NOT_IMPORTED"
            )
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
                        warning=unavailable_warning,
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
                    warning=(
                        "HISTORICAL_MEMBERSHIP_COVERAGE_INCOMPLETE"
                        if snapshot_count
                        else unavailable_warning
                    ),
                )
            )
        return results
