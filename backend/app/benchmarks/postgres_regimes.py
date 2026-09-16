from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import UTC, date, datetime, time as clock, timedelta
from decimal import Decimal

from sqlalchemy import delete, event, func, insert, select, text

from app.database.session import SessionLocal, engine
from app.models import IndexDailyPrice, MarketIndex
from app.regimes.schemas import RegimeHistoryRequest
from app.regimes.service import MarketRegimeService


INDEX_PREFIX = "P11REGIMEBENCH"
NAMESPACE = uuid.UUID("04a829ed-61a0-42fb-9fe4-f2020696fb92")
SESSION_COUNTS = (300, 1000, 3000)


def _verify_identity() -> dict[str, object]:
    if engine.dialect.name != "postgresql" or engine.url.host not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise RuntimeError(
            "Refusing benchmark because the configured database is not local PostgreSQL"
        )
    with engine.connect() as connection:
        database, user, version = connection.execute(
            text("select current_database(), current_user, version()")
        ).one()
    if database != "alphadesk_dev" or user != "alphadesk":
        raise RuntimeError("Refusing benchmark because the database identity changed")
    return {"database": database, "user": user, "version": version}


def _sessions(count: int) -> list[date]:
    output: list[date] = []
    current = date(2010, 1, 4)
    while len(output) < count:
        if current.weekday() < 5:
            output.append(current)
        current += timedelta(days=1)
    return output


def _index_id(count: int) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"index:{count}")


def _namespace_counts(session) -> dict[str, int]:
    index_ids = select(MarketIndex.id).where(
        MarketIndex.symbol.like(f"{INDEX_PREFIX}%")
    )
    return {
        "indices": int(
            session.scalar(
                select(func.count()).select_from(MarketIndex).where(
                    MarketIndex.symbol.like(f"{INDEX_PREFIX}%")
                )
            )
            or 0
        ),
        "index_prices": int(
            session.scalar(
                select(func.count()).select_from(IndexDailyPrice).where(
                    IndexDailyPrice.index_id.in_(index_ids)
                )
            )
            or 0
        ),
    }


def cleanup() -> dict[str, int]:
    with SessionLocal() as session:
        index_ids = list(
            session.scalars(
                select(MarketIndex.id).where(
                    MarketIndex.symbol.like(f"{INDEX_PREFIX}%")
                )
            )
        )
        if index_ids:
            session.execute(
                delete(IndexDailyPrice).where(
                    IndexDailyPrice.index_id.in_(index_ids)
                )
            )
            session.execute(delete(MarketIndex).where(MarketIndex.id.in_(index_ids)))
        session.commit()
        return _namespace_counts(session)


def prepare_data() -> float:
    started = time.perf_counter()
    with SessionLocal() as session:
        if _namespace_counts(session) != {"indices": 0, "index_prices": 0}:
            raise RuntimeError("Synthetic Phase 11 benchmark namespace is not empty")
        for count in SESSION_COUNTS:
            index_id = _index_id(count)
            symbol = f"{INDEX_PREFIX}{count}"
            session.execute(
                insert(MarketIndex),
                [
                    {
                        "id": index_id,
                        "name": f"Phase 11 Regime Benchmark {count}",
                        "symbol": symbol,
                        "provider": "SYNTHETIC_BENCHMARK",
                        "exchange": "NSE",
                    }
                ],
            )
            close = Decimal("1000")
            rows = []
            for position, day in enumerate(_sessions(count)):
                prior = close
                if position % 420 in range(160, 205):
                    factor = Decimal("1.025") if position % 2 == 0 else Decimal("0.976")
                elif position % 420 >= 300:
                    factor = Decimal("0.9985")
                else:
                    factor = Decimal("1.0010")
                close = (close * factor).quantize(Decimal("0.000001"))
                rows.append(
                    {
                        "id": uuid.uuid5(NAMESPACE, f"price:{count}:{day}"),
                        "index_id": index_id,
                        "trading_date": day,
                        "open": prior,
                        "high": max(prior, close) * Decimal("1.003"),
                        "low": min(prior, close) * Decimal("0.997"),
                        "close": close,
                        "source_mode": "DEMO",
                        "source": "SYNTHETIC_BENCHMARK",
                        "available_at": datetime.combine(
                            day, clock(15, 30), tzinfo=UTC
                        ),
                    }
                )
            session.execute(insert(IndexDailyPrice), rows)
        session.commit()
    return (time.perf_counter() - started) * 1_000


def run_case(count: int) -> dict[str, object]:
    sessions = _sessions(count)
    request = RegimeHistoryRequest(
        benchmark=f"{INDEX_PREFIX}{count}",
        start_date=sessions[0],
        end_date=sessions[-1],
        source_mode="DEMO",
    )
    statements = 0
    selects = 0

    def count_statements(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal statements, selects
        statements += 1
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(engine, "before_cursor_execute", count_statements)
    try:
        started = time.perf_counter()
        with SessionLocal() as session:
            result = MarketRegimeService(session).history(request)
        wall_ms = (time.perf_counter() - started) * 1_000
    finally:
        event.remove(engine, "before_cursor_execute", count_statements)
    return {
        "session_count": count,
        "wall_ms": round(wall_ms, 3),
        "repository_load_ms": result.timings.repository_load_ms,
        "feature_generation_ms": result.timings.feature_generation_ms,
        "classification_ms": result.timings.classification_ms,
        "statement_count": statements,
        "select_count": selects,
        "transition_count": len(result.transitions),
        "timeline_fingerprint": result.regime_timeline_fingerprint,
    }


def run() -> dict[str, object]:
    identity = _verify_identity()
    preparation_ms = prepare_data()
    cases = [run_case(count) for count in SESSION_COUNTS]
    with SessionLocal() as session:
        before_cleanup = _namespace_counts(session)
    return {
        "identity": identity,
        "synthetic_data_preparation_ms": round(preparation_ms, 3),
        "cases": cases,
        "namespace_before_cleanup": before_cleanup,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synthetic PostgreSQL benchmark for Phase 11 market regimes"
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
    if cleaned != {"indices": 0, "index_prices": 0}:
        raise RuntimeError("Synthetic Phase 11 benchmark cleanup was incomplete")
    result["namespace_after_cleanup"] = cleaned
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
