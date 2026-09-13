from __future__ import annotations

import math
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext

from app.backtests.costs import money
from app.schemas.portfolio import (
    DailyPortfolioSnapshot,
    DrawdownDetails,
    PortfolioMetrics,
    PortfolioPosition,
    PortfolioSegmentMetrics,
)

METRIC_QUANTUM = Decimal("0.00000001")


def metric(value: Decimal | float) -> Decimal:
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    with localcontext() as context:
        context.prec = max(34, len(decimal_value.as_tuple().digits) + 12)
        return decimal_value.quantize(METRIC_QUANTUM, rounding=ROUND_HALF_UP)


def _mean(values: list[Decimal]) -> Decimal | None:
    return metric(sum(values, Decimal(0)) / Decimal(len(values))) if values else None


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return metric(ordered[middle])
    return metric((ordered[middle - 1] + ordered[middle]) / Decimal(2))


def _sample_std(values: list[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    average = sum(values, Decimal(0)) / Decimal(len(values))
    variance = sum((value - average) ** 2 for value in values) / Decimal(len(values) - 1)
    return metric(variance.sqrt())


def _returns(snapshots: list[DailyPortfolioSnapshot]) -> list[Decimal]:
    output: list[Decimal] = []
    for previous, current in zip(snapshots, snapshots[1:]):
        if previous.portfolio_equity > 0:
            output.append(metric(current.portfolio_equity / previous.portfolio_equity - Decimal(1)))
    return output


def _cagr(starting: Decimal, ending: Decimal, start: date, end: date) -> Decimal | None:
    elapsed = (end - start).days
    if starting <= 0 or ending <= 0 or elapsed <= 0:
        return None
    exponent = 365.2425 / elapsed
    return metric(math.pow(float(ending / starting), exponent) - 1)


def _risk_metrics(
    returns: list[Decimal],
    annual_risk_free_rate: Decimal,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, list[str]]:
    warnings: list[str] = []
    if len(returns) < 2:
        return None, None, None, ["INSUFFICIENT_DAILY_RETURNS_FOR_RISK_METRICS"]
    standard_deviation = _sample_std(returns)
    annualized_volatility = (
        metric(standard_deviation * Decimal(str(math.sqrt(252)))) if standard_deviation is not None else None
    )
    daily_rf = Decimal(str(math.pow(float(Decimal(1) + annual_risk_free_rate), 1 / 252) - 1))
    excess = [value - daily_rf for value in returns]
    average_excess = sum(excess, Decimal(0)) / Decimal(len(excess))
    excess_std = _sample_std(excess)
    sharpe = None
    if excess_std is not None and excess_std != 0:
        sharpe = metric(average_excess / excess_std * Decimal(str(math.sqrt(252))))
    else:
        warnings.append("SHARPE_UNDEFINED_ZERO_VARIANCE")
    downside = [min(value, Decimal(0)) for value in excess]
    downside_deviation = (
        sum((value * value for value in downside), Decimal(0)) / Decimal(len(downside))
    ).sqrt()
    sortino = None
    if downside_deviation != 0:
        sortino = metric(average_excess * Decimal(str(math.sqrt(252))) / downside_deviation)
    else:
        warnings.append("SORTINO_UNDEFINED_ZERO_DOWNSIDE")
    return annualized_volatility, sharpe, sortino, warnings


def drawdown_details(snapshots: list[DailyPortfolioSnapshot]) -> DrawdownDetails:
    if not snapshots:
        return DrawdownDetails(
            max_drawdown_pct=None,
            max_drawdown_inr=None,
            peak_date=None,
            trough_date=None,
            recovery_date=None,
            duration_sessions=None,
        )
    peak_value = snapshots[0].portfolio_equity
    peak_date = snapshots[0].session_date
    peak_index = 0
    worst_pct = Decimal(0)
    worst_inr = Decimal(0)
    worst_peak_date = peak_date
    worst_peak_index = peak_index
    trough_date = peak_date
    trough_index = 0
    recovery_date: date | None = None
    recovery_index: int | None = None
    worst_peak_value = peak_value

    for index, snapshot in enumerate(snapshots):
        equity = snapshot.portfolio_equity
        if equity > peak_value:
            peak_value = equity
            peak_date = snapshot.session_date
            peak_index = index
        drawdown = equity / peak_value - Decimal(1) if peak_value else Decimal(0)
        if drawdown < worst_pct:
            worst_pct = drawdown
            worst_inr = peak_value - equity
            worst_peak_date = peak_date
            worst_peak_index = peak_index
            worst_peak_value = peak_value
            trough_date = snapshot.session_date
            trough_index = index

    if worst_pct < 0:
        for index in range(trough_index + 1, len(snapshots)):
            if snapshots[index].portfolio_equity >= worst_peak_value:
                recovery_date = snapshots[index].session_date
                recovery_index = index
                break
        duration = (recovery_index if recovery_index is not None else len(snapshots) - 1) - worst_peak_index
    else:
        duration = 0
    return DrawdownDetails(
        max_drawdown_pct=metric(worst_pct),
        max_drawdown_inr=money(worst_inr),
        peak_date=worst_peak_date,
        trough_date=trough_date,
        recovery_date=recovery_date,
        duration_sessions=duration,
    )


def calculate_portfolio_metrics(
    snapshots: list[DailyPortfolioSnapshot],
    positions: list[PortfolioPosition],
    *,
    initial_capital: Decimal,
    annual_risk_free_rate: Decimal,
) -> PortfolioMetrics:
    ending_equity = snapshots[-1].portfolio_equity if snapshots else money(initial_capital)
    returns = _returns(snapshots)
    annualized_volatility, sharpe, sortino, warnings = _risk_metrics(returns, annual_risk_free_rate)
    if not snapshots:
        warnings.append("NO_PORTFOLIO_SESSIONS")
    cagr = _cagr(initial_capital, ending_equity, snapshots[0].session_date, snapshots[-1].session_date) if snapshots else None
    if cagr is None:
        warnings.append("CAGR_UNDEFINED_INSUFFICIENT_ELAPSED_TIME")
    drawdown = drawdown_details(snapshots)
    calmar = None
    if cagr is not None and drawdown.max_drawdown_pct not in {None, Decimal(0)}:
        calmar = metric(cagr / abs(drawdown.max_drawdown_pct))
    else:
        warnings.append("CALMAR_UNDEFINED_ZERO_DRAWDOWN_OR_CAGR")

    closed = [position for position in positions if position.net_pnl is not None and position.net_return is not None]
    realized_returns = [position.net_return for position in closed if position.net_return is not None]
    profitable = [position for position in closed if position.net_pnl is not None and position.net_pnl > 0]
    losing = [position for position in closed if position.net_pnl is not None and position.net_pnl < 0]
    breakeven = [position for position in closed if position.net_pnl == 0]
    entry_turnover = sum((position.entry_turnover for position in positions), Decimal(0))
    exit_turnover = sum((position.exit_turnover for position in positions if position.exit_turnover is not None), Decimal(0))
    total_turnover = money(entry_turnover + exit_turnover)
    total_costs = money(
        sum((position.entry_costs.total_charges for position in positions), Decimal(0))
        + sum(
            (position.exit_costs.total_charges for position in positions if position.exit_costs is not None),
            Decimal(0),
        )
    )
    average_equity = _mean([snapshot.portfolio_equity for snapshot in snapshots])
    exposure_values = [
        snapshot.gross_exposure_pct
        for snapshot in snapshots
        if snapshot.gross_exposure_pct is not None
    ]
    cash_values = [snapshot.cash_pct for snapshot in snapshots if snapshot.cash_pct is not None]
    if len(closed) < 10:
        warnings.append("LOW_CLOSED_POSITION_SAMPLE_SIZE")
    return PortfolioMetrics(
        initial_capital=money(initial_capital),
        ending_equity=money(ending_equity),
        net_portfolio_pnl=money(ending_equity - initial_capital),
        total_portfolio_return=metric(ending_equity / initial_capital - Decimal(1)),
        cagr=cagr,
        annualized_volatility=annualized_volatility,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        drawdown=drawdown,
        calmar_ratio=calmar,
        average_gross_exposure=_mean(exposure_values),
        maximum_gross_exposure=max(exposure_values, default=None),
        average_cash_pct=_mean(cash_values),
        minimum_cash=min((snapshot.cash for snapshot in snapshots), default=money(initial_capital)),
        average_open_positions=_mean([Decimal(snapshot.open_position_count) for snapshot in snapshots]),
        maximum_open_positions=max((snapshot.open_position_count for snapshot in snapshots), default=0),
        total_traded_turnover=total_turnover,
        portfolio_turnover=metric(total_turnover / average_equity) if average_equity else None,
        total_modeled_transaction_costs=total_costs,
        cost_drag=metric(total_costs / total_turnover) if total_turnover else None,
        entry_count=len(positions),
        exit_count=len(closed),
        profitable_positions=len(profitable),
        losing_positions=len(losing),
        breakeven_positions=len(breakeven),
        portfolio_win_rate=metric(Decimal(len(profitable)) / Decimal(len(closed))) if closed else None,
        average_realized_position_return=_mean(realized_returns),
        median_realized_position_return=_median(realized_returns),
        warnings=sorted(set(warnings)),
    )


def calculate_segment_metrics(
    label: str,
    snapshots: list[DailyPortfolioSnapshot],
    *,
    annual_risk_free_rate: Decimal,
) -> PortfolioSegmentMetrics:
    if not snapshots:
        return PortfolioSegmentMetrics(
            label=label,
            start_date=None,
            end_date=None,
            starting_equity=None,
            ending_equity=None,
            total_return=None,
            cagr=None,
            annualized_volatility=None,
            sharpe_ratio=None,
            sortino_ratio=None,
            max_drawdown_pct=None,
            warnings=["NO_SESSIONS_IN_SEGMENT"],
        )
    starting = snapshots[0].portfolio_equity
    ending = snapshots[-1].portfolio_equity
    returns = _returns(snapshots)
    volatility, sharpe, sortino, warnings = _risk_metrics(returns, annual_risk_free_rate)
    cagr = _cagr(starting, ending, snapshots[0].session_date, snapshots[-1].session_date)
    if cagr is None:
        warnings.append("CAGR_UNDEFINED_INSUFFICIENT_ELAPSED_TIME")
    return PortfolioSegmentMetrics(
        label=label,
        start_date=snapshots[0].session_date,
        end_date=snapshots[-1].session_date,
        starting_equity=starting,
        ending_equity=ending,
        total_return=metric(ending / starting - Decimal(1)) if starting > 0 else None,
        cagr=cagr,
        annualized_volatility=volatility,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown_pct=drawdown_details(snapshots).max_drawdown_pct,
        warnings=sorted(set(warnings)),
    )


__all__ = [
    "calculate_portfolio_metrics",
    "calculate_segment_metrics",
    "drawdown_details",
    "metric",
]
