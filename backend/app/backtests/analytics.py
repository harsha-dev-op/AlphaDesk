from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, ROUND_HALF_UP, localcontext

from app.backtests.costs import money
from app.schemas.backtests import (
    BacktestAnalytics,
    PeriodBreakdown,
    SimulatedTrade,
)

METRIC_QUANTUM = Decimal("0.00000001")


def metric(value: Decimal) -> Decimal:
    return value.quantize(METRIC_QUANTUM, rounding=ROUND_HALF_UP)


def _mean(values: list[Decimal]) -> Decimal | None:
    return metric(sum(values, Decimal(0)) / Decimal(len(values))) if values else None


def _percentile(values: list[Decimal], percentile: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return metric(ordered[0])
    position = percentile * Decimal(len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - Decimal(lower)
    return metric(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _closed(trades: Iterable[SimulatedTrade]) -> list[SimulatedTrade]:
    return [trade for trade in trades if trade.net_pnl is not None and trade.net_return_pct is not None]


def calculate_analytics(
    trades: list[SimulatedTrade],
    *,
    signal_count: int,
    executable_setup_count: int,
) -> BacktestAnalytics:
    closed = _closed(trades)
    returns = [trade.net_return_pct for trade in closed if trade.net_return_pct is not None]
    gross_returns = [trade.gross_return_pct for trade in closed if trade.gross_return_pct is not None]
    gross_pnls = [trade.gross_pnl for trade in closed if trade.gross_pnl is not None]
    net_pnls = [trade.net_pnl for trade in closed if trade.net_pnl is not None]
    costs = [trade.total_costs for trade in closed if trade.total_costs is not None]
    deployed = [trade.deployed_notional for trade in closed]
    maes = [trade.maximum_adverse_excursion for trade in closed if trade.maximum_adverse_excursion is not None]
    mfes = [trade.maximum_favorable_excursion for trade in closed if trade.maximum_favorable_excursion is not None]
    winners = [value for value in net_pnls if value > 0]
    losers = [value for value in net_pnls if value < 0]
    breakeven = [value for value in net_pnls if value == 0]
    warnings: list[str] = []
    count = len(closed)
    if count == 0:
        warnings.append("NO_TRADES")
    elif count < 10:
        warnings.append("VERY_LOW_SAMPLE_SIZE")
    elif count < 30:
        warnings.append("LOW_SAMPLE_SIZE")

    profit_factor = None
    if losers:
        profit_factor = metric(sum(winners, Decimal(0)) / abs(sum(losers, Decimal(0))))
    elif winners:
        warnings.append("PROFIT_FACTOR_UNDEFINED_NO_LOSING_TRADES")
    elif count:
        warnings.append("PROFIT_FACTOR_UNDEFINED_NO_GAINS_OR_LOSSES")

    standard_deviation = None
    if len(returns) >= 2:
        with localcontext() as context:
            context.prec = 34
            average = sum(returns, Decimal(0)) / Decimal(len(returns))
            variance = sum((value - average) ** 2 for value in returns) / Decimal(len(returns))
            standard_deviation = metric(variance.sqrt())
    elif returns:
        warnings.append("INSUFFICIENT_TRADES_STANDARD_DEVIATION")

    total_costs = money(sum(costs, Decimal(0)))
    total_deployed = sum(deployed, Decimal(0))
    return BacktestAnalytics(
        signal_count=signal_count,
        executable_setup_count=executable_setup_count,
        executed_trade_count=len(trades),
        closed_trade_count=count,
        forced_end_count=sum(trade.exit_reason == "FORCED_END_OF_TEST" for trade in closed),
        closed_normal_count=sum(trade.exit_reason != "FORCED_END_OF_TEST" for trade in closed),
        winning_trades=len(winners),
        losing_trades=len(losers),
        breakeven_trades=len(breakeven),
        win_rate=metric(Decimal(len(winners)) / Decimal(count)) if count else None,
        loss_rate=metric(Decimal(len(losers)) / Decimal(count)) if count else None,
        average_gross_return=_mean(gross_returns),
        average_net_return=_mean(returns),
        median_net_return=_percentile(returns, Decimal("0.5")),
        average_gross_pnl=money(sum(gross_pnls, Decimal(0)) / Decimal(count)) if count else None,
        average_net_pnl=money(sum(net_pnls, Decimal(0)) / Decimal(count)) if count else None,
        total_gross_pnl=money(sum(gross_pnls, Decimal(0))),
        total_net_pnl=money(sum(net_pnls, Decimal(0))),
        total_transaction_costs=total_costs,
        cost_drag_pct=metric(total_costs / total_deployed) if total_deployed else None,
        average_transaction_cost_per_trade=money(total_costs / Decimal(count)) if count else None,
        profit_factor=profit_factor,
        expectancy_per_trade=money(sum(net_pnls, Decimal(0)) / Decimal(count)) if count else None,
        best_trade_return=max(returns) if returns else None,
        worst_trade_return=min(returns) if returns else None,
        standard_deviation_net_returns=standard_deviation,
        percentile_05=_percentile(returns, Decimal("0.05")),
        percentile_25=_percentile(returns, Decimal("0.25")),
        percentile_50=_percentile(returns, Decimal("0.50")),
        percentile_75=_percentile(returns, Decimal("0.75")),
        percentile_95=_percentile(returns, Decimal("0.95")),
        average_holding_sessions=_mean([Decimal(trade.holding_sessions) for trade in closed]),
        maximum_holding_sessions=max((trade.holding_sessions for trade in closed), default=None),
        average_mae=_mean(maes),
        average_mfe=_mean(mfes),
        warnings=warnings,
    )


def period_breakdown(label: str, trades: list[SimulatedTrade], *, setup_count: int) -> PeriodBreakdown:
    analytics = calculate_analytics(
        trades,
        signal_count=setup_count,
        executable_setup_count=len(trades),
    )
    return PeriodBreakdown(
        label=label,
        setup_count=setup_count,
        trade_count=analytics.closed_trade_count,
        average_net_return=analytics.average_net_return,
        median_net_return=analytics.median_net_return,
        win_rate=analytics.win_rate,
        profit_factor=analytics.profit_factor,
        total_net_pnl=analytics.total_net_pnl,
        total_costs=analytics.total_transaction_costs,
    )


__all__ = ["calculate_analytics", "metric", "period_breakdown"]
