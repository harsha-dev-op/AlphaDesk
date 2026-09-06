from __future__ import annotations

import argparse
import json
import math
import sys
import time
from statistics import median

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.benchmarks.postgres_scanner import verify_identity
from app.benchmarks.postgres_scanner_scale import (
    AS_OF,
    BENCHMARK_DATASET,
    BENCHMARK_EXCHANGE,
    BENCHMARK_PROVIDER,
    BENCHMARK_VERSION,
    OBSERVATION_DATE,
    PROFILE_SIZES,
    SESSION_COUNT,
    TARGET_SECONDS,
    _expected_counts,
    _index_symbol,
    _namespace_counts,
    _schema_guard,
    _validate_namespace_ownership,
)
from app.database.session import SessionLocal, engine
from app.main import app
from app.schemas.strategies import StrategyEvaluationRequest
from app.strategies.service import StrategyService


def _request(size: int) -> StrategyEvaluationRequest:
    return StrategyEvaluationRequest(
        strategy_code="BREAKOUT_20D",
        strategy_version="1",
        universe=_index_symbol(size),
        observation_date=OBSERVATION_DATE,
        as_of=AS_OF,
        adjustment_policy="ADJUSTED",
    )


def _instrumented_run(request: StrategyEvaluationRequest) -> dict[str, object]:
    query_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal query_count
        if statement.lstrip().lower().startswith("select"):
            query_count += 1

    event.listen(engine, "after_cursor_execute", count_selects)
    try:
        with SessionLocal() as session:
            started = time.perf_counter()
            response = StrategyService(session).evaluate(request)
            service_ms = (time.perf_counter() - started) * 1_000
            serialization_started = time.perf_counter()
            serialized = response.model_dump_json()
            serialization_ms = (time.perf_counter() - serialization_started) * 1_000
    finally:
        event.remove(engine, "after_cursor_execute", count_selects)
    return {
        "service_ms": round(service_ms, 3),
        "serialization_ms": round(serialization_ms, 3),
        "total_with_serialization_ms": round(service_ms + serialization_ms, 3),
        "query_count": query_count,
        "result_count": len(response.results),
        "matched_count": response.matched_count,
        "serialized_bytes": len(serialized.encode()),
        "evaluation_fingerprint": response.evaluation_fingerprint,
        "service_phases_ms": response.timings.model_dump(),
    }


def _api_timing(request: StrategyEvaluationRequest) -> dict[str, object]:
    with TestClient(app) as client:
        started = time.perf_counter()
        response = client.post(
            "/api/v1/strategies/evaluate",
            json=request.model_dump(mode="json"),
        )
        elapsed_ms = (time.perf_counter() - started) * 1_000
    if response.status_code != 200:
        raise RuntimeError(f"Strategy benchmark API failed with HTTP {response.status_code}")
    payload = response.json()
    return {
        "api_ms": round(elapsed_ms, 3),
        "result_count": len(payload["results"]),
        "matched_count": payload["matched_count"],
        "evaluation_fingerprint": payload["evaluation_fingerprint"],
        "serialized_bytes": len(response.content),
    }


def run_benchmarks(runs: int, sizes: tuple[int, ...] = PROFILE_SIZES) -> dict[str, object]:
    identity = verify_identity()
    _schema_guard()
    if runs < 3 or runs > 6:
        raise ValueError("runs must be between 3 and 6")
    unsupported = set(sizes) - set(PROFILE_SIZES)
    if unsupported:
        raise ValueError(f"Unsupported strategy benchmark sizes: {sorted(unsupported)}")
    with SessionLocal() as session:
        _validate_namespace_ownership(session)
        counts = _namespace_counts(session)
    if counts != _expected_counts():
        raise RuntimeError("Complete namespaced Phase 3.6 scale fixture is required")

    cases: dict[str, object] = {}
    for size in sizes:
        request = _request(size)
        individual = [_instrumented_run(request) for _ in range(runs)]
        fingerprints = {str(run["evaluation_fingerprint"]) for run in individual}
        if len(fingerprints) != 1:
            raise RuntimeError(f"Non-deterministic strategy fingerprint at size {size}")
        expected_queries = 3 + 4 * math.ceil(size / 50)
        if any(run["query_count"] != expected_queries for run in individual):
            raise RuntimeError(f"Unexpected strategy query count at size {size}")
        if any(run["result_count"] != size for run in individual):
            raise RuntimeError(f"Unexpected strategy result count at size {size}")
        api = _api_timing(request)
        if api["evaluation_fingerprint"] not in fingerprints:
            raise RuntimeError(f"API and direct strategy fingerprints differ at size {size}")
        warm = individual[1:]
        warm_total = [float(run["total_with_serialization_ms"]) for run in warm]
        cases[str(size)] = {
            "members": size,
            "price_rows": size * SESSION_COUNT,
            "feature_count": 38,
            "condition_count": 6,
            "expected_queries": expected_queries,
            "individual_runs": individual,
            "summary": {
                "first_service_ms": individual[0]["service_ms"],
                "warm_service_median_ms": round(median(float(run["service_ms"]) for run in warm), 3),
                "warm_total_with_serialization_median_ms": round(median(warm_total), 3),
                "warm_total_range_ms": [min(warm_total), max(warm_total)],
                **api,
            },
            "target_ms": TARGET_SECONDS[size] * 1_000,
            "target_met": median(warm_total) <= TARGET_SECONDS[size] * 1_000 and float(api["api_ms"]) <= TARGET_SECONDS[size] * 1_000,
        }
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
        "strategy": "BREAKOUT_20D v1",
        "cases": cases,
        "measurement_notes": [
            "The first run per size is process-level cold and excluded from warm medians.",
            "The API sample follows warmed direct-service runs.",
            "Every historical member is serialized with all six explainable condition results.",
            "The fixture lifecycle remains owned by postgres_scanner_scale setup and cleanup.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only PostgreSQL strategy-engine scale benchmark")
    parser.add_argument("--runs", type=int, default=4)
    parser.add_argument("--sizes", type=int, nargs="+", default=list(PROFILE_SIZES))
    args = parser.parse_args()
    print(json.dumps(run_benchmarks(args.runs, tuple(args.sizes)), indent=2, default=str))
    engine.dispose()


if __name__ == "__main__":
    main()
