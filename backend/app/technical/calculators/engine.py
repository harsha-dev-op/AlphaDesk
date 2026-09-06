from decimal import Decimal, localcontext

from app.technical.calculators.common import FeatureValue, PricePoint
from app.technical.calculators.liquidity import calculate_liquidity
from app.technical.calculators.momentum import calculate_rsi
from app.technical.calculators.price_structure import calculate_price_structure
from app.technical.calculators.returns import calculate_returns
from app.technical.calculators.session import calculate_session
from app.technical.calculators.trend import calculate_trend, calculate_trend_states
from app.technical.calculators.volatility import calculate_volatility


def calculate_feature_frame(
    points: list[PricePoint],
    feature_codes: tuple[str, ...] | None = None,
) -> list[dict[str, FeatureValue]]:
    """Orchestrate independently testable feature-family calculators."""
    if not points:
        return []
    with localcontext() as context:
        context.prec = 34
        selected = set(feature_codes) if feature_codes is not None else None
        output: list[dict[str, FeatureValue]] = [{} for _ in points]
        closes = [Decimal(point.close) for point in points]
        opens = [Decimal(point.open) for point in points]
        highs = [Decimal(point.high) for point in points]
        lows = [Decimal(point.low) for point in points]
        return_codes = {"RET_1D", "RET_5D", "RET_10D", "RET_20D", "MOM_1M_21D", "MOM_3M_63D", "MOM_6M_126D"}
        trend_codes = {
            "SMA_20", "SMA_50", "SMA_100", "SMA_200", "EMA_20", "EMA_50",
            "DISTANCE_SMA_20", "DISTANCE_SMA_50", "DISTANCE_SMA_200",
            "ABOVE_SMA_20", "ABOVE_SMA_50", "ABOVE_SMA_200", "SMA_20_ABOVE_50", "SMA_50_ABOVE_200",
        }
        state_codes = {"ABOVE_SMA_20", "ABOVE_SMA_50", "ABOVE_SMA_200", "SMA_20_ABOVE_50", "SMA_50_ABOVE_200"}
        volatility_codes = {"TRUE_RANGE", "ATR_14", "ATR_PCT_14", "VOLATILITY_20", "VOLATILITY_60"}
        liquidity_codes = {"AVG_VOLUME_20", "AVG_VOLUME_50", "VOLUME_RATIO_20", "VOLUME_RATIO_50", "AVG_TRADED_VALUE_20"}
        session_codes = {"GAP_PCT_1D", "INTRADAY_RETURN", "RANGE_PCT", "CLOSE_LOCATION"}
        structure_codes = {"PRIOR_HIGH_20", "BREAKOUT_PCT_20"}
        if selected is None or selected & return_codes:
            calculate_returns(closes, output)
        moving_averages = calculate_trend(closes, output) if selected is None or selected & trend_codes else None
        if selected is None or "RSI_14" in selected:
            calculate_rsi(closes, output)
        if selected is None or selected & volatility_codes:
            calculate_volatility(highs, lows, closes, output, selected)
        if selected is None or selected & liquidity_codes:
            calculate_liquidity(points, output)
        if selected is None or selected & session_codes:
            calculate_session(opens, highs, lows, closes, output)
        if selected is None or selected & structure_codes:
            calculate_price_structure(highs, closes, output)
        if (selected is None or selected & state_codes) and moving_averages is not None:
            calculate_trend_states(closes, moving_averages, output)
        return output
