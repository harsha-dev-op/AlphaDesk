from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.fundamentals.definitions import (
    SECTOR_BENCHMARK_MAPPING_CODE,
    SECTOR_BENCHMARK_MAPPING_VERSION,
    normalize_concept,
    sector_benchmark_symbol,
)
from app.fundamentals.service import FundamentalIntelligenceService
from app.ingestion.nse.artifacts import ArtifactBytes
from app.ingestion.nse.definitions import ArtifactType
from app.ingestion.nse.service import NseIngestionService
from app.models import (
    DailyPrice,
    DataIngestionRun,
    FundamentalFact,
    FundamentalFiling,
    IndexDailyPrice,
    IndexMembership,
    MarketIndex,
    Security,
    SecurityIndustryClassification,
    SourceArtifact,
)
from app.strategies.registry import STRATEGY_DEFINITIONS
from app.services.data_sources import DataSourceCoverageService
from app.technical.definitions import CORE_TECHNICAL_SET, FEATURE_DEFINITIONS, STRATEGY_TECHNICAL_SET


class _MemoryStore:
    def retain(self, artifact: ArtifactBytes) -> str:
        return f"raw/{artifact.sha256[:16]}"

    def retain_rejected(self, artifact: ArtifactBytes) -> str:
        return f"rejected/{artifact.sha256[:16]}"

    def read_inbox(self, _name: str):
        raise AssertionError("not used")


def _security(symbol: str = "ALPHA", number: int = 1, origin: str = "OFFICIAL_NSE_PUBLIC") -> Security:
    return Security(
        exchange="NSE", symbol=symbol, trading_symbol=f"{symbol}-EQ", series="EQ",
        company_name=f"{symbol} Limited", isin=f"INE{number:09d}", security_type="EQUITY",
        currency="INR", is_active=True, data_origin=origin,
    )


def _provenance(db):
    run = DataIngestionRun(dataset_code="test", dataset_version="1", provider="OFFICIAL_NSE_PUBLIC", status="SUCCEEDED")
    db.add(run)
    db.flush()
    artifact = SourceArtifact(
        ingestion_run_id=run.id, provider="OFFICIAL_NSE_PUBLIC", artifact_type="FINANCIAL_RESULTS",
        source_date=date(2026, 7, 20), original_file_name="fixture.csv", source_locator="fixture:test",
        imported_at=datetime(2026, 7, 20, tzinfo=UTC), sha256="a" * 64, byte_size=1,
        parser_code="TEST", parser_version="1", normalized_fingerprint="b" * 64,
        parse_status="SUCCEEDED", artifact_metadata={},
    )
    db.add(artifact)
    db.flush()
    return run, artifact


def _filing(
    db,
    security: Security,
    run: DataIngestionRun,
    artifact: SourceArtifact,
    *,
    available_at: datetime,
    period_start: date,
    period_end: date,
    fiscal_year: int,
    fiscal_quarter: int | None,
    values: dict[str, Decimal | None],
    scope: str = "CONSOLIDATED",
    value_nature: str = "QUARTERLY",
    supersedes: FundamentalFiling | None = None,
    suffix: str = "",
) -> FundamentalFiling:
    filing = FundamentalFiling(
        security_id=security.id, source="TEST", source_filing_id=f"F{fiscal_year}{fiscal_quarter}{suffix}",
        filing_type="FINANCIAL_RESULTS", reporting_frequency="ANNUAL" if value_nature == "ANNUAL" else "QUARTERLY",
        period_start=period_start, period_end=period_end, fiscal_year=fiscal_year, fiscal_quarter=fiscal_quarter,
        scope=scope, audit_status="UNAUDITED", submission_at=available_at, available_at=available_at,
        revision_status="REVISED" if supersedes else "ORIGINAL", supersedes_filing_id=supersedes.id if supersedes else None,
        source_artifact_id=artifact.id, ingestion_run_id=run.id, parser_version="1",
        normalized_fingerprint=f"{fiscal_year:04d}{fiscal_quarter or 0}{scope[0]}{suffix}".ljust(64, "0"),
    )
    db.add(filing)
    db.flush()
    instant = {"TOTAL_EQUITY", "TOTAL_BORROWINGS", "TOTAL_ASSETS", "CASH_AND_CASH_EQUIVALENTS"}
    for concept, value in values.items():
        db.add(FundamentalFact(
            filing_id=filing.id, normalized_concept=concept, source_concept=concept, value=value,
            unit="INR", scale=0, fact_kind="INSTANT" if concept in instant else "DURATION",
            value_nature="INSTANT" if concept in instant else value_nature,
            period_start=period_start, period_end=period_end, fact_metadata={},
        ))
    db.flush()
    return filing


def _quarter_dates(year: int, quarter: int) -> tuple[date, date]:
    starts = {1: (4, 1), 2: (7, 1), 3: (10, 1), 4: (1, 1)}
    ends = {1: (6, 30), 2: (9, 30), 3: (12, 31), 4: (3, 31)}
    start_year = year if quarter == 4 else year - 1
    end_year = year if quarter == 4 else year - 1
    return date(start_year, *starts[quarter]), date(end_year, *ends[quarter])


def test_pit_future_filing_is_excluded_and_period_end_is_not_availability(db):
    security = _security(); db.add(security); db.flush(); run, artifact = _provenance(db)
    start, end = _quarter_dates(2026, 1)
    _filing(db, security, run, artifact, available_at=datetime(2026, 7, 20, tzinfo=UTC), period_start=start, period_end=end, fiscal_year=2026, fiscal_quarter=1, values={"REVENUE": Decimal("100")})
    db.commit()
    before = FundamentalIntelligenceService(db).fundamentals("ALPHA", as_of=datetime(2026, 7, 10, tzinfo=UTC))
    after = FundamentalIntelligenceService(db).fundamentals("ALPHA", as_of=datetime(2026, 7, 21, tzinfo=UTC))
    assert before.status == "UNAVAILABLE"
    assert after.filings[0].facts[0].value == Decimal("100")


def test_revision_preserves_historical_knowledge(db):
    security = _security(); db.add(security); db.flush(); run, artifact = _provenance(db)
    start, end = _quarter_dates(2026, 1)
    original = _filing(db, security, run, artifact, available_at=datetime(2026, 7, 20, tzinfo=UTC), period_start=start, period_end=end, fiscal_year=2026, fiscal_quarter=1, values={"REVENUE": Decimal("100")}, suffix="A")
    _filing(db, security, run, artifact, available_at=datetime(2026, 8, 20, tzinfo=UTC), period_start=start, period_end=end, fiscal_year=2026, fiscal_quarter=1, values={"REVENUE": Decimal("120")}, supersedes=original, suffix="B")
    db.commit()
    service = FundamentalIntelligenceService(db)
    july = service.fundamentals("ALPHA", as_of=datetime(2026, 7, 31, tzinfo=UTC))
    september = service.fundamentals("ALPHA", as_of=datetime(2026, 9, 1, tzinfo=UTC))
    assert july.filings[0].facts[0].value == Decimal("100")
    assert september.filings[0].facts[0].value == Decimal("120")


def test_scope_is_never_mixed_and_standalone_fallback_is_explicit(db):
    security = _security(); db.add(security); db.flush(); run, artifact = _provenance(db)
    start, end = _quarter_dates(2026, 1)
    _filing(db, security, run, artifact, available_at=datetime(2026, 7, 20, tzinfo=UTC), period_start=start, period_end=end, fiscal_year=2026, fiscal_quarter=1, values={"REVENUE": Decimal("90")}, scope="STANDALONE")
    db.commit()
    result = FundamentalIntelligenceService(db).fundamentals("ALPHA", as_of=datetime(2026, 9, 1, tzinfo=UTC))
    assert result.status == "PARTIAL" and result.fallback_used
    assert result.effective_scope == "STANDALONE"
    assert {filing.scope for filing in result.filings} == {"STANDALONE"}


def test_explicit_concept_mapping_and_unknown_concept():
    assert normalize_concept("Revenue From Operations") == "REVENUE"
    assert normalize_concept("Basic Earnings Per Share") == "EPS_BASIC"
    assert normalize_concept("Revenue-ish custom label") is None


def test_missing_fact_value_remains_missing_not_zero(db):
    security = _security(); db.add(security); db.flush(); run, artifact = _provenance(db)
    start, end = _quarter_dates(2026, 1)
    _filing(db, security, run, artifact, available_at=datetime(2026, 7, 20, tzinfo=UTC), period_start=start, period_end=end, fiscal_year=2026, fiscal_quarter=1, values={"REVENUE": None})
    db.commit()
    response = FundamentalIntelligenceService(db).fundamentals("ALPHA", as_of=datetime(2026, 9, 1, tzinfo=UTC))
    assert response.filings[0].facts[0].value is None


def test_ttm_uses_four_independent_non_overlapping_quarters(db):
    security = _security(); db.add(security); db.flush(); run, artifact = _provenance(db)
    for quarter, value in enumerate((10, 20, 30, 40), 1):
        start, end = _quarter_dates(2026, quarter)
        _filing(db, security, run, artifact, available_at=datetime(2026, 8, 1, tzinfo=UTC), period_start=start, period_end=end, fiscal_year=2026, fiscal_quarter=quarter, values={"REVENUE": Decimal(value), "PROFIT_AFTER_TAX": Decimal(value) / 10, "EPS_BASIC": Decimal(value) / 20}, suffix=str(quarter))
    db.commit()
    metrics = {item.code: item for item in FundamentalIntelligenceService(db).metrics("ALPHA", as_of=datetime(2026, 9, 1, tzinfo=UTC)).metrics}
    assert metrics["REVENUE_TTM"].value == Decimal("100.00000000")
    assert metrics["PAT_TTM"].value == Decimal("10.00000000")
    assert metrics["NET_MARGIN_TTM"].value == Decimal("0.10000000")


def test_ttm_rejects_ytd_or_insufficient_periods(db):
    security = _security(); db.add(security); db.flush(); run, artifact = _provenance(db)
    for quarter in (1, 2, 3, 4):
        start, end = _quarter_dates(2026, quarter)
        _filing(db, security, run, artifact, available_at=datetime(2026, 8, 1, tzinfo=UTC), period_start=start, period_end=end, fiscal_year=2026, fiscal_quarter=quarter, values={"REVENUE": Decimal("25")}, value_nature="YTD", suffix=str(quarter))
    db.commit()
    metric = next(item for item in FundamentalIntelligenceService(db).metrics("ALPHA", as_of=datetime(2026, 9, 1, tzinfo=UTC)).metrics if item.code == "REVENUE_TTM")
    assert metric.status == "UNAVAILABLE"
    assert metric.reason == "FOUR_INDEPENDENT_QUARTERS_REQUIRED"


def test_ttm_rejects_quarter_labels_with_non_quarter_duration(db):
    security = _security(); db.add(security); db.flush(); run, artifact = _provenance(db)
    for quarter in (1, 2, 3, 4):
        end = date(2026, quarter * 2 + 2, 1)
        _filing(db, security, run, artifact, available_at=datetime(2026, 9, 1, tzinfo=UTC), period_start=end - timedelta(days=150), period_end=end, fiscal_year=2026, fiscal_quarter=quarter, values={"REVENUE": Decimal("25")}, suffix=str(quarter))
    db.commit()
    metric = next(item for item in FundamentalIntelligenceService(db).metrics("ALPHA", as_of=datetime(2026, 9, 2, tzinfo=UTC)).metrics if item.code == "REVENUE_TTM")
    assert metric.status == "UNAVAILABLE"


def test_growth_and_balance_sheet_metric_formulas(db):
    security = _security(); db.add(security); db.flush(); run, artifact = _provenance(db)
    for year, revenue in ((2025, 100), (2026, 125)):
        start, end = _quarter_dates(year, 1)
        _filing(db, security, run, artifact, available_at=datetime(year, 7, 20, tzinfo=UTC), period_start=start, period_end=end, fiscal_year=year, fiscal_quarter=1, values={"REVENUE": Decimal(revenue), "TOTAL_BORROWINGS": Decimal("50"), "TOTAL_EQUITY": Decimal("200")}, suffix=str(year))
    db.commit()
    metrics = {item.code: item for item in FundamentalIntelligenceService(db).metrics("ALPHA", as_of=datetime(2026, 9, 1, tzinfo=UTC)).metrics}
    assert metrics["REVENUE_YOY"].value == Decimal("0.25000000")
    assert metrics["DEBT_TO_EQUITY"].value == Decimal("0.25000000")


def test_metric_unavailable_reason_is_explicit(db):
    security = _security(); db.add(security); db.commit()
    metric = next(item for item in FundamentalIntelligenceService(db).metrics("ALPHA", as_of=datetime(2026, 9, 1, tzinfo=UTC)).metrics if item.code == "PE_TTM")
    assert metric.status == "UNAVAILABLE" and metric.reason


def _classification(db, security, run, artifact, *, snapshot=date(2026, 9, 20), available=None, sector="Technology", industry="Software", basic="IT Services", sector_benchmark_index_id=None):
    row = SecurityIndustryClassification(
        security_id=security.id, macro_economic_sector="Services", sector=sector,
        industry=industry, basic_industry=basic, snapshot_date=snapshot,
        available_at=available or datetime(2026, 9, 20, tzinfo=UTC), source="TEST",
        source_artifact_id=artifact.id, ingestion_run_id=run.id, parser_version="1",
        normalized_fingerprint=(security.symbol + str(snapshot)).ljust(64, "0"),
        sector_benchmark_index_id=sector_benchmark_index_id,
    )
    db.add(row); db.flush(); return row


def test_current_classification_and_peer_lookup(db):
    alpha, beta = _security(), _security("BETA", 2); db.add_all([alpha, beta]); db.flush(); run, artifact = _provenance(db)
    _classification(db, alpha, run, artifact); _classification(db, beta, run, artifact); db.commit()
    result = FundamentalIntelligenceService(db).classification("ALPHA", as_of=datetime(2026, 9, 21, tzinfo=UTC))
    assert result.status == "READY" and result.basic_industry == "IT Services"
    assert [item.symbol for item in result.peers["basic_industry"]] == ["BETA"]
    assert result.sector_benchmark_status == "UNAVAILABLE"


def test_current_classification_is_not_backcast(db):
    security = _security(); db.add(security); db.flush(); run, artifact = _provenance(db)
    _classification(db, security, run, artifact); db.commit()
    result = FundamentalIntelligenceService(db).classification("ALPHA", as_of=datetime(2026, 9, 1, tzinfo=UTC))
    assert result.status == "UNAVAILABLE"


def _price_foundation(db, *, sessions=130, include_official=True):
    alpha, beta = _security(), _security("BETA", 2); db.add_all([alpha, beta]); db.flush()
    index = MarketIndex(name="NIFTY 200", symbol="NIFTY200", provider="OFFICIAL_NSE_INDICES_PUBLIC", exchange="NSE")
    db.add(index); db.flush()
    start = date(2026, 1, 1)
    known_at = datetime(2026, 9, 20, tzinfo=UTC)
    for offset in range(sessions):
        day = start + timedelta(days=offset)
        db.add(IndexDailyPrice(index_id=index.id, trading_date=day, open=100 + offset, high=101 + offset, low=99 + offset, close=100 + offset, source_mode="OFFICIAL", source="TEST", available_at=known_at))
        for security, multiplier in ((alpha, Decimal("2")), (beta, Decimal("1.5"))):
            close = Decimal(100 + offset) * multiplier
            db.add(DailyPrice(security_id=security.id, trading_date=day, open=close, high=close, low=close, close=close, volume=100, source="TEST", ingested_at=known_at, data_origin="OFFICIAL_NSE_PUBLIC" if include_official else "DEMO"))
    db.add_all([
        IndexMembership(index_id=index.id, security_id=alpha.id, valid_from=start, source="TEST", data_origin="OFFICIAL_NSE_PUBLIC"),
        IndexMembership(index_id=index.id, security_id=beta.id, valid_from=start, source="TEST", data_origin="OFFICIAL_NSE_PUBLIC"),
    ])
    db.commit(); return alpha, beta, index


def test_stock_vs_nifty_relative_strength_and_percentile_are_deterministic(db):
    _price_foundation(db)
    service = FundamentalIntelligenceService(db)
    first = service.relative_strength("ALPHA", as_of=datetime(2026, 9, 21, tzinfo=UTC))
    second = service.relative_strength("ALPHA", as_of=datetime(2026, 9, 21, tzinfo=UTC))
    assert all(item.status == "AVAILABLE" for item in first.metrics)
    assert first.model_dump() == second.model_dump()
    assert all(item.percentile is not None for item in first.metrics)
    assert all(item.security_return is not None for item in first.metrics)
    assert all(item.benchmark_return is not None for item in first.metrics)


def test_sector_mapping_registry_is_explicit_and_versioned():
    assert SECTOR_BENCHMARK_MAPPING_CODE == "ALPHADESK_NSE_SECTOR_BENCHMARK_MAPPING"
    assert SECTOR_BENCHMARK_MAPPING_VERSION == "1"
    assert sector_benchmark_symbol("Information Technology") == "NIFTYIT"
    assert sector_benchmark_symbol("information technology") is None
    assert sector_benchmark_symbol("Information Technology Services") is None


def test_stock_vs_sector_relative_strength_uses_common_official_sessions(db):
    alpha, _, _ = _price_foundation(db)
    run, artifact = _provenance(db)
    sector = MarketIndex(name="NIFTY IT", symbol="NIFTYIT", provider="OFFICIAL_NSE_INDICES_PUBLIC", exchange="NSE")
    db.add(sector); db.flush()
    start = date(2026, 1, 1)
    known_at = datetime(2026, 9, 20, tzinfo=UTC)
    for offset in range(130):
        if offset == 25:  # a missing benchmark day must not be interpolated
            continue
        day = start + timedelta(days=offset)
        db.add(IndexDailyPrice(index_id=sector.id, trading_date=day, open=100 + offset, high=101 + offset, low=99 + offset, close=100 + offset, source_mode="OFFICIAL", source="TEST", available_at=known_at))
    _classification(db, alpha, run, artifact, sector="Information Technology", sector_benchmark_index_id=sector.id)
    db.commit()
    result = FundamentalIntelligenceService(db).relative_strength("ALPHA", as_of=datetime(2026, 9, 21, tzinfo=UTC))
    assert result.sector_benchmark == "NIFTYIT"
    assert result.sector_metrics_status == "AVAILABLE"
    assert result.sector_metrics[-1].status == "AVAILABLE"
    assert result.sector_metrics[-1].security_return is not None
    assert result.sector_metrics[-1].benchmark_return is not None


def test_sector_relative_strength_unavailable_without_verified_mapping(db):
    _price_foundation(db)
    result = FundamentalIntelligenceService(db).relative_strength("ALPHA", as_of=datetime(2026, 9, 21, tzinfo=UTC))
    assert result.sector_benchmark is None
    assert result.sector_metrics_status == "UNAVAILABLE"
    assert all(item.value is None for item in result.sector_metrics)


def test_relative_strength_insufficient_history(db):
    _price_foundation(db, sessions=20)
    result = FundamentalIntelligenceService(db).relative_strength("ALPHA", as_of=datetime(2026, 9, 21, tzinfo=UTC))
    assert all(item.status == "UNAVAILABLE" for item in result.metrics)


def test_relative_strength_does_not_mix_demo_equity_with_official_benchmark(db):
    _price_foundation(db, include_official=False)
    result = FundamentalIntelligenceService(db).relative_strength("ALPHA", as_of=datetime(2026, 9, 21, tzinfo=UTC))
    assert all(item.status == "UNAVAILABLE" for item in result.metrics)


def _financial_csv(value="100", concept="Revenue", source_id="F1", supersedes="", submission="2026-07-20T10:00:00+05:30") -> ArtifactBytes:
    header = "Symbol,Series,ISIN,Source Filing ID,Supersedes Filing ID,Filing Type,Reporting Frequency,Period Start,Period End,Fiscal Year,Fiscal Quarter,Scope,Audit Status,Submission Timestamp,Concept,Value,Unit,Scale,Fact Kind,Value Nature\n"
    row = f"ALPHA,EQ,INE000000001,{source_id},{supersedes},FINANCIAL_RESULTS,QUARTERLY,2026-04-01,2026-06-30,2026,1,CONSOLIDATED,UNAUDITED,{submission},{concept},{value},INR,0,DURATION,QUARTERLY\n"
    return ArtifactBytes("fundamentals.csv", (header + row).encode(), "fixture:fundamentals")


def test_fundamental_import_is_idempotent_and_maps_concept(db):
    db.add(_security()); db.commit(); service = NseIngestionService(db, store=_MemoryStore())
    artifact = _financial_csv()
    first = service.import_artifact(ArtifactType.FINANCIAL_RESULTS, date(2026, 7, 20), artifact)
    second = service.import_artifact(ArtifactType.FINANCIAL_RESULTS, date(2026, 7, 20), artifact)
    assert first.inserted == 1 and second.repeated_artifact
    assert db.scalar(select(FundamentalFact.normalized_concept)) == "REVENUE"


def test_unknown_imported_concept_is_retained_unmapped(db):
    db.add(_security()); db.commit(); service = NseIngestionService(db, store=_MemoryStore())
    result = service.import_artifact(ArtifactType.FINANCIAL_RESULTS, date(2026, 7, 20), _financial_csv(concept="Custom Regulatory Line"))
    fact = db.scalar(select(FundamentalFact))
    assert result.warnings == 1 and fact.normalized_concept is None
    assert fact.source_concept == "Custom Regulatory Line"


def test_conflicting_artifact_for_same_source_date_is_quarantined(db):
    db.add(_security()); db.commit(); service = NseIngestionService(db, store=_MemoryStore())
    service.import_artifact(ArtifactType.FINANCIAL_RESULTS, date(2026, 7, 20), _financial_csv("100"))
    conflict = service.import_artifact(ArtifactType.FINANCIAL_RESULTS, date(2026, 7, 20), _financial_csv("101"))
    assert conflict.status == "CONFLICT" and conflict.conflicts == 1
    assert db.scalar(select(func.count()).select_from(FundamentalFiling)) == 1


def test_ingested_revision_chain_is_point_in_time(db):
    db.add(_security()); db.commit(); service = NseIngestionService(db, store=_MemoryStore())
    service.import_artifact(ArtifactType.FINANCIAL_RESULTS, date(2026, 7, 20), _financial_csv("100"))
    revised = service.import_artifact(
        ArtifactType.FINANCIAL_RESULTS,
        date(2026, 8, 20),
        _financial_csv("120", source_id="F2", supersedes="F1", submission="2026-08-20T10:00:00+05:30"),
    )
    intelligence = FundamentalIntelligenceService(db)
    before = intelligence.fundamentals("ALPHA", as_of=datetime(2026, 8, 1, tzinfo=UTC))
    after = intelligence.fundamentals("ALPHA", as_of=datetime(2026, 8, 21, tzinfo=UTC))
    assert revised.inserted == 1
    assert before.filings[0].facts[0].value == Decimal("100")
    assert after.filings[0].facts[0].value == Decimal("120")


def _classification_csv() -> ArtifactBytes:
    text = (
        "Symbol,Series,ISIN,Macro Economic Sector,Sector,Industry,Basic Industry\n"
        "ALPHA,EQ,INE000000001,Services,Technology,Software,IT Services\n"
    )
    return ArtifactBytes("classification.csv", text.encode(), "fixture:classification")


def test_classification_import_is_idempotent_and_not_backcast(db):
    db.add(_security()); db.commit(); service = NseIngestionService(db, store=_MemoryStore())
    artifact = _classification_csv()
    first = service.import_artifact(ArtifactType.INDUSTRY_CLASSIFICATION, date(2026, 9, 20), artifact)
    second = service.import_artifact(ArtifactType.INDUSTRY_CLASSIFICATION, date(2026, 9, 20), artifact)
    intelligence = FundamentalIntelligenceService(db)
    assert first.inserted == 1 and second.repeated_artifact
    assert intelligence.classification("ALPHA", as_of=datetime(2026, 9, 19, tzinfo=UTC)).status == "UNAVAILABLE"
    assert intelligence.classification("ALPHA", as_of=datetime(2026, 9, 21, tzinfo=UTC)).status == "READY"


def test_classification_import_links_only_existing_official_exact_sector_index(db):
    db.add(_security())
    db.add(MarketIndex(name="NIFTY IT", symbol="NIFTYIT", provider="OFFICIAL_NSE_INDICES_PUBLIC", exchange="NSE"))
    db.commit()
    service = NseIngestionService(db, store=_MemoryStore())
    artifact = ArtifactBytes(
        "classification.csv",
        b"Symbol,Series,ISIN,Macro Economic Sector,Sector,Industry,Basic Industry\n"
        b"ALPHA,EQ,INE000000001,Services,Information Technology,Software,IT Services\n",
        "fixture:classification",
    )
    result = service.import_artifact(ArtifactType.INDUSTRY_CLASSIFICATION, date(2026, 9, 20), artifact)
    row = db.scalar(select(SecurityIndustryClassification))
    assert result.inserted == 1
    assert row is not None and row.sector_benchmark_index_id is not None


def test_research_summary_exposes_eod_return_classification_and_relative_components(db):
    alpha, _, _ = _price_foundation(db)
    run, artifact = _provenance(db)
    _classification(db, alpha, run, artifact)
    db.commit()
    result = FundamentalIntelligenceService(db).research_summary("ALPHA", as_of=datetime(2026, 9, 21, tzinfo=UTC))
    assert result.latest_market_date is not None
    assert result.one_day_return is not None
    assert result.data_source == "OFFICIAL_NSE_PUBLIC"
    assert result.basic_industry == "IT Services"
    assert len(result.market_relative_strength) == 3
    assert len(result.sector_relative_strength) == 3


def test_data_health_reports_infrastructure_without_data_as_unavailable(db):
    datasets = {item.code: item for item in DataSourceCoverageService(db).coverage().activation_datasets}
    assert datasets["FUNDAMENTALS"].status == "UNAVAILABLE"
    assert datasets["INDUSTRY_CLASSIFICATION"].status == "UNAVAILABLE"
    assert datasets["SECTOR_BENCHMARKS"].status == "UNAVAILABLE"


def test_financial_parser_rejects_naive_submission_timestamp(db):
    db.add(_security()); db.commit(); service = NseIngestionService(db, store=_MemoryStore())
    artifact = _financial_csv()
    artifact = ArtifactBytes(artifact.file_name, artifact.content.replace(b"2026-07-20T10:00:00+05:30", b"2026-07-20T10:00:00"), artifact.source_locator)
    result = service.import_artifact(ArtifactType.FINANCIAL_RESULTS, date(2026, 7, 20), artifact, dry_run=True)
    assert result.rejected == 1 and result.inserted == 0


def test_api_metadata_schema_and_naive_as_of_validation(client, db):
    db.add(_security()); db.commit()
    metadata = client.get("/api/v1/fundamentals/metadata")
    invalid = client.get("/api/v1/securities/ALPHA/fundamentals?as_of=2026-09-20T10:00:00")
    assert metadata.status_code == 200
    assert metadata.json()["concept_registry_version"] == "1"
    assert invalid.status_code == 422


def test_research_summary_composes_truthful_unavailable_states(client, db):
    db.add(_security()); db.commit()
    response = client.get("/api/v1/securities/ALPHA/research-summary?as_of=2026-09-20T10:00:00%2B05:30")
    assert response.status_code == 200
    assert response.json()["fundamental_status"] == "UNAVAILABLE"
    assert response.json()["classification_status"] == "UNAVAILABLE"


def test_phase13_invariants_remain_unchanged():
    assert len(CORE_TECHNICAL_SET.feature_codes) == 36
    assert len(STRATEGY_TECHNICAL_SET.feature_codes) == 38
    assert "EMA_200" not in FEATURE_DEFINITIONS
    assert [(code, version) for code, version in STRATEGY_DEFINITIONS] == [
        ("MOMENTUM_TREND", "1"), ("BREAKOUT_20D", "1"), ("MEAN_REVERSION_PULLBACK", "1")
    ]
