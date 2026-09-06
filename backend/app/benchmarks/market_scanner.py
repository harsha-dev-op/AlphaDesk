from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from datetime import UTC, datetime, time as day_time
from decimal import Decimal
from uuid import NAMESPACE_DNS, uuid5

from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.benchmarks.technical_features import build_dataset
from app.database.base import Base
from app.models import (
    CorporateAction,
    DailyPrice,
    DataIngestionRun,
    IndexMembership,
    MarketIndex,
    Security,
    TradingCalendar,
)
from app.scanner import ScannerService
from app.schemas.scanner import MarketScanRequest
from app.technical.service import MARKET_TIMEZONE


def _seed_database(session: Session, securities: int, rows: int) -> tuple[str, object]:
    first_prices, _ = build_dataset(0, rows)
    security_ids = [uuid5(NAMESPACE_DNS, f"alphadesk-phase3-security-{number}") for number in range(securities)]
    index_id = uuid5(NAMESPACE_DNS, f"alphadesk-phase3-index-{securities}-{rows}")
    session.execute(
        insert(Security),
        [
            {
                "id": security_id,
                "exchange": "NSE",
                "symbol": f"BENCH{number:04d}",
                "trading_symbol": f"BENCH{number:04d}-EQ",
                "company_name": f"Benchmark Security {number:04d}",
                "security_type": "EQUITY",
                "currency": "INR",
                "is_active": True,
            }
            for number, security_id in enumerate(security_ids)
        ],
    )
    session.execute(
        insert(MarketIndex),
        [
            {
                "id": index_id,
                "name": "Phase 3 Benchmark Universe",
                "symbol": "P3BENCH",
                "provider": "ALPHADESK_SYNTHETIC",
                "exchange": "NSE",
            }
        ],
    )
    session.execute(
        insert(IndexMembership),
        [
            {
                "index_id": index_id,
                "security_id": security_id,
                "valid_from": first_prices[0].trading_date,
                "valid_to": None,
                "source": "ALPHADESK_SYNTHETIC",
            }
            for security_id in security_ids
        ],
    )
    session.execute(
        insert(TradingCalendar),
        [
            {
                "exchange": "NSE",
                "trading_date": price.trading_date,
                "is_trading_day": True,
                "session_open": day_time(9, 15),
                "session_close": day_time(15, 30),
                "session_type": "REGULAR",
            }
            for price in first_prices
        ],
    )
    for number, security_id in enumerate(security_ids):
        prices, actions = build_dataset(number, rows)
        session.execute(
            insert(DailyPrice),
            [
                {
                    "security_id": security_id,
                    "trading_date": price.trading_date,
                    "open": price.open,
                    "high": price.high,
                    "low": price.low,
                    "close": price.close,
                    "volume": price.volume,
                    "traded_value": price.traded_value,
                    "source": price.source,
                }
                for price in prices
            ],
        )
        session.execute(
            insert(CorporateAction),
            [
                {
                    "security_id": security_id,
                    "action_type": action.action_type,
                    "ex_date": action.ex_date,
                    "ratio_numerator": action.ratio_numerator,
                    "ratio_denominator": action.ratio_denominator,
                    "source": "ALPHADESK_SYNTHETIC",
                }
                for action in actions
            ],
        )
    session.add(
        DataIngestionRun(
            dataset_code="phase3_scanner_benchmark",
            dataset_version="deterministic-v1",
            provider="ALPHADESK_SYNTHETIC",
            status="SUCCESS",
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
            records_written=securities * rows,
        )
    )
    session.commit()
    return "P3BENCH", first_prices[-1].trading_date


def run_case(securities: int, rows: int, adjustment_policy: str) -> dict[str, object]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        universe, observation_date = _seed_database(session, securities, rows)
        database_ms = {"universe": 0.0, "features": 0.0}
        query_count = 0

        def before_cursor_execute(_connection, _cursor, _statement, _parameters, context, _executemany):
            context._alphadesk_started = time.perf_counter()

        def after_cursor_execute(_connection, _cursor, statement, _parameters, context, _executemany):
            nonlocal query_count
            elapsed = (time.perf_counter() - context._alphadesk_started) * 1_000
            normalized = statement.lower()
            phase = "universe" if "index_memberships" in normalized or "indices." in normalized else "features"
            database_ms[phase] += elapsed
            if normalized.lstrip().startswith("select"):
                query_count += 1

        event.listen(engine, "before_cursor_execute", before_cursor_execute)
        event.listen(engine, "after_cursor_execute", after_cursor_execute)
        request = MarketScanRequest(
            universe=universe,
            observation_date=observation_date,
            as_of=datetime.combine(observation_date, day_time(15, 30), tzinfo=MARKET_TIMEZONE),
            adjustment_policy=adjustment_policy,
            filters=[
                {"feature": "RET_20D", "operator": ">", "value": "-1"},
                {"feature": "RSI_14", "operator": "between", "value": "0", "upper_value": "100"},
                {"feature": "AVG_VOLUME_20", "operator": ">=", "value": "0"},
            ],
        )
        started = time.perf_counter()
        response = ScannerService(session).scan(request)
        service_elapsed_ms = (time.perf_counter() - started) * 1_000
        serialization_started = time.perf_counter()
        serialized = response.model_dump_json()
        serialization_ms = (time.perf_counter() - serialization_started) * 1_000
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
        event.remove(engine, "after_cursor_execute", after_cursor_execute)

    engine.dispose()
    feature_database_ms = database_ms["features"]
    return {
        "securities": securities,
        "rows_per_security": rows,
        "rows_loaded": securities * rows,
        "conditions": 3,
        "adjustment_policy": adjustment_policy,
        "result_count": response.matched_count,
        "universe_resolution_ms": response.timings.universe_resolution_ms,
        "database_cursor_ms": round(database_ms["universe"] + feature_database_ms, 3),
        "technical_compute_and_materialization_ms": round(
            max(0.0, response.timings.feature_computation_ms - feature_database_ms),
            3,
        ),
        "predicate_evaluation_ms": response.timings.predicate_evaluation_ms,
        "response_build_ms": response.timings.response_build_ms,
        "serialization_ms": round(serialization_ms, 3),
        "service_elapsed_ms": round(service_elapsed_ms, 3),
        "total_with_serialization_ms": round(service_elapsed_ms + serialization_ms, 3),
        "query_count": query_count,
        "serialized_bytes": len(serialized.encode()),
        "scan_fingerprint": response.scan_fingerprint,
        "payload_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the complete point-in-time Market Scanner service.")
    parser.add_argument("--securities", nargs="+", type=int, default=[1, 10, 50, 200])
    parser.add_argument("--rows", type=int, default=2_500)
    parser.add_argument("--adjustment-policy", choices=("RAW", "ADJUSTED"), default="ADJUSTED")
    args = parser.parse_args()
    results = [run_case(count, args.rows, args.adjustment_policy) for count in args.securities]
    print(
        json.dumps(
            {
                "environment": {
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "database": "isolated in-memory SQLite",
                    "synthetic_data": True,
                    "network_access": False,
                    "feature_set": "ALPHADESK_CORE_TECHNICAL",
                    "feature_set_version": "1",
                },
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
