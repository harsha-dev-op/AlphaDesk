from __future__ import annotations

import argparse
import json
import math
import sys
import time
import uuid
from datetime import UTC, date, datetime, time as clock_time, timedelta
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, inspect, insert, select

from app.benchmarks.postgres_scanner import _instrumented_run, _profile, verify_identity
from app.database.session import SessionLocal, engine
from app.main import app
from app.models import (
    CorporateAction,
    DailyPrice,
    DataIngestionRun,
    IndexMembership,
    MarketIndex,
    Security,
    TradingCalendar,
)
from app.schemas.scanner import MarketScanRequest

BENCHMARK_PROVIDER = "ALPHADESK_SYNTHETIC_BENCH"
BENCHMARK_DATASET = "phase36_synthetic_scanner"
BENCHMARK_VERSION = "v1"
BENCHMARK_EXCHANGE = "XADB"
SECURITY_COUNT = 500
SESSION_COUNT = 300
UNIVERSE_SIZES = (1, 10, 50, 200, 500)
PROFILE_SIZES = (50, 200, 500)
TARGET_SECONDS = {50: 2, 200: 5, 500: 10}
IST = ZoneInfo("Asia/Kolkata")
UUID_NAMESPACE = uuid.UUID("f778b2bc-f63b-46f9-b688-2f9e02c30dca")
FILTERS = [
    {"feature": "RET_20D", "operator": ">", "value": "-1"},
    {"feature": "RSI_14", "operator": "between", "value": "0", "upper_value": "100"},
    {"feature": "AVG_VOLUME_20", "operator": ">=", "value": "0"},
]


def _stable_id(kind: str, value: str) -> uuid.UUID:
    return uuid.uuid5(UUID_NAMESPACE, f"{kind}:{value}")


def _symbol(number: int) -> str:
    return f"P36B{number:04d}"


def _index_symbol(size: int) -> str:
    return f"P36BENCH{size:03d}"


def _sessions() -> list[date]:
    sessions: list[date] = []
    current = date(2024, 1, 2)
    while len(sessions) < SESSION_COUNT:
        if current.weekday() < 5:
            sessions.append(current)
        current += timedelta(days=1)
    return sessions


SESSIONS = _sessions()
OBSERVATION_DATE = SESSIONS[-1]
AS_OF = datetime.combine(OBSERVATION_DATE, clock_time(15, 30), tzinfo=IST)


def _schema_guard() -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("corporate_actions")}
    required = {"source_published_at", "available_at", "supersedes_action_id"}
    missing = required - columns
    if missing:
        raise RuntimeError(f"Phase 3.6 migration is not applied; missing columns: {sorted(missing)}")


def _expected_security_symbols() -> list[str]:
    return [_symbol(number) for number in range(1, SECURITY_COUNT + 1)]


def _namespace_counts(session) -> dict[str, int]:
    symbols = _expected_security_symbols()
    index_symbols = [_index_symbol(size) for size in UNIVERSE_SIZES]
    security_ids = list(
        session.scalars(
            select(Security.id).where(
                Security.exchange == BENCHMARK_EXCHANGE,
                Security.symbol.in_(symbols),
            )
        )
    )
    index_ids = list(
        session.scalars(
            select(MarketIndex.id).where(
                MarketIndex.provider == BENCHMARK_PROVIDER,
                MarketIndex.symbol.in_(index_symbols),
            )
        )
    )
    return {
        "securities": len(security_ids),
        "daily_prices": int(
            session.scalar(
                select(func.count()).select_from(DailyPrice).where(DailyPrice.security_id.in_(security_ids))
            )
            or 0
        )
        if security_ids
        else 0,
        "corporate_actions": int(
            session.scalar(
                select(func.count()).select_from(CorporateAction).where(CorporateAction.security_id.in_(security_ids))
            )
            or 0
        )
        if security_ids
        else 0,
        "indices": len(index_ids),
        "index_memberships": int(
            session.scalar(
                select(func.count()).select_from(IndexMembership).where(IndexMembership.index_id.in_(index_ids))
            )
            or 0
        )
        if index_ids
        else 0,
        "trading_calendar": int(
            session.scalar(
                select(func.count()).select_from(TradingCalendar).where(TradingCalendar.exchange == BENCHMARK_EXCHANGE)
            )
            or 0
        ),
        "ingestion_runs": int(
            session.scalar(
                select(func.count())
                .select_from(DataIngestionRun)
                .where(
                    DataIngestionRun.dataset_code == BENCHMARK_DATASET,
                    DataIngestionRun.provider == BENCHMARK_PROVIDER,
                )
            )
            or 0
        ),
    }


def _expected_counts() -> dict[str, int]:
    return {
        "securities": SECURITY_COUNT,
        "daily_prices": SECURITY_COUNT * SESSION_COUNT,
        "corporate_actions": SECURITY_COUNT // 10,
        "indices": len(UNIVERSE_SIZES),
        "index_memberships": sum(UNIVERSE_SIZES),
        "trading_calendar": SESSION_COUNT,
        "ingestion_runs": 1,
    }


def _validate_namespace_ownership(session) -> None:
    symbols = _expected_security_symbols()
    conflicting_security = session.scalar(
        select(Security).where(
            Security.symbol.in_(symbols),
            (
                (Security.exchange != BENCHMARK_EXCHANGE)
                | (~Security.company_name.like("AlphaDesk Phase 3.6 Benchmark %"))
            ),
        )
    )
    if conflicting_security is not None:
        raise RuntimeError("Benchmark security namespace collides with non-benchmark data")
    index_symbols = [_index_symbol(size) for size in UNIVERSE_SIZES]
    conflicting_index = session.scalar(
        select(MarketIndex).where(
            MarketIndex.symbol.in_(index_symbols),
            MarketIndex.provider != BENCHMARK_PROVIDER,
        )
    )
    if conflicting_index is not None:
        raise RuntimeError("Benchmark index namespace collides with non-benchmark data")


def setup_dataset() -> dict[str, object]:
    identity = verify_identity()
    _schema_guard()
    with SessionLocal() as session:
        _validate_namespace_ownership(session)
        existing = _namespace_counts(session)
        expected = _expected_counts()
        if existing == expected:
            return {"status": "ALREADY_PRESENT", "identity": identity, "counts": existing}
        if any(existing.values()):
            raise RuntimeError(
                "Benchmark namespace is partially populated; run the guarded cleanup before setup"
            )

        security_rows = []
        for number in range(1, SECURITY_COUNT + 1):
            symbol = _symbol(number)
            security_rows.append(
                {
                    "id": _stable_id("security", symbol),
                    "exchange": BENCHMARK_EXCHANGE,
                    "symbol": symbol,
                    "trading_symbol": f"{symbol}-EQ",
                    "company_name": f"AlphaDesk Phase 3.6 Benchmark {number:04d}",
                    "isin": f"P36B{number:08d}",
                    "security_type": "EQUITY",
                    "sector": f"Synthetic sector {number % 11:02d}",
                    "industry": f"Synthetic industry {number % 23:02d}",
                    "currency": "INR",
                    "listing_date": SESSIONS[0] - timedelta(days=365),
                    "is_active": True,
                }
            )
        session.execute(insert(Security), security_rows)

        calendar_rows = [
            {
                "id": _stable_id("calendar", day.isoformat()),
                "exchange": BENCHMARK_EXCHANGE,
                "trading_date": day,
                "is_trading_day": True,
                "session_open": clock_time(9, 15),
                "session_close": clock_time(15, 30),
                "session_type": "REGULAR",
                "notes": "Deterministic Phase 3.6 benchmark session",
            }
            for day in SESSIONS
        ]
        session.execute(insert(TradingCalendar), calendar_rows)

        for first_number in range(1, SECURITY_COUNT + 1, 25):
            price_rows = []
            for number in range(first_number, min(first_number + 25, SECURITY_COUNT + 1)):
                security_id = _stable_id("security", _symbol(number))
                base = Decimal(60 + (number % 40) * 4)
                slope = Decimal((number % 9) - 4) / Decimal("50")
                split_security = number % 10 == 0
                for session_number, trading_date in enumerate(SESSIONS):
                    cycle = Decimal(((session_number + number * 3) % 19) - 9) * Decimal("0.07")
                    regime = Decimal((session_number // 60) * ((number % 3) - 1)) * Decimal("0.15")
                    economic_close = base + Decimal(session_number) * slope + cycle + regime
                    raw_close = economic_close * 2 if split_security and session_number < 180 else economic_close
                    raw_open = raw_close + Decimal(((session_number + number) % 5) - 2) * Decimal("0.03")
                    high = max(raw_open, raw_close) + Decimal("0.65")
                    low = min(raw_open, raw_close) - Decimal("0.65")
                    volume = 120_000 + number * 113 + session_number * 271
                    if split_security and session_number >= 180:
                        volume *= 2
                    close = raw_close.quantize(Decimal("0.0001"))
                    price_rows.append(
                        {
                            "id": _stable_id("price", f"{number}:{session_number}"),
                            "security_id": security_id,
                            "trading_date": trading_date,
                            "open": raw_open.quantize(Decimal("0.0001")),
                            "high": high.quantize(Decimal("0.0001")),
                            "low": low.quantize(Decimal("0.0001")),
                            "close": close,
                            "volume": volume,
                            "traded_value": (close * volume).quantize(Decimal("0.01")),
                            "source": BENCHMARK_PROVIDER,
                        }
                    )
            session.execute(insert(DailyPrice), price_rows)

        announcement_date = SESSIONS[170]
        published_at = datetime.combine(announcement_date, clock_time(18), tzinfo=IST)
        action_rows = []
        for number in range(10, SECURITY_COUNT + 1, 10):
            action_rows.append(
                {
                    "id": _stable_id("action", str(number)),
                    "security_id": _stable_id("security", _symbol(number)),
                    "action_type": "STOCK_SPLIT",
                    "announcement_date": announcement_date,
                    "ex_date": SESSIONS[180],
                    "record_date": SESSIONS[180],
                    "ratio_numerator": Decimal("2"),
                    "ratio_denominator": Decimal("1"),
                    "notes": "Deterministic synthetic 2-for-1 split",
                    "source": BENCHMARK_PROVIDER,
                    "source_published_at": published_at,
                    "available_at": published_at,
                    "ingested_at": published_at,
                }
            )
        session.execute(insert(CorporateAction), action_rows)

        membership_rows = []
        for size in UNIVERSE_SIZES:
            index_symbol = _index_symbol(size)
            index_id = _stable_id("index", index_symbol)
            session.execute(
                insert(MarketIndex),
                [
                    {
                        "id": index_id,
                        "name": f"AlphaDesk Phase 3.6 Synthetic {size}",
                        "symbol": index_symbol,
                        "provider": BENCHMARK_PROVIDER,
                        "exchange": BENCHMARK_EXCHANGE,
                    }
                ],
            )
            for number in range(1, size + 1):
                membership_rows.append(
                    {
                        "id": _stable_id("membership", f"{size}:{number}"),
                        "index_id": index_id,
                        "security_id": _stable_id("security", _symbol(number)),
                        "valid_from": SESSIONS[0],
                        "source": BENCHMARK_PROVIDER,
                    }
                )
        session.execute(insert(IndexMembership), membership_rows)
        session.execute(
            insert(DataIngestionRun),
            [
                {
                    "id": _stable_id("ingestion", BENCHMARK_VERSION),
                    "dataset_code": BENCHMARK_DATASET,
                    "dataset_version": BENCHMARK_VERSION,
                    "provider": BENCHMARK_PROVIDER,
                    "status": "SUCCESS",
                    "started_at": datetime.now(UTC),
                    "completed_at": datetime.now(UTC),
                    "records_written": SECURITY_COUNT * SESSION_COUNT + SECURITY_COUNT,
                }
            ],
        )
        session.commit()
        counts = _namespace_counts(session)
        if counts != expected:
            raise RuntimeError(f"Benchmark setup count mismatch: {counts} != {expected}")
    return {"status": "CREATED", "identity": identity, "counts": counts}


def _request(size: int) -> MarketScanRequest:
    return MarketScanRequest(
        universe=_index_symbol(size),
        observation_date=OBSERVATION_DATE,
        as_of=AS_OF,
        adjustment_policy="ADJUSTED",
        filters=FILTERS,
    )


def _api_timing(request: MarketScanRequest) -> dict[str, object]:
    with TestClient(app) as client:
        started = time.perf_counter()
        response = client.post(
            "/api/v1/scanner/scan",
            json=request.model_dump(mode="json"),
        )
        elapsed_ms = (time.perf_counter() - started) * 1_000
    if response.status_code != 200:
        raise RuntimeError(f"Benchmark API failed with HTTP {response.status_code}")
    payload = response.json()
    return {
        "api_ms": round(elapsed_ms, 3),
        "matched_count": payload["matched_count"],
        "scan_fingerprint": payload["scan_fingerprint"],
        "serialized_bytes": len(response.content),
    }


def run_benchmarks(
    runs: int,
    sizes: tuple[int, ...] = UNIVERSE_SIZES,
) -> dict[str, object]:
    identity = verify_identity()
    _schema_guard()
    if runs < 3 or runs > 6:
        raise ValueError("runs must be between 3 and 6")
    unsupported_sizes = set(sizes) - set(UNIVERSE_SIZES)
    if unsupported_sizes:
        raise ValueError(f"Unsupported benchmark sizes: {sorted(unsupported_sizes)}")
    with SessionLocal() as session:
        _validate_namespace_ownership(session)
        counts = _namespace_counts(session)
    if counts != _expected_counts():
        raise RuntimeError("Complete benchmark dataset is required before running measurements")

    cases: dict[str, object] = {}
    for size in sizes:
        request = _request(size)
        individual = [_instrumented_run(request) for _ in range(runs)]
        fingerprints = {str(result["scan_fingerprint"]) for result in individual}
        if len(fingerprints) != 1:
            raise RuntimeError(f"Non-deterministic scanner fingerprint at size {size}")
        expected_queries = 3 + 4 * math.ceil(size / 50)
        if any(result["query_count"] != expected_queries for result in individual):
            raise RuntimeError(f"Unexpected query count at size {size}")
        if any(result["result_count"] != size for result in individual):
            raise RuntimeError(f"Unexpected result count at size {size}")

        warm = individual[1:]
        api = _api_timing(request)
        if api["scan_fingerprint"] not in fingerprints:
            raise RuntimeError(f"API and direct-service fingerprints differ at size {size}")
        case: dict[str, object] = {
            "members": size,
            "price_rows": size * SESSION_COUNT,
            "action_rows": size // 10,
            "feature_count": 36,
            "condition_count": len(FILTERS),
            "expected_queries": expected_queries,
            "individual_runs": individual,
            "summary": {
                "first_service_ms": individual[0]["service_ms"],
                "warm_service_median_ms": round(
                    median(float(result["service_ms"]) for result in warm),
                    3,
                ),
                "warm_service_range_ms": [
                    min(float(result["service_ms"]) for result in warm),
                    max(float(result["service_ms"]) for result in warm),
                ],
                "warm_total_with_serialization_median_ms": round(
                    median(float(result["total_with_serialization_ms"]) for result in warm),
                    3,
                ),
                **api,
            },
        }
        if size in PROFILE_SIZES:
            case["profile_top_project_functions"] = _profile(request)
        if size in TARGET_SECONDS:
            case["target_ms"] = TARGET_SECONDS[size] * 1_000
            case["target_met"] = (
                float(case["summary"]["warm_total_with_serialization_median_ms"])
                <= TARGET_SECONDS[size] * 1_000
                and float(api["api_ms"]) <= TARGET_SECONDS[size] * 1_000
            )
        cases[str(size)] = case

    return {
        "identity": identity,
        "runtime": f"CPython {sys.version.split()[0]} direct service plus in-process FastAPI TestClient",
        "dataset": {
            "code": BENCHMARK_DATASET,
            "version": BENCHMARK_VERSION,
            "provider": BENCHMARK_PROVIDER,
            "exchange": BENCHMARK_EXCHANGE,
            "sessions_per_security": SESSION_COUNT,
            "observation_date": OBSERVATION_DATE.isoformat(),
            "as_of": AS_OF.isoformat(),
            "counts": counts,
        },
        "cases": cases,
        "measurement_notes": [
            "The first run per size is process-level cold only; PostgreSQL and operating-system caches are not cleared.",
            "Warm medians exclude the first direct-service run; the API sample follows warmed direct runs.",
            "SQL cursor timing excludes ORM transfer/materialization/grouping, which is reported as a repository residual.",
            "Profiles are captured once for 50, 200, and 500 members and are diagnostic rather than latency samples.",
        ],
    }


def run_api_samples(runs: int, sizes: tuple[int, ...]) -> dict[str, object]:
    identity = verify_identity()
    _schema_guard()
    if runs < 3 or runs > 6:
        raise ValueError("runs must be between 3 and 6")
    unsupported_sizes = set(sizes) - set(UNIVERSE_SIZES)
    if unsupported_sizes:
        raise ValueError(f"Unsupported benchmark sizes: {sorted(unsupported_sizes)}")
    with SessionLocal() as session:
        _validate_namespace_ownership(session)
        if _namespace_counts(session) != _expected_counts():
            raise RuntimeError("Complete benchmark dataset is required before API sampling")

    cases: dict[str, object] = {}
    for size in sizes:
        samples = [_api_timing(_request(size)) for _ in range(runs)]
        fingerprints = {str(sample["scan_fingerprint"]) for sample in samples}
        if len(fingerprints) != 1:
            raise RuntimeError(f"Non-deterministic API scanner fingerprint at size {size}")
        warm_ms = [float(sample["api_ms"]) for sample in samples[1:]]
        cases[str(size)] = {
            "samples": samples,
            "first_api_ms": samples[0]["api_ms"],
            "warm_api_median_ms": round(median(warm_ms), 3),
            "warm_api_range_ms": [min(warm_ms), max(warm_ms)],
            "target_ms": TARGET_SECONDS.get(size, 0) * 1_000 or None,
            "target_met": (
                median(warm_ms) <= TARGET_SECONDS[size] * 1_000
                if size in TARGET_SECONDS
                else None
            ),
        }
    return {"identity": identity, "cases": cases}


def cleanup_dataset() -> dict[str, object]:
    identity = verify_identity()
    _schema_guard()
    expected_symbols = _expected_security_symbols()
    expected_index_symbols = [_index_symbol(size) for size in UNIVERSE_SIZES]
    with SessionLocal() as session:
        _validate_namespace_ownership(session)
        before = _namespace_counts(session)
        security_ids = list(
            session.scalars(
                select(Security.id).where(
                    Security.exchange == BENCHMARK_EXCHANGE,
                    Security.symbol.in_(expected_symbols),
                    Security.company_name.like("AlphaDesk Phase 3.6 Benchmark %"),
                )
            )
        )
        index_ids = list(
            session.scalars(
                select(MarketIndex.id).where(
                    MarketIndex.provider == BENCHMARK_PROVIDER,
                    MarketIndex.symbol.in_(expected_index_symbols),
                )
            )
        )
        if index_ids:
            session.execute(delete(IndexMembership).where(IndexMembership.index_id.in_(index_ids)))
            session.execute(delete(MarketIndex).where(MarketIndex.id.in_(index_ids)))
        if security_ids:
            session.execute(delete(CorporateAction).where(CorporateAction.security_id.in_(security_ids)))
            session.execute(delete(DailyPrice).where(DailyPrice.security_id.in_(security_ids)))
            session.execute(delete(Security).where(Security.id.in_(security_ids)))
        session.execute(
            delete(TradingCalendar).where(TradingCalendar.exchange == BENCHMARK_EXCHANGE)
        )
        session.execute(
            delete(DataIngestionRun).where(
                DataIngestionRun.dataset_code == BENCHMARK_DATASET,
                DataIngestionRun.provider == BENCHMARK_PROVIDER,
            )
        )
        session.commit()
        after = _namespace_counts(session)
        if any(after.values()):
            raise RuntimeError(f"Benchmark cleanup left namespaced rows: {after}")
    return {
        "status": "REMOVED",
        "identity": identity,
        "removed": before,
        "remaining": after,
    }


def status() -> dict[str, object]:
    identity = verify_identity()
    _schema_guard()
    with SessionLocal() as session:
        _validate_namespace_ownership(session)
        counts = _namespace_counts(session)
    return {"identity": identity, "counts": counts, "expected": _expected_counts()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Guarded synthetic PostgreSQL scanner scale benchmark"
    )
    parser.add_argument("mode", choices=("setup", "run", "api", "cleanup", "status"))
    parser.add_argument("--runs", type=int, default=4)
    parser.add_argument("--sizes", type=int, nargs="+", default=list(UNIVERSE_SIZES))
    args = parser.parse_args()
    if args.mode == "setup":
        output = setup_dataset()
    elif args.mode == "run":
        output = run_benchmarks(args.runs, tuple(args.sizes))
    elif args.mode == "api":
        output = run_api_samples(args.runs, tuple(args.sizes))
    elif args.mode == "cleanup":
        output = cleanup_dataset()
    else:
        output = status()
    print(json.dumps(output, indent=2, default=str))
    engine.dispose()


if __name__ == "__main__":
    main()
