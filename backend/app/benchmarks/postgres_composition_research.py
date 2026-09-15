from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import time
import uuid
from datetime import date, time as clock, timedelta
from decimal import Decimal

from sqlalchemy import delete, event, func, insert, select, text

from app.database.session import SessionLocal, engine
from app.models import DailyPrice, IndexMembership, MarketIndex, Security, TradingCalendar
from app.research.historical import HistoricalCompositionService
from app.schemas.research import CompositionEvaluationRequest
from app.schemas.research_history import CompositionBacktestRequest, CompositionPortfolioRequest


SYMBOL_PREFIX = "P9B"
INDEX_PREFIX = "P9BENCH"
CALENDAR_NOTE = "PHASE9_SYNTHETIC_BENCHMARK"
BENCHMARK_EXCHANGE = "NSE_P9_BENCH"
NAMESPACE = uuid.UUID("0fef123d-d8a7-4e21-9d88-84046d2d2490")
SESSION_COUNT = 300


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


def _sessions() -> list[date]:
    output: list[date] = []
    current = date(1985, 1, 2)
    while len(output) < SESSION_COUNT:
        if current.weekday() < 5:
            output.append(current)
        current += timedelta(days=1)
    return output


def _security_id(index: int) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"security:{index}")


def _index_id(count: int) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"index:{count}")


def _namespace_counts(session) -> dict[str, int]:
    security_ids = select(Security.id).where(Security.symbol.like(f"{SYMBOL_PREFIX}%"))
    index_ids = select(MarketIndex.id).where(MarketIndex.symbol.like(f"{INDEX_PREFIX}%"))
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
                    DailyPrice.security_id.in_(security_ids)
                )
            )
            or 0
        ),
        "indices": int(
            session.scalar(
                select(func.count()).select_from(MarketIndex).where(
                    MarketIndex.id.in_(index_ids)
                )
            )
            or 0
        ),
        "memberships": int(
            session.scalar(
                select(func.count()).select_from(IndexMembership).where(
                    IndexMembership.index_id.in_(index_ids)
                )
            )
            or 0
        ),
        "calendar": int(
            session.scalar(
                select(func.count()).select_from(TradingCalendar).where(
                    TradingCalendar.notes == CALENDAR_NOTE
                )
            )
            or 0
        ),
    }


def cleanup() -> dict[str, int]:
    with SessionLocal() as session:
        security_ids = list(
            session.scalars(
                select(Security.id).where(Security.symbol.like(f"{SYMBOL_PREFIX}%"))
            )
        )
        index_ids = list(
            session.scalars(
                select(MarketIndex.id).where(MarketIndex.symbol.like(f"{INDEX_PREFIX}%"))
            )
        )
        if security_ids:
            session.execute(
                delete(DailyPrice).where(DailyPrice.security_id.in_(security_ids))
            )
        if index_ids:
            session.execute(
                delete(IndexMembership).where(IndexMembership.index_id.in_(index_ids))
            )
            session.execute(delete(MarketIndex).where(MarketIndex.id.in_(index_ids)))
        if security_ids:
            session.execute(delete(Security).where(Security.id.in_(security_ids)))
        session.execute(
            delete(TradingCalendar).where(TradingCalendar.notes == CALENDAR_NOTE)
        )
        session.commit()
        return _namespace_counts(session)


def prepare_data() -> tuple[list[date], float]:
    sessions = _sessions()
    started = time.perf_counter()
    with SessionLocal() as session:
        empty = {"securities": 0, "prices": 0, "indices": 0, "memberships": 0, "calendar": 0}
        if _namespace_counts(session) != empty:
            raise RuntimeError("Synthetic Phase 9 benchmark namespace is not empty")
        session.execute(
            insert(TradingCalendar),
            [
                {
                    "id": uuid.uuid5(NAMESPACE, f"calendar:{day.isoformat()}"),
                    "exchange": BENCHMARK_EXCHANGE,
                    "trading_date": day,
                    "is_trading_day": True,
                    "session_open": clock(9, 15),
                    "session_close": clock(15, 30),
                    "session_type": "REGULAR",
                    "notes": CALENDAR_NOTE,
                    "data_origin": "SYNTHETIC_BENCHMARK",
                }
                for day in sessions
            ],
        )
        session.execute(
            insert(Security),
            [
                {
                    "id": _security_id(index),
                    "exchange": BENCHMARK_EXCHANGE,
                    "symbol": f"{SYMBOL_PREFIX}{index:04d}",
                    "trading_symbol": f"{SYMBOL_PREFIX}{index:04d}-EQ",
                    "series": "EQ",
                    "company_name": f"Phase 9 Synthetic Benchmark {index}",
                    "security_type": "EQUITY",
                    "currency": "INR",
                    "is_active": True,
                    "data_origin": "SYNTHETIC_BENCHMARK",
                }
                for index in range(500)
            ],
        )
        for count in (50, 200, 500):
            session.execute(
                insert(MarketIndex),
                [
                    {
                        "id": _index_id(count),
                        "name": f"Phase 9 Synthetic Benchmark {count}",
                        "symbol": f"{INDEX_PREFIX}{count}",
                        "provider": "SYNTHETIC_BENCHMARK",
                        "exchange": BENCHMARK_EXCHANGE,
                    }
                ],
            )
            session.execute(
                insert(IndexMembership),
                [
                    {
                        "id": uuid.uuid5(NAMESPACE, f"membership:{count}:{index}"),
                        "index_id": _index_id(count),
                        "security_id": _security_id(index),
                        "valid_from": sessions[0],
                        "source": "PHASE9_SYNTHETIC_BENCHMARK",
                        "data_origin": "SYNTHETIC_BENCHMARK",
                    }
                    for index in range(count)
                ],
            )
        price_batch: list[dict[str, object]] = []
        for security_index in range(500):
            for session_index, day in enumerate(sessions):
                close = (
                    Decimal("50")
                    + Decimal(session_index) * Decimal("0.25")
                    + Decimal(security_index % 25) * Decimal("0.10")
                )
                volume = 1_000_000 + session_index * 1_000 + security_index
                price_batch.append(
                    {
                        "security_id": _security_id(security_index),
                        "trading_date": day,
                        "open": close - Decimal("0.05"),
                        "high": close + Decimal("0.40"),
                        "low": close - Decimal("0.40"),
                        "close": close,
                        "volume": volume,
                        "traded_value": close * volume,
                        "source": "PHASE9_SYNTHETIC_BENCHMARK",
                        "data_origin": "SYNTHETIC_BENCHMARK",
                    }
                )
                if len(price_batch) == 10_000:
                    session.execute(insert(DailyPrice), price_batch)
                    price_batch.clear()
        if price_batch:
            session.execute(insert(DailyPrice), price_batch)
        session.commit()
    return sessions, round((time.perf_counter() - started) * 1_000, 3)


def _inline_source(sessions: list[date]) -> CompositionEvaluationRequest:
    return CompositionEvaluationRequest(
        policy_code="CONSENSUS_N_OF_M",
        policy_version="1",
        universe=f"{INDEX_PREFIX}500",
        observation_date=sessions[-1],
        as_of=f"{sessions[-1].isoformat()}T15:30:00+05:30",
        adjustment_policy="RAW",
        required_match_count=2,
        components=[
            {
                "strategy_code": "MOMENTUM_TREND",
                "strategy_version": "1",
                "parameter_overrides": {
                    "min_momentum_3m": "-1",
                    "min_momentum_6m": "-1",
                    "min_volume_ratio_20": "0",
                },
            },
            {
                "strategy_code": "BREAKOUT_20D",
                "strategy_version": "1",
                "parameter_overrides": {
                    "min_breakout_pct_20": "-1",
                    "min_volume_ratio_20": "0",
                    "min_close_location": "0",
                    "max_atr_pct_14": "2",
                },
            },
            {
                "strategy_code": "MEAN_REVERSION_PULLBACK",
                "strategy_version": "1",
                "parameter_overrides": {
                    "max_rsi_14": "100",
                    "max_distance_sma_20": "2",
                    "max_return_5d": "5",
                    "max_atr_pct_14": "2",
                },
            },
        ],
    )


def _measure(callable_):
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
        result = callable_()
        wall_ms = round((time.perf_counter() - started) * 1_000, 3)
    finally:
        event.remove(engine, "before_cursor_execute", count_queries)
    serialization_started = time.perf_counter()
    encoded = result.model_dump_json()
    serialization_ms = round((time.perf_counter() - serialization_started) * 1_000, 3)
    return result, {
        "wall_ms": wall_ms,
        "serialization_ms": serialization_ms,
        "serialized_bytes": len(encoded.encode()),
        "select_count": select_count,
        "statement_count": statement_count,
    }


def _measure_preparation(callable_):
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
        result = callable_()
        wall_ms = round((time.perf_counter() - started) * 1_000, 3)
    finally:
        event.remove(engine, "before_cursor_execute", count_queries)
    return result, {
        "wall_ms": wall_ms,
        "select_count": select_count,
        "statement_count": statement_count,
    }


def run_case(count: int, sessions: list[date]) -> dict[str, object]:
    universe = f"{INDEX_PREFIX}{count}"
    source = {"inline_composition": _inline_source(sessions)}
    with SessionLocal() as session:
        service = HistoricalCompositionService(session)
        backtest_request = CompositionBacktestRequest(
            source=source,
            universe=universe,
            start_date=sessions[0],
            end_date=sessions[-1],
            adjustment_policy="RAW",
            holding_sessions=20,
            diagnostic_detail_limit=0,
            trade_detail_limit=0,
        )
        portfolio_request = CompositionPortfolioRequest(
            source=source,
            universe=universe,
            start_date=sessions[0],
            end_date=sessions[-1],
            adjustment_policy="RAW",
            holding_sessions=20,
            diagnostic_detail_limit=0,
            position_detail_limit=0,
            ledger_detail_limit=0,
        )
        backtest, backtest_measurement = _measure(
            lambda: service.run_backtest(backtest_request)
        )
        portfolio, portfolio_measurement = _measure(
            lambda: service.run_portfolio(portfolio_request)
        )
        prepared, preparation_measurement = _measure_preparation(
            lambda: service.prepare(backtest_request)
        )
        shared_backtest, shared_backtest_measurement = _measure(
            lambda: service.run_backtest_prepared(backtest_request, prepared)
        )
        shared_portfolio, shared_portfolio_measurement = _measure(
            lambda: service.run_portfolio_prepared(portfolio_request, prepared)
        )
        if backtest.historical_signal_fingerprint != portfolio.historical_signal_fingerprint:
            raise RuntimeError("Backtest and portfolio did not consume the same signal stream")
        if (
            shared_backtest.backtest_run_fingerprint
            != backtest.backtest_run_fingerprint
            or shared_portfolio.portfolio_run_fingerprint
            != portfolio.portfolio_run_fingerprint
        ):
            raise RuntimeError("Shared prepared context changed deterministic research output")
        return {
            "security_count": count,
            "session_count": len(sessions),
            "price_row_count": count * len(sessions),
            "eligible_evaluations": backtest.diagnostics.eligible_evaluations,
            "signal_count": backtest.diagnostics.matched_setups,
            "trade_count": backtest.total_trade_count,
            "portfolio_trade_count": portfolio.total_position_count,
            "backtest": {
                **backtest_measurement,
                "data_fetch_ms": backtest.timings.data_load_ms,
                "feature_generation_ms": backtest.timings.feature_generation_ms,
                "composition_evaluation_ms": backtest.timings.composition_evaluation_ms,
                "phase5_execution_ms": backtest.timings.execution_ms,
                "analytics_ms": backtest.timings.analytics_ms,
            },
            "portfolio": {
                **portfolio_measurement,
                "data_fetch_ms": portfolio.timings.data_load_ms,
                "feature_generation_ms": portfolio.timings.feature_generation_ms,
                "composition_evaluation_ms": portfolio.timings.composition_evaluation_ms,
                "phase6_execution_ms": portfolio.timings.execution_ms,
                "analytics_ms": portfolio.timings.analytics_ms,
            },
            "shared_context": {
                "preparation": {
                    **preparation_measurement,
                    "data_fetch_ms": prepared.series_batch.repository_load_ms,
                    "feature_generation_ms": prepared.series_batch.calculation_ms,
                    "composition_evaluation_ms": prepared.composition_evaluation_ms,
                    "signal_fingerprint_ms": prepared.signal_fingerprint_ms,
                },
                "backtest_adapter": shared_backtest_measurement,
                "portfolio_adapter": shared_portfolio_measurement,
            },
        }


def profile_case(count: int, sessions: list[date]) -> str:
    universe = f"{INDEX_PREFIX}{count}"
    request = CompositionBacktestRequest(
        source={"inline_composition": _inline_source(sessions)},
        universe=universe,
        start_date=sessions[0],
        end_date=sessions[-1],
        adjustment_policy="RAW",
        holding_sessions=20,
        diagnostic_detail_limit=0,
        trade_detail_limit=0,
    )
    profiler = cProfile.Profile()
    with SessionLocal() as session:
        service = HistoricalCompositionService(session)
        profiler.enable()
        service.prepare(request)
        profiler.disable()
    output = io.StringIO()
    pstats.Stats(profiler, stream=output).strip_dirs().sort_stats(
        "cumulative"
    ).print_stats(40)
    return output.getvalue()


def run() -> dict[str, object]:
    identity = _verify_identity()
    sessions, preparation_ms = prepare_data()
    cases = [run_case(count, sessions) for count in (50, 200, 500)]
    with SessionLocal() as session:
        before_cleanup = _namespace_counts(session)
    return {
        "identity": identity,
        "synthetic_data_preparation_ms": preparation_ms,
        "cases": cases,
        "namespace_before_cleanup": before_cleanup,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synthetic PostgreSQL benchmark for Phase 9 composition research"
    )
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument("--profile-count", type=int, choices=(50, 200, 500))
    args = parser.parse_args()
    _verify_identity()
    if args.cleanup_only:
        print(json.dumps({"cleanup": cleanup()}, indent=2))
        return
    if args.profile_count is not None:
        try:
            sessions, preparation_ms = prepare_data()
            profile = profile_case(args.profile_count, sessions)
        finally:
            cleaned = cleanup()
        print(
            json.dumps(
                {
                    "synthetic_data_preparation_ms": preparation_ms,
                    "profile_security_count": args.profile_count,
                    "profile": profile,
                    "namespace_after_cleanup": cleaned,
                },
                indent=2,
            )
        )
        return
    try:
        result = run()
    finally:
        cleaned = cleanup()
    expected = {"securities": 0, "prices": 0, "indices": 0, "memberships": 0, "calendar": 0}
    if cleaned != expected:
        raise RuntimeError("Synthetic Phase 9 benchmark cleanup was incomplete")
    result["namespace_after_cleanup"] = cleaned
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
