from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ingestion.nse.artifacts import (
    ArtifactBytes,
    ArtifactSchemaDriftError,
    ArtifactValidationError,
    NseArtifactStore,
)
from app.ingestion.nse.client import DownloadedArtifact, OfficialHttpClient
from app.ingestion.nse.definitions import (
    ArtifactType,
    DataOrigin,
    IngestionStatus,
    IssueSeverity,
    SOURCE_DEFINITIONS,
    public_artifact_url,
)
from app.ingestion.nse.parsers import (
    NormalizedConstituent,
    NormalizedCorporateAction,
    NormalizedHoliday,
    NormalizedPrice,
    NormalizedSecurity,
    ParseResult,
    ParsedIssue,
    parse_bhavcopy,
    parse_constituents,
    parse_corporate_actions,
    parse_holidays,
    parse_security_master,
)
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


class NseIngestionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IngestionSummary:
    artifact_type: str
    source_date: date
    dry_run: bool
    repeated_artifact: bool
    status: str
    artifact_id: UUID | None
    ingestion_run_id: UUID | None
    checksum: str
    normalized_fingerprint: str
    rows_parsed: int
    inserted: int
    unchanged: int
    rejected: int
    conflicts: int
    warnings: int
    issues: tuple[ParsedIssue, ...]
    parse_ms: float
    persistence_ms: float
    total_ms: float

    def as_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["source_date"] = self.source_date.isoformat()
        result["artifact_id"] = str(self.artifact_id) if self.artifact_id else None
        result["ingestion_run_id"] = str(self.ingestion_run_id) if self.ingestion_run_id else None
        result["issues"] = [
            {**asdict(issue), "severity": issue.severity.value} for issue in self.issues
        ]
        return result


@dataclass(slots=True)
class _WritePlan:
    inserted: int = 0
    unchanged: int = 0
    rejected: int = 0
    conflicts: int = 0
    warnings: int = 0
    issues: list[ParsedIssue] | None = None

    def __post_init__(self) -> None:
        if self.issues is None:
            self.issues = []

    def issue(
        self,
        severity: IssueSeverity,
        code: str,
        message: str,
        *,
        row_key: str | None = None,
        metadata: dict[str, object] | None = None,
        rejected: bool = False,
        conflict: bool = False,
    ) -> None:
        if rejected:
            self.rejected += 1
        if conflict:
            self.conflicts += 1
        if severity == IssueSeverity.WARNING:
            self.warnings += 1
        if len(self.issues or ()) < 200:
            assert self.issues is not None
            self.issues.append(
                ParsedIssue(
                    severity=severity,
                    code=code,
                    message=message,
                    row_key=row_key,
                    metadata=metadata or {},
                )
            )


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _price_hash(row: NormalizedPrice | DailyPrice) -> str:
    return _fingerprint(
        {
            "open": str(row.open),
            "high": str(row.high),
            "low": str(row.low),
            "close": str(row.close),
            "volume": row.volume,
            "traded_value": str(row.traded_value) if row.traded_value is not None else None,
        }
    )


def _price_values_equal(existing: DailyPrice, incoming: NormalizedPrice) -> bool:
    return (
        Decimal(existing.open) == incoming.open
        and Decimal(existing.high) == incoming.high
        and Decimal(existing.low) == incoming.low
        and Decimal(existing.close) == incoming.close
        and existing.volume == incoming.volume
        and (
            (existing.traded_value is None and incoming.traded_value is None)
            or (
                existing.traded_value is not None
                and incoming.traded_value is not None
                and Decimal(existing.traded_value) == incoming.traded_value
            )
        )
    )


ParserResult = ParseResult[object]


class NseIngestionService:
    def __init__(self, session: Session, *, store: NseArtifactStore | None = None) -> None:
        self.session = session
        self.store = store or NseArtifactStore()

    def import_local(
        self,
        artifact_type: ArtifactType,
        source_date: date,
        file_name: str | Path,
        *,
        dry_run: bool = False,
    ) -> IngestionSummary:
        artifact = self.store.read_inbox(file_name)
        return self.import_artifact(
            artifact_type,
            source_date,
            artifact,
            dry_run=dry_run,
            fetched_at=None,
        )

    def fetch_and_import(
        self,
        artifact_type: ArtifactType,
        source_date: date,
        *,
        dry_run: bool = False,
        client_factory: Callable[[], OfficialHttpClient] = OfficialHttpClient,
    ) -> IngestionSummary:
        url = public_artifact_url(artifact_type, source_date)
        with client_factory() as client:
            downloaded: DownloadedArtifact = client.fetch(url)
        file_name = Path(urlparse(downloaded.source_url).path).name
        artifact = ArtifactBytes(file_name=file_name, content=downloaded.content, source_locator=url)
        return self.import_artifact(
            artifact_type,
            source_date,
            artifact,
            dry_run=dry_run,
            fetched_at=downloaded.fetched_at,
        )

    def backfill(
        self,
        start: date,
        end: date,
        *,
        dry_run: bool = False,
        client_factory: Callable[[], OfficialHttpClient] = OfficialHttpClient,
    ) -> list[IngestionSummary]:
        if start > end:
            raise ValueError("start must be on or before end")
        if (end - start).days > 92:
            raise ValueError("one backfill command is limited to 93 calendar days")
        results: list[IngestionSummary] = []
        confirmed_holidays = set(
            self.session.scalars(
                select(TradingCalendar.trading_date).where(
                    TradingCalendar.exchange == "NSE",
                    TradingCalendar.trading_date.between(start, end),
                    TradingCalendar.is_trading_day.is_(False),
                )
            )
        )
        current = start
        with client_factory() as client:
            while current <= end:
                if current.weekday() < 5 and current not in confirmed_holidays:
                    existing = self.session.scalar(
                        select(SourceArtifact.id).where(
                            SourceArtifact.provider == "OFFICIAL_NSE_PUBLIC",
                            SourceArtifact.artifact_type == ArtifactType.EOD_BHAVCOPY.value,
                            SourceArtifact.source_date == current,
                            SourceArtifact.parse_status.in_(("SUCCEEDED", "PARTIAL")),
                        )
                    )
                    if existing is None or dry_run:
                        url = public_artifact_url(ArtifactType.EOD_BHAVCOPY, current)
                        downloaded = client.fetch(url)
                        results.append(
                            self.import_artifact(
                                ArtifactType.EOD_BHAVCOPY,
                                current,
                                ArtifactBytes(
                                    file_name=Path(urlparse(downloaded.source_url).path).name,
                                    content=downloaded.content,
                                    source_locator=url,
                                ),
                                dry_run=dry_run,
                                fetched_at=downloaded.fetched_at,
                            )
                        )
                current += timedelta(days=1)
        return results

    def import_artifact(
        self,
        artifact_type: ArtifactType,
        source_date: date,
        artifact: ArtifactBytes,
        *,
        dry_run: bool = False,
        fetched_at: datetime | None = None,
    ) -> IngestionSummary:
        started = time.perf_counter()
        if source_date > date.today():
            raise ValueError("source_date cannot be in the future")
        definition = SOURCE_DEFINITIONS[artifact_type]
        checksum = artifact.sha256
        normalized_fingerprint = _fingerprint(
            {
                "provider": definition.provider.value,
                "artifact_type": artifact_type.value,
                "source_date": source_date,
                "sha256": checksum,
                "parser_version": definition.parser_version,
            }
        )
        if not dry_run:
            identical = self.session.scalar(
                select(SourceArtifact).where(
                    SourceArtifact.provider == definition.provider.value,
                    SourceArtifact.artifact_type == artifact_type.value,
                    SourceArtifact.source_date == source_date,
                    SourceArtifact.sha256 == checksum,
                )
            )
            if identical is not None:
                total_ms = (time.perf_counter() - started) * 1_000
                return IngestionSummary(
                    artifact_type=artifact_type.value,
                    source_date=source_date,
                    dry_run=False,
                    repeated_artifact=True,
                    status=identical.parse_status,
                    artifact_id=identical.id,
                    ingestion_run_id=identical.ingestion_run_id,
                    checksum=checksum,
                    normalized_fingerprint=identical.normalized_fingerprint,
                    rows_parsed=identical.row_count,
                    inserted=0,
                    unchanged=identical.accepted_row_count,
                    rejected=identical.rejected_row_count,
                    conflicts=0,
                    warnings=identical.warning_count,
                    issues=(),
                    parse_ms=0.0,
                    persistence_ms=0.0,
                    total_ms=round(total_ms, 3),
                )

        parse_started = time.perf_counter()
        try:
            parsed = self._parse(artifact_type, source_date, artifact)
        except (ArtifactValidationError, ArtifactSchemaDriftError, ValueError) as exc:
            if dry_run:
                raise
            return self._persist_failed_artifact(
                artifact_type,
                source_date,
                artifact,
                fetched_at,
                checksum,
                normalized_fingerprint,
                str(exc),
                started,
            )
        parse_ms = (time.perf_counter() - parse_started) * 1_000

        if dry_run:
            persistence_started = time.perf_counter()
            plan = self._apply(artifact_type, source_date, parsed, None, None, mutate=False)
            persistence_ms = (time.perf_counter() - persistence_started) * 1_000
            issues = tuple(parsed.issues) + tuple(plan.issues or ())
            return IngestionSummary(
                artifact_type=artifact_type.value,
                source_date=source_date,
                dry_run=True,
                repeated_artifact=False,
                status="DRY_RUN",
                artifact_id=None,
                ingestion_run_id=None,
                checksum=checksum,
                normalized_fingerprint=normalized_fingerprint,
                rows_parsed=parsed.source_row_count,
                inserted=plan.inserted,
                unchanged=plan.unchanged,
                rejected=parsed.rejected_row_count + plan.rejected,
                conflicts=plan.conflicts,
                warnings=parsed.warning_count + plan.warnings,
                issues=issues[:200],
                parse_ms=round(parse_ms, 3),
                persistence_ms=round(persistence_ms, 3),
                total_ms=round((time.perf_counter() - started) * 1_000, 3),
            )

        existing_revision = self.session.scalar(
            select(SourceArtifact).where(
                SourceArtifact.provider == definition.provider.value,
                SourceArtifact.artifact_type == artifact_type.value,
                SourceArtifact.source_date == source_date,
                SourceArtifact.sha256 != checksum,
            )
        )
        if existing_revision is not None:
            return self._persist_source_revision_conflict(
                artifact_type,
                source_date,
                artifact,
                fetched_at,
                checksum,
                normalized_fingerprint,
                parsed,
                started,
                parse_ms,
            )

        persistence_started = time.perf_counter()
        run = DataIngestionRun(
            dataset_code=f"nse_{artifact_type.value.lower()}",
            dataset_version=source_date.isoformat(),
            provider=definition.provider.value,
            status=IngestionStatus.RUNNING.value,
            requested_start=source_date,
            requested_end=source_date,
            parser_version=definition.parser_version,
            dry_run=False,
        )
        source_artifact = SourceArtifact(
            ingestion_run_id=run.id,
            provider=definition.provider.value,
            artifact_type=artifact_type.value,
            source_date=source_date,
            original_file_name=artifact.file_name,
            source_locator=artifact.source_locator,
            fetched_at=fetched_at,
            imported_at=datetime.now(UTC),
            sha256=checksum,
            byte_size=len(artifact.content),
            parser_code=definition.parser_code,
            parser_version=definition.parser_version,
            source_schema_version=None,
            normalized_fingerprint=normalized_fingerprint,
            parse_status="FAILED",
            artifact_metadata={"mode": "FETCH" if fetched_at else "LOCAL_IMPORT"},
        )
        self.session.add(run)
        self.session.flush()
        source_artifact.ingestion_run_id = run.id
        self.session.add(source_artifact)
        self.session.flush()
        storage_key = self.store.retain(artifact)
        source_artifact.storage_key = storage_key

        try:
            plan = self._apply(
                artifact_type,
                source_date,
                parsed,
                source_artifact.id,
                run.id,
                mutate=True,
            )
            issues = tuple(parsed.issues) + tuple(plan.issues or ())
            self._persist_issues(source_artifact.id, run.id, issues)
            rejected = parsed.rejected_row_count + plan.rejected
            warnings = parsed.warning_count + plan.warnings
            status = (
                IngestionStatus.PARTIAL
                if rejected or plan.conflicts or warnings
                else IngestionStatus.SUCCEEDED
            )
            run.status = status.value
            run.completed_at = datetime.now(UTC)
            run.records_written = plan.inserted
            run.inserted_count = plan.inserted
            run.unchanged_count = plan.unchanged
            run.rejected_count = rejected
            run.conflict_count = plan.conflicts
            run.warning_count = warnings
            source_artifact.parse_status = status.value
            source_artifact.row_count = parsed.source_row_count
            source_artifact.accepted_row_count = plan.inserted + plan.unchanged
            source_artifact.rejected_row_count = rejected
            source_artifact.warning_count = warnings
            self.session.commit()
        except Exception as exc:
            self.session.rollback()
            return self._persist_failed_artifact(
                artifact_type,
                source_date,
                artifact,
                fetched_at,
                checksum,
                normalized_fingerprint,
                f"Transactional import failed: {type(exc).__name__}",
                started,
            )
        persistence_ms = (time.perf_counter() - persistence_started) * 1_000
        return IngestionSummary(
            artifact_type=artifact_type.value,
            source_date=source_date,
            dry_run=False,
            repeated_artifact=False,
            status=status.value,
            artifact_id=source_artifact.id,
            ingestion_run_id=run.id,
            checksum=checksum,
            normalized_fingerprint=normalized_fingerprint,
            rows_parsed=parsed.source_row_count,
            inserted=plan.inserted,
            unchanged=plan.unchanged,
            rejected=rejected,
            conflicts=plan.conflicts,
            warnings=warnings,
            issues=issues[:200],
            parse_ms=round(parse_ms, 3),
            persistence_ms=round(persistence_ms, 3),
            total_ms=round((time.perf_counter() - started) * 1_000, 3),
        )

    def _parse(
        self, artifact_type: ArtifactType, source_date: date, artifact: ArtifactBytes
    ) -> ParserResult:
        if artifact_type == ArtifactType.SECURITY_MASTER:
            return parse_security_master(artifact)
        if artifact_type == ArtifactType.EOD_BHAVCOPY:
            return parse_bhavcopy(artifact, expected_date=source_date)
        if artifact_type in {
            ArtifactType.NIFTY_200_CONSTITUENTS,
            ArtifactType.NIFTY_500_CONSTITUENTS,
        }:
            return parse_constituents(artifact)
        if artifact_type == ArtifactType.CORPORATE_ACTIONS:
            return parse_corporate_actions(artifact)
        if artifact_type == ArtifactType.TRADING_HOLIDAYS:
            return parse_holidays(artifact)
        raise ValueError("Unsupported artifact type")

    def _apply(
        self,
        artifact_type: ArtifactType,
        source_date: date,
        parsed: ParserResult,
        artifact_id: UUID | None,
        run_id: UUID | None,
        *,
        mutate: bool,
    ) -> _WritePlan:
        if artifact_type == ArtifactType.SECURITY_MASTER:
            return self._apply_securities(parsed.rows, artifact_id, run_id, mutate=mutate)
        if artifact_type == ArtifactType.EOD_BHAVCOPY:
            return self._apply_prices(parsed.rows, source_date, artifact_id, run_id, mutate=mutate)
        if artifact_type in {
            ArtifactType.NIFTY_200_CONSTITUENTS,
            ArtifactType.NIFTY_500_CONSTITUENTS,
        }:
            return self._apply_constituents(
                artifact_type, parsed.rows, source_date, artifact_id, run_id, mutate=mutate
            )
        if artifact_type == ArtifactType.CORPORATE_ACTIONS:
            return self._apply_actions(parsed.rows, artifact_id, run_id, mutate=mutate)
        if artifact_type == ArtifactType.TRADING_HOLIDAYS:
            return self._apply_holidays(parsed.rows, artifact_id, run_id, mutate=mutate)
        raise ValueError("Unsupported artifact type")

    def _apply_securities(
        self,
        rows: Iterable[object],
        artifact_id: UUID | None,
        run_id: UUID | None,
        *,
        mutate: bool,
    ) -> _WritePlan:
        typed = tuple(row for row in rows if isinstance(row, NormalizedSecurity))
        plan = _WritePlan()
        symbols = {row.symbol for row in typed}
        isins = {row.isin for row in typed}
        existing = list(
            self.session.scalars(
                select(Security).where(
                    Security.exchange == "NSE",
                    or_(Security.symbol.in_(symbols), Security.isin.in_(isins)),
                )
            )
        )
        by_symbol = {row.symbol: row for row in existing}
        by_isin = {row.isin: row for row in existing if row.isin}
        for row in typed:
            symbol_match = by_symbol.get(row.symbol)
            isin_match = by_isin.get(row.isin)
            if symbol_match is not None or isin_match is not None:
                if symbol_match is not isin_match or symbol_match is None:
                    plan.issue(
                        IssueSeverity.ERROR,
                        "SECURITY_IDENTITY_CONFLICT",
                        "Symbol and ISIN do not resolve to one existing security",
                        row_key=f"{row.symbol}:{row.series}",
                        rejected=True,
                        conflict=True,
                    )
                    continue
                if symbol_match.series not in {None, row.series}:
                    plan.issue(
                        IssueSeverity.ERROR,
                        "SECURITY_IDENTITY_CONFLICT",
                        "Existing security series differs from official artifact",
                        row_key=f"{row.symbol}:{row.series}",
                        rejected=True,
                        conflict=True,
                    )
                    continue
                plan.unchanged += 1
                if symbol_match.company_name != row.company_name:
                    plan.issue(
                        IssueSeverity.WARNING,
                        "SECURITY_METADATA_DRIFT",
                        "Company name differs; Phase 8 does not rewrite security identity metadata",
                        row_key=f"{row.symbol}:{row.series}",
                    )
                continue
            plan.inserted += 1
            if mutate:
                security = Security(
                    exchange="NSE",
                    symbol=row.symbol,
                    trading_symbol=f"{row.symbol}-{row.series}",
                    series=row.series,
                    company_name=row.company_name,
                    isin=row.isin,
                    security_type="EQUITY",
                    currency="INR",
                    listing_date=row.listing_date,
                    is_active=row.is_active,
                    data_origin=DataOrigin.OFFICIAL_NSE_PUBLIC.value,
                    source_artifact_id=artifact_id,
                    ingestion_run_id=run_id,
                )
                self.session.add(security)
                by_symbol[row.symbol] = security
                by_isin[row.isin] = security
        return plan

    def _resolve_price_securities(
        self, rows: tuple[NormalizedPrice, ...], plan: _WritePlan
    ) -> list[tuple[NormalizedPrice, Security]]:
        symbols = {row.symbol for row in rows}
        isins = {row.isin for row in rows if row.isin}
        securities = list(
            self.session.scalars(
                select(Security).where(
                    Security.exchange == "NSE",
                    or_(Security.symbol.in_(symbols), Security.isin.in_(isins)),
                )
            )
        )
        by_symbol = {item.symbol: item for item in securities}
        by_isin = {item.isin: item for item in securities if item.isin}
        resolved: list[tuple[NormalizedPrice, Security]] = []
        for row in rows:
            security = by_symbol.get(row.symbol)
            isin_security = by_isin.get(row.isin) if row.isin else security
            if security is None:
                plan.issue(
                    IssueSeverity.ERROR,
                    "SECURITY_NOT_IN_MASTER",
                    "EOD row cannot be resolved to the security master",
                    row_key=f"{row.symbol}:{row.series}:{row.trading_date}",
                    rejected=True,
                )
                continue
            if isin_security is not security or security.series not in {None, row.series}:
                plan.issue(
                    IssueSeverity.ERROR,
                    "AMBIGUOUS_SECURITY",
                    "EOD row symbol, series, and ISIN do not resolve unambiguously",
                    row_key=f"{row.symbol}:{row.series}:{row.trading_date}",
                    rejected=True,
                    conflict=True,
                )
                continue
            resolved.append((row, security))
        return resolved

    def _apply_prices(
        self,
        rows: Iterable[object],
        source_date: date,
        artifact_id: UUID | None,
        run_id: UUID | None,
        *,
        mutate: bool,
    ) -> _WritePlan:
        typed = tuple(row for row in rows if isinstance(row, NormalizedPrice))
        plan = _WritePlan()
        resolved = self._resolve_price_securities(typed, plan)
        calendar = self.session.scalar(
            select(TradingCalendar).where(
                TradingCalendar.exchange == "NSE", TradingCalendar.trading_date == source_date
            )
        )
        if calendar is not None and not calendar.is_trading_day:
            plan.issue(
                IssueSeverity.ERROR,
                "CALENDAR_CONFLICT",
                "EOD artifact date is persisted as a confirmed non-trading day",
                row_key=source_date.isoformat(),
                rejected=len(resolved) > 0,
                conflict=True,
            )
            plan.rejected += max(0, len(resolved) - 1)
            return plan
        security_ids = {security.id for _, security in resolved}
        existing = {
            (row.security_id, row.trading_date): row
            for row in self.session.scalars(
                select(DailyPrice).where(
                    DailyPrice.security_id.in_(security_ids),
                    DailyPrice.trading_date == source_date,
                )
            )
        }
        pending: list[DailyPrice] = []
        for row, security in resolved:
            current = existing.get((security.id, row.trading_date))
            row_key = f"{row.symbol}:{row.series}:{row.trading_date}"
            if current is None:
                plan.inserted += 1
                pending.append(
                    DailyPrice(
                        security_id=security.id,
                        trading_date=row.trading_date,
                        open=row.open,
                        high=row.high,
                        low=row.low,
                        close=row.close,
                        volume=row.volume,
                        traded_value=row.traded_value,
                        source="NSE_CM_UDIFF",
                        data_origin=DataOrigin.OFFICIAL_NSE_PUBLIC.value,
                        source_artifact_id=artifact_id,
                        ingestion_run_id=run_id,
                    )
                )
                continue
            if (
                current.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value
                and _price_values_equal(current, row)
            ):
                plan.unchanged += 1
                continue
            plan.issue(
                IssueSeverity.ERROR,
                "HISTORICAL_DATA_CONFLICT",
                "Existing historical price was not overwritten",
                row_key=row_key,
                metadata={"existing_hash": _price_hash(current), "incoming_hash": _price_hash(row)},
                rejected=True,
                conflict=True,
            )
        if mutate:
            self.session.add_all(pending)
            if calendar is None:
                self.session.add(
                    TradingCalendar(
                        exchange="NSE",
                        trading_date=source_date,
                        is_trading_day=True,
                        session_type="REGULAR",
                        notes="Confirmed by official NSE CM EOD artifact",
                        data_origin=DataOrigin.OFFICIAL_NSE_PUBLIC.value,
                        source_artifact_id=artifact_id,
                        ingestion_run_id=run_id,
                    )
                )
        return plan

    def _apply_constituents(
        self,
        artifact_type: ArtifactType,
        rows: Iterable[object],
        source_date: date,
        artifact_id: UUID | None,
        run_id: UUID | None,
        *,
        mutate: bool,
    ) -> _WritePlan:
        typed = tuple(row for row in rows if isinstance(row, NormalizedConstituent))
        plan = _WritePlan()
        expected_count = 200 if artifact_type == ArtifactType.NIFTY_200_CONSTITUENTS else 500
        index_symbol = "NIFTY200" if expected_count == 200 else "NIFTY500"
        index_name = "NIFTY 200" if expected_count == 200 else "NIFTY 500"
        symbols = {row.symbol for row in typed}
        isins = {row.isin for row in typed}
        securities = list(
            self.session.scalars(
                select(Security).where(
                    Security.exchange == "NSE",
                    or_(Security.symbol.in_(symbols), Security.isin.in_(isins)),
                )
            )
        )
        by_symbol = {item.symbol: item for item in securities}
        by_isin = {item.isin: item for item in securities if item.isin}
        resolved: list[Security] = []
        for row in typed:
            symbol_match = by_symbol.get(row.symbol)
            isin_match = by_isin.get(row.isin)
            if (
                symbol_match is None
                or isin_match is not symbol_match
                or symbol_match.series not in {None, row.series}
            ):
                plan.issue(
                    IssueSeverity.ERROR,
                    "UNKNOWN_INDEX_SECURITY",
                    "Constituent does not resolve to one security-master identity",
                    row_key=f"{row.symbol}:{row.series}",
                    rejected=True,
                )
                continue
            resolved.append(symbol_match)
        if len(typed) != expected_count:
            plan.issue(
                IssueSeverity.WARNING,
                "UNEXPECTED_MEMBER_COUNT",
                f"Expected {expected_count} constituents but parsed {len(typed)}",
                row_key=index_symbol,
            )
        if len(resolved) != len(typed) or len(typed) != expected_count:
            plan.rejected += len(resolved)
            plan.inserted = 0
            return plan
        market_index = self.session.scalar(
            select(MarketIndex).where(
                MarketIndex.provider == "OFFICIAL_NSE_INDICES_PUBLIC",
                MarketIndex.symbol == index_symbol,
            )
        )
        if market_index is None and mutate:
            market_index = MarketIndex(
                name=index_name,
                symbol=index_symbol,
                provider="OFFICIAL_NSE_INDICES_PUBLIC",
                exchange="NSE",
            )
            self.session.add(market_index)
            self.session.flush()
        if market_index is None:
            plan.inserted = len(resolved)
            return plan
        exact = list(
            self.session.scalars(
                select(IndexMembership).where(
                    IndexMembership.index_id == market_index.id,
                    IndexMembership.valid_from == source_date,
                )
            )
        )
        exact_ids = {item.security_id for item in exact}
        incoming_ids = {item.id for item in resolved}
        if exact:
            if exact_ids == incoming_ids:
                plan.unchanged = len(resolved)
            else:
                plan.rejected = len(resolved)
                plan.conflicts = 1
                plan.issue(
                    IssueSeverity.ERROR,
                    "INDEX_SNAPSHOT_CONFLICT",
                    "A differing snapshot already exists for this as-of date",
                    row_key=f"{index_symbol}:{source_date}",
                )
            return plan
        plan.inserted = len(resolved)
        if mutate:
            previous_open = list(
                self.session.scalars(
                    select(IndexMembership).where(
                        IndexMembership.index_id == market_index.id,
                        IndexMembership.valid_from < source_date,
                        IndexMembership.valid_to.is_(None),
                    )
                )
            )
            for item in previous_open:
                item.valid_to = source_date - timedelta(days=1)
            self.session.add_all(
                [
                    IndexMembership(
                        index_id=market_index.id,
                        security_id=security.id,
                        valid_from=source_date,
                        valid_to=None,
                        source="NSE_INDICES_CURRENT_SNAPSHOT",
                        data_origin=DataOrigin.OFFICIAL_NSE_PUBLIC.value,
                        source_artifact_id=artifact_id,
                        ingestion_run_id=run_id,
                    )
                    for security in resolved
                ]
            )
        return plan

    def _apply_actions(
        self,
        rows: Iterable[object],
        artifact_id: UUID | None,
        run_id: UUID | None,
        *,
        mutate: bool,
    ) -> _WritePlan:
        typed = tuple(row for row in rows if isinstance(row, NormalizedCorporateAction))
        plan = _WritePlan()
        promotable = [row for row in typed if row.source_published_at is not None]
        if not promotable:
            return plan
        symbols = {row.symbol for row in promotable}
        securities = list(
            self.session.scalars(
                select(Security).where(Security.exchange == "NSE", Security.symbol.in_(symbols))
            )
        )
        by_symbol = {item.symbol: item for item in securities}
        security_ids = {item.id for item in securities}
        earliest = min(row.ex_date for row in promotable)
        latest = max(row.ex_date for row in promotable)
        existing = list(
            self.session.scalars(
                select(CorporateAction).where(
                    CorporateAction.security_id.in_(security_ids),
                    CorporateAction.ex_date.between(earliest, latest),
                )
            )
        )
        by_key: dict[tuple[UUID, str, date], list[CorporateAction]] = {}
        for item in existing:
            by_key.setdefault((item.security_id, item.action_type, item.ex_date), []).append(item)
        pending: list[CorporateAction] = []
        for row in promotable:
            security = by_symbol.get(row.symbol)
            row_key = f"{row.symbol}:{row.action_type}:{row.ex_date}"
            if security is None or security.series not in {None, row.series}:
                plan.issue(
                    IssueSeverity.ERROR,
                    "AMBIGUOUS_SECURITY",
                    "Corporate action cannot be resolved to the security master",
                    row_key=row_key,
                    rejected=True,
                )
                continue
            candidates = by_key.get((security.id, row.action_type, row.ex_date), [])
            identical = next(
                (
                    item
                    for item in candidates
                    if Decimal(item.ratio_numerator or 0) == row.ratio_numerator
                    and Decimal(item.ratio_denominator or 0) == row.ratio_denominator
                    and item.source_published_at == row.source_published_at
                ),
                None,
            )
            if identical is not None:
                plan.unchanged += 1
                continue
            if candidates:
                plan.issue(
                    IssueSeverity.ERROR,
                    "CORPORATE_ACTION_REVISION_UNRESOLVED",
                    "Differing action requires an explicit append-only supersedes relationship",
                    row_key=row_key,
                    rejected=True,
                    conflict=True,
                )
                continue
            plan.inserted += 1
            pending.append(
                CorporateAction(
                    security_id=security.id,
                    action_type=row.action_type,
                    announcement_date=row.source_published_at.date(),
                    ex_date=row.ex_date,
                    record_date=row.record_date,
                    ratio_numerator=row.ratio_numerator,
                    ratio_denominator=row.ratio_denominator,
                    notes=row.purpose,
                    source="NSE_CORPORATE_ACTIONS_PUBLIC",
                    source_published_at=row.source_published_at,
                    available_at=row.source_published_at,
                    data_origin=DataOrigin.OFFICIAL_NSE_PUBLIC.value,
                    source_artifact_id=artifact_id,
                    ingestion_run_id=run_id,
                )
            )
        if mutate:
            self.session.add_all(pending)
        return plan

    def _apply_holidays(
        self,
        rows: Iterable[object],
        artifact_id: UUID | None,
        run_id: UUID | None,
        *,
        mutate: bool,
    ) -> _WritePlan:
        typed = tuple(row for row in rows if isinstance(row, NormalizedHoliday))
        plan = _WritePlan()
        dates = {row.trading_date for row in typed}
        existing = {
            item.trading_date: item
            for item in self.session.scalars(
                select(TradingCalendar).where(
                    TradingCalendar.exchange == "NSE",
                    TradingCalendar.trading_date.in_(dates),
                )
            )
        }
        pending: list[TradingCalendar] = []
        for row in typed:
            current = existing.get(row.trading_date)
            if current is None:
                plan.inserted += 1
                pending.append(
                    TradingCalendar(
                        exchange="NSE",
                        trading_date=row.trading_date,
                        is_trading_day=False,
                        session_type="CLOSED",
                        notes=row.description[:255],
                        data_origin=DataOrigin.OFFICIAL_NSE_PUBLIC.value,
                        source_artifact_id=artifact_id,
                        ingestion_run_id=run_id,
                    )
                )
            elif current.is_trading_day:
                plan.issue(
                    IssueSeverity.ERROR,
                    "CALENDAR_CONFLICT",
                    "Official holiday conflicts with a confirmed trading session",
                    row_key=row.trading_date.isoformat(),
                    rejected=True,
                    conflict=True,
                )
            else:
                plan.unchanged += 1
        if mutate:
            self.session.add_all(pending)
        return plan

    def _persist_issues(
        self, artifact_id: UUID, run_id: UUID, issues: tuple[ParsedIssue, ...]
    ) -> None:
        self.session.add_all(
            [
                IngestionIssue(
                    source_artifact_id=artifact_id,
                    ingestion_run_id=run_id,
                    severity=issue.severity.value,
                    code=issue.code,
                    row_number=issue.row_number,
                    row_key=issue.row_key,
                    message=issue.message[:500],
                    issue_metadata=issue.metadata,
                )
                for issue in issues[:200]
            ]
        )

    def _persist_failed_artifact(
        self,
        artifact_type: ArtifactType,
        source_date: date,
        artifact: ArtifactBytes,
        fetched_at: datetime | None,
        checksum: str,
        normalized_fingerprint: str,
        error_summary: str,
        started: float,
    ) -> IngestionSummary:
        definition = SOURCE_DEFINITIONS[artifact_type]
        run = DataIngestionRun(
            dataset_code=f"nse_{artifact_type.value.lower()}",
            dataset_version=source_date.isoformat(),
            provider=definition.provider.value,
            status=IngestionStatus.FAILED.value,
            completed_at=datetime.now(UTC),
            requested_start=source_date,
            requested_end=source_date,
            parser_version=definition.parser_version,
            rejected_count=1,
            error_summary=error_summary[:500],
        )
        source_artifact = SourceArtifact(
            ingestion_run_id=run.id,
            provider=definition.provider.value,
            artifact_type=artifact_type.value,
            source_date=source_date,
            original_file_name=artifact.file_name,
            source_locator=artifact.source_locator,
            fetched_at=fetched_at,
            imported_at=datetime.now(UTC),
            sha256=checksum,
            byte_size=len(artifact.content),
            parser_code=definition.parser_code,
            parser_version=definition.parser_version,
            normalized_fingerprint=normalized_fingerprint,
            parse_status="FAILED",
            rejected_row_count=1,
            error_summary=error_summary[:500],
            artifact_metadata={"mode": "FETCH" if fetched_at else "LOCAL_IMPORT"},
        )
        self.session.add(run)
        self.session.flush()
        source_artifact.ingestion_run_id = run.id
        self.session.add(source_artifact)
        self.session.flush()
        try:
            source_artifact.storage_key = self.store.retain_rejected(artifact)
        except ArtifactValidationError:
            source_artifact.storage_key = None
        self.session.commit()
        issue = ParsedIssue(IssueSeverity.ERROR, "ARTIFACT_VALIDATION_FAILED", error_summary[:500])
        return IngestionSummary(
            artifact_type=artifact_type.value,
            source_date=source_date,
            dry_run=False,
            repeated_artifact=False,
            status="FAILED",
            artifact_id=source_artifact.id,
            ingestion_run_id=run.id,
            checksum=checksum,
            normalized_fingerprint=normalized_fingerprint,
            rows_parsed=0,
            inserted=0,
            unchanged=0,
            rejected=1,
            conflicts=0,
            warnings=0,
            issues=(issue,),
            parse_ms=0.0,
            persistence_ms=0.0,
            total_ms=round((time.perf_counter() - started) * 1_000, 3),
        )

    def _persist_source_revision_conflict(
        self,
        artifact_type: ArtifactType,
        source_date: date,
        artifact: ArtifactBytes,
        fetched_at: datetime | None,
        checksum: str,
        normalized_fingerprint: str,
        parsed: ParserResult,
        started: float,
        parse_ms: float,
    ) -> IngestionSummary:
        definition = SOURCE_DEFINITIONS[artifact_type]
        run = DataIngestionRun(
            dataset_code=f"nse_{artifact_type.value.lower()}",
            dataset_version=source_date.isoformat(),
            provider=definition.provider.value,
            status=IngestionStatus.FAILED.value,
            completed_at=datetime.now(UTC),
            requested_start=source_date,
            requested_end=source_date,
            parser_version=definition.parser_version,
            rejected_count=parsed.source_row_count,
            conflict_count=1,
            error_summary="SOURCE_ARTIFACT_REVISION",
        )
        source_artifact = SourceArtifact(
            ingestion_run_id=run.id,
            provider=definition.provider.value,
            artifact_type=artifact_type.value,
            source_date=source_date,
            original_file_name=artifact.file_name,
            source_locator=artifact.source_locator,
            fetched_at=fetched_at,
            imported_at=datetime.now(UTC),
            sha256=checksum,
            byte_size=len(artifact.content),
            parser_code=definition.parser_code,
            parser_version=definition.parser_version,
            normalized_fingerprint=normalized_fingerprint,
            parse_status="CONFLICT",
            row_count=parsed.source_row_count,
            rejected_row_count=parsed.source_row_count,
            error_summary="A different checksum already exists for the provider/type/date",
            artifact_metadata={"mode": "FETCH" if fetched_at else "LOCAL_IMPORT"},
        )
        self.session.add(run)
        self.session.flush()
        source_artifact.ingestion_run_id = run.id
        self.session.add(source_artifact)
        self.session.flush()
        source_artifact.storage_key = self.store.retain_rejected(artifact)
        issue = ParsedIssue(
            IssueSeverity.ERROR,
            "SOURCE_ARTIFACT_REVISION",
            "Different bytes for an existing provider/type/date were quarantined",
            row_key=f"{artifact_type.value}:{source_date}",
        )
        self._persist_issues(source_artifact.id, run.id, (issue,))
        self.session.commit()
        return IngestionSummary(
            artifact_type=artifact_type.value,
            source_date=source_date,
            dry_run=False,
            repeated_artifact=False,
            status="CONFLICT",
            artifact_id=source_artifact.id,
            ingestion_run_id=run.id,
            checksum=checksum,
            normalized_fingerprint=normalized_fingerprint,
            rows_parsed=parsed.source_row_count,
            inserted=0,
            unchanged=0,
            rejected=parsed.source_row_count,
            conflicts=1,
            warnings=parsed.warning_count,
            issues=(issue,),
            parse_ms=round(parse_ms, 3),
            persistence_ms=0.0,
            total_ms=round((time.perf_counter() - started) * 1_000, 3),
        )
