from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_FLOOR
from uuid import UUID

from app.backtests.costs import IndiaCashDeliveryCostCalculator, money
from app.backtests.definitions import BacktestProfileDefinition, CostModelDefinition
from app.backtests.fingerprints import fingerprint
from app.backtests.simulator import SetupEvent, TradeSimulator
from app.models import TradingCalendar
from app.portfolio.analytics import metric
from app.portfolio.definitions import PortfolioPolicyDefinition
from app.schemas.backtests import CorporateActionEffect, CostBreakdown
from app.schemas.portfolio import (
    CashLedgerEvent,
    DailyPortfolioSnapshot,
    PortfolioPosition,
    RejectedPortfolioCandidate,
)
from app.technical.series import HistoricalSecuritySeries


class PortfolioInvariantError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PortfolioSimulationSettings:
    start_date: date
    adjustment_policy: str
    holding_sessions: int
    slippage_bps: Decimal
    stop_loss_pct: Decimal | None
    profit_target_pct: Decimal | None
    max_exit_delay_sessions: int
    initial_capital: Decimal
    max_concurrent_positions: int
    max_position_weight: Decimal
    max_gross_exposure: Decimal
    minimum_cash_reserve_pct: Decimal
    profile_fingerprint: str
    cost_fingerprint: str
    policy_fingerprint: str


@dataclass(frozen=True, slots=True)
class PortfolioSimulationResult:
    positions: tuple[PortfolioPosition, ...]
    rejected_candidates: tuple[RejectedPortfolioCandidate, ...]
    ledger_events: tuple[CashLedgerEvent, ...]
    snapshots: tuple[DailyPortfolioSnapshot, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Candidate:
    setup: SetupEvent
    intended_entry_index: int | None
    intended_entry_date: date | None
    candidate_id: str


@dataclass(slots=True)
class _PositionState:
    setup: SetupEvent
    position_id: str
    entry_index: int
    scheduled_exit_index: int
    exit_deadline_index: int
    entry_date: date
    entry_raw_price: Decimal
    entry_slipped_price: Decimal
    initial_quantity: int
    current_quantity: Decimal
    entry_turnover: Decimal
    entry_costs: CostBreakdown
    cost_basis: Decimal
    entry_portfolio_weight: Decimal
    stop_price: Decimal | None
    target_price: Decimal | None
    last_mark_price: Decimal
    last_mark_date: date
    last_close_price: Decimal | None = None
    last_close_date: date | None = None
    corporate_action_events: list[CorporateActionEffect] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exit_unavailable: bool = False


class PortfolioSimulator:
    """Deterministic session event loop over one shared, non-negative cash balance."""

    def __init__(self, execution: TradeSimulator | None = None):
        self.execution = execution or TradeSimulator()

    @staticmethod
    def largest_affordable_quantity(
        budget: Decimal,
        slipped_entry_price: Decimal,
        costs: IndiaCashDeliveryCostCalculator,
    ) -> tuple[int, CostBreakdown | None]:
        if budget <= 0 or slipped_entry_price <= 0:
            return 0, None
        high = int((budget / slipped_entry_price).to_integral_value(rounding=ROUND_FLOOR))
        low = 0
        best_cost: CostBreakdown | None = None
        while low < high:
            candidate = (low + high + 1) // 2
            turnover = slipped_entry_price * Decimal(candidate)
            candidate_cost = costs.calculate(turnover, "BUY")
            required_cash = money(turnover) + candidate_cost.total_charges
            if required_cash <= budget:
                low = candidate
                best_cost = candidate_cost
            else:
                high = candidate - 1
        if low == 0:
            return 0, None
        if best_cost is None:
            best_cost = costs.calculate(slipped_entry_price * Decimal(low), "BUY")
        return low, best_cost

    def simulate(
        self,
        setups: Sequence[SetupEvent],
        *,
        series_by_security: dict[UUID, HistoricalSecuritySeries],
        sessions: Sequence[TradingCalendar],
        profile: BacktestProfileDefinition,
        cost_definition: CostModelDefinition,
        policy: PortfolioPolicyDefinition,
        settings: PortfolioSimulationSettings,
        costs: IndiaCashDeliveryCostCalculator,
    ) -> PortfolioSimulationResult:
        ordered_sessions = sorted(
            (entry for entry in sessions if entry.is_trading_day),
            key=lambda entry: entry.trading_date,
        )
        session_index = {entry.trading_date: index for index, entry in enumerate(ordered_sessions)}
        prices_by_security = {
            security_id: {row.trading_date: row for row in series.prices}
            for security_id, series in series_by_security.items()
        }
        candidates_by_index: dict[int, list[_Candidate]] = {}
        rejected: list[RejectedPortfolioCandidate] = []
        for setup in sorted(setups, key=lambda item: (item.signal_date, item.symbol.casefold(), str(item.security_id))):
            signal_index = session_index.get(setup.signal_date)
            intended_index = signal_index + 1 if signal_index is not None else None
            intended_date = (
                ordered_sessions[intended_index].trading_date
                if intended_index is not None and intended_index < len(ordered_sessions)
                else None
            )
            candidate = _Candidate(
                setup=setup,
                intended_entry_index=intended_index,
                intended_entry_date=intended_date,
                candidate_id=fingerprint(
                    {
                        "signal_fingerprint": setup.signal_result_fingerprint,
                        "intended_entry_date": intended_date,
                        "policy_fingerprint": settings.policy_fingerprint,
                    }
                )[:24],
            )
            if intended_index is None or intended_index >= len(ordered_sessions):
                rejected.append(self._rejection(candidate, "ENTRY_OUTSIDE_TEST_RANGE"))
            else:
                candidates_by_index.setdefault(intended_index, []).append(candidate)
        for bucket in candidates_by_index.values():
            bucket.sort(key=lambda item: (item.setup.symbol.casefold(), str(item.setup.security_id), item.candidate_id))

        cash = Decimal("0.00")
        realized_pnl = Decimal("0.00")
        cumulative_costs = Decimal("0.00")
        ledger: list[CashLedgerEvent] = []
        snapshots: list[DailyPortfolioSnapshot] = []
        completed: list[PortfolioPosition] = []
        open_positions: dict[UUID, _PositionState] = {}
        run_warnings: set[str] = set()
        previous_equity: Decimal | None = None
        running_peak: Decimal | None = None
        daily_costs = Decimal("0.00")

        def ledger_event(
            session_date: date,
            event_type: str,
            net_change: Decimal,
            *,
            gross_amount: Decimal = Decimal("0"),
            cost_amount: Decimal = Decimal("0"),
            state: _PositionState | None = None,
        ) -> None:
            nonlocal cash
            sequence = len(ledger) + 1
            cash = money(cash + net_change)
            if cash < 0:
                raise PortfolioInvariantError("Portfolio cash became negative")
            payload = {
                "sequence": sequence,
                "session_date": session_date,
                "event_type": event_type,
                "gross_amount": gross_amount,
                "cost_amount": cost_amount,
                "net_cash_change": net_change,
                "resulting_cash_balance": cash,
                "position_id": state.position_id if state else None,
                "security_id": str(state.setup.security_id) if state else None,
            }
            event_fingerprint = fingerprint(payload)
            ledger.append(
                CashLedgerEvent(
                    sequence=sequence,
                    ledger_event_id=event_fingerprint[:24],
                    session_date=session_date,
                    security_id=state.setup.security_id if state else None,
                    symbol=state.setup.symbol if state else None,
                    event_type=event_type,
                    gross_amount=money(gross_amount),
                    cost_amount=money(cost_amount),
                    net_cash_change=money(net_change),
                    resulting_cash_balance=cash,
                    position_id=state.position_id if state else None,
                    event_fingerprint=event_fingerprint,
                )
            )

        initial_event_date = ordered_sessions[0].trading_date if ordered_sessions else settings.start_date
        ledger_event(
            initial_event_date,
            "INITIAL_CAPITAL",
            money(settings.initial_capital),
            gross_amount=money(settings.initial_capital),
        )

        def close_position(
            state: _PositionState,
            *,
            session_date: date,
            exit_date: date,
            base_exit_price: Decimal,
            reason: str,
            holding_sessions: int,
        ) -> None:
            nonlocal realized_pnl, cumulative_costs, daily_costs
            slipped_exit = self.execution.slipped_price(base_exit_price, settings.slippage_bps, side="SELL")
            exact_turnover = slipped_exit * state.current_quantity
            exit_turnover = money(exact_turnover)
            exit_costs = costs.calculate(exact_turnover, "SELL")
            ledger_event(
                session_date,
                "EXIT_PROCEEDS",
                exit_turnover,
                gross_amount=exit_turnover,
                state=state,
            )
            ledger_event(
                session_date,
                "EXIT_COST",
                -exit_costs.total_charges,
                cost_amount=exit_costs.total_charges,
                state=state,
            )
            daily_costs = money(daily_costs + exit_costs.total_charges)
            cumulative_costs = money(cumulative_costs + exit_costs.total_charges)
            gross_pnl = money(exit_turnover - state.entry_turnover)
            net_pnl = money(gross_pnl - state.entry_costs.total_charges - exit_costs.total_charges)
            realized_pnl = money(realized_pnl + net_pnl)
            completed.append(
                self._position_record(
                    state,
                    policy=policy,
                    profile=profile,
                    cost_definition=cost_definition,
                    initial_capital=settings.initial_capital,
                    exit_date=exit_date,
                    exit_raw_price=self.execution.slipped_price(base_exit_price, Decimal(0), side="SELL"),
                    exit_slipped_price=slipped_exit,
                    exit_reason=reason,
                    exit_turnover=exit_turnover,
                    exit_costs=exit_costs,
                    gross_pnl=gross_pnl,
                    net_pnl=net_pnl,
                    holding_sessions=holding_sessions,
                )
            )
            del open_positions[state.setup.security_id]

        for index, session in enumerate(ordered_sessions):
            day = session.trading_date
            daily_costs = Decimal("0.00")

            for state in sorted(open_positions.values(), key=lambda item: (item.setup.symbol.casefold(), item.position_id)):
                effects_before = len(state.corporate_action_events)
                state.current_quantity, state.stop_price, state.target_price = self.execution.apply_actions(
                    series_by_security[state.setup.security_id].action_history,
                    session,
                    state.current_quantity,
                    state.stop_price,
                    state.target_price,
                    state.corporate_action_events,
                    state.warnings,
                )
                for effect in state.corporate_action_events[effects_before:]:
                    state.last_mark_price *= effect.reference_factor
                    if state.last_close_price is not None:
                        state.last_close_price *= effect.reference_factor

            for state in sorted(list(open_positions.values()), key=lambda item: (item.setup.symbol.casefold(), item.position_id)):
                row = prices_by_security[state.setup.security_id].get(day)
                raw_open = Decimal(row.open) if row is not None and Decimal(row.open) > 0 else None
                if state.exit_unavailable:
                    continue
                if index >= state.scheduled_exit_index:
                    if index <= state.exit_deadline_index and raw_open is not None:
                        close_position(
                            state,
                            session_date=day,
                            exit_date=day,
                            base_exit_price=raw_open,
                            reason="TIME_EXIT",
                            holding_sessions=index - state.entry_index,
                        )
                    elif index > state.exit_deadline_index:
                        state.exit_unavailable = True
                        state.warnings.append("EXIT_PRICE_UNAVAILABLE_AFTER_BOUNDED_SEARCH")
                    continue
                if raw_open is not None:
                    reason, base, warning = self.execution.intraday_exit(
                        raw_open,
                        raw_open,
                        raw_open,
                        state.stop_price,
                        state.target_price,
                    )
                    if warning:
                        state.warnings.append(warning)
                    if reason is not None and base is not None:
                        close_position(
                            state,
                            session_date=day,
                            exit_date=day,
                            base_exit_price=base,
                            reason=reason,
                            holding_sessions=index - state.entry_index + 1,
                        )

            open_marks: dict[UUID, Decimal] = {}
            for security_id, state in open_positions.items():
                row = prices_by_security[security_id].get(day)
                if row is not None and Decimal(row.open) > 0:
                    open_marks[security_id] = Decimal(row.open)
                else:
                    open_marks[security_id] = state.last_mark_price
            session_start_gross = sum(
                (open_marks[security_id] * state.current_quantity for security_id, state in open_positions.items()),
                Decimal(0),
            )
            session_start_equity = money(cash + session_start_gross)
            target_slot = session_start_equity / Decimal(settings.max_concurrent_positions)
            position_cap = settings.max_position_weight * session_start_equity
            reserve_floor = settings.minimum_cash_reserve_pct * session_start_equity

            for candidate in candidates_by_index.get(index, []):
                setup = candidate.setup
                if setup.security_id in open_positions:
                    rejected.append(self._rejection(candidate, "ALREADY_OPEN_POSITION"))
                    continue
                if len(open_positions) >= settings.max_concurrent_positions:
                    rejected.append(self._rejection(candidate, "PORTFOLIO_CAPACITY_REACHED"))
                    continue
                row = prices_by_security[setup.security_id].get(day)
                if row is None or Decimal(row.open) <= 0:
                    rejected.append(self._rejection(candidate, "ENTRY_PRICE_UNAVAILABLE"))
                    continue
                raw_entry = Decimal(row.open)
                slipped_entry = self.execution.slipped_price(raw_entry, settings.slippage_bps, side="BUY")
                exposure_room = settings.max_gross_exposure * session_start_equity - session_start_gross
                spendable_cash = cash - reserve_floor
                budget = min(target_slot, position_cap, exposure_room, spendable_cash)
                quantity, entry_costs = self.largest_affordable_quantity(budget, slipped_entry, costs)
                if quantity < 1 or entry_costs is None:
                    one_share_cost = money(slipped_entry) + costs.calculate(slipped_entry, "BUY").total_charges
                    if position_cap < one_share_cost:
                        reason = "MAX_POSITION_WEIGHT_LIMIT"
                    elif exposure_room < one_share_cost:
                        reason = "GROSS_EXPOSURE_LIMIT"
                    else:
                        reason = "INSUFFICIENT_PORTFOLIO_CASH"
                    rejected.append(self._rejection(candidate, reason))
                    continue
                entry_turnover = money(slipped_entry * Decimal(quantity))
                total_spend = money(entry_turnover + entry_costs.total_charges)
                if total_spend > spendable_cash or total_spend > cash:
                    raise PortfolioInvariantError("Accepted entry exceeded spendable cash")
                position_id = fingerprint(
                    {
                        "signal_fingerprint": setup.signal_result_fingerprint,
                        "policy_fingerprint": settings.policy_fingerprint,
                        "profile_fingerprint": settings.profile_fingerprint,
                        "entry_date": day,
                    }
                )[:24]
                state = _PositionState(
                    setup=setup,
                    position_id=position_id,
                    entry_index=index,
                    scheduled_exit_index=index + settings.holding_sessions,
                    exit_deadline_index=index + settings.holding_sessions + settings.max_exit_delay_sessions,
                    entry_date=day,
                    entry_raw_price=raw_entry,
                    entry_slipped_price=slipped_entry,
                    initial_quantity=quantity,
                    current_quantity=Decimal(quantity),
                    entry_turnover=entry_turnover,
                    entry_costs=entry_costs,
                    cost_basis=money(entry_turnover + entry_costs.total_charges),
                    entry_portfolio_weight=metric(entry_turnover / session_start_equity),
                    stop_price=(
                        self.execution.slipped_price(
                            slipped_entry * (Decimal(1) - settings.stop_loss_pct),
                            Decimal(0),
                            side="BUY",
                        )
                        if settings.stop_loss_pct is not None
                        else None
                    ),
                    target_price=(
                        self.execution.slipped_price(
                            slipped_entry * (Decimal(1) + settings.profit_target_pct),
                            Decimal(0),
                            side="BUY",
                        )
                        if settings.profit_target_pct is not None
                        else None
                    ),
                    last_mark_price=raw_entry,
                    last_mark_date=day,
                )
                open_positions[setup.security_id] = state
                ledger_event(
                    day,
                    "ENTRY_PRINCIPAL",
                    -entry_turnover,
                    gross_amount=entry_turnover,
                    state=state,
                )
                ledger_event(
                    day,
                    "ENTRY_COST",
                    -entry_costs.total_charges,
                    cost_amount=entry_costs.total_charges,
                    state=state,
                )
                daily_costs = money(daily_costs + entry_costs.total_charges)
                cumulative_costs = money(cumulative_costs + entry_costs.total_charges)
                session_start_gross += raw_entry * Decimal(quantity)

            for state in sorted(list(open_positions.values()), key=lambda item: (item.setup.symbol.casefold(), item.position_id)):
                if state.exit_unavailable or index >= state.scheduled_exit_index:
                    continue
                row = prices_by_security[state.setup.security_id].get(day)
                if row is None:
                    continue
                reason, base, warning = self.execution.intraday_exit(
                    Decimal(row.open),
                    Decimal(row.high),
                    Decimal(row.low),
                    state.stop_price,
                    state.target_price,
                )
                if warning:
                    state.warnings.append(warning)
                if reason is not None and base is not None:
                    close_position(
                        state,
                        session_date=day,
                        exit_date=day,
                        base_exit_price=base,
                        reason=reason,
                        holding_sessions=index - state.entry_index + 1,
                    )

            if index == len(ordered_sessions) - 1:
                for state in sorted(list(open_positions.values()), key=lambda item: (item.setup.symbol.casefold(), item.position_id)):
                    if state.exit_unavailable or state.scheduled_exit_index < len(ordered_sessions):
                        continue
                    row = prices_by_security[state.setup.security_id].get(day)
                    final_close = Decimal(row.close) if row is not None and Decimal(row.close) > 0 else state.last_close_price
                    final_date = day if row is not None and Decimal(row.close) > 0 else state.last_close_date
                    if final_close is None or final_date is None:
                        state.exit_unavailable = True
                        state.warnings.append("EXIT_PRICE_UNAVAILABLE_AT_END_OF_TEST")
                        continue
                    close_position(
                        state,
                        session_date=day,
                        exit_date=final_date,
                        base_exit_price=final_close,
                        reason="FORCED_END_OF_TEST",
                        holding_sessions=index - state.entry_index + 1,
                    )

            snapshot_warnings: set[str] = set()
            gross_market_value = Decimal(0)
            stale_mark_count = 0
            for security_id, state in open_positions.items():
                row = prices_by_security[security_id].get(day)
                if row is not None and Decimal(row.close) > 0:
                    mark = Decimal(row.close)
                    state.last_mark_price = mark
                    state.last_mark_date = day
                    state.last_close_price = mark
                    state.last_close_date = day
                else:
                    mark = state.last_mark_price
                    stale_mark_count += 1
                    snapshot_warnings.add(f"STALE_MARK_PRICE:{state.setup.symbol}")
                gross_market_value += mark * state.current_quantity
            gross_market_value = money(gross_market_value)
            equity = money(cash + gross_market_value)
            daily_return = metric(equity / previous_equity - Decimal(1)) if previous_equity else None
            running_peak = equity if running_peak is None else max(running_peak, equity)
            drawdown = metric(equity / running_peak - Decimal(1)) if running_peak else None
            exposure = metric(gross_market_value / equity) if equity > 0 else None
            cash_pct = metric(cash / equity) if equity > 0 else None
            unrealized = money(equity - money(settings.initial_capital) - realized_pnl)
            if exposure is not None and exposure > settings.max_gross_exposure + Decimal("0.00000001"):
                snapshot_warnings.add("GROSS_EXPOSURE_DRIFT_ABOVE_ENTRY_LIMIT")
            if exposure is not None and exposure > Decimal("1.00000001"):
                raise PortfolioInvariantError("Unlevered gross exposure exceeded portfolio equity")
            if len(open_positions) > settings.max_concurrent_positions:
                raise PortfolioInvariantError("Open-position capacity exceeded")
            snapshots.append(
                DailyPortfolioSnapshot(
                    session_date=day,
                    cash=cash,
                    gross_market_value=gross_market_value,
                    portfolio_equity=equity,
                    realized_pnl_to_date=realized_pnl,
                    unrealized_pnl=unrealized,
                    daily_costs=daily_costs,
                    cumulative_costs=cumulative_costs,
                    open_position_count=len(open_positions),
                    gross_exposure_pct=exposure,
                    cash_pct=cash_pct,
                    daily_return=daily_return,
                    drawdown_pct=drawdown,
                    stale_mark_count=stale_mark_count,
                    warnings=sorted(snapshot_warnings),
                )
            )
            run_warnings.update(snapshot_warnings)
            previous_equity = equity

        final_index = len(ordered_sessions) - 1
        for state in sorted(open_positions.values(), key=lambda item: (item.entry_date, item.setup.symbol.casefold(), item.position_id)):
            state.exit_unavailable = True
            if "EXIT_PRICE_UNAVAILABLE_AFTER_BOUNDED_SEARCH" not in state.warnings:
                state.warnings.append("EXIT_PRICE_UNAVAILABLE_AT_END_OF_TEST")
            completed.append(
                self._position_record(
                    state,
                    policy=policy,
                    profile=profile,
                    cost_definition=cost_definition,
                    initial_capital=settings.initial_capital,
                    exit_date=None,
                    exit_raw_price=None,
                    exit_slipped_price=None,
                    exit_reason="EXIT_PRICE_UNAVAILABLE",
                    exit_turnover=None,
                    exit_costs=None,
                    gross_pnl=None,
                    net_pnl=None,
                    holding_sessions=max(final_index - state.entry_index + 1, 0),
                )
            )

        reconciled_cash = money(sum((event.net_cash_change for event in ledger), Decimal(0)))
        if reconciled_cash != cash:
            raise PortfolioInvariantError("Cash ledger does not reconcile")
        completed.sort(key=lambda item: (item.entry_date, item.symbol.casefold(), item.position_id))
        rejected.sort(
            key=lambda item: (
                item.intended_entry_date or date.max,
                item.symbol.casefold(),
                item.candidate_id,
            )
        )
        return PortfolioSimulationResult(
            positions=tuple(completed),
            rejected_candidates=tuple(rejected),
            ledger_events=tuple(ledger),
            snapshots=tuple(snapshots),
            warnings=tuple(sorted(run_warnings | {warning for item in completed for warning in item.warnings})),
        )

    @staticmethod
    def _rejection(candidate: _Candidate, reason: str) -> RejectedPortfolioCandidate:
        payload = {
            "candidate_id": candidate.candidate_id,
            "security_id": str(candidate.setup.security_id),
            "signal_date": candidate.setup.signal_date,
            "intended_entry_date": candidate.intended_entry_date,
            "reason": reason,
            "signal_fingerprint": candidate.setup.signal_result_fingerprint,
        }
        return RejectedPortfolioCandidate(
            candidate_id=candidate.candidate_id,
            security_id=candidate.setup.security_id,
            symbol=candidate.setup.symbol,
            signal_date=candidate.setup.signal_date,
            intended_entry_date=candidate.intended_entry_date,
            reason=reason,
            signal_fingerprint=candidate.setup.signal_result_fingerprint,
            candidate_fingerprint=fingerprint(payload),
        )

    @staticmethod
    def _position_record(
        state: _PositionState,
        *,
        policy: PortfolioPolicyDefinition,
        profile: BacktestProfileDefinition,
        cost_definition: CostModelDefinition,
        initial_capital: Decimal,
        exit_date: date | None,
        exit_raw_price: Decimal | None,
        exit_slipped_price: Decimal | None,
        exit_reason: str,
        exit_turnover: Decimal | None,
        exit_costs: CostBreakdown | None,
        gross_pnl: Decimal | None,
        net_pnl: Decimal | None,
        holding_sessions: int,
    ) -> PortfolioPosition:
        net_return = metric(net_pnl / state.entry_turnover) if net_pnl is not None and state.entry_turnover else None
        contribution = metric(net_pnl / initial_capital) if net_pnl is not None and initial_capital else None
        payload = {
            "position_id": state.position_id,
            "signal_fingerprint": state.setup.signal_result_fingerprint,
            "entry_date": state.entry_date,
            "entry_price": state.entry_slipped_price,
            "initial_quantity": state.initial_quantity,
            "current_quantity": state.current_quantity,
            "entry_turnover": state.entry_turnover,
            "entry_costs": state.entry_costs.model_dump(mode="json"),
            "actions": [item.model_dump(mode="json") for item in state.corporate_action_events],
            "exit_date": exit_date,
            "exit_price": exit_slipped_price,
            "exit_reason": exit_reason,
            "exit_costs": exit_costs.model_dump(mode="json") if exit_costs else None,
            "net_pnl": net_pnl,
            "warnings": sorted(set(state.warnings)),
        }
        return PortfolioPosition(
            position_id=state.position_id,
            security_id=state.setup.security_id,
            symbol=state.setup.symbol,
            company_name=state.setup.company_name,
            strategy_code=state.setup.strategy_code,
            strategy_version=state.setup.strategy_version,
            strategy_fingerprint=state.setup.strategy_fingerprint,
            signal_date=state.setup.signal_date,
            signal_fingerprint=state.setup.signal_result_fingerprint,
            portfolio_policy_code=policy.policy_code,
            portfolio_policy_version=policy.policy_version,
            profile_code=profile.profile_code,
            profile_version=profile.profile_version,
            cost_model_code=cost_definition.code,
            cost_model_version=cost_definition.version,
            entry_date=state.entry_date,
            entry_raw_price=state.entry_raw_price,
            entry_slipped_price=state.entry_slipped_price,
            initial_quantity=state.initial_quantity,
            current_quantity=state.current_quantity,
            entry_turnover=state.entry_turnover,
            entry_costs=state.entry_costs,
            cost_basis=state.cost_basis,
            entry_portfolio_weight=state.entry_portfolio_weight,
            stop_price=state.stop_price,
            target_price=state.target_price,
            corporate_action_events=state.corporate_action_events,
            exit_date=exit_date,
            exit_raw_price=exit_raw_price,
            exit_slipped_price=exit_slipped_price,
            exit_reason=exit_reason,
            exit_turnover=exit_turnover,
            exit_costs=exit_costs,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            net_return=net_return,
            portfolio_contribution=contribution,
            holding_sessions=holding_sessions,
            warnings=sorted(set(state.warnings)),
            position_fingerprint=fingerprint(payload),
        )


__all__ = [
    "PortfolioInvariantError",
    "PortfolioSimulationResult",
    "PortfolioSimulationSettings",
    "PortfolioSimulator",
]
