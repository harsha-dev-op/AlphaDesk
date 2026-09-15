from decimal import Decimal

from app.technical.calculators.common import FeatureValue, divide


def session_values(
    opens: list[Decimal],
    highs: list[Decimal],
    lows: list[Decimal],
    closes: list[Decimal],
    index: int,
    feature_codes: set[str] | None = None,
) -> dict[str, Decimal | None]:
    values = {
        "GAP_PCT_1D": (divide(opens[index], closes[index - 1]) - Decimal(1)) if index and closes[index - 1] != 0 else None,
        "INTRADAY_RETURN": (divide(closes[index], opens[index]) - Decimal(1)) if opens[index] != 0 else None,
        "RANGE_PCT": divide(highs[index] - lows[index], closes[index]),
        "CLOSE_LOCATION": divide(closes[index] - lows[index], highs[index] - lows[index]),
    }
    if feature_codes is None:
        return values
    return {code: value for code, value in values.items() if code in feature_codes}


def calculate_session(
    opens: list[Decimal],
    highs: list[Decimal],
    lows: list[Decimal],
    closes: list[Decimal],
    output: list[dict[str, FeatureValue]],
    feature_codes: set[str] | None = None,
) -> None:
    for index in range(len(closes)):
        output[index].update(session_values(opens, highs, lows, closes, index, feature_codes))
