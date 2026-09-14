from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
import re

import pytest
from sqlalchemy import event, func, select

from app.ingestion.nse.artifacts import ArtifactBytes
from app.ingestion.nse.definitions import ArtifactType, DataOrigin
from app.ingestion.nse.client import DownloadedArtifact
from app.ingestion.nse.service import NseIngestionService
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
from app.repositories.indices import IndexRepository
from app.services.quality import DataQualityService


class _MemoryArtifactStore:
    def retain(self, artifact: ArtifactBytes) -> str:
        return f"raw/{artifact.sha256[:16]}-{artifact.file_name}"

    def retain_rejected(self, artifact: ArtifactBytes) -> str:
        return f"rejected/{artifact.sha256[:16]}-{artifact.file_name}"


def _service(db) -> NseIngestionService:
    return NseIngestionService(db, store=_MemoryArtifactStore())


def _artifact(name: str, text: str) -> ArtifactBytes:
    return ArtifactBytes(name, text.encode(), f"fixture:{name}")


def _security_csv(*rows: str) -> ArtifactBytes:
    return _artifact(
        "security.csv",
        "TckrSymb,SctySrs,FinInstrmNm,ISIN,DtOfListing,Normal Market Status\n"
        + "\n".join(rows)
        + "\n",
    )


def _price_csv(source_date: date, *rows: str, name: str = "prices.csv") -> ArtifactBytes:
    return _artifact(
        name,
        "TradDt,Sgmt,TckrSymb,SctySrs,ISIN,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol,TtlTrfVal\n"
        + "\n".join(f"{source_date.isoformat()},CM,{row}" for row in rows)
        + "\n",
    )


def _security(symbol: str, number: int, *, origin: str = "OFFICIAL_NSE_PUBLIC") -> Security:
    return Security(
        exchange="NSE",
        symbol=symbol,
        trading_symbol=f"{symbol}-EQ",
        series="EQ",
        company_name=f"{symbol} Limited",
        isin=f"INE{number:09d}",
        security_type="EQUITY",
        currency="INR",
        is_active=True,
        data_origin=origin,
    )


def _constituent_artifact(symbols: list[str], *, name: str) -> ArtifactBytes:
    lines = ["Company Name,Industry,Symbol,Series,ISIN Code"]
    for symbol in symbols:
        number = int(symbol[1:])
        lines.append(f"{symbol} Limited,Industry,{symbol},EQ,INE{number:09d}")
    return _artifact(name, "\n".join(lines) + "\n")


def test_successful_security_artifact_is_transactional_idempotent_and_provenanced(db):
    artifact = _security_csv("ALPHA,EQ,Alpha Limited,INE000000001,2020-01-02,ACTIVE")
    first = _service(db).import_artifact(ArtifactType.SECURITY_MASTER, date(2026, 9, 11), artifact)
    second = _service(db).import_artifact(ArtifactType.SECURITY_MASTER, date(2026, 9, 11), artifact)

    security = db.scalar(select(Security).where(Security.symbol == "ALPHA"))
    source = db.scalar(select(SourceArtifact))
    run = db.scalar(select(DataIngestionRun))
    assert first.status == "SUCCEEDED"
    assert first.inserted == 1
    assert second.repeated_artifact is True
    assert second.unchanged == 1
    assert security.isin == "INE000000001"
    assert security.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value
    assert security.source_artifact_id == source.id
    assert security.ingestion_run_id == run.id
    assert source.sha256 == artifact.sha256
    assert source.parser_version == "1.0.0"
    assert source.storage_key.startswith("raw/")
    assert run.status == "SUCCEEDED"


def test_failed_artifact_records_failure_without_domain_rows(db):
    result = _service(db).import_artifact(
        ArtifactType.SECURITY_MASTER,
        date(2026, 9, 11),
        _artifact("wrong.csv", "wrong,schema\na,b\n"),
    )
    assert result.status == "FAILED"
    assert db.scalar(select(func.count()).select_from(Security)) == 0
    assert db.scalar(select(SourceArtifact.parse_status)) == "FAILED"
    assert db.scalar(select(SourceArtifact.storage_key)).startswith("rejected/")
    assert db.scalar(select(DataIngestionRun.status)) == "FAILED"
    assert "missing required columns" in db.scalar(select(SourceArtifact.error_summary))


def test_dry_run_reports_intended_writes_without_persistence(db):
    result = _service(db).import_artifact(
        ArtifactType.SECURITY_MASTER,
        date(2026, 9, 11),
        _security_csv("ALPHA,EQ,Alpha Limited,INE000000001,2020-01-02,ACTIVE"),
        dry_run=True,
    )
    assert result.status == "DRY_RUN"
    assert result.inserted == 1
    assert db.scalar(select(func.count()).select_from(Security)) == 0
    assert db.scalar(select(func.count()).select_from(SourceArtifact)) == 0
    assert db.scalar(select(func.count()).select_from(DataIngestionRun)) == 0


def test_transaction_exception_rolls_back_domain_writes_and_records_sanitized_failure(db, monkeypatch):
    service = _service(db)

    def fail_apply(*_args, **_kwargs):
        db.add(_security("LEAK", 999))
        raise RuntimeError("sensitive internal detail")

    monkeypatch.setattr(service, "_apply", fail_apply)
    result = service.import_artifact(
        ArtifactType.SECURITY_MASTER,
        date(2026, 9, 11),
        _security_csv("ALPHA,EQ,Alpha Limited,INE000000001,2020-01-02,ACTIVE"),
    )
    assert result.status == "FAILED"
    assert db.scalar(select(func.count()).select_from(Security)) == 0
    assert "RuntimeError" in db.scalar(select(SourceArtifact.error_summary))
    assert "sensitive internal detail" not in db.scalar(select(SourceArtifact.error_summary))


def test_security_symbol_isin_conflict_is_rejected_without_rewriting_identity(db):
    db.add_all([_security("ALPHA", 1), _security("BETA", 2)])
    db.commit()
    result = _service(db).import_artifact(
        ArtifactType.SECURITY_MASTER,
        date(2026, 9, 11),
        _security_csv("ALPHA,EQ,Ambiguous,INE000000002,2020-01-02,ACTIVE"),
    )
    assert result.status == "PARTIAL"
    assert result.conflicts == 1
    assert result.rejected == 1
    assert db.scalar(select(Security.isin).where(Security.symbol == "ALPHA")) == "INE000000001"


def test_partial_price_import_accepts_valid_rows_and_accounts_for_unknown_security(db):
    db.add(_security("ALPHA", 1))
    db.commit()
    day = date(2026, 9, 11)
    result = _service(db).import_artifact(
        ArtifactType.EOD_BHAVCOPY,
        day,
        _price_csv(
            day,
            "ALPHA,EQ,INE000000001,100,110,90,105,12,1260",
            "UNKNOWN,EQ,INE000000099,50,55,45,52,10,520",
        ),
    )
    price = db.scalar(select(DailyPrice))
    calendar = db.scalar(select(TradingCalendar))
    assert result.status == "PARTIAL"
    assert (result.inserted, result.rejected) == (1, 1)
    assert price.open == Decimal("100")
    assert price.close == Decimal("105")
    assert price.volume == 12
    assert price.source == "NSE_CM_UDIFF"
    assert price.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value
    assert calendar.is_trading_day is True
    assert calendar.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value


def test_existing_identical_official_price_is_unchanged(db):
    security = _security("ALPHA", 1)
    db.add(security)
    db.flush()
    day = date(2026, 9, 11)
    db.add(
        DailyPrice(
            security_id=security.id,
            trading_date=day,
            open=100,
            high=110,
            low=90,
            close=105,
            volume=12,
            traded_value=1260,
            source="NSE_CM_UDIFF",
            data_origin=DataOrigin.OFFICIAL_NSE_PUBLIC.value,
        )
    )
    db.commit()
    result = _service(db).import_artifact(
        ArtifactType.EOD_BHAVCOPY,
        day,
        _price_csv(day, "ALPHA,EQ,INE000000001,100,110,90,105,12,1260"),
    )
    assert result.status == "SUCCEEDED"
    assert (result.inserted, result.unchanged) == (0, 1)
    assert db.scalar(select(func.count()).select_from(DailyPrice)) == 1


def test_differing_historical_price_conflicts_and_never_overwrites(db):
    security = _security("ALPHA", 1)
    db.add(security)
    db.flush()
    day = date(2026, 9, 11)
    db.add(
        DailyPrice(
            security_id=security.id,
            trading_date=day,
            open=100,
            high=110,
            low=90,
            close=105,
            volume=12,
            source="PREEXISTING",
            data_origin=DataOrigin.OFFICIAL_NSE_PUBLIC.value,
        )
    )
    db.commit()
    result = _service(db).import_artifact(
        ArtifactType.EOD_BHAVCOPY,
        day,
        _price_csv(day, "ALPHA,EQ,INE000000001,100,110,90,106,12,1272"),
    )
    price = db.scalar(select(DailyPrice))
    assert result.status == "PARTIAL"
    assert result.conflicts == 1
    assert price.close == Decimal("105")
    conflict = next(issue for issue in result.issues if issue.code == "HISTORICAL_DATA_CONFLICT")
    assert set(conflict.metadata) == {"existing_hash", "incoming_hash"}
    assert all(len(str(value)) == 64 for value in conflict.metadata.values())


def test_different_bytes_for_same_source_identity_are_quarantined_as_revision(db):
    first = _security_csv("ALPHA,EQ,Alpha Limited,INE000000001,2020-01-02,ACTIVE")
    changed = _security_csv("ALPHA,EQ,Alpha Ltd,INE000000001,2020-01-02,ACTIVE")
    service = _service(db)
    service.import_artifact(ArtifactType.SECURITY_MASTER, date(2026, 9, 11), first)
    result = service.import_artifact(ArtifactType.SECURITY_MASTER, date(2026, 9, 11), changed)
    assert result.status == "CONFLICT"
    assert result.conflicts == 1
    assert db.scalar(select(func.count()).select_from(Security)) == 1
    assert db.scalar(select(func.count()).select_from(SourceArtifact)) == 2
    assert db.scalar(
        select(SourceArtifact.storage_key).where(SourceArtifact.parse_status == "CONFLICT")
    ).startswith("rejected/")
    assert db.scalar(
        select(func.count()).select_from(IngestionIssue).where(
            IngestionIssue.code == "SOURCE_ARTIFACT_REVISION"
        )
    ) == 1


def test_corporate_actions_promote_only_rows_with_real_availability_timestamps(db):
    db.add(_security("ALPHA", 1))
    db.commit()
    artifact = _artifact(
        "actions.csv",
        "Symbol,Series,Purpose,Ex-Date,Record Date,Source Published At\n"
        "ALPHA,EQ,Bonus Issue 1:1,2026-09-20,2026-09-21,2026-09-01T10:00:00+05:30\n"
        "ALPHA,EQ,Split from Rs 10 to Rs 2,2026-10-01,2026-10-02,2026-09-02T10:00:00Z\n"
        "ALPHA,EQ,Bonus Issue 2:1,2026-10-07,2026-10-08,\n"
        "ALPHA,EQ,Dividend Rs 5,2026-10-09,2026-10-10,2026-09-03T10:00:00Z\n",
    )
    result = _service(db).import_artifact(
        ArtifactType.CORPORATE_ACTIONS, date(2026, 9, 11), artifact
    )
    actions = list(db.scalars(select(CorporateAction).order_by(CorporateAction.ex_date)))
    assert result.status == "PARTIAL"
    assert (result.inserted, result.rejected) == (2, 2)
    assert [item.action_type for item in actions] == ["BONUS", "STOCK_SPLIT"]
    assert actions[0].available_at == actions[0].source_published_at
    assert actions[0].available_at.isoformat() == "2026-09-01T04:30:00"
    assert all(item.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value for item in actions)
    assert db.scalar(
        select(func.count()).select_from(IngestionIssue).where(
            IngestionIssue.code == "AVAILABILITY_TIMESTAMP_UNKNOWN"
        )
    ) == 1


@pytest.mark.parametrize(
    ("artifact_type", "member_count", "symbol"),
    [
        (ArtifactType.NIFTY_200_CONSTITUENTS, 200, "NIFTY200"),
        (ArtifactType.NIFTY_500_CONSTITUENTS, 500, "NIFTY500"),
    ],
)
def test_current_constituent_snapshots_are_exact_and_not_backcast(
    db, artifact_type, member_count, symbol
):
    symbols = [f"S{number:04d}" for number in range(1, member_count + 1)]
    db.add_all([_security(item, number) for number, item in enumerate(symbols, start=1)])
    db.commit()
    snapshot_date = date(2026, 8, 31)
    result = _service(db).import_artifact(
        artifact_type,
        snapshot_date,
        _constituent_artifact(symbols, name=f"{symbol.lower()}.csv"),
    )
    market_index = db.scalar(select(MarketIndex).where(MarketIndex.symbol == symbol))
    repository = IndexRepository(db)
    assert result.status == "SUCCEEDED"
    assert result.inserted == member_count
    assert len(repository.members_as_of(market_index.id, snapshot_date)) == member_count
    assert repository.members_as_of(market_index.id, snapshot_date.replace(day=30)) == []
    with pytest.raises(ValueError, match="HISTORICAL_MEMBERSHIP_COVERAGE_INCOMPLETE"):
        repository.assert_historical_coverage(market_index, snapshot_date.replace(day=30))


def test_future_constituent_snapshot_closes_prior_interval_without_changing_prior_members(db):
    initial = [f"S{number:04d}" for number in range(1, 201)]
    changed = initial[1:] + ["S0201"]
    db.add_all([_security(item, number) for number, item in enumerate(initial + ["S0201"], start=1)])
    db.commit()
    service = _service(db)
    service.import_artifact(
        ArtifactType.NIFTY_200_CONSTITUENTS,
        date(2026, 8, 31),
        _constituent_artifact(initial, name="nifty200-a.csv"),
    )
    market_index = db.scalar(select(MarketIndex).where(MarketIndex.symbol == "NIFTY200"))
    repository = IndexRepository(db)
    before = [item.security.symbol for item in repository.members_as_of(market_index.id, date(2026, 9, 1))]
    service.import_artifact(
        ArtifactType.NIFTY_200_CONSTITUENTS,
        date(2026, 9, 10),
        _constituent_artifact(changed, name="nifty200-b.csv"),
    )
    after_prior = [item.security.symbol for item in repository.members_as_of(market_index.id, date(2026, 9, 1))]
    current = [item.security.symbol for item in repository.members_as_of(market_index.id, date(2026, 9, 10))]
    assert before == after_prior
    assert "S0001" in before and "S0001" not in current and "S0201" in current
    assert db.scalar(
        select(func.count()).select_from(IndexMembership).where(
            IndexMembership.index_id == market_index.id
        )
    ) == 400


def test_unexpected_or_unknown_constituent_snapshot_is_rejected_atomically(db):
    db.add(_security("ALPHA", 1))
    db.commit()
    result = _service(db).import_artifact(
        ArtifactType.NIFTY_200_CONSTITUENTS,
        date(2026, 8, 31),
        _artifact(
            "short.csv",
            "Company Name,Industry,Symbol,Series,ISIN Code\n"
            "Alpha Limited,Industry,ALPHA,EQ,INE000000001\n"
            "Unknown Limited,Industry,UNKNOWN,EQ,INE000000099\n",
        ),
    )
    assert result.status == "PARTIAL"
    assert result.inserted == 0
    assert result.rejected == 2
    assert {issue.code for issue in result.issues} == {
        "UNKNOWN_INDEX_SECURITY",
        "UNEXPECTED_MEMBER_COUNT",
    }
    assert db.scalar(select(func.count()).select_from(IndexMembership)) == 0


def test_holiday_import_marks_only_explicit_dates_and_leaves_unknown_dates_absent(db):
    holiday = date(2026, 10, 2)
    result = _service(db).import_artifact(
        ArtifactType.TRADING_HOLIDAYS,
        date(2026, 9, 11),
        _artifact("holidays.csv", "Date,Description\n2026-10-02,Gandhi Jayanti\n"),
    )
    persisted = db.scalar(
        select(TradingCalendar).where(TradingCalendar.trading_date == holiday)
    )
    unknown = db.scalar(
        select(TradingCalendar).where(TradingCalendar.trading_date == date(2026, 10, 5))
    )
    assert result.status == "SUCCEEDED"
    assert persisted.is_trading_day is False
    assert persisted.notes == "Gandhi Jayanti"
    assert unknown is None


def test_confirmed_holiday_conflicts_with_price_artifact_without_reclassification(db):
    security = _security("ALPHA", 1)
    db.add_all(
        [
            security,
            TradingCalendar(
                exchange="NSE",
                trading_date=date(2026, 9, 11),
                is_trading_day=False,
                session_type="CLOSED",
                notes="Confirmed holiday",
                data_origin=DataOrigin.OFFICIAL_NSE_PUBLIC.value,
            ),
        ]
    )
    db.commit()
    result = _service(db).import_artifact(
        ArtifactType.EOD_BHAVCOPY,
        date(2026, 9, 11),
        _price_csv(
            date(2026, 9, 11),
            "ALPHA,EQ,INE000000001,100,110,90,105,12,1260",
        ),
    )
    assert result.conflicts == 1
    assert result.rejected == 1
    assert db.scalar(select(func.count()).select_from(DailyPrice)) == 0
    assert db.scalar(select(TradingCalendar.is_trading_day)) is False


def test_coverage_endpoints_report_mixed_mode_artifacts_and_pit_warnings(client, db):
    db.add_all([_security("REAL", 1), _security("DEMO", 2, origin="DEMO")])
    db.commit()
    _service(db).import_artifact(
        ArtifactType.EOD_BHAVCOPY,
        date(2026, 9, 11),
        _price_csv(
            date(2026, 9, 11),
            "REAL,EQ,INE000000001,100,110,90,105,12,1260",
        ),
    )
    sources = client.get("/api/v1/data-sources")
    coverage = client.get("/api/v1/data-coverage")
    assert sources.status_code == 200
    assert coverage.status_code == 200
    assert sources.json()["mode"] == "MIXED"
    payload = coverage.json()
    assert payload["mode"] == "MIXED"
    assert payload["official_security_count"] == 1
    assert payload["demo_security_count"] == 1
    assert payload["official_daily_price_count"] == 1
    assert payload["earliest_official_price_session"] == "2026-09-11"
    assert len(payload["latest_artifacts"]) == 1
    assert payload["index_coverage"][0]["coverage_kind"] == "NONE"
    assert "OFFICIAL_PRICE_HISTORY_NOT_IMPORTED" not in payload["warnings"]


def test_missing_session_quality_check_uses_fixed_query_count(db):
    days = [date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11)]
    db.add_all(
        [
            TradingCalendar(
                exchange="NSE",
                trading_date=day,
                is_trading_day=True,
                session_type="REGULAR",
            )
            for day in days
        ]
    )
    securities = [_security(f"Q{number:04d}", number) for number in range(1, 51)]
    db.add_all(securities)
    db.flush()
    db.add_all(
        [
            DailyPrice(
                security_id=security.id,
                trading_date=day,
                open=100,
                high=110,
                low=90,
                close=105,
                volume=10,
                source="TEST",
            )
            for security in securities
            for day in (days[0], days[2])
        ]
    )
    db.commit()
    selects = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(db.get_bind(), "before_cursor_execute", count_selects)
    try:
        result = DataQualityService(db)._missing_session_check()
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", count_selects)
    assert result.issue_count == 50
    assert selects == 3


def test_backfill_is_bounded_resumable_and_skips_confirmed_non_sessions(db):
    db.add(_security("ALPHA", 1))
    db.add(
        TradingCalendar(
            exchange="NSE",
            trading_date=date(2026, 9, 8),
            is_trading_day=False,
            session_type="CLOSED",
            notes="Confirmed holiday fixture",
        )
    )
    db.commit()
    service = _service(db)
    existing_day = date(2026, 9, 9)
    service.import_artifact(
        ArtifactType.EOD_BHAVCOPY,
        existing_day,
        _price_csv(
            existing_day,
            "ALPHA,EQ,INE000000001,100,110,90,105,12,1260",
            name="existing.csv",
        ),
    )

    class FakeClient:
        def __init__(self):
            self.days: list[date] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def fetch(self, url: str) -> DownloadedArtifact:
            stamp = re.search(r"_(\d{8})_F_", url)
            assert stamp
            source_date = datetime.strptime(stamp.group(1), "%Y%m%d").date()
            self.days.append(source_date)
            return DownloadedArtifact(
                content=_price_csv(
                    source_date,
                    "ALPHA,EQ,INE000000001,100,110,90,105,12,1260",
                ).content,
                source_url=f"https://nsearchives.nseindia.com/fixture-{source_date}.csv",
                fetched_at=datetime(2026, 9, 14, tzinfo=UTC),
                content_type="text/csv",
            )

    fake = FakeClient()
    results = service.backfill(
        date(2026, 9, 7),
        date(2026, 9, 11),
        client_factory=lambda: fake,
    )
    assert fake.days == [date(2026, 9, 7), date(2026, 9, 10), date(2026, 9, 11)]
    assert len(results) == 3
    assert all(result.status == "SUCCEEDED" for result in results)
    with pytest.raises(ValueError, match="93 calendar days"):
        service.backfill(date(2026, 1, 1), date(2026, 4, 5), client_factory=lambda: fake)
