from __future__ import annotations

import hashlib
import time as runtime
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import CorporateAction, DataIngestionRun, Security, TradingCalendar
from app.repositories.securities import PriceRecord, SecurityRepository
from app.services.adjustments import PriceAdjustmentService
from app.technical.calculators import PricePoint, calculate_feature_frame
from app.technical.definitions import FEATURE_DEFINITIONS, FeatureSetDefinition
from app.technical.service import MARKET_TIMEZONE, TechnicalFeatureService


@dataclass(frozen=True, slots=True)
class HistoricalFeatureObservation:
    observation_date: date
    available_at: datetime
    values: dict[str, Decimal | bool | None]
    unavailable: dict[str, str]
    input_fingerprint: str
    eligible_actions: tuple[CorporateAction, ...]


@dataclass(frozen=True, slots=True)
class HistoricalSecuritySeries:
    security: Security
    prices: tuple[PriceRecord, ...]
    action_history: tuple[CorporateAction, ...]
    observations: dict[date, HistoricalFeatureObservation]
    dataset_fingerprint: str
    quality_warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistoricalFeatureBatch:
    series: tuple[HistoricalSecuritySeries, ...]
    ingestion_run: DataIngestionRun | None
    repository_load_ms: float
    calculation_ms: float


def _point(row: object) -> PricePoint:
    return PricePoint(
        trading_date=row.trading_date,
        open=Decimal(row.open),
        high=Decimal(row.high),
        low=Decimal(row.low),
        close=Decimal(row.close),
        volume=int(row.volume),
        traded_value=Decimal(row.traded_value) if row.traded_value is not None else None,
        source=row.source,
    )


def _known_at(value: datetime, cutoff: datetime) -> bool:
    if value.tzinfo is None or value.utcoffset() is None:
        return value <= cutoff.astimezone(MARKET_TIMEZONE).replace(tzinfo=None)
    return value.astimezone(UTC) <= cutoff.astimezone(UTC)


def _action_payload(action: CorporateAction) -> str:
    return (
        f"{action.id}|{action.supersedes_action_id}|{action.action_type}|"
        f"{action.announcement_date}|{action.ex_date}|{action.record_date}|"
        f"{action.ratio_numerator}|{action.ratio_denominator}|{action.cash_amount}|"
        f"{action.source}|{action.source_published_at}|{action.available_at}"
    )


def _decision_at(security: Security, day: date, calendar: dict[tuple[str, date], TradingCalendar]) -> datetime:
    entry = calendar.get((security.exchange, day))
    close = entry.session_close if entry and entry.session_close else time(23, 59, 59)
    return datetime.combine(day, close, tzinfo=MARKET_TIMEZONE)


class HistoricalTechnicalSeriesService:
    """Efficient point-in-time historical series using the authoritative calculators."""

    def __init__(self, session: Session):
        self.securities = SecurityRepository(session)
        self.technical = TechnicalFeatureService(session)
        self.adjustments = PriceAdjustmentService()

    def compute_batch(
        self,
        securities: list[Security],
        *,
        start_date: date,
        end_date: date,
        adjustment_policy: str,
        feature_set: FeatureSetDefinition,
        calendar_entries: dict[tuple[str, date], TradingCalendar],
        required_feature_codes: tuple[str, ...] | None = None,
        batch_size: int = 50,
    ) -> HistoricalFeatureBatch:
        if adjustment_policy not in {"RAW", "ADJUSTED"}:
            raise ValueError("adjustment_policy must be RAW or ADJUSTED")
        if batch_size < 1 or batch_size > 200:
            raise ValueError("batch_size must be between 1 and 200")
        if start_date > end_date:
            raise ValueError("start_date cannot be after end_date")

        run = self.technical._latest_successful_run()
        if not securities:
            return HistoricalFeatureBatch(series=(), ingestion_run=run, repository_load_ms=0.0, calculation_ms=0.0)
        end_cutoffs = [
            _decision_at(security, end_date, calendar_entries)
            for security in securities
        ]
        max_as_of = max(end_cutoffs)
        output: list[HistoricalSecuritySeries] = []
        repository_load_ms = 0.0
        calculation_ms = 0.0

        for chunk_start in range(0, len(securities), batch_size):
            chunk = securities[chunk_start : chunk_start + batch_size]
            ids = [security.id for security in chunk]
            phase = runtime.perf_counter()
            prices_by_security = self.securities.prices_for_securities(ids, end=end_date)
            actions_by_security = self.securities.corporate_action_history_for_securities(
                ids,
                as_of=max_as_of,
            )
            repository_load_ms += (runtime.perf_counter() - phase) * 1_000
            for security in chunk:
                phase = runtime.perf_counter()
                output.append(
                    self._compute_security(
                        security,
                        prices_by_security[security.id],
                        actions_by_security[security.id],
                        start_date=start_date,
                        end_date=end_date,
                        adjustment_policy=adjustment_policy,
                        feature_set=feature_set,
                        calendar_entries=calendar_entries,
                        required_feature_codes=required_feature_codes,
                    )
                )
                calculation_ms += (runtime.perf_counter() - phase) * 1_000
        return HistoricalFeatureBatch(
            series=tuple(output),
            ingestion_run=run,
            repository_load_ms=repository_load_ms,
            calculation_ms=calculation_ms,
        )

    def _compute_security(
        self,
        security: Security,
        prices: list[PriceRecord],
        action_history: list[CorporateAction],
        *,
        start_date: date,
        end_date: date,
        adjustment_policy: str,
        feature_set: FeatureSetDefinition,
        calendar_entries: dict[tuple[str, date], TradingCalendar],
        required_feature_codes: tuple[str, ...] | None,
    ) -> HistoricalSecuritySeries:
        relevant_ids = {action.id for action in action_history if action.ex_date <= end_date}
        changed = True
        while changed:
            changed = False
            for action in action_history:
                if action.id in relevant_ids and action.supersedes_action_id and action.supersedes_action_id not in relevant_ids:
                    relevant_ids.add(action.supersedes_action_id)
                    changed = True
                if action.supersedes_action_id in relevant_ids and action.id not in relevant_ids:
                    relevant_ids.add(action.id)
                    changed = True
        action_history = [action for action in action_history if action.id in relevant_ids]
        points = [_point(row) for row in prices]
        selected_codes = required_feature_codes or feature_set.feature_codes
        raw_frame = calculate_feature_frame(points, selected_codes)
        frame_cache: dict[tuple[UUID, ...], list[dict[str, Decimal | bool | None]]] = {(): raw_frame}

        prefix = hashlib.sha256(
            f"{security.id}|{adjustment_policy}|{feature_set.code}|{feature_set.version}".encode()
        )
        prefix_fingerprints: dict[date, object] = {}
        for row in prices:
            prefix.update(
                (
                    f"|{row.trading_date}|{row.open}|{row.high}|{row.low}|{row.close}|"
                    f"{row.volume}|{row.traded_value}|{row.source}"
                ).encode()
            )
            prefix_fingerprints[row.trading_date] = prefix.copy()

        observations: dict[date, HistoricalFeatureObservation] = {}
        for index, row in enumerate(prices):
            day = row.trading_date
            if day < start_date or day > end_date:
                continue
            decision_at = _decision_at(security, day, calendar_entries)
            known = SecurityRepository._resolve_action_revisions(
                [
                    action
                    for action in action_history
                    if _known_at(action.available_at, decision_at)
                ]
            )
            eligible = [action for action in known if action.ex_date <= day]
            action_key = tuple(action.id for action in eligible)
            if adjustment_policy == "ADJUSTED" and action_key not in frame_cache:
                adjusted = self.adjustments.adjust(prices, eligible)
                frame_cache[action_key] = calculate_feature_frame([_point(item) for item in adjusted], selected_codes)
            frame = raw_frame if adjustment_policy == "RAW" else frame_cache[action_key]
            values = {code: frame[index][code] for code in selected_codes}
            unavailable = TechnicalFeatureService._unavailable(values, index + 1)
            signal_digest = prefix_fingerprints[day].copy()
            for action in eligible:
                signal_digest.update(f"|{_action_payload(action)}".encode())
            observations[day] = HistoricalFeatureObservation(
                observation_date=day,
                available_at=decision_at,
                values=values,
                unavailable=unavailable,
                input_fingerprint=signal_digest.hexdigest(),
                eligible_actions=tuple(eligible),
            )

        dataset_digest = prefix.copy()
        for action in action_history:
            dataset_digest.update(f"|{_action_payload(action)}".encode())
        expected_dates = {
            day
            for (exchange, day), entry in calendar_entries.items()
            if exchange == security.exchange and entry.is_trading_day and start_date <= day <= end_date
        }
        observed_dates = {row.trading_date for row in prices if start_date <= row.trading_date <= end_date}
        missing = sorted(expected_dates - observed_dates)
        warnings: list[str] = []
        if missing:
            preview = ", ".join(day.isoformat() for day in missing[:8])
            if len(missing) > 8:
                preview += f" (+{len(missing) - 8} more)"
            warnings.append(f"Missing expected trading sessions: {preview}")

        return HistoricalSecuritySeries(
            security=security,
            prices=tuple(prices),
            action_history=tuple(action_history),
            observations=observations,
            dataset_fingerprint=dataset_digest.hexdigest(),
            quality_warnings=tuple(warnings),
        )


__all__ = [
    "HistoricalFeatureBatch",
    "HistoricalFeatureObservation",
    "HistoricalSecuritySeries",
    "HistoricalTechnicalSeriesService",
]
