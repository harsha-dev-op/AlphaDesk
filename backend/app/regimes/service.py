from __future__ import annotations

import bisect
import math
import time
from collections import Counter, deque
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.backtests.analytics import calculate_analytics
from app.backtests.costs import money
from app.backtests.fingerprints import fingerprint
from app.models import MarketIndex
from app.regimes.definitions import (
    DEFAULT_BENCHMARK,
    MARKET_REGIME_4_STATE_V1,
    REGIME_DEFINITIONS,
    MarketRegimeDefinition,
    RegimeClassification,
    find_regime_definition,
)
from app.regimes.schemas import (
    BenchmarkCoverage,
    BenchmarkMetadata,
    RegimeAttributionMetrics,
    RegimeAttributionTimings,
    RegimeDefinitionMetadata,
    RegimeDistribution,
    RegimeEvaluation,
    RegimeHistoryRequest,
    RegimeHistoryResponse,
    RegimeHistoryTimings,
    RegimeMetadataResponse,
    RegimeResearchAttributionRequest,
    RegimeResearchAttributionResponse,
    RegimeTransition,
)
from app.repositories.indices import (
    IndexPriceCoverageRecord,
    IndexPriceRecord,
    IndexRepository,
)
from app.research.historical import HistoricalCompositionService
from app.schemas.backtests import SimulatedTrade
from app.technical.calculators import PricePoint, calculate_feature_frame


REGIME_ORDER: tuple[RegimeClassification, ...] = (
    "TRENDING_BULL",
    "TRENDING_BEAR",
    "SIDEWAYS",
    "HIGH_VOLATILITY",
    "INSUFFICIENT_HISTORY",
)
REGIME_RESEARCH_DISCLAIMER = (
    "Explainable point-in-time market-regime research only — not predictive, not "
    "investment advice, and never used to filter or resize trades."
)


class RegimeNotFoundError(ValueError):
    pass


class RegimeValidationError(ValueError):
    pass


def nearest_rank_percentile(
    ordered_values: list[Decimal], percentile: Decimal
) -> Decimal | None:
    """Return ceil(p*N), one-based, from an already sorted non-empty window."""
    if not ordered_values:
        return None
    rank = math.ceil(percentile * len(ordered_values))
    rank = max(1, min(rank, len(ordered_values)))
    return ordered_values[rank - 1]


def classify_regime(
    *,
    close: Decimal,
    sma_50: Decimal | None,
    sma_200: Decimal | None,
    momentum_3m_63d: Decimal | None,
    volatility_20: Decimal | None,
    volatility_threshold: Decimal | None,
    prior_valid_volatility_count: int,
    definition: MarketRegimeDefinition = MARKET_REGIME_4_STATE_V1,
) -> tuple[RegimeClassification, bool, bool, bool, list[str]]:
    missing = [
        code
        for code, value in (
            ("SMA_50", sma_50),
            ("SMA_200", sma_200),
            ("MOM_3M_63D", momentum_3m_63d),
            ("VOLATILITY_20", volatility_20),
        )
        if value is None
    ]
    if missing or (
        prior_valid_volatility_count
        < definition.minimum_prior_volatility_observations
    ):
        reasons = [f"MISSING_REQUIRED_INPUT:{code}" for code in missing]
        if (
            prior_valid_volatility_count
            < definition.minimum_prior_volatility_observations
        ):
            reasons.append(
                "PRIOR_VOLATILITY_HISTORY_BELOW_"
                f"{definition.minimum_prior_volatility_observations}"
            )
        return "INSUFFICIENT_HISTORY", False, False, False, reasons

    assert sma_50 is not None
    assert sma_200 is not None
    assert momentum_3m_63d is not None
    assert volatility_20 is not None
    assert volatility_threshold is not None
    high_volatility = volatility_20 >= volatility_threshold
    bull = close > sma_200 and sma_50 > sma_200 and momentum_3m_63d > 0
    bear = close < sma_200 and sma_50 < sma_200 and momentum_3m_63d < 0
    if high_volatility:
        return (
            "HIGH_VOLATILITY",
            True,
            bull,
            bear,
            ["VOLATILITY_20_AT_OR_ABOVE_PRIOR_NEAREST_RANK_THRESHOLD"],
        )
    if bull:
        return "TRENDING_BULL", False, True, False, ["ALL_BULL_CONDITIONS_MET"]
    if bear:
        return "TRENDING_BEAR", False, False, True, ["ALL_BEAR_CONDITIONS_MET"]
    return "SIDEWAYS", False, False, False, ["NO_HIGH_VOLATILITY_OR_TREND_RULE_MATCH"]


def _definition_metadata(
    definition: MarketRegimeDefinition,
) -> RegimeDefinitionMetadata:
    return RegimeDefinitionMetadata(
        **asdict(definition),
        regimes=[
            "TRENDING_BULL",
            "TRENDING_BEAR",
            "SIDEWAYS",
            "HIGH_VOLATILITY",
        ],
        rule_explanation=(
            "Require all trend inputs and at least 126 prior valid VOLATILITY_20 "
            "observations. Then apply high volatility first, bull second, bear third, "
            "and sideways otherwise. The current observation is excluded from its "
            "own 252-observation nearest-rank threshold."
        ),
    )


def _coverage(
    index: MarketIndex,
    source_mode: str,
    record: IndexPriceCoverageRecord | None,
) -> BenchmarkCoverage:
    return BenchmarkCoverage(
        benchmark_id=index.id,
        benchmark_symbol=index.symbol,
        benchmark_name=index.name,
        benchmark_provider=index.provider,
        source_mode=source_mode,
        first_available_date=record.first_date if record else None,
        last_available_date=record.last_date if record else None,
        session_count=record.session_count if record else 0,
        enough_history_for_regime=bool(
            record and record.first_classifiable_date is not None
        ),
        first_classifiable_date=record.first_classifiable_date if record else None,
    )


def _benchmark_metadata(
    index: MarketIndex, coverages: list[BenchmarkCoverage]
) -> BenchmarkMetadata:
    return BenchmarkMetadata(
        id=index.id,
        symbol=index.symbol,
        name=index.name,
        provider=index.provider,
        exchange=index.exchange,
        coverage=coverages,
    )


def _price_point(row: IndexPriceRecord) -> PricePoint:
    return PricePoint(
        trading_date=row.trading_date,
        open=Decimal(row.open),
        high=Decimal(row.high),
        low=Decimal(row.low),
        close=Decimal(row.close),
        volume=0,
        traded_value=None,
        source=row.source,
    )


class MarketRegimeService:
    def __init__(self, session: Session):
        self.session = session
        self.indices = IndexRepository(session)

    @staticmethod
    def _definition(code: str, version: str) -> MarketRegimeDefinition:
        definition = find_regime_definition(code, version)
        if definition is not None:
            return definition
        if any(item.code == code for item in REGIME_DEFINITIONS.values()):
            raise RegimeNotFoundError(
                f"Unsupported market regime definition version: {code} v{version}"
            )
        raise RegimeNotFoundError(f"Unknown market regime definition: {code}")

    def metadata(self) -> RegimeMetadataResponse:
        indices = self.indices.list()
        catalog = self.indices.price_coverage_catalog()
        by_index: dict[object, list[IndexPriceCoverageRecord]] = {}
        for item in catalog:
            by_index.setdefault(item.index_id, []).append(item)
        benchmarks = [
            _benchmark_metadata(
                index,
                [
                    _coverage(index, record.source_mode, record)
                    for record in by_index.get(index.id, [])
                ],
            )
            for index in indices
        ]
        return RegimeMetadataResponse(
            definitions=[
                _definition_metadata(definition)
                for definition in REGIME_DEFINITIONS.values()
            ],
            benchmarks=benchmarks,
            default_benchmark=DEFAULT_BENCHMARK,
            supported_source_modes=["DEMO", "OFFICIAL"],
            research_disclaimer=REGIME_RESEARCH_DISCLAIMER,
        )

    def history(self, request: RegimeHistoryRequest) -> RegimeHistoryResponse:
        started = time.perf_counter()
        definition = self._definition(
            request.regime_definition_code, request.regime_definition_version
        )
        index = self.indices.get_by_name_or_symbol(request.benchmark)
        if index is None:
            raise RegimeNotFoundError("Benchmark index not found")

        phase = time.perf_counter()
        effective_as_of = request.as_of or datetime.now(UTC)
        records = sorted(
            self.indices.price_history(
                index.id, source_mode=request.source_mode, as_of=effective_as_of
            ),
            key=lambda item: (
                item.trading_date,
                item.available_at,
                str(item.id),
            ),
        )
        repository_load_ms = (time.perf_counter() - phase) * 1_000
        benchmark = _benchmark_metadata(index, [])
        if not records:
            coverage = _coverage(index, request.source_mode, None)
            benchmark.coverage = [coverage]
            empty_fingerprint = fingerprint(
                {
                    "benchmark_id": str(index.id),
                    "definition": asdict(definition),
                    "request": request.model_dump(mode="json"),
                    "source_mode": request.source_mode,
                    "dataset": [],
                }
            )
            return RegimeHistoryResponse(
                availability="UNAVAILABLE",
                normalized_request=request,
                benchmark=benchmark,
                definition=_definition_metadata(definition),
                coverage=coverage,
                classifications=[],
                distributions=[
                    RegimeDistribution(
                        classification=classification,
                        session_count=0,
                        percentage_of_classified_sessions=None,
                    )
                    for classification in REGIME_ORDER
                ],
                insufficient_history_sessions=0,
                latest_classification=None,
                latest_regime_start_date=None,
                latest_regime_duration_sessions=0,
                transitions=[],
                regime_timeline_fingerprint=empty_fingerprint,
                benchmark_dataset_fingerprint=fingerprint([]),
                timings=RegimeHistoryTimings(
                    repository_load_ms=round(repository_load_ms, 3),
                    feature_generation_ms=0,
                    classification_ms=0,
                    response_build_ms=0,
                    total_service_ms=round((time.perf_counter() - started) * 1_000, 3),
                ),
                warnings=["BENCHMARK_SOURCE_COVERAGE_UNAVAILABLE"],
                research_disclaimer=REGIME_RESEARCH_DISCLAIMER,
            )

        phase = time.perf_counter()
        frame = calculate_feature_frame(
            [_price_point(row) for row in records], definition.required_feature_codes
        )
        feature_generation_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        full_evaluations = self._classify_timeline(records, frame, definition)
        classifications = [
            item
            for item in full_evaluations
            if request.start_date <= item.observation_date <= request.end_date
        ]
        transitions = self._transitions(classifications, index.symbol)
        counts = Counter(item.classification for item in classifications)
        classified_count = sum(
            counts[item]
            for item in REGIME_ORDER
            if item != "INSUFFICIENT_HISTORY"
        )
        distributions = [
            RegimeDistribution(
                classification=classification,
                session_count=counts[classification],
                percentage_of_classified_sessions=(
                    (Decimal(counts[classification]) / Decimal(classified_count)).quantize(
                        Decimal("0.00000001")
                    )
                    if classified_count and classification != "INSUFFICIENT_HISTORY"
                    else None
                ),
            )
            for classification in REGIME_ORDER
        ]
        latest = classifications[-1] if classifications else None
        latest_start = None
        latest_duration = 0
        if latest is not None:
            latest_start = latest.observation_date
            for item in reversed(classifications):
                if item.classification != latest.classification:
                    break
                latest_start = item.observation_date
                latest_duration += 1

        used_records = [row for row in records if row.trading_date <= request.end_date]
        dataset_payload = [
            {
                "date": row.trading_date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "source_mode": row.source_mode,
                "source": row.source,
                "available_at": row.available_at,
                "source_artifact_id": row.source_artifact_id,
                "ingestion_run_id": row.ingestion_run_id,
            }
            for row in used_records
        ]
        dataset_fingerprint = fingerprint(
            {"benchmark_id": str(index.id), "prices": dataset_payload}
        )
        timeline_fingerprint = fingerprint(
            {
                "benchmark_id": str(index.id),
                "definition": asdict(definition),
                "start_date": request.start_date,
                "end_date": request.end_date,
                "as_of": request.as_of,
                "source_mode": request.source_mode,
                "benchmark_dataset_fingerprint": dataset_fingerprint,
                "ordered_evaluations": [
                    {
                        "date": item.observation_date,
                        "classification": item.classification,
                        "close": item.close,
                        "sma_50": item.sma_50,
                        "sma_200": item.sma_200,
                        "momentum_3m_63d": item.momentum_3m_63d,
                        "volatility_20": item.volatility_20,
                        "historical_volatility_threshold": (
                            item.historical_volatility_threshold
                        ),
                    }
                    for item in classifications
                ],
            }
        )
        classification_ms = (time.perf_counter() - phase) * 1_000

        all_count = len(records)
        first_classifiable = next(
            (
                item.observation_date
                for item in full_evaluations
                if item.classification != "INSUFFICIENT_HISTORY"
            ),
            None,
        )
        coverage = BenchmarkCoverage(
            benchmark_id=index.id,
            benchmark_symbol=index.symbol,
            benchmark_name=index.name,
            benchmark_provider=index.provider,
            source_mode=request.source_mode,
            first_available_date=records[0].trading_date,
            last_available_date=records[-1].trading_date,
            session_count=all_count,
            enough_history_for_regime=first_classifiable is not None,
            first_classifiable_date=first_classifiable,
        )
        benchmark.coverage = [coverage]
        response_phase = time.perf_counter()
        response = RegimeHistoryResponse(
            availability="AVAILABLE",
            normalized_request=request,
            benchmark=benchmark,
            definition=_definition_metadata(definition),
            coverage=coverage,
            classifications=classifications,
            distributions=distributions,
            insufficient_history_sessions=counts["INSUFFICIENT_HISTORY"],
            latest_classification=latest,
            latest_regime_start_date=latest_start,
            latest_regime_duration_sessions=latest_duration,
            transitions=transitions,
            regime_timeline_fingerprint=timeline_fingerprint,
            benchmark_dataset_fingerprint=dataset_fingerprint,
            timings=RegimeHistoryTimings(
                repository_load_ms=round(repository_load_ms, 3),
                feature_generation_ms=round(feature_generation_ms, 3),
                classification_ms=round(classification_ms, 3),
                response_build_ms=0,
                total_service_ms=0,
            ),
            warnings=(
                ["NO_BENCHMARK_SESSIONS_IN_REQUESTED_RANGE"]
                if not classifications
                else []
            ),
            research_disclaimer=REGIME_RESEARCH_DISCLAIMER,
        )
        response.timings.response_build_ms = round(
            (time.perf_counter() - response_phase) * 1_000, 3
        )
        response.timings.total_service_ms = round(
            (time.perf_counter() - started) * 1_000, 3
        )
        return response

    @staticmethod
    def _classify_timeline(
        records: list[IndexPriceRecord],
        frame: list[dict[str, Decimal | bool | None]],
        definition: MarketRegimeDefinition,
    ) -> list[RegimeEvaluation]:
        prior_window: deque[Decimal] = deque()
        ordered_window: list[Decimal] = []
        output: list[RegimeEvaluation] = []
        for row, values in zip(records, frame, strict=True):
            threshold = (
                nearest_rank_percentile(
                    ordered_window, definition.volatility_percentile
                )
                if len(ordered_window)
                >= definition.minimum_prior_volatility_observations
                else None
            )
            classification, high, bull, bear, reasons = classify_regime(
                close=Decimal(row.close),
                sma_50=values["SMA_50"],
                sma_200=values["SMA_200"],
                momentum_3m_63d=values["MOM_3M_63D"],
                volatility_20=values["VOLATILITY_20"],
                volatility_threshold=threshold,
                prior_valid_volatility_count=len(ordered_window),
                definition=definition,
            )
            evaluation_fingerprint = fingerprint(
                {
                    "date": row.trading_date,
                    "available_at": row.available_at,
                    "classification": classification,
                    "close": row.close,
                    "sma_50": values["SMA_50"],
                    "sma_200": values["SMA_200"],
                    "momentum_3m_63d": values["MOM_3M_63D"],
                    "volatility_20": values["VOLATILITY_20"],
                    "historical_volatility_threshold": threshold,
                    "definition": (definition.code, definition.version),
                }
            )
            output.append(
                RegimeEvaluation(
                    observation_date=row.trading_date,
                    available_at=row.available_at,
                    status=(
                        "INSUFFICIENT_HISTORY"
                        if classification == "INSUFFICIENT_HISTORY"
                        else "CLASSIFIED"
                    ),
                    classification=classification,
                    regime=(
                        None
                        if classification == "INSUFFICIENT_HISTORY"
                        else classification
                    ),
                    close=row.close,
                    sma_50=values["SMA_50"],
                    sma_200=values["SMA_200"],
                    momentum_3m_63d=values["MOM_3M_63D"],
                    volatility_20=values["VOLATILITY_20"],
                    historical_volatility_threshold=threshold,
                    prior_valid_volatility_count=len(ordered_window),
                    high_volatility_triggered=high,
                    bull_conditions_met=bull,
                    bear_conditions_met=bear,
                    reasons=reasons,
                    evaluation_fingerprint=evaluation_fingerprint,
                )
            )
            current_volatility = values["VOLATILITY_20"]
            if isinstance(current_volatility, Decimal):
                if len(prior_window) == definition.volatility_percentile_window:
                    expired = prior_window.popleft()
                    position = bisect.bisect_left(ordered_window, expired)
                    ordered_window.pop(position)
                prior_window.append(current_volatility)
                bisect.insort(ordered_window, current_volatility)
        return output

    @staticmethod
    def _transitions(
        classifications: list[RegimeEvaluation], benchmark_symbol: str
    ) -> list[RegimeTransition]:
        output: list[RegimeTransition] = []
        if not classifications:
            return output
        previous = classifications[0]
        duration = 1
        for current in classifications[1:]:
            if current.classification == previous.classification:
                duration += 1
                continue
            output.append(
                RegimeTransition(
                    transition_date=current.observation_date,
                    previous_classification=previous.classification,
                    new_classification=current.classification,
                    previous_regime_duration_sessions=duration,
                    benchmark_symbol=benchmark_symbol,
                )
            )
            previous = current
            duration = 1
        return output

    def research_attribution(
        self, request: RegimeResearchAttributionRequest
    ) -> RegimeResearchAttributionResponse:
        started = time.perf_counter()
        historical = HistoricalCompositionService(self.session)
        phase = time.perf_counter()
        prepared = historical.prepare(request.backtest)
        historical_preparation_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        execution = historical.execute_backtest_prepared(request.backtest, prepared)
        backtest_execution_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        regime_history = self.history(
            RegimeHistoryRequest(
                benchmark=request.benchmark,
                start_date=request.backtest.start_date,
                end_date=request.backtest.end_date,
                source_mode=request.source_mode,
                regime_definition_code=request.regime_definition_code,
                regime_definition_version=request.regime_definition_version,
                as_of=request.as_of,
            )
        )
        regime_timeline_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        regime_by_date = {
            item.observation_date: item.classification
            for item in regime_history.classifications
        }
        setup_buckets: dict[RegimeClassification, list[object]] = {
            key: [] for key in REGIME_ORDER
        }
        for setup in prepared.setups:
            classification = regime_by_date.get(
                setup.signal_date, "INSUFFICIENT_HISTORY"
            )
            setup_buckets[classification].append(setup)
        trade_buckets: dict[RegimeClassification, list[SimulatedTrade]] = {
            key: [] for key in REGIME_ORDER
        }
        for trade in execution.trades:
            classification = regime_by_date.get(
                trade.signal_date, "INSUFFICIENT_HISTORY"
            )
            trade_buckets[classification].append(trade)

        overall = self._attribution_metrics(
            "OVERALL", len(prepared.setups), list(execution.trades)
        )
        buckets = [
            self._attribution_metrics(
                classification,
                len(setup_buckets[classification]),
                trade_buckets[classification],
            )
            for classification in REGIME_ORDER
        ]
        attributed_trade_count = sum(item.executed_trade_count for item in buckets)
        attributed_net_pnl = money(
            sum((item.net_pnl for item in buckets), Decimal(0))
        )
        trade_reconciled = attributed_trade_count == len(execution.trades)
        pnl_reconciled = attributed_net_pnl == execution.response.analytics.total_net_pnl
        if not trade_reconciled or not pnl_reconciled:
            raise RegimeValidationError(
                "Regime attribution failed to reconcile with the authoritative backtest"
            )
        mapping = [
            {
                "signal_result_fingerprint": setup.signal_result_fingerprint,
                "signal_date": setup.signal_date,
                "classification": regime_by_date.get(
                    setup.signal_date, "INSUFFICIENT_HISTORY"
                ),
            }
            for setup in sorted(
                prepared.setups,
                key=lambda item: (
                    item.signal_date,
                    item.symbol.casefold(),
                    str(item.security_id),
                ),
            )
        ]
        attribution_fingerprint = fingerprint(
            {
                "historical_signal_fingerprint": prepared.signal_fingerprint,
                "backtest_run_fingerprint": execution.response.backtest_run_fingerprint,
                "regime_timeline_fingerprint": (
                    regime_history.regime_timeline_fingerprint
                ),
                "signal_date_regime_mapping": mapping,
                "attribution": [item.model_dump(mode="json") for item in buckets],
            }
        )
        attribution_ms = (time.perf_counter() - phase) * 1_000
        return RegimeResearchAttributionResponse(
            normalized_request=request,
            backtest=execution.response,
            regime_history=regime_history,
            overall=overall,
            buckets=buckets,
            attributed_trade_count=attributed_trade_count,
            trade_count_reconciled=trade_reconciled,
            attributed_net_pnl=attributed_net_pnl,
            net_pnl_reconciled=pnl_reconciled,
            regime_attribution_fingerprint=attribution_fingerprint,
            timings=RegimeAttributionTimings(
                historical_preparation_ms=round(historical_preparation_ms, 3),
                backtest_execution_ms=round(backtest_execution_ms, 3),
                regime_timeline_ms=round(regime_timeline_ms, 3),
                attribution_ms=round(attribution_ms, 3),
                total_service_ms=round((time.perf_counter() - started) * 1_000, 3),
            ),
            warnings=(
                ["BENCHMARK_COVERAGE_UNAVAILABLE_FOR_REQUESTED_SOURCE"]
                if regime_history.availability == "UNAVAILABLE"
                else []
            ),
            research_disclaimer=REGIME_RESEARCH_DISCLAIMER,
        )

    @staticmethod
    def _attribution_metrics(
        classification: RegimeClassification | str,
        signal_count: int,
        trades: list[SimulatedTrade],
    ) -> RegimeAttributionMetrics:
        analytics = calculate_analytics(
            trades,
            signal_count=signal_count,
            executable_setup_count=len(trades),
        )
        net_pnls = [
            trade.net_pnl for trade in trades if trade.net_pnl is not None
        ]
        winners = [value for value in net_pnls if value > 0]
        losers = [value for value in net_pnls if value < 0]
        return RegimeAttributionMetrics(
            classification=classification,
            signal_count=signal_count,
            executed_trade_count=len(trades),
            skipped_setup_count=signal_count - len(trades),
            winning_trade_count=analytics.winning_trades,
            losing_trade_count=analytics.losing_trades,
            breakeven_trade_count=analytics.breakeven_trades,
            win_rate=analytics.win_rate,
            gross_profit=money(sum(winners, Decimal(0))),
            gross_loss=money(sum(losers, Decimal(0))),
            net_pnl=analytics.total_net_pnl,
            mean_net_return_pct=analytics.average_net_return,
            median_net_return_pct=analytics.median_net_return,
            average_holding_sessions=analytics.average_holding_sessions,
            profit_factor=analytics.profit_factor,
            expectancy=analytics.expectancy_per_trade,
            average_win=(
                money(sum(winners, Decimal(0)) / Decimal(len(winners)))
                if winners
                else None
            ),
            average_loss=(
                money(sum(losers, Decimal(0)) / Decimal(len(losers)))
                if losers
                else None
            ),
        )


__all__ = [
    "MarketRegimeService",
    "RegimeNotFoundError",
    "RegimeValidationError",
    "classify_regime",
    "nearest_rank_percentile",
]
