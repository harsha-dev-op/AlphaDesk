from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta

from sqlalchemy import delete, event, func, select, text

from app.database.session import SessionLocal, engine
from app.ingestion.nse.artifacts import ArtifactBytes
from app.ingestion.nse.definitions import ArtifactType
from app.ingestion.nse.service import NseIngestionService
from app.models import (
    CorporateAction,
    DailyPrice,
    DataIngestionRun,
    IndexMembership,
    IngestionIssue,
    Security,
    SourceArtifact,
    TradingCalendar,
)


SOURCE_PREFIX = "synthetic-benchmark:phase8:"
SYMBOL_PREFIX = "P8B"
SECURITY_DATE = date(1990, 1, 1)
LARGE_ARTIFACT_DATE = date(1990, 4, 2)


def _verify_identity() -> dict[str, object]:
    if engine.dialect.name != "postgresql" or engine.url.host not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise RuntimeError("Refusing benchmark because the configured database is not local PostgreSQL")
    with engine.connect() as connection:
        database, user, version = connection.execute(
            text("select current_database(), current_user, version()")
        ).one()
    if database != "alphadesk_dev" or user != "alphadesk":
        raise RuntimeError("Refusing benchmark because the database identity changed")
    return {"database": database, "user": user, "version": version}


class _BenchmarkStore:
    def retain(self, artifact: ArtifactBytes) -> str:
        return f"benchmark/{artifact.sha256[:16]}-{artifact.file_name}"


def _security_artifact(count: int) -> ArtifactBytes:
    lines = ["TckrSymb,SctySrs,FinInstrmNm,ISIN,DtOfListing,Normal Market Status"]
    lines.extend(
        f"{SYMBOL_PREFIX}{number:04d},EQ,Phase 8 Benchmark {number},IN8B{number:08d},1989-01-01,ACTIVE"
        for number in range(1, count + 1)
    )
    return ArtifactBytes(
        "phase8-security-benchmark.csv",
        ("\n".join(lines) + "\n").encode(),
        f"{SOURCE_PREFIX}security-master",
    )


def _price_artifact(source_date: date, count: int, sequence: int) -> ArtifactBytes:
    lines = [
        "TradDt,Sgmt,TckrSymb,SctySrs,ISIN,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol,TtlTrfVal"
    ]
    for number in range(1, count + 1):
        base = 100 + (number % 50) + sequence
        lines.append(
            f"{source_date.isoformat()},CM,{SYMBOL_PREFIX}{number:04d},EQ,IN8B{number:08d},"
            f"{base},{base + 3},{base - 2},{base + 1},{1000 + number},{(base + 1) * (1000 + number)}"
        )
    return ArtifactBytes(
        f"phase8-price-benchmark-{source_date.isoformat()}.csv",
        ("\n".join(lines) + "\n").encode(),
        f"{SOURCE_PREFIX}prices:{source_date.isoformat()}",
    )


def _weekday_dates(start: date, count: int) -> list[date]:
    output: list[date] = []
    current = start
    while len(output) < count:
        if current.weekday() < 5:
            output.append(current)
        current += timedelta(days=1)
    return output


def _namespace_counts(session) -> dict[str, int]:
    artifact_ids = select(SourceArtifact.id).where(
        SourceArtifact.source_locator.like(f"{SOURCE_PREFIX}%")
    )
    return {
        "securities": int(
            session.scalar(
                select(func.count()).select_from(Security).where(
                    Security.symbol.like(f"{SYMBOL_PREFIX}%")
                )
            )
            or 0
        ),
        "prices": int(
            session.scalar(
                select(func.count()).select_from(DailyPrice).where(
                    DailyPrice.source_artifact_id.in_(artifact_ids)
                )
            )
            or 0
        ),
        "artifacts": int(
            session.scalar(
                select(func.count()).select_from(SourceArtifact).where(
                    SourceArtifact.source_locator.like(f"{SOURCE_PREFIX}%")
                )
            )
            or 0
        ),
    }


def cleanup() -> dict[str, int]:
    with SessionLocal() as session:
        artifacts = list(
            session.execute(
                select(SourceArtifact.id, SourceArtifact.ingestion_run_id).where(
                    SourceArtifact.source_locator.like(f"{SOURCE_PREFIX}%")
                )
            )
        )
        artifact_ids = [row[0] for row in artifacts]
        run_ids = [row[1] for row in artifacts if row[1] is not None]
        if artifact_ids:
            session.execute(delete(IngestionIssue).where(IngestionIssue.source_artifact_id.in_(artifact_ids)))
            session.execute(delete(IndexMembership).where(IndexMembership.source_artifact_id.in_(artifact_ids)))
            session.execute(delete(CorporateAction).where(CorporateAction.source_artifact_id.in_(artifact_ids)))
            session.execute(delete(DailyPrice).where(DailyPrice.source_artifact_id.in_(artifact_ids)))
            session.execute(delete(TradingCalendar).where(TradingCalendar.source_artifact_id.in_(artifact_ids)))
        session.execute(delete(Security).where(Security.symbol.like(f"{SYMBOL_PREFIX}%")))
        if artifact_ids:
            session.execute(delete(SourceArtifact).where(SourceArtifact.id.in_(artifact_ids)))
        if run_ids:
            session.execute(delete(DataIngestionRun).where(DataIngestionRun.id.in_(run_ids)))
        session.commit()
        return _namespace_counts(session)


def _measured_import(service: NseIngestionService, source_date: date, artifact: ArtifactBytes) -> dict[str, object]:
    select_count = 0
    statement_count = 0

    def count_queries(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal select_count, statement_count
        statement_count += 1
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine, "before_cursor_execute", count_queries)
    try:
        started = time.perf_counter()
        result = service.import_artifact(ArtifactType.EOD_BHAVCOPY, source_date, artifact)
        wall_ms = (time.perf_counter() - started) * 1_000
    finally:
        event.remove(engine, "before_cursor_execute", count_queries)
    if result.status != "SUCCEEDED" or result.inserted != result.rows_parsed:
        raise RuntimeError("Synthetic ingestion benchmark did not complete cleanly")
    return {
        "rows": result.rows_parsed,
        "parse_ms": result.parse_ms,
        "persistence_ms": result.persistence_ms,
        "service_total_ms": result.total_ms,
        "wall_ms": round(wall_ms, 3),
        "select_count": select_count,
        "statement_count": statement_count,
        "checksum": result.checksum,
    }


def run() -> dict[str, object]:
    identity = _verify_identity()
    with SessionLocal() as session:
        if _namespace_counts(session) != {"securities": 0, "prices": 0, "artifacts": 0}:
            raise RuntimeError("Synthetic Phase 8 benchmark namespace is not empty")
        service = NseIngestionService(session, store=_BenchmarkStore())
        setup = service.import_artifact(
            ArtifactType.SECURITY_MASTER,
            SECURITY_DATE,
            _security_artifact(2_000),
        )
        if setup.status != "SUCCEEDED" or setup.inserted != 2_000:
            raise RuntimeError("Synthetic security-master benchmark setup failed")
        two_thousand = _measured_import(
            service,
            LARGE_ARTIFACT_DATE,
            _price_artifact(LARGE_ARTIFACT_DATE, 2_000, 0),
        )
        sessions: list[dict[str, object]] = []
        for sequence, source_date in enumerate(_weekday_dates(date(1990, 2, 1), 30), start=1):
            sessions.append(
                _measured_import(
                    service,
                    source_date,
                    _price_artifact(source_date, 500, sequence),
                )
            )
        fifteen_thousand = {
            "rows": sum(int(item["rows"]) for item in sessions),
            "parse_ms": round(sum(float(item["parse_ms"]) for item in sessions), 3),
            "persistence_ms": round(
                sum(float(item["persistence_ms"]) for item in sessions), 3
            ),
            "service_total_ms": round(
                sum(float(item["service_total_ms"]) for item in sessions), 3
            ),
            "wall_ms": round(sum(float(item["wall_ms"]) for item in sessions), 3),
            "select_count": sum(int(item["select_count"]) for item in sessions),
            "statement_count": sum(int(item["statement_count"]) for item in sessions),
            "sessions": len(sessions),
            "rows_per_session": 500,
        }
        counts_before_cleanup = _namespace_counts(session)
    return {
        "identity": identity,
        "security_setup": {"rows": setup.rows_parsed, "total_ms": setup.total_ms},
        "two_thousand_row_artifact": two_thousand,
        "thirty_by_five_hundred": fifteen_thousand,
        "namespace_before_cleanup": counts_before_cleanup,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synthetic PostgreSQL benchmark for Phase 8 NSE ingestion"
    )
    parser.add_argument("--cleanup-only", action="store_true")
    args = parser.parse_args()
    _verify_identity()
    if args.cleanup_only:
        print(json.dumps({"cleanup": cleanup()}, indent=2))
        return
    try:
        result = run()
    finally:
        cleaned = cleanup()
    if cleaned != {"securities": 0, "prices": 0, "artifacts": 0}:
        raise RuntimeError("Synthetic Phase 8 benchmark cleanup was incomplete")
    result["namespace_after_cleanup"] = cleaned
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
