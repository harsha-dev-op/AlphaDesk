from decimal import Decimal, localcontext

from app.technical.calculators.common import FeatureValue, PricePoint
from app.technical.calculators.liquidity import calculate_liquidity
from app.technical.calculators.momentum import calculate_rsi
from app.technical.calculators.price_structure import calculate_price_structure
from app.technical.calculators.returns import calculate_returns
from app.technical.calculators.session import calculate_session
from app.technical.calculators.trend import calculate_trend, calculate_trend_states
from app.technical.calculators.volatility import calculate_volatility


def calculate_feature_frame(points: list[PricePoint]) -> list[dict[str, FeatureValue]]:
    """Orchestrate independently testable feature-family calculators."""
    if not points:
        return []
    with localcontext() as context:
        context.prec = 34
        output: list[dict[str, FeatureValue]] = [{} for _ in points]
        closes = [Decimal(point.close) for point in points]
        opens = [Decimal(point.open) for point in points]
        highs = [Decimal(point.high) for point in points]
        lows = [Decimal(point.low) for point in points]
        calculate_returns(closes, output)
        moving_averages = calculate_trend(closes, output)
        calculate_rsi(closes, output)
        calculate_volatility(highs, lows, closes, output)
        calculate_liquidity(points, output)
        calculate_session(opens, highs, lows, closes, output)
        calculate_price_structure(highs, closes, output)
        calculate_trend_states(closes, moving_averages, output)
        return output
