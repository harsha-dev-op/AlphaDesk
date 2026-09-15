from decimal import Decimal

from app.technical.calculators.common import FeatureValue, PricePoint, divide, mean


def liquidity_values(
    points: list[PricePoint],
    volumes: list[Decimal],
    index: int,
    feature_codes: set[str] | None = None,
) -> dict[str, Decimal | None]:
    values: dict[str, Decimal | None] = {}
    for window in (20, 50):
        average_code = f"AVG_VOLUME_{window}"
        ratio_code = f"VOLUME_RATIO_{window}"
        if feature_codes is not None and not {average_code, ratio_code} & feature_codes:
            continue
        average = mean(volumes[index - window + 1 : index + 1]) if index >= window - 1 else None
        if feature_codes is None or average_code in feature_codes:
            values[average_code] = average
        if feature_codes is None or ratio_code in feature_codes:
            values[ratio_code] = divide(volumes[index], average) if average is not None else None
    if feature_codes is None or "AVG_TRADED_VALUE_20" in feature_codes:
        traded_average: Decimal | None = None
        if index >= 19:
            traded_values = [points[position].traded_value for position in range(index - 19, index + 1)]
            if all(value is not None for value in traded_values):
                traded_average = mean([Decimal(value) for value in traded_values if value is not None])
        values["AVG_TRADED_VALUE_20"] = traded_average
    return values


def calculate_liquidity(
    points: list[PricePoint],
    output: list[dict[str, FeatureValue]],
    feature_codes: set[str] | None = None,
) -> None:
    volumes = [Decimal(point.volume) for point in points]
    for index in range(len(points)):
        output[index].update(liquidity_values(points, volumes, index, feature_codes))
