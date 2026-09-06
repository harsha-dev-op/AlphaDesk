from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CorporateAction, DataIngestionRun, DailyPrice, Security, TradingCalendar
from app.repositories.calendar import TradingCalendarRepository
from app.repositories.securities import SecurityRepository
from app.schemas.technical import DatasetContextResponse, FeatureObservationResponse, FeatureSetResponse, SecurityFeatureSeriesResponse
from app.services.adjustments import PriceAdjustmentService
from app.technical.calculators import PricePoint, calculate_feature_frame, calculate_latest_feature_snapshot
from app.technical.definitions import CORE_TECHNICAL_SET, FEATURE_DEFINITIONS, FeatureSetDefinition

MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")


def _feature_set_response(feature_set: FeatureSetDefinition) -> FeatureSetResponse:
    return FeatureSetResponse(
        code=feature_set.code,
        version=feature_set.version,
        name=feature_set.name,
        description=feature_set.description,
        feature_codes=list(feature_set.feature_codes),
        default_adjustment_policy="ADJUSTED",
        is_active=feature_set.is_active,
    )


def _to_point(row: DailyPrice | object) -> PricePoint:
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


class TechnicalFeatureService:
    def __init__(self, session: Session):
        self.session = session
        self.securities = SecurityRepository(session)
        self.calendar = TradingCalendarRepository(session)
        self.adjustments = PriceAdjustmentService()

    def _available_at(self, security: Security, observation_date: date) -> datetime:
        calendar_entry = self.calendar.entry(security.exchange, observation_date)
        close_time = calendar_entry.session_close if calendar_entry and calendar_entry.session_close else time(23, 59, 59)
        return datetime.combine(observation_date, close_time, tzinfo=MARKET_TIMEZONE)

    def _latest_successful_run(self) -> DataIngestionRun | None:
        return self.session.scalar(
            select(DataIngestionRun)
            .where(DataIngestionRun.status == "SUCCESS")
            .order_by(DataIngestionRun.completed_at.desc(), DataIngestionRun.started_at.desc())
            .limit(1)
        )

    def _dataset_context(
        self,
        security: Security,
        prices: list[DailyPrice | object],
        actions: list[CorporateAction],
        adjustment_policy: str,
        feature_set: FeatureSetDefinition,
        *,
        run: DataIngestionRun | None = None,
        run_loaded: bool = False,
    ) -> DatasetContextResponse:
        if not run_loaded:
            run = self._latest_successful_run()
        digest = hashlib.sha256()
        digest.update(f"{security.id}|{adjustment_policy}|{feature_set.code}|{feature_set.version}".encode())
        for row in prices:
            digest.update(f"|{row.trading_date}|{row.open}|{row.high}|{row.low}|{row.close}|{row.volume}|{row.traded_value}|{row.source}".encode())
        for action in actions:
            digest.update(
                (
                    f"|{action.id}|{action.supersedes_action_id}|{action.action_type}"
                    f"|{action.announcement_date}|{action.ex_date}|{action.record_date}"
                    f"|{action.ratio_numerator}|{action.ratio_denominator}|{action.cash_amount}"
                    f"|{action.source}|{action.source_published_at}|{action.available_at}"
                ).encode()
            )
        return DatasetContextResponse(
            dataset_code=run.dataset_code if run else "UNVERSIONED_LOCAL",
            dataset_version=run.dataset_version if run else "unversioned",
            provider=run.provider if run else (prices[0].source if prices else "unknown"),
            earliest_observation=prices[0].trading_date if prices else None,
            latest_observation=prices[-1].trading_date if prices else None,
            fingerprint=digest.hexdigest(),
        )

    @staticmethod
    def _unavailable(values: dict[str, Decimal | bool | None], observation_number: int) -> dict[str, str]:
        reasons: dict[str, str] = {}
        for code, value in values.items():
            if value is not None:
                continue
            minimum = FEATURE_DEFINITIONS[code].minimum_observations
            reasons[code] = f"Requires {minimum} observations" if observation_number < minimum else "Undefined for available inputs"
        return reasons

    def _adjusted_feature_rows(self, raw_prices: list[DailyPrice], actions: list[CorporateAction]) -> list[dict[str, Decimal | bool | None]]:
        """Compute once per corporate-action regime while preserving earlier snapshots."""
        if not raw_prices:
            return []
        rows: list[dict[str, Decimal | bool | None] | None] = [None] * len(raw_prices)
        segment_start = 0
        active_actions: list[CorporateAction] = []
        action_index = 0

        for index, price in enumerate(raw_prices):
            newly_active: list[CorporateAction] = []
            while action_index < len(actions) and actions[action_index].ex_date <= price.trading_date:
                newly_active.append(actions[action_index])
                action_index += 1
            if newly_active and index > segment_start:
                prefix = raw_prices[:index]
                adjusted = self.adjustments.adjust(prefix, active_actions)
                frame = calculate_feature_frame([_to_point(point) for point in adjusted])
                rows[segment_start:index] = frame[segment_start:index]
                segment_start = index
            active_actions.extend(newly_active)

        adjusted = self.adjustments.adjust(raw_prices, active_actions)
        frame = calculate_feature_frame([_to_point(point) for point in adjusted])
        rows[segment_start:] = frame[segment_start:]
        return [row for row in rows if row is not None]

    def compute(
        self,
        security: Security,
        *,
        start_date: date | None,
        end_date: date | None,
        adjustment_policy: str,
        as_of: datetime | None,
        feature_set: FeatureSetDefinition = CORE_TECHNICAL_SET,
    ) -> SecurityFeatureSeriesResponse:
        computed_at = datetime.now(UTC)
        effective_as_of = as_of or computed_at
        if effective_as_of.tzinfo is None or effective_as_of.utcoffset() is None:
            raise ValueError("as_of must include a timezone offset")

        as_of_local = effective_as_of.astimezone(MARKET_TIMEZONE)
        query_end = min(end_date, as_of_local.date()) if end_date else as_of_local.date()
        raw_prices = self.securities.prices(security.id, start=None, end=query_end)
        actions = self.securities.corporate_actions(
            security.id,
            as_of=effective_as_of,
            after=raw_prices[0].trading_date if raw_prices else None,
            through=query_end,
        )

        feature_rows: list[dict[str, Decimal | bool | None]] = []
        if adjustment_policy == "RAW":
            feature_rows = calculate_feature_frame([_to_point(row) for row in raw_prices])
        else:
            # Results are snapshotted per action regime. A later action can rebase the
            # working history for later rows, but never rewrites already emitted rows.
            feature_rows = self._adjusted_feature_rows(raw_prices, actions)

        items: list[FeatureObservationResponse] = []
        for index, (price, calculated_values) in enumerate(zip(raw_prices, feature_rows, strict=True)):
            values = {code: calculated_values[code] for code in feature_set.feature_codes}
            available_at = self._available_at(security, price.trading_date)
            if start_date and price.trading_date < start_date:
                continue
            if end_date and price.trading_date > end_date:
                continue
            if available_at > effective_as_of:
                continue
            items.append(
                FeatureObservationResponse(
                    observation_date=price.trading_date,
                    available_at=available_at,
                    values=values,
                    unavailable=self._unavailable(values, index + 1),
                )
            )

        warnings: list[str] = []
        if raw_prices:
            expected = self.calendar.trading_days(security.exchange, raw_prices[0].trading_date, query_end)
            observed = {row.trading_date for row in raw_prices}
            missing = [day.isoformat() for day in expected if day not in observed]
            if missing:
                preview = ", ".join(missing[:8]) + (f" (+{len(missing) - 8} more)" if len(missing) > 8 else "")
                warnings.append(f"Missing expected trading sessions: {preview}")

        return SecurityFeatureSeriesResponse(
            security_id=security.id,
            symbol=security.symbol,
            exchange=security.exchange,
            feature_set=_feature_set_response(feature_set),
            feature_versions={code: FEATURE_DEFINITIONS[code].version for code in feature_set.feature_codes},
            adjustment_policy=adjustment_policy,
            dataset=self._dataset_context(security, raw_prices, actions, adjustment_policy, feature_set),
            requested_start=start_date,
            requested_end=end_date,
            as_of=effective_as_of,
            computed_at=computed_at,
            quality_warnings=warnings,
            items=items,
        )

    def compute_latest_batch(
        self,
        securities: list[Security],
        *,
        adjustment_policy: str,
        as_of: datetime | None,
        observation_date: date | None = None,
        batch_size: int = 50,
        feature_set: FeatureSetDefinition = CORE_TECHNICAL_SET,
    ) -> list[SecurityFeatureSeriesResponse]:
        """Compute latest point-in-time-safe snapshots with bounded batch reads."""
        if adjustment_policy not in {"RAW", "ADJUSTED"}:
            raise ValueError("adjustment_policy must be RAW or ADJUSTED")
        if batch_size < 1 or batch_size > 200:
            raise ValueError("batch_size must be between 1 and 200")
        computed_at = datetime.now(UTC)
        effective_as_of = as_of or computed_at
        if effective_as_of.tzinfo is None or effective_as_of.utcoffset() is None:
            raise ValueError("as_of must include a timezone offset")

        as_of_date = effective_as_of.astimezone(MARKET_TIMEZONE).date()
        query_end = min(observation_date, as_of_date) if observation_date else as_of_date
        run = self._latest_successful_run()
        responses: list[SecurityFeatureSeriesResponse] = []
        for chunk_start in range(0, len(securities), batch_size):
            chunk = securities[chunk_start : chunk_start + batch_size]
            security_ids = [security.id for security in chunk]
            prices_by_security = self.securities.prices_for_securities(security_ids, end=query_end)
            actions_by_security = self.securities.corporate_actions_for_securities(
                security_ids,
                as_of=effective_as_of,
                through=query_end,
            )
            exchange_dates = {
                (security.exchange, row.trading_date)
                for security in chunk
                for row in prices_by_security[security.id][-2:]
            }
            calendar_entries = self.calendar.entries_for_dates(exchange_dates)
            first_dates = [prices_by_security[security.id][0].trading_date for security in chunk if prices_by_security[security.id]]
            calendar_days = self.calendar.trading_days_for_exchanges(
                {security.exchange for security in chunk},
                min(first_dates) if first_dates else query_end,
                query_end,
            )

            for security in chunk:
                queried_prices = prices_by_security[security.id]
                raw_prices = queried_prices
                if raw_prices:
                    latest_available_at = self._available_at_from_entries(
                        security,
                        raw_prices[-1].trading_date,
                        calendar_entries,
                    )
                    if latest_available_at > effective_as_of:
                        raw_prices = raw_prices[:-1]

                actions = [
                    action
                    for action in actions_by_security[security.id]
                    if raw_prices
                    and action.ex_date > raw_prices[0].trading_date
                    and action.ex_date <= raw_prices[-1].trading_date
                ]
                items: list[FeatureObservationResponse] = []
                if raw_prices:
                    effective_prices = raw_prices if adjustment_policy == "RAW" else self.adjustments.adjust(raw_prices, actions)
                    calculated_values = calculate_latest_feature_snapshot([_to_point(row) for row in effective_prices])
                    values = {code: calculated_values[code] for code in feature_set.feature_codes}
                    items.append(
                        FeatureObservationResponse(
                            observation_date=raw_prices[-1].trading_date,
                            available_at=self._available_at_from_entries(
                                security,
                                raw_prices[-1].trading_date,
                                calendar_entries,
                            ),
                            values=values,
                            unavailable=self._unavailable(values, len(raw_prices)),
                        )
                    )

                warnings: list[str] = []
                if queried_prices:
                    expected = [day for day in calendar_days[security.exchange] if day >= queried_prices[0].trading_date]
                    observed = {row.trading_date for row in queried_prices}
                    missing = [day.isoformat() for day in expected if day not in observed]
                    if missing:
                        preview = ", ".join(missing[:8]) + (f" (+{len(missing) - 8} more)" if len(missing) > 8 else "")
                        warnings.append(f"Missing expected trading sessions: {preview}")

                responses.append(
                    SecurityFeatureSeriesResponse(
                        security_id=security.id,
                        symbol=security.symbol,
                        exchange=security.exchange,
                        feature_set=_feature_set_response(feature_set),
                        feature_versions={code: FEATURE_DEFINITIONS[code].version for code in feature_set.feature_codes},
                        adjustment_policy=adjustment_policy,
                        dataset=self._dataset_context(
                            security,
                            raw_prices,
                            actions,
                            adjustment_policy,
                            feature_set,
                            run=run,
                            run_loaded=True,
                        ),
                        requested_start=None,
                        requested_end=observation_date,
                        as_of=effective_as_of,
                        computed_at=computed_at,
                        quality_warnings=warnings,
                        items=items,
                    )
                )
        return responses

    @staticmethod
    def _available_at_from_entries(
        security: Security,
        observation_date: date,
        entries: dict[tuple[str, date], TradingCalendar],
    ) -> datetime:
        entry = entries.get((security.exchange, observation_date))
        close_time = entry.session_close if entry and entry.session_close else time(23, 59, 59)
        return datetime.combine(observation_date, close_time, tzinfo=MARKET_TIMEZONE)


feature_set_response = _feature_set_response
