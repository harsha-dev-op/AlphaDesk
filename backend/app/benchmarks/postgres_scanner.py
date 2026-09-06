from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import re
import sys
import time
from contextlib import ExitStack
from datetime import date, datetime
from pathlib import Path
from statistics import median
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import event, func, inspect, select, text

import app.technical.service as technical_service_module
from app.database.session import SessionLocal, engine
from app.main import app
from app.models import (
    CorporateAction,
    DailyPrice,
    DataIngestionRun,
    MarketIndex,
    Security,
    TradingCalendar,
)
from app.repositories.calendar import TradingCalendarRepository
from app.repositories.indices import IndexRepository
from app.repositories.securities import SecurityRepository
from app.scanner import ScannerService
from app.schemas.scanner import MarketScanRequest
from app.services.adjustments import PriceAdjustmentService
from app.technical.service import TechnicalFeatureService

EXPECTED_DATABASE = "alphadesk_dev"
EXPECTED_USER = "alphadesk"
BENCHMARK_DATE = date(2025, 1, 31)
AS_OF = "2025-01-31T15:30:00+05:30"
FILTERS = [
    {"feature": "RET_20D", "operator": ">", "value": "-1"},
    {"feature": "RSI_14", "operator": "between", "value": "0", "upper_value": "100"},
    {"feature": "AVG_VOLUME_20", "operator": ">=", "value": "0"},
]


def verify_identity() -> dict[str, object]:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Refusing validation because the configured database is not PostgreSQL")
    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Refusing validation because PostgreSQL is not local")
    with engine.connect() as connection:
        database, user, version = connection.execute(
            text("select current_database(), current_user, version()")
        ).one()
    if database != EXPECTED_DATABASE or user != EXPECTED_USER:
        raise RuntimeError("Refusing validation because the database identity changed")
    return {
        "status": "CONNECTED",
        "host": engine.url.host,
        "port": engine.url.port or 5432,
        "database": database,
        "user": user,
        "version": version,
    }


def verify_schema_runtime() -> dict[str, object]:
    inspector = inspect(engine)
    expected_tables = {
        "alembic_version",
        "corporate_actions",
        "daily_prices",
        "data_ingestion_runs",
        "fundamental_reports",
        "index_memberships",
        "indices",
        "securities",
        "strategy_definitions",
        "trading_calendar",
    }
    tables = set(inspector.get_table_names())
    missing = expected_tables - tables
    if missing:
        raise RuntimeError(f"Expected schema tables are missing: {sorted(missing)}")

    selected_columns = {
        "daily_prices": {"close", "traded_value", "ingested_at"},
        "securities": {"is_active", "delisting_date"},
        "corporate_actions": {
            "ratio_numerator",
            "cash_amount",
            "source_published_at",
            "ingested_at",
            "available_at",
            "supersedes_action_id",
        },
        "trading_calendar": {"is_trading_day", "session_close"},
        "data_ingestion_runs": {"completed_at"},
    }
    column_contract: dict[str, dict[str, object]] = {}
    json_columns: list[str] = []
    for table in sorted(expected_tables - {"alembic_version"}):
        for column in inspector.get_columns(table):
            sql_type = str(column["type"])
            if "JSON" in sql_type.upper():
                json_columns.append(f"{table}.{column['name']}")
            if column["name"] in selected_columns.get(table, set()):
                column_contract[f"{table}.{column['name']}"] = {
                    "sql_type": sql_type,
                    "nullable": column["nullable"],
                    "timezone": getattr(column["type"], "timezone", None),
                }

    with SessionLocal() as session:
        price = session.scalar(select(DailyPrice).limit(1))
        inactive = session.scalar(select(Security).where(Security.is_active.is_(False)))
        closed_day = session.scalar(
            select(TradingCalendar).where(TradingCalendar.is_trading_day.is_(False)).limit(1)
        )
        ingestion = session.scalar(select(DataIngestionRun).limit(1))
    if price is None or inactive is None or closed_day is None or ingestion is None:
        raise RuntimeError("Seeded rows required for runtime type verification are missing")

    return {
        "tables": sorted(tables),
        "column_contract": column_contract,
        "json_columns": json_columns,
        "runtime_types": {
            "numeric_decimal": type(price.close).__name__,
            "boolean": type(inactive.is_active).__name__,
            "nullable_date_is_present": inactive.delisting_date is not None,
            "nullable_closed_session_is_none": closed_day.session_close is None,
            "timestamp_timezone": str(ingestion.started_at.tzinfo),
        },
        "constraint_totals": {
            "indexes": sum(len(inspector.get_indexes(table)) for table in expected_tables),
            "foreign_keys": sum(
                len(inspector.get_foreign_keys(table)) for table in expected_tables
            ),
            "unique_constraints": sum(
                len(inspector.get_unique_constraints(table)) for table in expected_tables
            ),
        },
    }


def _assert_ok(response, label: str) -> dict:
    if response.status_code != 200:
        raise RuntimeError(f"{label} failed with HTTP {response.status_code}")
    return response.json()


def _scan_payload(
    observation_date: str,
    *,
    adjustment_policy: str = "ADJUSTED",
    filters: list[dict] | None = None,
) -> dict[str, object]:
    return {
        "universe": "NIFTYDEMO100",
        "observation_date": observation_date,
        "as_of": f"{observation_date}T15:30:00+05:30",
        "feature_set": "ALPHADESK_CORE_TECHNICAL",
        "feature_set_version": "1",
        "adjustment_policy": adjustment_policy,
        "logic": "AND",
        "filters": filters or FILTERS,
    }


def _valid_fingerprint(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value))


def run_smoke() -> dict[str, object]:
    with TestClient(app) as client:
        health = _assert_ok(client.get("/health"), "health")
        securities = _assert_ok(
            client.get("/api/v1/securities", params={"page_size": 10}),
            "securities",
        )
        security = _assert_ok(
            client.get("/api/v1/securities/ALPHAIND"),
            "security detail",
        )
        prices = _assert_ok(
            client.get(
                "/api/v1/securities/ALPHAIND/prices",
                params={"start_date": "2025-01-02", "end_date": "2025-03-31"},
            ),
            "prices",
        )
        indices = _assert_ok(client.get("/api/v1/indices"), "indices")
        members_before = _assert_ok(
            client.get(
                "/api/v1/indices/NIFTYDEMO100/members",
                params={"as_of": "2025-02-14"},
            ),
            "historical members before transition",
        )
        members_after = _assert_ok(
            client.get(
                "/api/v1/indices/NIFTYDEMO100/members",
                params={"as_of": "2025-02-17"},
            ),
            "historical members after transition",
        )
        quality = _assert_ok(
            client.get("/api/v1/data-quality/status"),
            "data quality",
        )

        catalog = _assert_ok(client.get("/api/v1/features/catalog"), "feature catalog")
        feature_sets = _assert_ok(client.get("/api/v1/feature-sets"), "feature sets")
        feature_params = {
            "start_date": "2025-02-03",
            "end_date": "2025-02-03",
            "as_of": "2025-02-03T15:30:00+05:30",
        }
        raw_features = _assert_ok(
            client.get(
                "/api/v1/securities/ALPHAIND/features",
                params={**feature_params, "adjustment_policy": "raw"},
            ),
            "raw features",
        )
        adjusted_features = _assert_ok(
            client.get(
                "/api/v1/securities/ALPHAIND/features",
                params={**feature_params, "adjustment_policy": "adjusted"},
            ),
            "adjusted features",
        )

        scanner_metadata = _assert_ok(
            client.get("/api/v1/scanner/metadata"),
            "scanner metadata",
        )
        simple_payload = _scan_payload(
            "2025-01-31",
            filters=[{"feature": "RET_1D", "operator": ">", "value": "-1"}],
        )
        simple_scan = _assert_ok(
            client.post("/api/v1/scanner/scan", json=simple_payload),
            "simple scan",
        )
        repeated_scan = _assert_ok(
            client.post("/api/v1/scanner/scan", json=simple_payload),
            "repeated scan",
        )
        multi_scan = _assert_ok(
            client.post("/api/v1/scanner/scan", json=_scan_payload("2025-01-31")),
            "multi-filter scan",
        )
        historical_scan = _assert_ok(
            client.post(
                "/api/v1/scanner/scan",
                json=_scan_payload(
                    "2025-02-17",
                    filters=[
                        {"feature": "TRUE_RANGE", "operator": ">=", "value": "0"}
                    ],
                ),
            ),
            "historical scan",
        )
        split_filter = [
            {
                "feature": "RET_1D",
                "operator": "between",
                "value": "-1",
                "upper_value": "1",
            }
        ]
        raw_scan = _assert_ok(
            client.post(
                "/api/v1/scanner/scan",
                json=_scan_payload(
                    "2025-02-03",
                    adjustment_policy="RAW",
                    filters=split_filter,
                ),
            ),
            "raw scanner",
        )
        adjusted_scan = _assert_ok(
            client.post(
                "/api/v1/scanner/scan",
                json=_scan_payload(
                    "2025-02-03",
                    adjustment_policy="ADJUSTED",
                    filters=split_filter,
                ),
            ),
            "adjusted scanner",
        )

    before_symbols = [item["security"]["symbol"] for item in members_before["members"]]
    after_symbols = [item["security"]["symbol"] for item in members_after["members"]]
    if before_symbols != ["ALPHAIND", "BETATECH", "OLDCO"]:
        raise RuntimeError("Unexpected pre-transition historical membership")
    if after_symbols != ["ALPHAIND", "GAMMAFIN"]:
        raise RuntimeError("Unexpected post-transition historical membership")
    if simple_scan["scan_fingerprint"] != repeated_scan["scan_fingerprint"]:
        raise RuntimeError("Repeated scan fingerprint changed")
    if [item["symbol"] for item in simple_scan["results"]] != sorted(
        item["symbol"] for item in simple_scan["results"]
    ):
        raise RuntimeError("Scanner result order is not deterministic")
    if not _valid_fingerprint(simple_scan["scan_fingerprint"]):
        raise RuntimeError("Scan fingerprint is invalid")
    for result in simple_scan["results"]:
        if result["observation_date"] != "2025-01-31":
            raise RuntimeError("Scanner returned a non-exact observation")
        if result["available_at"] > simple_scan["as_of"]:
            raise RuntimeError("Scanner returned data after as_of")
        if result["dataset"]["latest_observation"] > "2025-01-31":
            raise RuntimeError("Future price data entered the scanner dataset")
        if not _valid_fingerprint(result["input_fingerprint"]):
            raise RuntimeError("Input fingerprint is invalid")
        if any(version != "1" for version in result["feature_versions"].values()):
            raise RuntimeError("Unexpected feature version")

    raw_alpha = next(item for item in raw_scan["results"] if item["symbol"] == "ALPHAIND")
    adjusted_alpha = next(
        item for item in adjusted_scan["results"] if item["symbol"] == "ALPHAIND"
    )
    raw_return = raw_alpha["matched_values"]["RET_1D"]
    adjusted_return = adjusted_alpha["matched_values"]["RET_1D"]
    if raw_return == adjusted_return:
        raise RuntimeError("RAW and ADJUSTED scanner values unexpectedly match on split day")

    with SessionLocal() as session:
        market_index = session.scalar(
            select(MarketIndex).where(MarketIndex.symbol == "NIFTYDEMO100")
        )
        if market_index is None:
            raise RuntimeError("Seeded scanner universe is missing")
        member_ids = [
            membership.security_id
            for membership in IndexRepository(session).members_as_of(
                market_index.id,
                BENCHMARK_DATE,
            )
        ]
        total_actions = session.scalar(select(func.count()).select_from(CorporateAction))
        eligible_actions = SecurityRepository(session).corporate_actions_for_securities(
            member_ids,
            as_of=datetime.fromisoformat(AS_OF),
            through=BENCHMARK_DATE,
        )
        eligible_action_count = sum(len(actions) for actions in eligible_actions.values())
        future_price_count = session.scalar(
            select(func.count())
            .select_from(DailyPrice)
            .where(
                DailyPrice.security_id.in_(member_ids),
                DailyPrice.trading_date > BENCHMARK_DATE,
            )
        )
    if not total_actions or eligible_action_count:
        raise RuntimeError("Future corporate actions entered the historical action horizon")
    if not future_price_count:
        raise RuntimeError("Seed fixture does not contain future prices for horizon verification")

    return {
        "phase1": {
            "health": health,
            "securities_total": securities["total"],
            "security_symbol": security["symbol"],
            "price_rows": len(prices["items"]),
            "indices": [item["symbol"] for item in indices],
            "members_2025_02_14": before_symbols,
            "members_2025_02_17": after_symbols,
            "quality_status": quality["status"],
        },
        "phase2": {
            "feature_count": catalog["count"],
            "feature_sets": [item["code"] for item in feature_sets["feature_sets"]],
            "raw_ret_1d": raw_features["items"][0]["values"]["RET_1D"],
            "adjusted_ret_1d": adjusted_features["items"][0]["values"]["RET_1D"],
            "raw_adjusted_differ": raw_features["items"][0]["values"]["RET_1D"]
            != adjusted_features["items"][0]["values"]["RET_1D"],
        },
        "phase3": {
            "metadata_feature_count": len(scanner_metadata["features"]),
            "simple_members": simple_scan["universe_member_count"],
            "simple_matches": simple_scan["matched_count"],
            "multi_members": multi_scan["universe_member_count"],
            "multi_matches": multi_scan["matched_count"],
            "historical_symbols": [item["symbol"] for item in historical_scan["results"]],
            "fingerprint_deterministic": True,
            "exact_date_and_availability_verified": True,
            "future_price_horizon_verified": True,
            "future_action_horizon_verified": True,
            "future_actions_in_database": total_actions,
            "actions_eligible_on_2025_01_31": eligible_action_count,
            "future_price_rows_excluded": future_price_count,
            "raw_ret_1d": raw_return,
            "adjusted_ret_1d": adjusted_return,
            "raw_adjusted_differ": True,
        },
    }


def _request() -> MarketScanRequest:
    return MarketScanRequest(
        universe="NIFTYDEMO100",
        observation_date=BENCHMARK_DATE,
        as_of=AS_OF,
        adjustment_policy="ADJUSTED",
        filters=FILTERS,
    )


def _timed_method(name: str, original, stages: dict[str, float]):
    def timed(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            stages[name] = stages.get(name, 0.0) + (time.perf_counter() - started) * 1_000

    return timed


def _sql_category(statement: str) -> str:
    normalized = statement.lower()
    for table in (
        "index_memberships",
        "indices",
        "daily_prices",
        "corporate_actions",
        "trading_calendar",
        "data_ingestion_runs",
    ):
        if table in normalized:
            return table
    return "other"


def _instrumented_run(request: MarketScanRequest | None = None) -> dict[str, object]:
    stages: dict[str, float] = {}
    sql_ms: dict[str, float] = {}
    query_count = 0

    def before_cursor_execute(_connection, _cursor, _statement, _parameters, context, _many):
        context._alphadesk_started = time.perf_counter()

    def after_cursor_execute(_connection, _cursor, statement, _parameters, context, _many):
        nonlocal query_count
        elapsed = (time.perf_counter() - context._alphadesk_started) * 1_000
        category = _sql_category(statement)
        sql_ms[category] = sql_ms.get(category, 0.0) + elapsed
        if statement.lstrip().lower().startswith("select"):
            query_count += 1

    originals = {
        "universe_lookup": IndexRepository.get_by_name_or_symbol,
        "membership_resolution": IndexRepository.members_as_of,
        "price_retrieval": SecurityRepository.prices_for_securities,
        "action_retrieval": SecurityRepository.corporate_actions_for_securities,
        "calendar_entries": TradingCalendarRepository.entries_for_dates,
        "calendar_days": TradingCalendarRepository.trading_days_for_exchanges,
        "adjustment": PriceAdjustmentService.adjust,
        "feature_calculation": technical_service_module.calculate_latest_feature_snapshot,
        "decimal_conversion": technical_service_module._to_point,
        "dataset_fingerprint": TechnicalFeatureService._dataset_context,
    }
    targets = {
        "universe_lookup": (IndexRepository, "get_by_name_or_symbol"),
        "membership_resolution": (IndexRepository, "members_as_of"),
        "price_retrieval": (SecurityRepository, "prices_for_securities"),
        "action_retrieval": (SecurityRepository, "corporate_actions_for_securities"),
        "calendar_entries": (TradingCalendarRepository, "entries_for_dates"),
        "calendar_days": (TradingCalendarRepository, "trading_days_for_exchanges"),
        "adjustment": (PriceAdjustmentService, "adjust"),
        "feature_calculation": (technical_service_module, "calculate_latest_feature_snapshot"),
        "decimal_conversion": (technical_service_module, "_to_point"),
        "dataset_fingerprint": (TechnicalFeatureService, "_dataset_context"),
    }

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    try:
        with ExitStack() as stack:
            for name, (target, attribute) in targets.items():
                stack.enter_context(
                    patch.object(
                        target,
                        attribute,
                        _timed_method(name, originals[name], stages),
                    )
                )
            with SessionLocal() as session:
                started = time.perf_counter()
                response = ScannerService(session).scan(request or _request())
                service_ms = (time.perf_counter() - started) * 1_000
                serialization_started = time.perf_counter()
                serialized = response.model_dump_json()
                serialization_ms = (time.perf_counter() - serialization_started) * 1_000
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
        event.remove(engine, "after_cursor_execute", after_cursor_execute)

    price_sql = sql_ms.get("daily_prices", 0.0)
    action_sql = sql_ms.get("corporate_actions", 0.0)
    return {
        "service_ms": round(service_ms, 3),
        "serialization_ms": round(serialization_ms, 3),
        "total_with_serialization_ms": round(service_ms + serialization_ms, 3),
        "query_count": query_count,
        "result_count": response.matched_count,
        "serialized_bytes": len(serialized.encode()),
        "scan_fingerprint": response.scan_fingerprint,
        "service_phases_ms": response.timings.model_dump(),
        "instrumented_stages_ms": {key: round(value, 3) for key, value in stages.items()},
        "sql_cursor_ms": {key: round(value, 3) for key, value in sql_ms.items()},
        "price_transfer_materialization_grouping_ms": round(
            max(0.0, stages.get("price_retrieval", 0.0) - price_sql),
            3,
        ),
        "action_transfer_materialization_grouping_ms": round(
            max(0.0, stages.get("action_retrieval", 0.0) - action_sql),
            3,
        ),
        "dataset_version": response.results[0].dataset.dataset_version
        if response.results
        else None,
    }


def _profile(request: MarketScanRequest | None = None) -> list[dict[str, object]]:
    profiler = cProfile.Profile()
    with SessionLocal() as session:
        profiler.enable()
        ScannerService(session).scan(request or _request())
        profiler.disable()
    stats = pstats.Stats(profiler)
    backend_root = Path(__file__).resolve().parents[2]
    ranked = sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)
    output: list[dict[str, object]] = []
    for (filename, line, function), (_cc, calls, self_seconds, cumulative_seconds, _callers) in ranked:
        path = Path(filename).resolve()
        try:
            relative = path.relative_to(backend_root)
        except ValueError:
            continue
        output.append(
            {
                "function": f"{relative.as_posix()}:{line}:{function}",
                "calls": calls,
                "self_ms": round(self_seconds * 1_000, 3),
                "cumulative_ms": round(cumulative_seconds * 1_000, 3),
            }
        )
        if len(output) == 12:
            break
    return output


def run_benchmark(runs: int) -> dict[str, object]:
    if runs < 2 or runs > 10:
        raise ValueError("runs must be between 2 and 10")
    with SessionLocal() as session:
        market_index = session.scalar(
            select(MarketIndex).where(MarketIndex.symbol == "NIFTYDEMO100")
        )
        if market_index is None:
            raise RuntimeError("Seeded benchmark universe is missing")
        member_ids = [
            item.security_id
            for item in IndexRepository(session).members_as_of(market_index.id, BENCHMARK_DATE)
        ]
        price_rows = session.scalar(
            select(func.count())
            .select_from(DailyPrice)
            .where(
                DailyPrice.security_id.in_(member_ids),
                DailyPrice.trading_date <= BENCHMARK_DATE,
            )
        )
    results = [_instrumented_run() for _ in range(runs)]
    warm = results[1:]

    api_timings: list[float] = []
    payload = _scan_payload(BENCHMARK_DATE.isoformat())
    with TestClient(app) as client:
        for _ in range(runs):
            started = time.perf_counter()
            response = client.post("/api/v1/scanner/scan", json=payload)
            elapsed = (time.perf_counter() - started) * 1_000
            if response.status_code != 200:
                raise RuntimeError(f"Benchmark API failed with HTTP {response.status_code}")
            api_timings.append(round(elapsed, 3))

    service_warm = [float(item["service_ms"]) for item in warm]
    total_warm = [float(item["total_with_serialization_ms"]) for item in warm]
    api_warm = api_timings[1:]
    return {
        "process_mode": f"CPython {sys.version.split()[0]} direct service plus in-process FastAPI TestClient",
        "database": EXPECTED_DATABASE,
        "observation_date": BENCHMARK_DATE.isoformat(),
        "member_count": len(member_ids),
        "price_row_count": int(price_rows or 0),
        "feature_count": 36,
        "condition_count": len(FILTERS),
        "adjustment_policy": "ADJUSTED",
        "dataset": "phase1_demo / 2025.03.v1",
        "cold_definition": "First process-level benchmark call; PostgreSQL caches may already be warm from smoke checks",
        "individual_runs": results,
        "api_request_ms": api_timings,
        "summary": {
            "first_service_ms": results[0]["service_ms"],
            "warm_service_median_ms": round(median(service_warm), 3),
            "warm_service_range_ms": [min(service_warm), max(service_warm)],
            "warm_total_with_serialization_median_ms": round(median(total_warm), 3),
            "warm_api_median_ms": round(median(api_warm), 3),
            "warm_api_range_ms": [min(api_warm), max(api_warm)],
        },
        "profile_top_project_functions": _profile(),
        "measurement_limitations": [
            "The deterministic seed supports at most three members with sufficient warm-up; larger sizes were not fabricated.",
            "SQLAlchemy cursor events measure statement execution, while repository wrapper residuals combine row transfer, ORM materialization, and grouping.",
            "The first run is process-cold only; PostgreSQL and operating-system caches were not cleared.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only PostgreSQL API smoke and Market Scanner benchmark"
    )
    parser.add_argument("--mode", choices=("smoke", "benchmark", "all"), default="all")
    parser.add_argument("--runs", type=int, default=4)
    args = parser.parse_args()
    output: dict[str, object] = {
        "connection": verify_identity(),
        "schema": verify_schema_runtime(),
    }
    if args.mode in {"smoke", "all"}:
        output["smoke"] = run_smoke()
    if args.mode in {"benchmark", "all"}:
        output["benchmark"] = run_benchmark(args.runs)
    print(json.dumps(output, indent=2))
    engine.dispose()


if __name__ == "__main__":
    main()
