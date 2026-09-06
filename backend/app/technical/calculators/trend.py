from decimal import Decimal

from app.technical.calculators.common import FeatureValue, divide, mean


def simple_moving_average(closes: list[Decimal], index: int, window: int) -> Decimal | None:
    return mean(closes[index - window + 1 : index + 1]) if index >= window - 1 else None


def exponential_moving_average_series(closes: list[Decimal], window: int) -> list[Decimal | None]:
    series: list[Decimal | None] = [None] * len(closes)
    if len(closes) >= window:
        alpha = Decimal(2) / Decimal(window + 1)
        series[window - 1] = mean(closes[:window])
        for index in range(window, len(closes)):
            previous = series[index - 1]
            assert previous is not None
            series[index] = alpha * closes[index] + (Decimal(1) - alpha) * previous
    return series


def distance_from_average(close: Decimal, average: Decimal | None) -> Decimal | None:
    return divide(close, average) - Decimal(1) if average not in (None, Decimal(0)) else None


def trend_state_values(
    close: Decimal,
    sma20: Decimal | None,
    sma50: Decimal | None,
    sma200: Decimal | None,
) -> dict[str, bool | None]:
    return {
        "ABOVE_SMA_20": close > sma20 if sma20 is not None else None,
        "ABOVE_SMA_50": close > sma50 if sma50 is not None else None,
        "ABOVE_SMA_200": close > sma200 if sma200 is not None else None,
        "SMA_20_ABOVE_50": sma20 > sma50 if sma20 is not None and sma50 is not None else None,
        "SMA_50_ABOVE_200": sma50 > sma200 if sma50 is not None and sma200 is not None else None,
    }


def calculate_trend(closes: list[Decimal], output: list[dict[str, FeatureValue]]) -> dict[int, list[Decimal | None]]:
    count = len(closes)
    moving_averages: dict[int, list[Decimal | None]] = {}
    for window in (20, 50, 100, 200):
        series: list[Decimal | None] = [None] * count
        for index in range(window - 1, count):
            series[index] = simple_moving_average(closes, index, window)
        moving_averages[window] = series
        for index, value in enumerate(series):
            output[index][f"SMA_{window}"] = value

    for window in (20, 50):
        series = exponential_moving_average_series(closes, window)
        for index, value in enumerate(series):
            output[index][f"EMA_{window}"] = value

    for window in (20, 50, 200):
        for index, average in enumerate(moving_averages[window]):
            output[index][f"DISTANCE_SMA_{window}"] = distance_from_average(closes[index], average)
    return moving_averages


def calculate_trend_states(closes: list[Decimal], moving_averages: dict[int, list[Decimal | None]], output: list[dict[str, FeatureValue]]) -> None:
    for index in range(len(closes)):
        sma20 = moving_averages[20][index]
        sma50 = moving_averages[50][index]
        sma200 = moving_averages[200][index]
        output[index].update(trend_state_values(closes[index], sma20, sma50, sma200))
