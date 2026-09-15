from decimal import Decimal

from app.technical.calculators.common import FeatureValue, divide


def price_structure_values(
    highs: list[Decimal],
    closes: list[Decimal],
    index: int,
    window: int = 20,
    feature_codes: set[str] | None = None,
) -> dict[str, Decimal | None]:
    """Return prior-range context without allowing the current high into that range."""
    prior_high = max(highs[index - window : index]) if index >= window else None
    breakout = divide(closes[index], prior_high) if prior_high is not None else None
    values = {
        "PRIOR_HIGH_20": prior_high,
        "BREAKOUT_PCT_20": breakout - Decimal(1) if breakout is not None else None,
    }
    if feature_codes is None:
        return values
    return {code: value for code, value in values.items() if code in feature_codes}


def calculate_price_structure(
    highs: list[Decimal],
    closes: list[Decimal],
    output: list[dict[str, FeatureValue]],
    feature_codes: set[str] | None = None,
) -> None:
    for index in range(len(closes)):
        output[index].update(
            price_structure_values(highs, closes, index, feature_codes=feature_codes)
        )
