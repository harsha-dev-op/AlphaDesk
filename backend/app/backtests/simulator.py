from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from uuid import UUID

from app.backtests.costs import IndiaCashDeliveryCostCalculator, money
from app.backtests.definitions import BacktestProfileDefinition
from app.backtests.fingerprints import fingerprint
from app.models import CorporateAction, TradingCalendar
from app.repositories.securities import PriceRecord, SecurityRepository
from app.schemas.backtests import CorporateActionEffect, SimulatedTrade
from app.services.adjustments import PriceAdjustmentService
from app.technical.series import HistoricalSecuritySeries
from app.technical.service import MARKET_TIMEZONE

PRICE_QUANTUM = Decimal("0.0001")
RETURN_QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class SetupEvent:
    security_id: UUID
    symbol: str
    company_name: str
    signal_date: date
    signal_decision_at: datetime
    strategy_code: str
    strategy_version: str
    strategy_fingerprint: str
    signal_result_fingerprint: str


@dataclass(frozen=True, slots=True)
class SimulationSettings:
    adjustment_policy: str
    holding_sessions: int
    trade_notional_inr: Decimal
    slippage_bps: Decimal
    stop_loss_pct: Decimal | None
    profit_target_pct: Decimal | None
    max_exit_delay_sessions: int
    profile_fingerprint: str
    cost_fingerprint: str


@dataclass(frozen=True, slots=True)
class SimulationResult:
    trades: tuple[SimulatedTrade, ...]
    skipped_reasons: dict[str, int]


def _price(value: Decimal) -> Decimal:
    return value.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)


def _return(value: Decimal) -> Decimal:
    return value.quantize(RETURN_QUANTUM, rounding=ROUND_HALF_UP)


def _known_at(value: datetime, cutoff: datetime) -> bool:
    if value.tzinfo is None or value.utcoffset() is None:
        return value <= cutoff.astimezone(MARKET_TIMEZONE).replace(tzinfo=None)
    return value.astimezone(UTC) <= cutoff.astimezone(UTC)


def _session_at(entry: TradingCalendar, *, at_open: bool) -> datetime:
    clock = entry.session_open if at_open else entry.session_close
    fallback = time(9, 15) if at_open else time(23, 59, 59)
    return datetime.combine(entry.trading_date, clock or fallback, tzinfo=MARKET_TIMEZONE)


class TradeSimulator:
    def __init__(self, adjustment_service: PriceAdjustmentService | None = None):
        self.adjustments = adjustment_service or PriceAdjustmentService()

    def simulate(
        self,
        setups: list[SetupEvent],
        *,
        series_by_security: dict[UUID, HistoricalSecuritySeries],
        sessions: list[TradingCalendar],
        profile: BacktestProfileDefinition,
        settings: SimulationSettings,
        costs: IndiaCashDeliveryCostCalculator,
    ) -> SimulationResult:
        ordered_sessions = sorted(
            (entry for entry in sessions if entry.is_trading_day),
            key=lambda entry: entry.trading_date,
        )
        session_index = {entry.trading_date: index for index, entry in enumerate(ordered_sessions)}
        blocked_until: dict[UUID, date | None] = {}
        trades: list[SimulatedTrade] = []
        skipped: dict[str, int] = {}

        def skip(reason: str) -> None:
            skipped[reason] = skipped.get(reason, 0) + 1

        for setup in sorted(setups, key=lambda item: (item.signal_date, item.symbol.casefold(), str(item.security_id))):
            previous_exit = blocked_until.get(setup.security_id, date.min)
            if previous_exit is None or setup.signal_date < previous_exit:
                skip("ALREADY_OPEN_POSITION")
                continue
            signal_index = session_index.get(setup.signal_date)
            if signal_index is None or signal_index + 1 >= len(ordered_sessions):
                skip("ENTRY_OUTSIDE_TEST_RANGE")
                continue
            entry_index = signal_index + 1
            entry_session = ordered_sessions[entry_index]
            series = series_by_security[setup.security_id]
            prices = {row.trading_date: row for row in series.prices}
            entry_row = prices.get(entry_session.trading_date)
            if entry_row is None or Decimal(entry_row.open) <= 0:
                skip("ENTRY_PRICE_UNAVAILABLE")
                continue

            raw_entry = Decimal(entry_row.open)
            slipped_entry = self._slipped(raw_entry, settings.slippage_bps, side="BUY")
            quantity = int(
                (settings.trade_notional_inr / slipped_entry).to_integral_value(rounding=ROUND_FLOOR)
            )
            if quantity < 1:
                skip("INSUFFICIENT_NOTIONAL_FOR_ONE_SHARE")
                continue

            trade = self._simulate_trade(
                setup,
                series=series,
                prices=prices,
                sessions=ordered_sessions,
                entry_index=entry_index,
                raw_entry=raw_entry,
                slipped_entry=slipped_entry,
                entry_quantity=quantity,
                profile=profile,
                settings=settings,
                costs=costs,
            )
            trades.append(trade)
            blocked_until[setup.security_id] = trade.exit_date

        trades.sort(key=lambda item: (item.entry_date, item.symbol.casefold(), item.trade_id))
        return SimulationResult(trades=tuple(trades), skipped_reasons=dict(sorted(skipped.items())))

    def _simulate_trade(
        self,
        setup: SetupEvent,
        *,
        series: HistoricalSecuritySeries,
        prices: dict[date, PriceRecord],
        sessions: list[TradingCalendar],
        entry_index: int,
        raw_entry: Decimal,
        slipped_entry: Decimal,
        entry_quantity: int,
        profile: BacktestProfileDefinition,
        settings: SimulationSettings,
        costs: IndiaCashDeliveryCostCalculator,
    ) -> SimulatedTrade:
        with localcontext() as context:
            context.prec = 34
            entry_day = sessions[entry_index].trading_date
            initial_quantity = Decimal(entry_quantity)
            current_quantity = initial_quantity
            stop = _price(slipped_entry * (Decimal(1) - settings.stop_loss_pct)) if settings.stop_loss_pct else None
            target = _price(slipped_entry * (Decimal(1) + settings.profit_target_pct)) if settings.profit_target_pct else None
            action_effects: list[CorporateActionEffect] = []
            warnings: list[str] = []
            min_equivalent = slipped_entry
            max_equivalent = slipped_entry
            scheduled_index = entry_index + settings.holding_sessions
            force_close = scheduled_index >= len(sessions)
            if force_close:
                available_indices = [
                    index
                    for index in range(entry_index, len(sessions))
                    if sessions[index].trading_date in prices and Decimal(prices[sessions[index].trading_date].close) > 0
                ]
                path_last_index = available_indices[-1] if available_indices else entry_index
            else:
                path_last_index = scheduled_index - 1

            base_exit: Decimal | None = None
            raw_exit: Decimal | None = None
            exit_date: date | None = None
            exit_reason: str | None = None
            held_sessions = 0

            for index in range(entry_index, path_last_index + 1):
                session = sessions[index]
                if index > entry_index:
                    current_quantity, stop, target = self._apply_actions(
                        series.action_history,
                        session,
                        current_quantity,
                        stop,
                        target,
                        action_effects,
                        warnings,
                    )
                row = prices.get(session.trading_date)
                held_sessions = index - entry_index + 1
                if row is None:
                    continue
                raw_open = Decimal(row.open)
                raw_high = Decimal(row.high)
                raw_low = Decimal(row.low)
                quantity_factor = current_quantity / initial_quantity
                day_exit_reason, day_base, day_warning = self._intraday_exit(
                    raw_open,
                    raw_high,
                    raw_low,
                    stop,
                    target,
                )
                if day_warning:
                    warnings.append(day_warning)
                if day_exit_reason is not None and day_base is not None:
                    exit_reason = day_exit_reason
                    base_exit = day_base
                    raw_exit = day_base
                    exit_date = session.trading_date
                    event_equivalent = day_base * quantity_factor
                    min_equivalent = min(min_equivalent, event_equivalent)
                    max_equivalent = max(max_equivalent, event_equivalent)
                    break
                min_equivalent = min(min_equivalent, raw_low * quantity_factor)
                max_equivalent = max(max_equivalent, raw_high * quantity_factor)

            if exit_reason is None and force_close:
                force_session = sessions[path_last_index]
                row = prices.get(force_session.trading_date)
                if row is not None and Decimal(row.close) > 0:
                    base_exit = Decimal(row.close)
                    raw_exit = base_exit
                    exit_date = force_session.trading_date
                    exit_reason = "FORCED_END_OF_TEST"
                    held_sessions = path_last_index - entry_index + 1
            elif exit_reason is None:
                for delay in range(0, settings.max_exit_delay_sessions + 1):
                    index = scheduled_index + delay
                    if index >= len(sessions):
                        break
                    session = sessions[index]
                    current_quantity, stop, target = self._apply_actions(
                        series.action_history,
                        session,
                        current_quantity,
                        stop,
                        target,
                        action_effects,
                        warnings,
                    )
                    row = prices.get(session.trading_date)
                    if row is not None and Decimal(row.open) > 0:
                        base_exit = Decimal(row.open)
                        raw_exit = base_exit
                        exit_date = session.trading_date
                        exit_reason = "TIME_EXIT"
                        held_sessions = index - entry_index
                        break

            if exit_reason == "TIME_EXIT" and base_exit is not None:
                exit_equivalent = base_exit * current_quantity / initial_quantity
                min_equivalent = min(min_equivalent, exit_equivalent)
                max_equivalent = max(max_equivalent, exit_equivalent)

            if exit_reason is None:
                exit_reason = "EXIT_PRICE_UNAVAILABLE"
                warnings.append("EXIT_PRICE_UNAVAILABLE_AFTER_BOUNDED_SEARCH")

            entry_turnover = slipped_entry * initial_quantity
            entry_cost = costs.calculate(entry_turnover, "BUY")
            slipped_exit = self._slipped(base_exit, settings.slippage_bps, side="SELL") if base_exit is not None else None
            exit_cost = costs.calculate(slipped_exit * current_quantity, "SELL") if slipped_exit is not None else None
            gross_pnl = money(slipped_exit * current_quantity - entry_turnover) if slipped_exit is not None else None
            total_costs = money(entry_cost.total_charges + exit_cost.total_charges) if exit_cost is not None else None
            net_pnl = money(gross_pnl - total_costs) if gross_pnl is not None and total_costs is not None else None
            gross_return = _return(gross_pnl / entry_turnover) if gross_pnl is not None and entry_turnover else None
            net_return = _return(net_pnl / entry_turnover) if net_pnl is not None and entry_turnover else None
            mae = _return(min_equivalent / slipped_entry - Decimal(1))
            mfe = _return(max_equivalent / slipped_entry - Decimal(1))
            trade_id = fingerprint(
                {
                    "signal_result_fingerprint": setup.signal_result_fingerprint,
                    "profile_fingerprint": settings.profile_fingerprint,
                    "entry_date": entry_day,
                }
            )[:24]
            trade_payload = {
                "trade_id": trade_id,
                "signal_result_fingerprint": setup.signal_result_fingerprint,
                "entry_date": entry_day,
                "raw_entry_price": raw_entry,
                "slipped_entry_price": slipped_entry,
                "entry_quantity": entry_quantity,
                "quantity": current_quantity,
                "entry_cost": entry_cost.model_dump(mode="json"),
                "stop_price": stop,
                "target_price": target,
                "exit_date": exit_date,
                "raw_exit_price": raw_exit,
                "slipped_exit_price": slipped_exit,
                "exit_reason": exit_reason,
                "exit_cost": exit_cost.model_dump(mode="json") if exit_cost else None,
                "holding_sessions": held_sessions,
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "mae": mae,
                "mfe": mfe,
                "actions": [effect.model_dump(mode="json") for effect in action_effects],
                "warnings": warnings,
                "cost_fingerprint": settings.cost_fingerprint,
            }
            return SimulatedTrade(
                trade_id=trade_id,
                security_id=setup.security_id,
                symbol=setup.symbol,
                company_name=setup.company_name,
                strategy_code=setup.strategy_code,
                strategy_version=setup.strategy_version,
                strategy_fingerprint=setup.strategy_fingerprint,
                profile_code=profile.profile_code,
                profile_version=profile.profile_version,
                adjustment_policy=settings.adjustment_policy,
                signal_date=setup.signal_date,
                signal_decision_at=setup.signal_decision_at,
                signal_result_fingerprint=setup.signal_result_fingerprint,
                entry_date=entry_day,
                raw_entry_price=_price(raw_entry),
                slipped_entry_price=slipped_entry,
                entry_quantity=entry_quantity,
                quantity=current_quantity,
                deployed_notional=money(entry_turnover),
                entry_cost=entry_cost,
                stop_price=stop,
                target_price=target,
                exit_date=exit_date,
                raw_exit_price=_price(raw_exit) if raw_exit is not None else None,
                slipped_exit_price=slipped_exit,
                exit_reason=exit_reason,
                exit_cost=exit_cost,
                holding_sessions=held_sessions,
                gross_pnl=gross_pnl,
                total_costs=total_costs,
                net_pnl=net_pnl,
                gross_return_pct=gross_return,
                net_return_pct=net_return,
                maximum_favorable_excursion=mfe,
                maximum_adverse_excursion=mae,
                corporate_action_events=action_effects,
                warnings=sorted(set(warnings)),
                trade_fingerprint=fingerprint(trade_payload),
            )

    @staticmethod
    def _slipped(base: Decimal, bps: Decimal, *, side: str) -> Decimal:
        direction = Decimal(1) if side == "BUY" else Decimal(-1)
        return _price(base * (Decimal(1) + direction * bps / Decimal(10000)))

    @staticmethod
    def _intraday_exit(
        open_price: Decimal,
        high: Decimal,
        low: Decimal,
        stop: Decimal | None,
        target: Decimal | None,
    ) -> tuple[str | None, Decimal | None, str | None]:
        if stop is not None and open_price <= stop:
            return "STOP_LOSS", open_price, None
        if target is not None and open_price >= target:
            return "PROFIT_TARGET", open_price, None
        stop_touched = stop is not None and low <= stop
        target_touched = target is not None and high >= target
        if stop_touched and target_touched:
            return "STOP_LOSS", stop, "AMBIGUOUS_INTRADAY_PATH_STOP_FIRST"
        if stop_touched:
            return "STOP_LOSS", stop, None
        if target_touched:
            return "PROFIT_TARGET", target, None
        return None, None, None

    def _apply_actions(
        self,
        action_history: tuple[CorporateAction, ...],
        session: TradingCalendar,
        quantity: Decimal,
        stop: Decimal | None,
        target: Decimal | None,
        effects: list[CorporateActionEffect],
        warnings: list[str],
    ) -> tuple[Decimal, Decimal | None, Decimal | None]:
        cutoff = _session_at(session, at_open=True)
        resolved = SecurityRepository._resolve_action_revisions(
            [action for action in action_history if _known_at(action.available_at, cutoff)]
        )
        for action in (item for item in resolved if item.ex_date == session.trading_date):
            if action.action_type not in {"STOCK_SPLIT", "BONUS"}:
                warnings.append(f"UNSUPPORTED_CORPORATE_ACTION:{action.action_type}")
                continue
            try:
                factor = self.adjustments.factor_for(action)
            except ValueError:
                warnings.append(f"INVALID_CORPORATE_ACTION_RATIO:{action.action_type}")
                continue
            before = quantity
            quantity = quantity / factor
            stop = _price(stop * factor) if stop is not None else None
            target = _price(target * factor) if target is not None else None
            effects.append(
                CorporateActionEffect(
                    action_id=action.id,
                    action_type=action.action_type,
                    ex_date=action.ex_date,
                    ratio_numerator=action.ratio_numerator,
                    ratio_denominator=action.ratio_denominator,
                    quantity_before=before,
                    quantity_after=quantity,
                    reference_factor=factor,
                )
            )
        return quantity, stop, target


__all__ = ["SetupEvent", "SimulationResult", "SimulationSettings", "TradeSimulator"]
