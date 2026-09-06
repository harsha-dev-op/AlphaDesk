from decimal import Decimal

from app.technical.calculators.common import FeatureValue, divide, mean


def true_range_series(highs: list[Decimal], lows: list[Decimal], closes: list[Decimal]) -> list[Decimal]:
    true_ranges: list[Decimal] = []
    for index in range(len(closes)):
        candidates = [highs[index] - lows[index]]
        if index:
            candidates.extend((abs(highs[index] - closes[index - 1]), abs(lows[index] - closes[index - 1])))
        true_ranges.append(max(candidates))
    return true_ranges


def atr_series(true_ranges: list[Decimal]) -> list[Decimal | None]:
    atr: list[Decimal | None] = [None] * len(true_ranges)
    if len(true_ranges) >= 14:
        atr[13] = mean(true_ranges[:14])
        for index in range(14, len(true_ranges)):
            previous = atr[index - 1]
            assert previous is not None
            atr[index] = (previous * Decimal(13) + true_ranges[index]) / Decimal(14)
    return atr


def daily_return_series(closes: list[Decimal]) -> list[Decimal | None]:
    daily_returns: list[Decimal | None] = [None]
    for index in range(1, len(closes)):
        daily_returns.append((divide(closes[index], closes[index - 1]) - Decimal(1)) if closes[index - 1] != 0 else None)
    return daily_returns


def annualized_volatility(daily_returns: list[Decimal | None], index: int, window: int) -> Decimal | None:
    if index < window:
        return None
    sample = daily_returns[index - window + 1 : index + 1]
    if not all(item is not None for item in sample):
        return None
    concrete = [item for item in sample if item is not None]
    average = mean(concrete)
    variance = sum(((item - average) ** 2 for item in concrete), Decimal(0)) / Decimal(window - 1)
    return variance.sqrt() * Decimal(252).sqrt()


def calculate_volatility(
    highs: list[Decimal],
    lows: list[Decimal],
    closes: list[Decimal],
    output: list[dict[str, FeatureValue]],
    feature_codes: set[str] | None = None,
) -> None:
    requested = {"TRUE_RANGE", "ATR_14", "ATR_PCT_14", "VOLATILITY_20", "VOLATILITY_60"} if feature_codes is None else feature_codes
    needs_atr = bool(requested & {"TRUE_RANGE", "ATR_14", "ATR_PCT_14"})
    true_ranges = true_range_series(highs, lows, closes) if needs_atr else []
    atr = atr_series(true_ranges) if needs_atr else []
    volatility_windows = [window for window in (20, 60) if f"VOLATILITY_{window}" in requested]
    daily_returns = daily_return_series(closes) if volatility_windows else []
    for index in range(len(closes)):
        if "TRUE_RANGE" in requested:
            output[index]["TRUE_RANGE"] = true_ranges[index]
        if "ATR_14" in requested:
            output[index]["ATR_14"] = atr[index]
        if "ATR_PCT_14" in requested:
            output[index]["ATR_PCT_14"] = divide(atr[index], closes[index]) if atr[index] is not None else None
        for window in volatility_windows:
            output[index][f"VOLATILITY_{window}"] = annualized_volatility(daily_returns, index, window)
