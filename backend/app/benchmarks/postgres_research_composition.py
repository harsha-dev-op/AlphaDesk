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
    _expected_counts,
    _index_symbol,
    _namespace_counts,
    _schema_guard,
    _validate_namespace_ownership,
)
from app.database.session import SessionLocal, engine
from app.main import app
from app.research.service import ResearchService
from app.schemas.research import CompositionEvaluationRequest


def _request(size: int) -> CompositionEvaluationRequest:
    return CompositionEvaluationRequest(
        policy_code="CONSENSUS_N_OF_M",
        policy_version="1",
        universe=_index_symbol(size),
        observation_date=OBSERVATION_DATE,
        as_of=AS_OF,
        adjustment_policy="ADJUSTED",
        required_match_count=2,
        components=[
            {"strategy_code": "MOMENTUM_TREND", "strategy_version": "1"},
            {"strategy_code": "BREAKOUT_20D", "strategy_version": "1"},
            {"strategy_code": "MEAN_REVERSION_PULLBACK", "strategy_version": "1"},
        ],
    )


def _instrumented_run(request: CompositionEvaluationRequest) -> dict[str, object]:
    query_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal query_count
        if statement.lstrip().lower().startswith("select"):
            query_count += 1

    event.listen(engine, "after_cursor_execute", count_selects)
    try:
        with SessionLocal() as session:
            started = time.perf_counter()
            response = ResearchService(session).evaluate(request)
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
        "member_count": response.evaluated_security_count,
        "component_evaluation_count": sum(
            len(member.component_results) for member in response.results
        ),
        "union_feature_count": len(response.union_feature_codes),
        "matched_count": response.matched_count,
        "insufficient_history_count": response.insufficient_history_count,
        "serialized_bytes": len(serialized.encode()),
        "run_fingerprint": response.composition_run_fingerprint,
        "service_phases_ms": response.timings.model_dump(),
    }


def _api_timing(request: CompositionEvaluationRequest) -> dict[str, object]:
    with TestClient(app) as client:
        started = time.perf_counter()
        response = client.post(
            "/api/v1/research/compositions/evaluate",
            json=request.model_dump(mode="json"),
        )
        elapsed_ms = (time.perf_counter() - started) * 1_000
    if response.status_code != 200:
        raise RuntimeError(f"Composition benchmark API failed with HTTP {response.status_code}")
    payload = response.json()
    return {
        "api_ms": round(elapsed_ms, 3),
        "member_count": payload["evaluated_security_count"],
        "component_evaluation_count": sum(
            len(member["component_results"]) for member in payload["results"]
        ),
        "run_fingerprint": payload["composition_run_fingerprint"],
        "serialized_bytes": len(response.content),
    }


def run_benchmarks(runs: int, sizes: tuple[int, ...] = PROFILE_SIZES) -> dict[str, object]:
    identity = verify_identity()
    _schema_guard()
    if runs < 3 or runs > 5:
        raise ValueError("runs must be between 3 and 5")
    unsupported = set(sizes) - set(PROFILE_SIZES)
    if unsupported:
        raise ValueError(f"Unsupported composition benchmark sizes: {sorted(unsupported)}")
    with SessionLocal() as session:
        _validate_namespace_ownership(session)
        counts = _namespace_counts(session)
    if counts != _expected_counts():
        raise RuntimeError("Complete namespaced Phase 3.6 scale fixture is required")

    cases: dict[str, object] = {}
    for size in sizes:
        request = _request(size)
        individual = [_instrumented_run(request) for _ in range(runs)]
        fingerprints = {str(run["run_fingerprint"]) for run in individual}
        if len(fingerprints) != 1:
            raise RuntimeError(f"Non-deterministic composition fingerprint at size {size}")
        expected_queries = 3 + 4 * math.ceil(size / 50)
        if any(run["query_count"] != expected_queries for run in individual):
            raise RuntimeError(f"Unexpected composition query count at size {size}")
        if any(run["member_count"] != size for run in individual):
            raise RuntimeError(f"Unexpected composition member count at size {size}")
        if any(run["component_evaluation_count"] != size * 3 for run in individual):
            raise RuntimeError(f"Unexpected component evaluation count at size {size}")
        api = _api_timing(request)
        if api["run_fingerprint"] not in fingerprints:
            raise RuntimeError(f"API and direct composition fingerprints differ at size {size}")
        warm = individual[1:]
        warm_total = [float(run["total_with_serialization_ms"]) for run in warm]
        cases[str(size)] = {
            "members": size,
            "price_rows": size * SESSION_COUNT,
            "strategies": 3,
            "component_evaluations": size * 3,
            "union_feature_count": individual[0]["union_feature_count"],
            "expected_queries": expected_queries,
            "individual_runs": individual,
            "summary": {
                "first_service_ms": individual[0]["service_ms"],
                "warm_service_median_ms": round(
                    median(float(run["service_ms"]) for run in warm), 3
                ),
                "warm_total_with_serialization_median_ms": round(median(warm_total), 3),
                "warm_total_range_ms": [min(warm_total), max(warm_total)],
                **api,
            },
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
        "composition": (
            "CONSENSUS_N_OF_M v1 · N=2 · MOMENTUM_TREND v1 + "
            "BREAKOUT_20D v1 + MEAN_REVERSION_PULLBACK v1 · ADJUSTED"
        ),
        "cases": cases,
        "measurement_notes": [
            "The first run per size is process-level cold and excluded from warm medians.",
            "The API sample follows warmed direct-service runs.",
            "All three strategies consume one shared bounded technical-data pass.",
            "Every member and each component's explainable conditions are serialized.",
            "The fixture lifecycle remains owned by postgres_scanner_scale setup and cleanup.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only PostgreSQL multi-strategy composition scale benchmark"
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--sizes", type=int, nargs="+", default=list(PROFILE_SIZES))
    args = parser.parse_args()
    print(json.dumps(run_benchmarks(args.runs, tuple(args.sizes)), indent=2, default=str))
    engine.dispose()


if __name__ == "__main__":
    main()
