from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from app.services.adjustments import PriceAdjustmentService
from app.technical.calculators import PricePoint, calculate_feature_frame, calculate_latest_feature_snapshot
from app.technical.registry import CORE_TECHNICAL_SET, FEATURE_DEFINITIONS

BenchmarkMode = Literal["full", "latest-baseline", "latest"]
AdjustmentPolicy = Literal["RAW", "ADJUSTED"]


@dataclass(frozen=True)
class SyntheticPrice:
    id: str
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    traded_value: Decimal
    source: str = "ALPHADESK_SYNTHETIC"


@dataclass(frozen=True)
class SyntheticAction:
    id: str
    action_type: str
    ex_date: date
    ratio_numerator: Decimal
    ratio_denominator: Decimal


@dataclass(frozen=True)
class BenchmarkResult:
    mode: BenchmarkMode
    adjustment_policy: AdjustmentPolicy
    securities: int
    rows_per_security: int
    feature_count: int
    feature_values_materialized: int
    input_prep_seconds: float
    adjusted_prep_seconds: float
    feature_calculation_seconds: float
    fingerprint_seconds: float
    orchestration_seconds: float
    total_seconds: float
    rows_per_second: float
    peak_memory_mib: float | None
    digest: str


def _trading_dates(rows: int) -> list[date]:
    result: list[date] = []
    current = date(2014, 1, 2)
    while len(result) < rows:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def build_dataset(security_number: int, rows: int) -> tuple[list[SyntheticPrice], list[SyntheticAction]]:
    """Build deterministic OHLCV history with two corporate-action regimes."""
    dates = _trading_dates(rows)
    prices: list[SyntheticPrice] = []
    security_offset = Decimal(security_number) / Decimal("10")
    for index, trading_date in enumerate(dates):
        economic_close = Decimal("100") + security_offset + Decimal(index) * Decimal("0.025")
        economic_close += Decimal((index % 19) - 9) * Decimal("0.037")
        raw_multiplier = Decimal("2") if index < 800 else (Decimal("1") if index < 1600 else Decimal("0.5"))
        open_price = economic_close * (Decimal("1") + Decimal((index % 7) - 3) * Decimal("0.0004"))
        close_price = economic_close * raw_multiplier
        open_price *= raw_multiplier
        high_price = max(open_price, close_price) + Decimal("1.15") * raw_multiplier
        low_price = min(open_price, close_price) - Decimal("1.05") * raw_multiplier
        volume = 900_000 + (security_number * 1_003) + ((index * 7_919) % 350_000)
        prices.append(
            SyntheticPrice(
                id=f"P-{security_number}-{index}",
                trading_date=trading_date,
                open=open_price.quantize(Decimal("0.0001")),
                high=high_price.quantize(Decimal("0.0001")),
                low=low_price.quantize(Decimal("0.0001")),
                close=close_price.quantize(Decimal("0.0001")),
                volume=volume,
                traded_value=(close_price * Decimal(volume)).quantize(Decimal("0.01")),
            )
        )
    actions: list[SyntheticAction] = []
    if rows > 800:
        actions.append(SyntheticAction("A-SPLIT", "STOCK_SPLIT", dates[800], Decimal("2"), Decimal("1")))
    if rows > 1600:
        actions.append(SyntheticAction("A-BONUS", "BONUS", dates[1600], Decimal("1"), Decimal("1")))
    return prices, actions


def _points(prices: list[object]) -> list[PricePoint]:
    return [
        PricePoint(
            trading_date=price.trading_date,
            open=Decimal(price.open),
            high=Decimal(price.high),
            low=Decimal(price.low),
            close=Decimal(price.close),
            volume=price.volume,
            traded_value=Decimal(price.traded_value) if price.traded_value is not None else None,
            source=price.source,
        )
        for price in prices
    ]


def _fingerprint_update(digest: "hashlib._Hash", security_number: int, values: dict[str, object]) -> None:
    digest.update(str(security_number).encode())
    for code in CORE_TECHNICAL_SET.feature_codes:
        value = values[code]
        digest.update(code.encode())
        digest.update(("null" if value is None else str(value)).encode())


def run_case(
    securities: int,
    rows: int,
    mode: BenchmarkMode,
    adjustment_policy: AdjustmentPolicy,
    measure_memory: bool,
) -> BenchmarkResult:
    if securities < 1 or rows < 1:
        raise ValueError("securities and rows must be positive")
    if measure_memory:
        tracemalloc.start()
    started = time.perf_counter()
    measured = {"input": 0.0, "adjustment": 0.0, "calculation": 0.0, "fingerprint": 0.0}
    digest = hashlib.sha256()
    materialized = 0
    adjustment_service = PriceAdjustmentService()
    for security_number in range(securities):
        phase = time.perf_counter()
        raw_prices, actions = build_dataset(security_number, rows)
        measured["input"] += time.perf_counter() - phase

        phase = time.perf_counter()
        effective_prices = (
            adjustment_service.adjust(raw_prices, actions)
            if adjustment_policy == "ADJUSTED"
            else raw_prices
        )
        points = _points(effective_prices)
        measured["adjustment"] += time.perf_counter() - phase

        phase = time.perf_counter()
        frame = calculate_feature_frame(points) if mode != "latest" else None
        latest = calculate_latest_feature_snapshot(points) if mode == "latest" else None
        measured["calculation"] += time.perf_counter() - phase

        phase = time.perf_counter()
        if mode == "full":
            assert frame is not None
            materialized += sum(len(values) for values in frame)
            for row_number, values in enumerate(frame):
                _fingerprint_update(digest, security_number * rows + row_number, values)
        elif mode == "latest-baseline":
            assert frame is not None
            latest = frame[-1]
            materialized += len(latest)
            _fingerprint_update(digest, security_number, latest)
        else:
            assert latest is not None
            materialized += len(latest)
            _fingerprint_update(digest, security_number, latest)
        measured["fingerprint"] += time.perf_counter() - phase
    total = time.perf_counter() - started
    peak_memory_mib: float | None = None
    if measure_memory:
        _, peak = tracemalloc.get_traced_memory()
        peak_memory_mib = round(peak / (1024 * 1024), 3)
        tracemalloc.stop()
    accounted = sum(measured.values())
    return BenchmarkResult(
        mode=mode,
        adjustment_policy=adjustment_policy,
        securities=securities,
        rows_per_security=rows,
        feature_count=len(FEATURE_DEFINITIONS),
        feature_values_materialized=materialized,
        input_prep_seconds=round(measured["input"], 6),
        adjusted_prep_seconds=round(measured["adjustment"], 6),
        feature_calculation_seconds=round(measured["calculation"], 6),
        fingerprint_seconds=round(measured["fingerprint"], 6),
        orchestration_seconds=round(max(0.0, total - accounted), 6),
        total_seconds=round(total, 6),
        rows_per_second=round((securities * rows) / total, 2),
        peak_memory_mib=peak_memory_mib,
        digest=digest.hexdigest(),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark deterministic AlphaDesk technical-feature workloads.")
    parser.add_argument("--securities", nargs="+", type=int, default=[1, 10, 50, 200])
    parser.add_argument("--rows", type=int, default=2_500)
    parser.add_argument("--mode", choices=("full", "latest-baseline", "latest"), default="full")
    parser.add_argument("--adjustment-policy", choices=("RAW", "ADJUSTED"), default="ADJUSTED")
    parser.add_argument("--memory", action="store_true", help="Measure peak Python allocation with tracemalloc (slower).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = [
        asdict(run_case(count, args.rows, args.mode, args.adjustment_policy, args.memory))
        for count in args.securities
    ]
    print(
        json.dumps(
            {
                "environment": {
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "feature_set": CORE_TECHNICAL_SET.code,
                    "feature_set_version": CORE_TECHNICAL_SET.version,
                    "synthetic_data": True,
                    "network_access": False,
                },
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
