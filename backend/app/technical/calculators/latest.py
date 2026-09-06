from decimal import Decimal, localcontext

from app.technical.calculators.common import FeatureValue, PricePoint, divide
from app.technical.calculators.liquidity import liquidity_values
from app.technical.calculators.momentum import rsi_series
from app.technical.calculators.price_structure import price_structure_values
from app.technical.calculators.returns import return_values
from app.technical.calculators.session import session_values
from app.technical.calculators.trend import (
    distance_from_average,
    exponential_moving_average_series,
    simple_moving_average,
    trend_state_values,
)
from app.technical.calculators.volatility import (
    annualized_volatility,
    atr_series,
    daily_return_series,
    true_range_series,
)


def calculate_latest_feature_snapshot(points: list[PricePoint]) -> dict[str, FeatureValue]:
    """Calculate only the latest row through the authoritative family primitives."""
    if not points:
        return {}
    with localcontext() as context:
        context.prec = 34
        closes = [Decimal(point.close) for point in points]
        opens = [Decimal(point.open) for point in points]
        highs = [Decimal(point.high) for point in points]
        lows = [Decimal(point.low) for point in points]
        volumes = [Decimal(point.volume) for point in points]
        index = len(points) - 1
        latest: dict[str, FeatureValue] = return_values(closes, index)

        moving_averages = {
            window: simple_moving_average(closes, index, window)
            for window in (20, 50, 100, 200)
        }
        for window, value in moving_averages.items():
            latest[f"SMA_{window}"] = value
        for window in (20, 50):
            latest[f"EMA_{window}"] = exponential_moving_average_series(closes, window)[-1]
        for window in (20, 50, 200):
            latest[f"DISTANCE_SMA_{window}"] = distance_from_average(closes[index], moving_averages[window])
        latest["RSI_14"] = rsi_series(closes)[-1]

        true_ranges = true_range_series(highs, lows, closes)
        atr = atr_series(true_ranges)[-1]
        daily_returns = daily_return_series(closes)
        latest["TRUE_RANGE"] = true_ranges[-1]
        latest["ATR_14"] = atr
        latest["ATR_PCT_14"] = divide(atr, closes[index]) if atr is not None else None
        for window in (20, 60):
            latest[f"VOLATILITY_{window}"] = annualized_volatility(daily_returns, index, window)

        latest.update(liquidity_values(points, volumes, index))
        latest.update(session_values(opens, highs, lows, closes, index))
        latest.update(price_structure_values(highs, closes, index))
        latest.update(
            trend_state_values(
                closes[index],
                moving_averages[20],
                moving_averages[50],
                moving_averages[200],
            )
        )
        return latest
